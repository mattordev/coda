# Semantic responses and pronunciation

CODA keeps the answer's factual text in console output, dashboard messages and
conversation history. Versions such as Python `3.11`/`3.12`, numbers such as
`0.0100`, file paths, IDs and technical notation must not be respelled by the
language model for speech. The system prompt appends a mandatory fidelity rule
after the custom style prompt. The interpretation prompt reserves space for
this rule and the untrusted-data boundary inside its one leading system message,
even when a custom prompt is long. A prompt instruction is a model safeguard,
not a guarantee against every hallucination; factual output still needs evaluation.

Pronunciation happens on a separate copy in `tts.spoken_text`. Runtime speech
already uses this boundary; synchronous speech now uses it too. Both preserve
the source shown to the user. Queued speech receives semantic text and the
speech worker normalizes it once before playback. Normalization is idempotent.

Decimals pronounce every fractional digit, including trailing zeros: `3.11`
becomes “three point one one”, and `0.0100` becomes “zero point zero one zero
zero”. Explicit `version`/`v` tokens pronounce each component after the first
digit by digit. Existing currency, time, date and measurement rules still apply.
Ambiguous addresses, URLs, paths, IDs and scientific notation remain literal;
the normalizer does not infer a meaning for them.

Directory summaries retain quoted names such as `"README.md"` in semantic text,
history and retained data. Only the TTS copy reads filename dots aloud and uses
`markdown`/`jay son` for `.md`/`.json`. Quoted directory names use the same
extension pronunciation. Unquoted filenames and URLs remain literal to avoid
rewriting ordinary technical prose. The existing bounded summaries, ordering,
redaction and complete retained tool results are preserved.

Custom `CODA_SYSTEM_PROMPT` values remain operator-owned. Existing configurations
that ask for numbers to be expanded remain stored unchanged, with the mandatory
factual rule taking precedence in actual requests. No new environment settings
are required for this separation.
