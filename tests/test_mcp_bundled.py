"""Portable bundle selection does not launch/import code or grant authority."""

from dataclasses import replace
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv
import zipfile

from ai.mcp import bundled
from ai.mcp.config import discover_definitions
from ai.mcp.transport import SDKStdioTransport


class BundledMCPTests(unittest.TestCase):
    def test_persistent_creation_rechecks_write_grants_without_restarting_session(self):
        from ai.intents import IntentRequest
        from ai.mcp.runtime import LocalMCPManager

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            permissions = root / "permissions.json"
            policy = {"read": {"allowed_roots": [str(root)], "blocked_paths": []},
                      "write": {"enabled": True, "allowed_roots": [str(root)]}}
            permissions.write_text(json.dumps(policy), encoding="utf-8")
            configs = root / "servers"
            configs.mkdir()
            (configs / "files.json").write_text(json.dumps({
                "id": "persistent", "bundled_server": "filesystem", "enabled": True,
                "trusted": True, "allowed_tools": ["create_file"], "session_mode": "persistent",
                "settings": {"config_file": str(permissions)},
            }), encoding="utf-8")
            manager = LocalMCPManager(discover_definitions(configs).servers)
            try:
                found = manager.discover("persistent")
                self.assertTrue(found.success, found.error)
                intent = next(i for i in found.data if i.name == "mcp.persistent.create_file")
                target = root / "new.txt"
                request = IntentRequest(intent, "Create a file", 1,
                                        arguments={"path": str(target), "content": "permitted"})
                self.assertEqual(manager.executor(intent.name).execute(request).error.code, "confirmation_required")
                self.assertFalse(target.exists())
                self.assertTrue(manager.executor(intent.name).execute(request, confirmed=True).success)
                self.assertEqual(target.read_text(encoding="utf-8"), "permitted")
                policy["write"]["enabled"] = False
                permissions.write_text(json.dumps(policy), encoding="utf-8")
                revoked = root / "revoked.txt"
                request = replace(request, arguments={"path": str(revoked)})
                denied = manager.executor(intent.name).execute(request, confirmed=True)
                self.assertFalse(denied.success)
                self.assertEqual(denied.data["structuredContent"]["error"]["code"], "write_disabled")
                self.assertFalse(revoked.exists())
            finally:
                self.assertTrue(manager.close()["persistent"].success)

    def test_real_range_reads_preserve_schemas_partiality_cancellation_and_revocation(self):
        from ai.intents import IntentRequest
        from ai.mcp.runtime import LocalMCPManager
        from threading import Event

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "large.txt"
            prefix_bytes = 2 * 1024 * 1024
            source.write_bytes(b"a" * prefix_bytes + "\u00e9\r\nend".encode("utf-8"))
            permissions = root / "permissions.json"
            policy = {"read": {"allowed_roots": [str(root)], "blocked_paths": []}}
            configs = root / "servers"
            configs.mkdir()
            for mode in ("operation", "persistent"):
                with self.subTest(mode=mode):
                    permissions.write_text(json.dumps(policy), encoding="utf-8")
                    (configs / "files.json").write_text(json.dumps({
                        "id": "ranges", "bundled_server": "filesystem", "enabled": True,
                        "trusted": True, "allowed_tools": ["read_file"], "session_mode": mode,
                        "settings": {"config_file": str(permissions)},
                    }), encoding="utf-8")
                    definitions = discover_definitions(configs)
                    self.assertFalse(definitions.errors)
                    manager = LocalMCPManager(definitions.servers)
                    try:
                        found = manager.discover("ranges")
                        self.assertTrue(found.success, found.error)
                        intent = next(i for i in found.data if i.name == "mcp.ranges.read_file")
                        self.assertIn("max_bytes", intent.input_schema["properties"])
                        request = IntentRequest(intent, "Read the tail", 1, arguments={
                            "path": str(source), "offset_bytes": prefix_bytes, "max_bytes": 4,
                        })
                        result = manager.executor(intent.name).execute(request)
                        self.assertTrue(result.success, result.error)
                        structured = result.data["structuredContent"]
                        self.assertTrue(structured["partial"])
                        self.assertEqual(structured["next_offset_bytes"], prefix_bytes + 4)
                        self.assertTrue(result.data["content"][0]["text"].endswith("\u00e9\n"))
                        cancelled = Event()
                        cancelled.set()
                        self.assertEqual(manager.executor(intent.name).execute(
                            request, cancel_event=cancelled).error.code, "cancelled")
                        malformed = replace(request, arguments={"path": str(source), "max_bytes": True})
                        self.assertEqual(manager.executor(intent.name).execute(malformed).error.code, "invalid_arguments")
                        permissions.write_text(json.dumps({"read": {
                            "allowed_roots": [], "blocked_paths": [],
                        }}), encoding="utf-8")
                        denied = manager.executor(intent.name).execute(request)
                        self.assertFalse(denied.success)
                        self.assertEqual(denied.data["structuredContent"]["error"]["code"], "outside_allowed_roots")
                    finally:
                        self.assertTrue(manager.close()["ranges"].success)

    def test_creation_requires_allowlist_and_confirmation_before_real_server_mutation(self):
        from ai.intents import IntentRequest
        from ai.mcp.runtime import LocalMCPManager

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            permissions = root / 'permissions.json'
            config = {'read': {'allowed_roots': [str(root)], 'blocked_paths': ['**/.env']},
                      'write': {'enabled': True, 'allowed_roots': [str(root)],
                                'require_confirmation': False}}
            permissions.write_text(json.dumps(config), encoding='utf-8')
            configs = root / 'servers'
            configs.mkdir()
            (configs / 'files.json').write_text(json.dumps({
                'id': 'files', 'bundled_server': 'filesystem', 'enabled': True,
                'trusted': True, 'allowed_tools': ['list_directory'],
                'settings': {'config_file': str(permissions)},
            }), encoding='utf-8')
            found = discover_definitions(configs)
            self.assertEqual(found.errors, {})
            definition = found.servers[0]
            manager = LocalMCPManager([definition])
            self.addCleanup(manager.close)
            discovered = manager.discover('files')
            self.assertTrue(discovered.success, discovered.error)
            intent = next(item for item in discovered.data if item.name == 'mcp.files.create_file')
            target = root / 'created.txt'
            request = IntentRequest(intent, 'Create a file', 1,
                                    arguments={'path': str(target), 'content': 'confirmed'})
            self.assertEqual(manager.executor(intent.name).execute(request).error.code, 'permission_denied')
            self.assertFalse(target.exists())
            allowed = replace(definition, allowed_tools=frozenset({'create_file', 'create_directory'}))
            self.assertTrue(manager.replace_definition(allowed).success)
            discovered = manager.discover('files')
            self.assertTrue(discovered.success, discovered.error)
            intent = next(item for item in discovered.data if item.name == 'mcp.files.create_file')
            request = IntentRequest(intent, 'Create a file', 1,
                                    arguments={'path': str(target), 'content': 'confirmed'})
            self.assertEqual(manager.executor(intent.name).execute(request).error.code, 'confirmation_required')
            self.assertFalse(target.exists())
            outcome = manager.executor(intent.name).execute(request, confirmed=True)
            self.assertTrue(outcome.success, outcome.error)
            self.assertEqual(target.read_text(encoding='utf-8'), 'confirmed')
            directory_intent = next(item for item in discovered.data if item.name == 'mcp.files.create_directory')
            directory_target = root / 'created-directory'
            directory_request = IntentRequest(directory_intent, 'Create a directory', 1,
                                              arguments={'path': str(directory_target)})
            self.assertEqual(manager.executor(directory_intent.name).execute(directory_request).error.code,
                             'confirmation_required')
            self.assertFalse(directory_target.exists())
            self.assertTrue(manager.executor(directory_intent.name).execute(directory_request, confirmed=True).success)
            self.assertTrue(directory_target.is_dir())
            config['write']['enabled'] = False
            permissions.write_text(json.dumps(config), encoding='utf-8')
            revoked = root / 'revoked.txt'
            request = IntentRequest(intent, 'Create another file', 1, arguments={'path': str(revoked)})
            outcome = manager.executor(intent.name).execute(request, confirmed=True)
            self.assertFalse(outcome.success)
            self.assertEqual(outcome.data['structuredContent']['error']['code'], 'write_disabled')
            self.assertFalse(revoked.exists())

    def test_new_read_tool_requires_explicit_grant_and_reads_through_real_server(self):
        from ai.intents import IntentRequest
        from ai.mcp.runtime import LocalMCPManager

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("Python 3.11 and 3.12", encoding="utf-8")
            permissions = root / "permissions.json"
            permissions.write_text(json.dumps({"read": {
                "allowed_roots": [str(root)], "blocked_paths": ["**/.env"],
            }}), encoding="utf-8")
            configs = root / "servers"
            configs.mkdir()
            (configs / "files.json").write_text(json.dumps({
                "id": "files", "bundled_server": "filesystem", "enabled": True,
                "trusted": True, "allowed_tools": ["list_directory"],
                "settings": {"config_file": str(permissions)},
            }), encoding="utf-8")
            definition = discover_definitions(configs).servers[0]
            manager = LocalMCPManager([definition])
            self.addCleanup(manager.close)
            discovered = manager.discover("files")
            self.assertTrue(discovered.success, discovered.error)
            self.assertEqual({item.name for item in discovered.data}, {
                "mcp.files.list_directory", "mcp.files.read_file",
                "mcp.files.create_file", "mcp.files.create_directory",
                "mcp.files.get_file_metadata",
                "mcp.files.search_files",
                "mcp.files.search_content",
                "mcp.files.edit_file", "mcp.files.append_file",
                "mcp.files.copy_path",
            })
            intent = next(item for item in discovered.data if item.name == "mcp.files.read_file")
            request = IntentRequest(intent, "Read README", 1, arguments={"path": str(source)})
            self.assertEqual(manager.executor(intent.name).execute(request).error.code, "permission_denied")
            self.assertTrue(manager.replace_definition(replace(
                definition, allowed_tools=frozenset({"list_directory", "read_file"}),
            )).success)
            discovered = manager.discover("files")
            self.assertTrue(discovered.success, discovered.error)
            intent = next(item for item in discovered.data if item.name == "mcp.files.read_file")
            request = IntentRequest(intent, "Read README", 1, arguments={"path": str(source)})
            result = manager.executor(intent.name).execute(request)
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.data["content"][0]["text"], "Python 3.11 and 3.12")
            self.assertEqual(result.data["structuredContent"], {"kind": "file_contents"})
            blocked = root / ".env"
            blocked.write_text("private", encoding="utf-8")
            request = IntentRequest(intent, "Read file", 1, arguments={"path": str(blocked)})
            result = manager.executor(intent.name).execute(request)
            self.assertFalse(result.success)
            self.assertEqual(result.data["structuredContent"]["error"]["code"], "blocked_path")

    def install_manifest(self, directory):
        source = Path(__file__).resolve().parents[1] / "integrations" / "coda-filesystem" / "manifest.json"
        (directory / "manifest.json").write_bytes(source.read_bytes())

    def test_launch_is_portable_across_install_roots_and_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ("first install", "second-安装"):
                root = base / name
                script = root / "integrations" / "coda-filesystem" / "server.py"
                script.parent.mkdir(parents=True)
                script.write_text("raise AssertionError('must not import')", encoding="utf-8")
                self.install_manifest(script.parent)
                (script.parent / "filesystem.config.json").write_text(json.dumps({"read": {
                    "allowed_roots": [str(base)], "blocked_paths": [],
                }}), encoding="utf-8")
                config_dir = root / "mcp_servers"
                config_dir.mkdir()
                (config_dir / "filesystem.json").write_text(json.dumps({
                    "id": "files", "bundled_server": "filesystem",
                }), encoding="utf-8")
                with patch.object(bundled, "INSTALL_ROOT", root), patch("subprocess.Popen") as launch:
                    found = discover_definitions(config_dir)
                launch.assert_not_called()
                self.assertEqual(found.errors, {})
                definition = found.servers[0]
                self.assertEqual(definition.command, sys.executable)
                self.assertEqual(definition.args, (str(script.resolve()),))
                self.assertFalse(definition.trusted)
                self.assertEqual(definition.allowed_tools, frozenset())
                self.assertIsNone(definition.cwd)

    def test_unknown_ids_and_mixed_launch_settings_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "filesystem.json"
            for change in ({"bundled_server": "../filesystem"}, {"command": "evil"},
                           {"args": ["override"]}, {"cwd": str(Path(directory))},
                           {"bundled_server": None}, {"transport": "streamable-http", "url": "https://example.test"}):
                with self.subTest(change=change):
                    path.write_text(json.dumps({"id": "files", "bundled_server": "filesystem", **change}), encoding="utf-8")
                    found = discover_definitions(directory)
                    self.assertEqual(found.servers, ())
                    self.assertIn("filesystem.json", found.errors)

    def test_same_definition_really_launches_from_two_relocated_installations(self):
        source = bundled.INSTALL_ROOT / "integrations" / "coda-filesystem" / "server.py"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            allowed = base / "allowed"
            allowed.mkdir()
            (allowed / "main.py").touch()
            definition_json = json.dumps({"id": "filesystem", "bundled_server": "filesystem",
                                          "enabled": True, "trusted": True, "allowed_tools": ["list_directory"]})
            for name in ("install one", "relocated-安装"):
                with self.subTest(installation=name):
                    root = base / name
                    server_dir = root / "integrations" / "coda-filesystem"
                    server_dir.mkdir(parents=True)
                    (server_dir / "server.py").write_bytes(source.read_bytes())
                    self.install_manifest(server_dir)
                    (server_dir / "filesystem.config.json").write_text(json.dumps({"read": {
                        "allowed_roots": [str(allowed)], "blocked_paths": [],
                    }}), encoding="utf-8")
                    configs = root / "mcp_servers"
                    configs.mkdir()
                    (configs / "filesystem.json").write_text(definition_json, encoding="utf-8")
                    with patch.object(bundled, "INSTALL_ROOT", root):
                        found = discover_definitions(configs)
                    self.assertEqual(found.errors, {})
                    result = SDKStdioTransport().request(found.servers[0], "invoke", tool_name="list_directory",
                                                        arguments={"path": str(allowed)})
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(result.data["structuredContent"]["entries"], [{
                        "name": "main.py", "type": "file", "modified_at": (allowed / "main.py").stat().st_mtime,
                    }])

    def test_missing_or_linked_entrypoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bundled, "INSTALL_ROOT", Path(directory)):
            with self.assertRaises(ValueError):
                bundled.bundled_launch("filesystem")
            script = Path(directory) / "integrations" / "coda-filesystem" / "server.py"
            script.parent.mkdir(parents=True)
            self.install_manifest(script.parent)
            target = Path(directory) / "outside.py"
            target.write_text("", encoding="utf-8")
            try:
                script.symlink_to(target)
            except OSError:
                self.skipTest("Symbolic links unavailable")
            with self.assertRaises(ValueError):
                bundled.bundled_launch("filesystem")

    def test_real_bundled_filesystem_uses_coda_transport_and_structured_feedback(self):
        from ai.tools.presentation import prepare_tool_display, prepare_tool_response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            (allowed / "main.py").write_text("", encoding="utf-8")
            (allowed / ".env").write_text("do-not-read", encoding="utf-8")
            permissions = root / "permissions.json"
            permissions.write_text(json.dumps({"read": {
                "allowed_roots": [str(allowed)], "blocked_paths": ["**/.env"],
            }}), encoding="utf-8")
            config_dir = root / "mcp_servers"
            config_dir.mkdir()
            (config_dir / "filesystem.json").write_text(json.dumps({
                "id": "filesystem", "bundled_server": "filesystem", "trusted": True,
                "enabled": True, "settings": {"config_file": str(permissions)},
                "allowed_tools": ["list_directory"],
            }), encoding="utf-8")
            definition = discover_definitions(config_dir).servers[0]
            self.assertIsNone(definition.bundle_error)
            definition = replace(definition, cwd=directory)
            transport = SDKStdioTransport()
            for mode in ("operation", "persistent"):
                with self.subTest(mode=mode):
                    current = replace(definition, session_mode=mode)
                    try:
                        found = transport.request(current, "discover")
                        self.assertTrue(found.success, found.error)
                        self.assertEqual(found.data["tools"][0]["name"], "list_directory")
                        result = transport.request(current, "invoke", tool_name="list_directory", arguments={"path": str(allowed)})
                        self.assertTrue(result.success, result.error)
                        self.assertEqual(result.data["structuredContent"]["entries"], [{
                            "name": "main.py", "type": "file", "modified_at": (allowed / "main.py").stat().st_mtime,
                        }])
                        self.assertNotIn(".env", prepare_tool_display(result))
                        self.assertIn("1 file", prepare_tool_response(result))
                        denied = transport.request(current, "invoke", tool_name="list_directory", arguments={"path": str(root)})
                        self.assertFalse(denied.success)
                        self.assertEqual(denied.error.code, "mcp_tool_error")
                        self.assertIn("allowed locations", prepare_tool_response(denied))
                        blocked = transport.request(current, "invoke", tool_name="list_directory", arguments={"path": str(allowed / ".env")})
                        self.assertIn("blocked", prepare_tool_response(blocked))
                    finally:
                        self.assertTrue(transport.retire("filesystem").success)

    def test_untrusted_and_empty_allowlist_do_not_launch_a_bundle(self):
        from ai.mcp.config import LocalServerDefinition
        definition = LocalServerDefinition("filesystem", sys.executable, ("unused.py",))
        transport = SDKStdioTransport()
        with patch("subprocess.Popen") as launch:
            self.assertEqual(transport.request(definition, "discover").error.code, "permission_denied")
            self.assertEqual(transport.request(replace(definition, trusted=True), "invoke", tool_name="list_directory").error.code, "permission_denied")
        launch.assert_not_called()

    def test_extracted_archive_uses_managed_interpreter_with_empty_path(self):
        from utils import update_manager as updater
        import mcp

        repository = Path(__file__).resolve().parents[1]
        helper = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ai.mcp import bundled
