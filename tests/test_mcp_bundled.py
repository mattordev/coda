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
