# Bundled MCP and pre-warming validation

Initial local implementation review, 28 October 2026. Subsequent CI results and
completion status are recorded on issues #166 and #117. A working subset is not
completion of every acceptance criterion.

## #166 audit and implementation

The starting implementation provided a hard-coded filesystem launch mapping using
the active interpreter. Missing were the general manifest catalog/schema, explicit
enablement, validated settings, inspectable availability, and upgrade preservation.

| Requirement | Implementation and evidence |
| --- | --- |
| Manifest/catalog and instance schemas | `ai/mcp/bundled.py`, `config.py`; strict keys, versions, IDs, duplicate JSON and self-contained JSON Schema checks. `tests/test_mcp_bundle_catalog.py` exercises malformed metadata, valid neighbors and duplicate IDs with no winner. |
| Portable/current interpreter launch | Bundle-relative assets resolve from the installation; `sys.executable`, no shell. `tests/test_mcp_bundled.py` launches real MCP from relocated installs and extracted archives, including spaces/Unicode and an empty PATH, through a separate disposable environment. |
| Data-only scanning/default deny | Catalog/manager construction tests forbid imports/launches. Explicit enablement, process trust and exact tool permissions are independent; disabled/untrusted instances cannot launch through manager, catalog, eager startup or direct transport. |
| Settings/defaults and root contract | Only explicitly non-security scalar defaults; strict overrides and optional JSON config checks. Missing/empty/relative fixture roots fail before activation. Existing filesystem server remains owner-authored and independently enforces its permissions. |
| Path and link policy | No absolute/drive/UNC/traversal entrypoints or symlink/junction assets. Windows junction test runs natively; symlink tests skip explicitly when privileges are unavailable. |
| Availability and errors | `inspect_bundles()`, manager `inspect()`, and `python -m ai.mcp.bundled`; separate catalog availability, instance enablement, trust and connection state, with safe per-file errors. Missing dependency/interpreter tests. |
| Existing permission/lifecycle semantics | Existing confirmation, dynamic denial, revision/expiry, session, local/remote and intent regression suites remain necessary and pass. No metadata grants trust or replays actions. |
| Update/rollback | Preserve exact private `mcp_servers/*.json` and `integrations/*/*.config.json`; reject links, active definitions in archives and collisions with shipped assets. Archive upgrade and failed-start rollback retain byte-identical permissions. Configuration fingerprints reject stale activation after an operator changes/revokes permissions. External config remains external. |
| Documentation | `mcp-bundled.md`, local MCP docs, walkthrough, filesystem setup, MCP directory README and updater recovery explain choices, migration and limitations. |

Layout decision: independent manifests beside existing `integrations/<directory>`
servers, preserving `coda-filesystem`; no mandatory index or server relocation.
The existing source/archive + shared locked dependencies contract remains. The
managed-environment archive test reuses the test runner's installed dependencies
offline; it does not claim a fresh network install of every locked requirement.

## #117 implementation

| Requirement | Implementation and evidence |
| --- | --- |
| Opt-in independent configuration | `CODA_PREWARM_LLM/TTS/STT`, corresponding positive/negative CLI overrides, default disabled. Existing model/provider configuration is reused; precedence tests cover each subsystem. |
| Eligible local providers | Existing first intent LLM provider, configured PocketTTS, selected faster-whisper chain. Cloud TTS/STT and commercial LLM APIs are not warmed. Normal device/model selection remains in effect. |
| Shared background initialization | `runtime/model_warmup.py`; LLM first, then speech models, with serialized background loads and a free-RAM guard. PocketTTS/Whisper use retained caches and initialization locks. Concurrent first-use tests construct each local model once. |
| LLM preparation | Bounded one-token synthetic request to configured Ollama/llama.cpp server; no conversation history or user input. Real disposable loopback HTTP test proves first inference waits for warming and uses the same model. |
| Status and failure | Safe warming/ready/failed/skipped snapshots, durations and memory observations; `debug status` shows state/duration. Failures remain nonfatal and leave normal fallback available. |
| Reload/shutdown | Retire before resetting providers and scheduling replacement tasks. Bounded cancellation, generation checks and closed owners discard late loads. Tests cover shutdown during native loading, stale readiness and reload order. |
| Measurement and trade-offs | Record elapsed preparation duration, process RSS and already-initialized PyTorch CUDA allocation where measurable. External server memory is unknown. `model-prewarming.md` explains cold/warm comparison, download/memory implications and native-loader cancellation limits. |

## Checks and outstanding verification

- Full local suite: **801 tests, OK, skipped=6**, with an empty private MCP config
  directory. Skips are explicitly gated platform/environment/link-privilege checks.
- Additional extracted-archive/current-interpreter test: **1 test, OK**.
- After making background order deterministic, focused lifecycle/catalog/shutdown:
  **37 tests, OK, skipped=1**.
- Changed production files: errors-only Pylint passed with the repository's existing
  `E1120` exclusion. `pip check` and `git diff --check` passed.
- No live user models were started for performance claims; no real-model cold/warm
  latency or RAM/VRAM improvement is asserted. Follow the documented comparison
  procedure with the intended providers/hardware for product verification.
- Windows/Python 3.11 is the local execution environment. The existing CI matrix
  covers Windows/Ubuntu and Python 3.11/3.12. It had not run on this implementation
  at the time of initial local validation. Refer to the issues for later CI evidence;
  earlier green runs belong to the prior HEAD.

Initial validation left the implementation on `feature/mcp-and-tools` for review,
before committing, pushing or changing issue/project status. The owner's filesystem
server edits and private configuration are separate from these infrastructure changes.