from ai.mcp.config import discover_definitions
from ai.mcp.transport import SDKStdioTransport
bundled.INSTALL_ROOT = Path(sys.argv[2])
found = discover_definitions(bundled.INSTALL_ROOT / 'mcp_servers')
assert not found.errors, found.errors
definition = found.servers[0]
result = SDKStdioTransport().request(definition, 'invoke', tool_name='echo', arguments={'message':'archive probe'})
assert result.success, result.error
print(json.dumps({'python': sys.executable, 'command': definition.command, 'result': result.data['structuredContent']}))
'''
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            environment = base / 'updater environment'
            venv.EnvBuilder(with_pip=False).create(environment)
            python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            # Reuse this test runner's locked dependencies offline. The interpreter
            # itself really belongs to a different environment; no pip/network.
            packages = (environment / 'Lib/site-packages' if os.name == 'nt' else
                        environment / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
            dependency_root = str(Path(mcp.__file__).resolve().parents[1])
            (packages / 'coda-test-dependencies.pth').write_text(
                f'import site; site.addsitedir({dependency_root!r})\n', encoding='utf-8')
            for name in ('first archive', 'relocated-\u5b89\u88c5'):
                workspace = base / name
                workspace.mkdir()
                with zipfile.ZipFile(workspace / updater.ZIP_NAME, 'w') as archive:
                    archive.writestr('install/integrations/fixture/server.py', (repository / 'tests/mcp_stdio_server.py').read_bytes())
                    archive.writestr('install/integrations/fixture/manifest.json', json.dumps({
                        'id': 'fixture', 'version': '1.0.0', 'entrypoint': 'server.py', 'dependencies': ['mcp'],
                        'settings_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                    }))
                updater.extract_download(workspace)
                root = workspace / updater.EXTRACT_DIR / 'install'
                configs = root / 'mcp_servers'
                configs.mkdir()
                (configs / 'fixture.json').write_text(json.dumps({
                    'id': 'probe', 'bundled_server': 'fixture', 'enabled': True,
                    'trusted': True, 'allowed_tools': ['echo'],
                }), encoding='utf-8')
                result = subprocess.run([str(python), '-c', helper, str(repository), str(root)],
                                        cwd=base, env={**os.environ, 'PATH': ''}, capture_output=True,
                                        text=True, timeout=45, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(Path(payload['python']).resolve(), python.resolve())
                self.assertEqual(Path(payload['command']).resolve(), python.resolve())
                self.assertEqual(payload['result']['message'], 'archive probe')
