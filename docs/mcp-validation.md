# MCP framework validation

Verified 2026-10-14 on Windows with Python 3.11.2, on the existing
`feature/mcp-and-tools` branch. This records implementation validation, not a
published release or a merge into a protected branch.

## Acceptance evidence

| Card / requirement | Implementation and verification |
| --- | --- |
| #139 architecture/security decisions | Common models, source-agnostic matching, separate parsing/permissions/context and SDK boundary described in `tool-execution.md` and `mcp-framework-walkthrough.md`; original issue decision discussion retained. |
| #140 native/external common execution | `test_tool_models`, `test_native_tool_adapter`, `test_external_tools`, `test_intent_dispatcher`; native parsing preserved and external exceptions normalized. |
| #141 local discovery/invocation | `test_mcp_config`, `test_mcp_runtime`; actual disposable SDK processes, schema/catalog validation, trust/allowlists, environment isolation, output limits, PID cleanup and failure isolation. |
| #145 retained sessions/shared state | `test_mcp_sessions`; two calls share a PID and observe counts 1 then 2, operation mode observes count 1 twice, retirement creates fresh state. |
| #145 first use/eager/idle/cleanup | Concurrent first discovery, eager failure isolation, idle/indefinite policies, active and queued retirement, timeout/cancellation, replacement invalidation and shutdown tests. Idle-boundary recovery does not replay submitted actions. |
| #142 natural-language/runtime routing | `test_mcp_intent_runtime`; a real stdio server invoked through `utils.on_command.run` with a deterministic classifier double returning schema arguments. Manual and voice feed this same runtime entry point. Existing voice and runtime suites verify queue/cancellation behavior. |
| #142 permissions/confirmation | Denial, exact saved arguments, one-use approval, expiry, dynamic denial recheck and runtime stop invalidation. Invalid/ambiguous/failed accepted calls never fall through to unrelated conversation. |
| #142 result context/privacy | Separate full `CommandResult.tool_result`, bounded local/cloud history, privacy before truncation, sanitized debug/display, existing nested context tests. |
| #143 remote support | `test_mcp_remote`; actual authenticated loopback HTTP servers, shared runtime invocation, persistent negotiation reuse, authentication and safe diagnostics, missing credentials, malformed/hanging responses, cancellation/recovery, domain errors and independent busy-server behavior. |
| #143 limits/recovery/ownership | JSON full-result limit, HTTP 413 and real streamed SSE event cap, disconnect accepted once, reconnect backoff. Client retirement/shutdown leaves the independently hosted server alive. |
| #144 reproducible setup/docs | Fresh isolated server venv installed `mcp==2.3.0`; the exact Python skeleton extracted from the walkthrough ran successfully with independent JSON loading, discovery, invocation and cleanup. Local relative documentation links checked. |

Natural-language tests deliberately use a controlled local classifier response:
they prove routing/schema/execution contracts, not accuracy of any particular
downloaded model or microphone. Exact canonical-name JSON requests work without
a classifier. Hardware audio, live third-party services and real integrations are
outside these disposable framework tests.

## Final commands and results

```text
python -m unittest discover -s tests -p "test_*.py" -q
697 tests in 120.473 seconds: OK, skipped=2

python -m pylint --rcfile=pylintrc --errors-only --disable=E1120 <changed Python files>
Passed

python -m pip check
No broken requirements found.

git diff --check
Passed
```

The two skips are the existing opt-in offline updater environment smoke and the
POSIX executable-symlink test (inapplicable on Windows). The full
suite was rerun after the final cancellation, configuration-revision and idle
retirement fixes. A focused 35-test lifecycle/routing group also passed. No
necessary tests were removed or weakened. The source-agnostic catalog test now
holds schema constant while changing source, since the classifier legitimately
needs schema metadata for argument extraction.

The opt-in updater smoke was subsequently enabled with
`CODA_RUN_UPDATE_ENV_SMOKE=1`: all 10 `tests.test_update_dependencies` tests passed
in 10.647 seconds, including real offline environment creation/verification.

The isolated smoke setup validates a fresh **server** environment with the
documented skeleton against the existing locked-dependency CODA host environment;
it does not claim a separate full fresh host installation.

Existing `.github/workflows/tests.yml` covers Windows/Ubuntu and Python 3.11/3.12,
with opt-in updater environment checks enabled. This machine has no installed
WSL distribution/container runtime and no Python 3.12 interpreter. No new Linux,
Python 3.12 or exact-commit CI result is claimed for these local commits. Run that
matrix when the branch is pushed/reviewed; publication is not part of this task.

## Architectural choices and practical limits

### Follow-up: readable tool speech

After the original full-suite run above, an echo smoke test exposed protocol JSON
being spoken. `ai/tools/presentation.py` now separates readable response text from
the result/context envelope. All 75 selected presentation, MCP/intent runtime,
context and execution tests passed in 34.445 seconds; errors-only Pylint and
whitespace checks passed. The author's actual echo server returned exactly
`Hello from CODA` and `Second call` as response text, while full payloads were
retained. The earlier 697-test result predates this presentation follow-up.

### Framework choices

Explicit-name first-use follow-up: 53 selected MCP/intent runtime, router and
factory tests passed in 17.275 seconds. Regression checks prove that an explicit
JSON call skips classification and discovers only its named server, and that
unavailable names do not trigger classification or fallback. The author's actual
lazy echo server took 0.917 seconds on first use and 0.010 seconds on reuse, with
no classifier invocation. Errors-only Pylint and whitespace checks passed.

- Existing Intent models and source-agnostic selection remain the shared contract;
  native parsing remains command-owned, external argument validation stays in the
  adapter, and SDK types stay in the MCP transport boundary.
- Operation/lazy is the compatible default. Persistent sessions own one
  worker thread/task each; optional eager discovery runs in the background.
- Trust is separate from exact per-tool allowlists. Confirmation is one saved
  action in the single-user runtime, consumed once, expiring after 60 seconds.
- Active timeout/cancellation retires conservatively; later connection recovery
  is distinct from replay. Equivalent-action fallback and richer permission
  profiles are extension possibilities, not shipped behavior.
- Full results remain separate from deterministic bounded display and local/cloud
  history. Privacy detection retains its existing limits.
- `max_output_bytes` is checked after parsing; SSE limits do not cap JSON bodies.
  SDK cleanup can extend beyond action deadlines. Remote state retention depends
  on the server, and CODA never stops remote server processes.
- Initial discovery failure is remembered; reconfigure/restart to retry. There is
  no file watcher/live catalog subscription, interactive OAuth/refresh, legacy
  HTTP+SSE configuration, slot filling or multi-tool planner.
- No real integration-specific modules were added. `mcp_stdio_server.py` and
  `mcp_http_server.py` are clearly disposable test fixtures.

Begin a personally authored integration with a read-only tool and exact JSON
invocation, then add natural-language descriptions/examples and permissions.
The [walkthrough](mcp-framework-walkthrough.md) explains the full implementation
and gives the manual skeleton; [local](mcp-local.md) and [remote](mcp-remote.md)
guides document configuration and troubleshooting.
