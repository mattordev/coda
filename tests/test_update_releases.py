import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests
from semantic_version import Version

from utils import update_releases as releases


COMMIT = "a" * 40
TAG_OBJECT = "b" * 40


def response(body=b"", *, status=200, headers=None, chunks=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.status_code = status
    result.headers = {"Content-Length": str(len(body))} if headers is None else headers
    result.iter_content.return_value = [body] if chunks is None else chunks
    return result


def metadata(value):
    return response(json.dumps(value).encode("utf-8"))


def release_metadata(**changes):
    value = {
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-20T12:00:00Z",
        "tag_name": "v1.4.4",
        # Neither this mutable branch nor arbitrary remote URLs may be used.
        "target_commitish": "main",
        "zipball_url": "https://untrusted.invalid/source.zip",
    }
    value.update(changes)
    return value


def reference(kind="commit", sha=COMMIT):
    return {"ref": "refs/tags/v1.4.4", "object": {"type": kind, "sha": sha}}


class ReleaseSelectionTests(unittest.TestCase):
    def test_stable_tag_is_pinned_to_commit_not_branch_or_supplied_url(self):
        with patch.object(releases.requests, "get", side_effect=[
            metadata(release_metadata()), metadata(reference()),
        ]) as get:
            selected = releases.latest_release()

        self.assertEqual(selected.version, Version("1.4.4"))
        self.assertEqual(selected.tag, "v1.4.4")
        self.assertEqual(selected.commit, COMMIT)
        self.assertEqual(selected.archive_root, f"coda-{COMMIT}")
        self.assertEqual(selected.archive_url, f"https://codeload.github.com/mattordev/coda/zip/{COMMIT}")
        self.assertEqual([call.args[0] for call in get.call_args_list], [
            f"{releases.API_ROOT}/releases/latest",
            f"{releases.API_ROOT}/git/ref/tags/v1.4.4",
        ])
        for call in get.call_args_list:
            self.assertTrue(call.kwargs["stream"])
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(call.kwargs["timeout"], releases.REQUEST_TIMEOUT)

    def test_annotated_tag_resolves_its_object_not_server_supplied_url(self):
        annotated = reference("tag", TAG_OBJECT)
        annotated["object"]["url"] = "https://untrusted.invalid/tag"
        with patch.object(releases.requests, "get", side_effect=[
            metadata(release_metadata()), metadata(annotated),
            metadata({"object": {"type": "commit", "sha": COMMIT}}),
        ]) as get:
            self.assertEqual(releases.latest_release().commit, COMMIT)
        self.assertEqual(get.call_args.args[0], f"{releases.API_ROOT}/git/tags/{TAG_OBJECT}")

    def test_five_annotated_tags_are_supported(self):
        values = [release_metadata(), reference("tag", TAG_OBJECT)]
        values.extend({"object": {"type": "tag", "sha": TAG_OBJECT}} for _ in range(4))
        values.append({"object": {"type": "commit", "sha": COMMIT}})
        with patch.object(releases.requests, "get", side_effect=[metadata(value) for value in values]) as get:
            self.assertEqual(releases.latest_release().commit, COMMIT)
        self.assertEqual(get.call_count, 7)

    def test_annotated_tag_loop_is_bounded(self):
        values = [release_metadata(), reference("tag", TAG_OBJECT)]
        values.extend({"object": {"type": "tag", "sha": TAG_OBJECT}} for _ in range(5))
        with patch.object(releases.requests, "get", side_effect=[metadata(value) for value in values]) as get:
            with self.assertRaisesRegex(ValueError, "five"):
                releases.latest_release()
        self.assertEqual(get.call_count, 7)

    def test_drafts_prereleases_and_missing_publication_are_rejected(self):
        for change in (
            {"draft": True}, {"prerelease": True}, {"draft": None},
            {"prerelease": 0}, {"published_at": None}, {"published_at": ""},
        ):
            with self.subTest(change=change), patch.object(
                releases.requests, "get", return_value=metadata(release_metadata(**change)),
            ) as get:
                with self.assertRaises(ValueError):
                    releases.latest_release()
                self.assertEqual(get.call_count, 1)

    def test_invalid_release_tags_are_rejected_before_resolving(self):
        for tag in (None, 144, "", "v", "main", "v1.4", "v1.4.4-rc.1", "v1.4.4+build", "v1.4.4/other"):
            with self.subTest(tag=tag), patch.object(
                releases.requests, "get", return_value=metadata(release_metadata(tag_name=tag)),
            ) as get:
                with self.assertRaises(ValueError):
                    releases.latest_release()
                self.assertEqual(get.call_count, 1)

    def test_different_returned_tag_is_rejected(self):
        wrong = dict(reference(), ref="refs/tags/v1.4.5")
        with patch.object(releases.requests, "get", side_effect=[
            metadata(release_metadata()), metadata(wrong),
        ]):
            with self.assertRaisesRegex(ValueError, "different release tag"):
                releases.latest_release()

    def test_invalid_git_objects_are_rejected(self):
        for obj in (None, [], {}, {"type": "tree", "sha": COMMIT},
                    {"type": "commit", "sha": "a" * 39},
                    {"type": "commit", "sha": "g" * 40},
                    {"type": "commit", "sha": int("1" * 40)},
                    {"type": "commit", "sha": "main"}):
            with self.subTest(obj=obj), patch.object(releases.requests, "get", side_effect=[
                metadata(release_metadata()), metadata(dict(reference(), object=obj)),
            ]):
                with self.assertRaises(ValueError):
                    releases.latest_release()

    def test_invalid_json_and_non_object_metadata_are_rejected(self):
        for body in (b"not json", b"[]", b"null", b'"version"', b"1"):
            with self.subTest(body=body), patch.object(releases.requests, "get", return_value=response(body)):
                with self.assertRaises(ValueError):
                    releases.latest_release()

    def test_metadata_response_limit_is_enforced_before_tag_lookup(self):
        with patch.object(releases, "METADATA_BYTES", 8), \
                patch.object(releases.requests, "get", return_value=metadata(release_metadata())) as get:
            with self.assertRaisesRegex(ValueError, "size limit"):
                releases.latest_release()
        get.assert_called_once()

    def test_discovery_budget_is_shared_across_metadata_requests(self):
        with patch.object(releases.time, "monotonic", side_effect=[0, 44, 44, 46]), \
                patch.object(releases.requests, "get", return_value=metadata(release_metadata())) as get:
            with self.assertRaises(TimeoutError):
                releases.latest_release()
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["timeout"], (1, 1))

    def test_release_identity_cannot_mismatch_version_or_use_partial_commit(self):
        for tag, version, sha in (
            ("v1.4.4", Version("1.4.5"), COMMIT),
            ("v1.4.4", Version("1.4.4"), "abc123"),
            ("v1.4.4-rc.1", Version("1.4.4-rc.1"), COMMIT),
        ):
            with self.subTest(tag=tag, version=version, sha=sha), self.assertRaises(ValueError):
                releases.Release(tag, version, sha)


