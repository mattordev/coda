# C.O.D.A v1.5.0 — MCP & External Tool Integration

CODA v1.5.0 adds a shared execution framework for native commands and external
tools using the Model Context Protocol. It can discover and invoke independently
configured local and remote servers, apply explicit trust and permissions, and
return useful tool output through the existing text and voice runtime.

## Highlights

- Native commands and MCP tools share Intent descriptors, dispatch and normalized
  results. Existing native commands keep their own argument parsing.
- Connect to local stdio servers and authenticated remote Streamable HTTP servers
  using the official pinned MCP Python SDK.
- Configure one independent JSON file per server, with environment-backed
  credentials, default-deny trust and exact tool allowlists.
- Reuse optional persistent sessions with lazy/eager startup, configurable idle
  retirement, explicit retirement and owned-resource cleanup. Operation-scoped
  sessions remain the default.
- Discover tools before natural-language classification. Explicit canonical
  tool names with JSON arguments skip intent classification, including first use.
- Validate tool schemas and arguments before invocation, and require one-use,
  expiring confirmation for configured actions or contextual host policy.
- Return explicit permission, validation, timeout, cancellation and failure
  outcomes without automatically replaying accepted actions or choosing an
  unrelated tool/conversation fallback.
- Present readable bounded tool output for display and speech, while retaining
  full results and separate privacy-aware local/cloud conversation context.
- Isolate optional server failures and coordinate concurrent server access,
  configuration replacement, reconnection and shutdown.
- Update AnyIO, urllib3, Werkzeug and fsspec to patched dependency versions.
- Add local/remote configuration examples, troubleshooting and a generalized
  technical walkthrough for writing standalone integrations.

## Validation

- Full Windows Python 3.11 automated suite: 711 tests, with the opt-in offline
  updater environment smoke enabled and one expected platform-specific skip.
- Release CI covers Windows and Ubuntu with Python 3.11 and 3.12; Pylint covers
  both supported Python versions.
- Real disposable stdio and authenticated loopback HTTP servers exercise tool
  discovery, invocation, state reuse, failure isolation, limits and cleanup.
- Live echo testing verifies explicit invocation, readable speech output and
  first-request natural-language selection through a configured local model.
- A fresh server virtual environment runs the documented integration skeleton.
- Dependency consistency, release tag/version validation and dependency audits
  are checked during release preparation. The CPU-only torch package is checked
  against its upstream version because the audit service does not recognize the
  CPU build identifier.

Automated tests do not validate every third-party server, microphone/audio
hardware, account, model download or deployment. Dependency audit results reflect
known advisories and are not a guarantee that dependencies are vulnerability-free.

## Getting started

A standalone MCP server is sufficient; it does not need a corresponding CODA
command module. Start with a read-only tool and an exact canonical-name JSON
request, then add natural-language descriptions and explicit permissions.

See [Local MCP servers](docs/mcp-local.md),
[Remote MCP servers](docs/mcp-remote.md) and the
[implementation walkthrough](docs/mcp-framework-walkthrough.md) for setup,
credential handling, lifecycle behavior and manual integration development.

Git checkouts should continue updating through Git. Installations with the repaired
v1.4.4+ updater can use published releases. Users on v1.4.2 or earlier should follow
the manual migration procedure in [Updates and recovery](docs/updater-recovery.md).

## Known limitations

- Natural-language selection and argument extraction depend on a configured
  local model and can wake the GPU or add model latency. Explicit canonical-name
  JSON calls do not need model classification or argument extraction. Cheaper
  semantic matching remains planned for v1.6.0.
- Interactive OAuth/token refresh, legacy HTTP+SSE configuration, permission
  profiles/UI, live catalog updates, multi-turn argument collection, multi-tool
  planning and equivalent-action fallback are not implemented.
- Decoded result limits apply after parsing; SSE limits do not cap JSON bodies.
  Full results are separate from the much smaller conversational representation.
- Cleanup can outlast an action deadline. Remote state retention depends on the
  server, and CODA never owns or stops remote server processes.
- Initial failed discovery requires reconfiguration/restart to retry. Privacy
  sanitization retains the existing detector's limitations.

See [framework validation](docs/mcp-validation.md) for implementation evidence
and the distinction between full results, spoken output and model context.
