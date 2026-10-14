# v1.5.0 MCP framework changes

Implementation notes for the feature branch; this document does not publish a
release or change the currently released source version.

- Native commands and external tools share Intent descriptors, a dispatcher and
  normalized `ToolResult`/`ToolError` outcomes. Native command parsing is preserved.
- Local stdio and remote Streamable HTTP servers are configured independently in
  `mcp_servers`, with environment-backed credentials and safe per-file errors.
- Trusted tools can be selected through manual/voice intent routing. Schema
  validation applies to structured local-model arguments and explicit JSON calls.
  Explicit canonical MCP requests discover their named server before matching
  and skip intent classification, including on first use or unavailable names.
  Natural-language requests discover trusted tools before classification, while
  deterministic native matches retain their fast path.
- Optional persistent clients avoid repeated startup; lazy/eager startup, idle
  retention, explicit retirement, configuration replacement and shutdown are
  supported. Operation-scoped behavior remains the default.
- Process/endpoint trust is separate from exact tool permissions. Actions deny by
  default; configured tools and contextual host policy can require one-use approval.
- Tool failures, invalid arguments and permission outcomes are explicit and do
  not silently become unrelated actions. No action replay or automatic fallback
  is introduced.
- Results have a separate bounded, sanitized display and privacy-aware local/cloud
  conversation representation. Full payloads stay outside conversation/speech.
  Display/speech extracts readable MCP text or structured values rather than
  reading the protocol envelope; an echo message is spoken as just that message.
- Disposable real stdio and authenticated loopback HTTP fixtures exercise
  persistence, routing, cancellation, limits, authentication and cleanup.

Known limits: natural-language reliability depends on a configured local provider;
use canonical tool names plus JSON for deterministic invocation. No interactive
OAuth/token refresh, legacy HTTP+SSE configuration, dynamic permission profiles,
live catalog updates, multi-turn argument collection, multi-tool planning or
equivalent-action fallback is implemented. Decoded-result limits apply after
parsing; SSE limits do not cap ordinary JSON bodies. Cleanup can outlast an action
deadline. Remote state retention depends on the server. Initial failed discovery
requires reconfiguration/restart to retry.

See [the walkthrough](docs/mcp-framework-walkthrough.md),
[local setup](docs/mcp-local.md), [remote setup](docs/mcp-remote.md) and
[validation evidence](docs/mcp-validation.md). Real integrations remain separate
from this framework implementation.
