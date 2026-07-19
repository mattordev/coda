# Privacy routing

CODA keeps a single internal conversation history with raw content. When a
provider is called, that history is rendered for the provider type.

- Local providers receive raw conversation history.
- Cloud providers receive cloud-safe conversation history.
- Low-risk requests can use cloud providers first.
- Medium-risk requests use local providers first, then cloud fallback according
  to policy.
- High-risk requests use local providers only by default.

## Privacy analysis

`analyze_privacy()` returns a dictionary with:

- `risk`: numeric score between `0.0` and `1.0`
- `level`: `low`, `medium`, or `high`
- `categories`: broad reasons the prompt was considered sensitive
- `matches`: exact regex spans that can be sanitised

`detect_privacy()` still returns the numeric score for older callers.

## Modes

`CODA_PRIVACY_MODE=strict`

Medium-risk and high-risk requests do not go to cloud providers.

`CODA_PRIVACY_MODE=balanced`

Medium-risk requests can fall back to cloud providers with sanitised content.
High-risk requests are blocked from cloud providers unless
`CODA_HIGH_RISK_CLOUD_FALLBACK` says otherwise.

`CODA_PRIVACY_MODE=permissive`

Medium-risk and high-risk requests can fall back to cloud providers with
sanitised content by default.

## Cloud actions

`raw`

Send the original content.

`sanitize`

Replace detected values such as emails, phone numbers, postcodes, payment cards,
and API keys with placeholders.

`summarize`

Send a short cloud-safe summary instead of the original message.

`block`

Do not route the request to cloud providers.

## Environment

```text
CODA_PRIVACY_MODE=balanced
CODA_PRIVACY_LOW_RISK_THRESHOLD=0.3
CODA_PRIVACY_HIGH_RISK_THRESHOLD=0.7
CODA_CLOUD_PRIVACY_ACTION=sanitize
CODA_HIGH_RISK_CLOUD_FALLBACK=block
```

The router decides which providers are allowed. `llm_service` decides what
content each provider sees.
