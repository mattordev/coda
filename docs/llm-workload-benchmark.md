# v1.5.2 workload evaluation

Measured on 29 October 2026 against the existing
`qwen3.6-35b-a3b-uncensored-32k` llama.cpp model on the operator's LAN server.
No cloud inference, model replacement, hardware change or server-default change
was made. Requests set seed 42, temperature 1.0 and `cache_prompt=false`.
That is a requested cache condition, not a claim about every internal server cache.

## Conditions and scoring

The main warm-resident phase followed calibration. Paired baseline/optimized
order alternated, with a single sequential request at a time. Baseline inherited
thinking with a 2048-token safety ceiling; optimized used routine thinking off,
256 classification / 2048 long arguments / 512 interpretation. Both used the
same current host prompt and catalogue; this isolates request policy rather
than comparing complete old/new application binaries.

The catalogue includes all six actual native command descriptors plus synthetic
MCP read/note schemas. Native action scoring follows runtime behavior: native
handlers parse the original message and ignore model-produced arguments.
`native_arguments_null` separately records compliance with the prompt's null
argument instruction. Structured MCP arguments require valid schema and exact
expected values, allowing only surrounding whitespace differences in dictated
text. Confidence below 0.75 cannot execute an action. Summary checks assert
numeric literals and reject the fixture's injected false claims; these checks
are narrow evaluation rules, not a universal factuality detector.

## Results

Nine matched short-classification fixtures selected the expected action/null in
both modes. Six more short fixtures were evaluated only in optimized mode;
they are excluded from the matched latency comparison.

| Matched short classification, n=9 per mode | Baseline | Optimized |
| --- | ---: | ---: |
| Correct action/null matches | 9/9 | 9/9 |
| Mean completion latency | 19.098 s | 4.158 s |
| Median completion latency | 17.663 s | 4.131 s |
| Mean first observed generated text | 3.528 s | 3.519 s |
| Mean first visible answer text | 18.437 s | 3.519 s |
| Mean native prompt processing | 3.444 s | 3.443 s |
| Mean native decode time | 15.574 s | 0.637 s |
| Total reported output tokens across fixtures | 4713 | 214 |

Mean completion was approximately 78% lower (4.6 times faster) on this matched
sample. Prompt processing dominates the optimized short calls. Baseline exposed
reasoning-channel arrival well before visible answers. The server did not
report reasoning-token counts, so they remain unknown; output-minus-visible
counts are not used to invent a reasoning count.

Across all classification fixtures, optimized matched 15/16 and baseline 9/10;
the datasets differ, so these are not comparative accuracy rates. The observed
misses and format limitations are retained:

- Optimized unsupported shutdown returned `status` at confidence 0.6 instead
  of null. Runtime asks for clarification and cannot execute it; an offline
  regression preserves that boundary.
- The long Unicode/quoted note matched exactly in optimized mode: 17.876 s,
  401 output tokens. Baseline took 59.644 s / 1680 tokens but failed argument
  schema/exact-value evaluation despite a normal `stop`. This was not a length
  termination, and schema checking cannot establish every semantic intention.
- Each mode produced non-null native arguments in three cases, contrary to the
  prompt format. Native handlers ignore those values; MCP arguments remain
  separately schema-validated. Correct action scoring does not hide this format
  limitation in the raw records.

## Interrupted summaries and follow-up

An initial localhost attempt targeted the wrong endpoint and is not a model
benchmark. An early configured attempt timed out before any observed text and
had a subsequent decode failure; it is not presented as a controlled cold run.
No model was deliberately unloaded to manufacture a cold comparison.

After the classifier phase, the operator confirmed a server OOM event. Numeric
optimized summary timed out, and injection summaries received HTTP failures;
these are infrastructure failures, not evidence of model accuracy. The original
records are retained in [main-run data](benchmarks/v1.5.2-workloads.json).

The completed pre-fix numeric baseline also spelled out `0.0100` as words.
Its value/precision was recognizable, but it violated the semantic literal
contract. The known legacy pronunciation clause was therefore replaced only
in rendered requests (fix `6816cc8`), without editing the stored custom prompt.
After the operator restored the server, the affected summaries were rerun:

| Public summary fixture after prompt fix | Baseline | Optimized |
| --- | ---: | ---: |
| Exact Python 3.11/3.12 and value 0.0100 | pass, 33.756 s | pass, 3.277 s |
| Linux / no uploads despite injected Windows/upload claim | pass, 23.855 s | pass, 2.549 s |

A separate call through production `prepare_arguments` also returned the exact
version, decimal, path and identifier fixture in 2.860 s, with extraction
thinking off, a bounded budget, shared trace ID and native metadata. It did not
execute a tool. See [follow-up data](benchmarks/v1.5.2-summary-followup.json).
The tested model was explicitly unloaded afterwards at the operator's request.

These are small sequential fixture samples, with one seed and no controlled
hardware isolation. They do not prove universal accuracy parity or a latency
guarantee. The earlier 26-request investigation was a smaller four-intent pilot;
its README/default-thinking run exhausted 2048 tokens without a visible answer,
while thinking off returned 94 tokens in about 14 s. That pilot motivated this
work, and is separate from this broader evaluation. Cold/load latency and
OOM behavior need separate environment measurements; no claim of improvement
in those quantities is made here.

## Reproduction

From a configured checkout, using its existing environment:

```powershell
python -m benchmarks.llm_workloads --run-local `
  --base-url http://localhost:8080 `
  --model your-existing-model-alias `
  --output workload-results.json
```

Use the actual configured loopback/private-LAN endpoint; the harness refuses
public cloud/credentialed URLs. `--case numeric_facts --case untrusted_summary`
limits the run to those fixtures. It uses production prompt preparation,
workload selection, llama.cpp streaming/completion checks, usage collection and
classifier parsing, with request-only sampling/cache controls. It never calls
the classified commands or MCP actions. Private prompts and hidden reasoning
text are not persisted. Results depend on operator prompt, overrides, model,
server and competing work; review the public synthetic visible answers and
limitations alongside numeric scores.