class ResponseBoundsTests(unittest.TestCase):
    def chunks(self, **overrides):
        options = {"byte_limit": 8, "deadline": 100}
        options.update(overrides)
        return list(releases.response_chunks("https://example.invalid/fixture", **options))

    def test_success_closes_response_and_ignores_empty_chunks(self):
        reply = response(b"abc", chunks=[b"a", b"", b"bc"])
        with patch.object(releases.time, "monotonic", return_value=0), \
                patch.object(releases.requests, "get", return_value=reply):
            self.assertEqual(self.chunks(), [b"a", b"bc"])
        reply.__exit__.assert_called_once()

    def test_http_errors_and_redirects_are_rejected_without_reading_body(self):
        for status in (403, 404, 429, 500, 301, 302, 307):
            reply = response(b"private remote error body", status=status)
            with self.subTest(status=status), patch.object(releases.time, "monotonic", return_value=0), \
                    patch.object(releases.requests, "get", return_value=reply) as get:
                with self.assertRaisesRegex(ValueError, f"HTTP {status}"):
                    self.chunks()
                self.assertFalse(get.call_args.kwargs["allow_redirects"])
                reply.iter_content.assert_not_called()
                reply.__exit__.assert_called_once()

    def test_network_timeout_is_propagated_without_retry(self):
        with patch.object(releases.time, "monotonic", return_value=0), \
                patch.object(releases.requests, "get", side_effect=requests.Timeout("offline")) as get:
            with self.assertRaises(requests.Timeout):
                self.chunks()
        get.assert_called_once()

    def test_declared_over_limit_negative_or_malformed_lengths_are_rejected(self):
        for length in ("9", "-1", "not a length"):
            reply = response(headers={"Content-Length": length})
            with self.subTest(length=length), patch.object(releases.time, "monotonic", return_value=0), \
                    patch.object(releases.requests, "get", return_value=reply):
                with self.assertRaises(ValueError):
                    self.chunks()
                reply.iter_content.assert_not_called()

    def test_body_size_is_bounded_without_content_length(self):
        with patch.object(releases.time, "monotonic", return_value=0), \
                patch.object(releases.requests, "get", return_value=response(b"123456789", headers={})):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.chunks()

    def test_truncated_and_overlong_bodies_are_rejected(self):
        for length in ("2", "4"):
            with self.subTest(length=length), patch.object(releases.time, "monotonic", return_value=0), \
                    patch.object(releases.requests, "get", return_value=response(b"abc", headers={"Content-Length": length})):
                with self.assertRaisesRegex(ValueError, "truncated"):
                    self.chunks()

    def test_expired_budget_makes_no_http_request(self):
        with patch.object(releases.time, "monotonic", return_value=100), \
                patch.object(releases.requests, "get") as get:
            with self.assertRaises(TimeoutError):
                self.chunks()
        get.assert_not_called()

    def test_budget_checked_between_chunks_and_response_closed_on_timeout(self):
        reply = response(b"ab", chunks=[b"a", b"b"])
        with patch.object(releases.time, "monotonic", side_effect=[0, 1, 2]), \
                patch.object(releases.requests, "get", return_value=reply):
            with self.assertRaises(TimeoutError):
                self.chunks(deadline=2)
        reply.__exit__.assert_called_once()

    def test_socket_timeouts_shrink_to_remaining_budget(self):
        with patch.object(releases.time, "monotonic", return_value=0), \
                patch.object(releases.requests, "get", return_value=response(b"a")) as get:
            self.assertEqual(self.chunks(deadline=2), [b"a"])
        self.assertEqual(get.call_args.kwargs["timeout"], (2, 2))

    def test_identity_encoding_is_requested_without_dropping_api_headers(self):
        with patch.object(releases.time, "monotonic", return_value=0), \
                patch.object(releases.requests, "get", return_value=response(b"a")) as get:
            self.chunks(headers={"Accept": "application/vnd.github+json"})
        self.assertEqual(get.call_args.kwargs["headers"]["Accept-Encoding"], "identity")
        self.assertEqual(get.call_args.kwargs["headers"]["Accept"], "application/vnd.github+json")

    def test_unexpected_content_encoding_is_rejected_before_decoding(self):
        reply = response(b"abc", headers={"Content-Length": "3", "Content-Encoding": "gzip"})
        with patch.object(releases.time, "monotonic", return_value=0), \
                patch.object(releases.requests, "get", return_value=reply):
            with self.assertRaises(ValueError):
                self.chunks()
        reply.iter_content.assert_not_called()


class InstalledVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.version_file = self.root / "version.json"

    def test_version_is_read_from_explicit_root_without_using_cwd(self):
        self.version_file.write_text('{"version":"1.4.4"}', encoding="utf-8")
        with patch.object(Path, "cwd", side_effect=AssertionError("cwd used")):
            self.assertEqual(releases.read_installed_version(self.root), Version("1.4.4"))

    def test_missing_version_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            releases.read_installed_version(self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_local_version_is_not_overwritten(self):
        for content in ("not json", "[]", "null", "{}", '{"version":1}',
                        '{"version":"1.4"}', '{"version":"1.4.4-rc.1"}',
                        '{"version":"1.4.4+build"}', " " * 4097):
            with self.subTest(content=content[:60]):
                self.version_file.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    releases.read_installed_version(self.root)
                self.assertEqual(self.version_file.read_text(encoding="utf-8"), content)

    def test_release_tag_gate_accepts_matching_version_only(self):
        self.version_file.write_text('{"version":"1.4.4"}', encoding="utf-8")
        for tag in ("v1.4.4", "1.4.4"):
            releases.validate_release_tag(self.root, tag)
        for tag in ("v1.4.5", "main", "v1.4.4-rc.1", "v1.4.4+build"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                releases.validate_release_tag(self.root, tag)

    def test_download_uses_exact_selected_commit_and_refuses_overwrite(self):
        selected = releases.Release("v1.4.4", Version("1.4.4"), COMMIT)
        with patch.object(releases, "response_chunks", return_value=iter([b"zip", b" fixture"])) as chunks:
            releases.download_archive(self.root, "release.zip", selected)
        self.assertEqual((self.root / "release.zip").read_bytes(), b"zip fixture")
        self.assertEqual(chunks.call_args.args, (selected.archive_url,))
        self.assertEqual(chunks.call_args.kwargs["byte_limit"], releases.ARCHIVE_BYTES)
        with patch.object(releases, "response_chunks") as chunks:
            with self.assertRaises(FileExistsError):
                releases.download_archive(self.root, "release.zip", selected)
        chunks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
