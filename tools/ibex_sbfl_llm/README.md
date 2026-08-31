# Ibex SBFL LLM Reranker

`ibex_sbfl_llm` contains only the model-facing part of the localization workflow. It
reads SBFL block candidates, collects patched RTL context, calls an
LLM-provider API, validates the structured response, and produces a
reranked JSON result.

Batch generation, checkpoint analysis, summary TSV generation, and statistics
are maintained separately in
[`tools/ibex_sbfl_batch`](../ibex_sbfl_batch/README.md).

## Environment

```bash
uv sync --project tools/ibex_sbfl_llm --all-groups --frozen
uv run --project tools/ibex_sbfl_llm --frozen ibex-sbfl --help
```

The lock file includes the lightweight local `ibex-sbfl-common` dependency used
for generic SBFL artifact parsing. The LLM and batch projects do not depend on
each other.

Model calls live in `src/ibex_sbfl_llm/llm/`. Shared types, validation, and
retry orchestration are in `models.py` and `llm_client.py`; `claude.py`,
`gpt.py`, and `glm.py` isolate the Anthropic, OpenAI, and Z.AI SDKs.

## Rerank

```bash
export OPENAI_API_KEY=...

uv run --project tools/ibex_sbfl_llm --frozen ibex-sbfl rerank \
  rtl/ \
  logs/sbfl/<case-logdir> \
  --model <model-name> \
  --patch verify_dataset/<case>/<bug>.sv.diff \
  --candidate-count 50 \
  --top-k 20 \
  --source-mode auto \
  --llm-weight 0.75 \
  --output logs/sbfl/<case-logdir>/llm_rerank.json
```

Models named `glm-*` use the official `zai-sdk` by default. Set `ZAI_API_KEY`
and optionally `ZAI_BASE_URL`; `OPENAI_API_KEY` and `OPENAI_BASE_URL` remain
compatibility fallbacks. Other models default to the OpenAI Responses API, and
Claude can be selected with `--api-protocol anthropic`. The default temperature
is `0` for every model.

Important controls:

- `--source-mode snippets|full|auto` selects the RTL context strategy;
- `--candidate-count` limits SBFL candidates sent to the model;
- `--top-k` controls the validated rerank result size;
- `--llm-weight` controls the LLM contribution from 0 to 1; use `1.0` for
  LLM-only ranking;
- `--structured-output auto|strict|off` controls response-schema enforcement;
- `--api-protocol auto|anthropic|zai|openai-responses|openai-chat-completions`
  selects the request protocol;
- `--include-reason` requests a concise reason for every score; by default the
  model returns only candidate IDs and scores;
- `--dry-run --save-prompt` prepares inputs without calling the model.

By default the RTL source must match the supplied patch. Use
`--allow-unpatched-source` only when the source tree is already patched and that
state has been independently verified.

## Output

The output JSON records resolved inputs, configuration, prompt hash, model
metadata, token usage, latency, candidate assessments, and final rankings.
Assessments contain only ranking scores by default and additionally include
`reason` when `--include-reason` is enabled. The batch project consumes this
schema for LLM summary/statistics commands:

```bash
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch summarize llm \
  verify_dataset logs/sbfl -o llm_rerank_summary.tsv
```

## Development

```bash
uv run --project tools/ibex_sbfl_llm --all-groups --frozen \
  pytest -q tools/ibex_sbfl_llm/tests
uv run --project tools/ibex_sbfl_llm --all-groups --frozen \
  ruff check tools/ibex_sbfl_llm/src/ibex_sbfl_llm tools/ibex_sbfl_llm/tests
```
