# Ibex SBFL + LLM Fault Localization

[中文文档](README_CN.md)

`ibex-sbfl-llm` is a `uv`-managed Python project for post-processing Ibex
Spectrum-Based Fault Localization (SBFL) results with a large language model.
It reconstructs the exact buggy RTL used by an SBFL run, sends compact
line-numbered SystemVerilog context through the OpenAI Responses API, scores
every candidate block, produces a deterministic fused ranking, and summarizes
both SBFL and LLM localization metrics.

The project replaces the former standalone scripts:

- `scripts/rerank_sbfl_with_llm.py`
- `scripts/summarize_sbfl_blocks.py`
- `scripts/summarize_llm_rerank.py`

Those files remain as compatibility entry points, but all implementation,
prompts, tests, and dependency management now live in this project.

## Why the patch reconstruction matters

SBFL is run against a temporary Ibex worktree after a mutation diff has been
applied. The temporary worktree is normally removed after the run. Passing the
repository's clean `rtl/` directory directly to an LLM would therefore hide the
inserted defect from the model.

This project reads the `[DIFF]` entry from `run.log`, copies `rtl/` to a
temporary directory, applies the diff there, and builds prompts from that
patched copy. It never modifies the caller's source directory. The patch and
known ground-truth metadata are recorded for auditing but are not exposed to
the model.

## Features

- Parses block rankings from the `Suspiciousness of block:` section of
  `result.log`.
- Enriches each ranked `(scope, bid)` with module, type, and source lines from
  `blocks.json`.
- Discovers the bug-insertion diff from `[DIFF]` in `run.log`, with an explicit
  `--patch` override.
- Applies the diff only to an isolated temporary copy of the RTL.
- Detects source trees that already contain the diff.
- Rejects unsafe patch paths and patches that do not match the supplied RTL.
- Builds deduplicated module-level snippets, removes blank lines, and preserves
  original source line numbers.
- Keeps candidate line mappings compact by representing them as ranges.
- Loads system, user, and repair prompts from standalone Markdown files.
- Uses `client.responses.create` from the official `openai` Python library,
  with configurable Responses-compatible `base_url` support.
- Prefers strict JSON Schema output and validates all responses with Pydantic.
- Scores every candidate, rather than asking the model to return only Top-K.
- Supports LLM-only, weighted score fusion, and Reciprocal Rank Fusion (RRF).
- Writes a versioned and auditable `llm_rerank.json` artifact.
- Generates SBFL and LLM summary TSVs while preserving legacy ranking rules.
- Reads both legacy rerank results and schema version 2 results.
- Includes mocked API, patch, prompt, ranking, parser, and end-to-end tests.

## Pipeline

```text
result.log + blocks.json
          |
          v
  parse SBFL candidates
          |
          v
run.log -> discover [DIFF] -> copy rtl/ -> apply patch in temporary workspace
                                              |
                                              v
                              build deduplicated RTL snippets
                                              |
test information ----------------------------+
                                              |
                                              v
                              Responses API structured call
                                              |
                                              v
                           validate all candidate assessments
                                              |
                                              v
                            fuse LLM and SBFL evidence
                                              |
                                              v
                 llm_rerank.json + summary TSV + statistics
```

## Requirements

- Python 3.11 or newer.
- [`uv`](https://docs.astral.sh/uv/) available in `PATH`.
- `git` available in `PATH`; temporary patch application uses `git apply`.
- An OpenAI Responses API endpoint, or a compatible endpoint implementing
  `/responses`, for actual reranking.
- `result.log` and `blocks.json` in the SBFL result directory.
- A readable bug-insertion diff, normally referenced by `run.log`.
- Candidate SystemVerilog modules below the supplied RTL directory.

No external service is contacted by `--dry-run`, summary commands, or the
default test suite.

## Installation

Run all commands below from the Ibex repository root.

Create or update the locked environment:

```bash
uv sync --project tools/sbfl_llm --all-groups --frozen
```

`uv.lock` pins the complete environment. The virtual environment is created at
`tools/sbfl_llm/.venv/` and is ignored by Git.

Check the CLI:

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl --help
```

## Expected input layout

A normal rerank input directory looks like this:

```text
logs/reduce/RUN/CASE/
├── result.log
├── blocks.json
├── run.log
├── status.txt                 # used by summary commands
├── sbfl_time.txt              # optional summary timing
├── fuzzing_time.txt           # optional summary timing
├── test_info.md               # optional
├── test_info.txt              # optional
└── test_info.json             # optional
```

The relevant `result.log` section is expected to contain records such as:

```text
Suspiciousness of block:
top-1: Block(scope: TOP.example.core.alu_i, bid: 17) with suspicious '1.000000'
```

`blocks.json` must be a JSON array. Candidate lookup uses `(scope, bid)`:

```json
[
  {
    "scope": "TOP.example.core.alu_i",
    "bid": 17,
    "module": "ibex_alu",
    "lines": [84, 85, 86, 87],
    "type": "Always(COMB)"
  }
]
```

Patch discovery looks for exactly one `[DIFF]` record in `run.log`:

```text
[DIFF] /absolute/path/to/verify_dataset/103/ibex_id_stage.sv.diff
```

## Quick start

Configure an API key through an environment variable:

```bash
export OPENAI_API_KEY="your-api-key"
```

Rerank one SBFL result:

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl \
  logs/reduce/RUN/CASE \
  --model MODEL
```

The default output is:

```text
logs/reduce/RUN/CASE/llm_rerank.json
```

## Single-result and sequential batch wrapper

The repository-level wrapper accepts either one complete result directory or a
parent directory. To process one result:

```bash
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN/CASE
```

To process every result below a parent sequentially:

```bash
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN
```

The second path is detected automatically. A directory containing
`blocks.json`, `run.log`, and `result.log` is handled once; any other directory
is treated as a parent and scanned recursively. Batch results are sorted by
path. Unless overridden, the wrapper adds `--model gpt-5.5` and runs:

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT_DIR --model gpt-5.5
```

A failed directory is recorded and the batch continues by default. The final
exit status is nonzero if any rerank failed. Useful options are:

```bash
# Inspect all commands without calling the model API.
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN --list-only

# Do not rerun directories that already contain llm_rerank.json.
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN --skip-existing

# Stop at the first failed rerank.
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN --stop-on-error

# Override the default model.
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN --model MODEL

# Build and print every actual model prompt without calling the API.
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/RUN --dry-run
```

A failed directory is recorded and the batch continues by default. The final
exit status is nonzero if any rerank failed. Direct rerank options are forwarded
to every selected directory. `--list-only` only prints commands, while the
forwarded `--dry-run` constructs every prompt and can produce large output. A
shared `--output` is rejected in parent mode because it would overwrite the
same file.

Directories containing `blocks.json` and `run.log` but missing `result.log` are
reported as incomplete and skipped.

## Rerank command

```text
ibex-sbfl rerank [OPTIONS] RTL_SOURCE SBFL_RESULT
```

`SBFL_RESULT` may be either the result directory or its `result.log` file.

### Rerank options

| Option | Default | Description |
| --- | --- | --- |
| `--model MODEL` | none | Model identifier sent to the endpoint. Required unless `--dry-run` is used. |
| `--api-base URL` | `OPENAI_BASE_URL` or `https://api.openai.com/v1` | Responses API-compatible base URL. A trailing `/responses` is normalized to its base. All model requests use `/responses`. |
| `--api-key-env NAME` | `OPENAI_API_KEY` | Environment variable containing the key. Use an empty name for an explicitly no-auth compatible endpoint. |
| `--candidate-count N` | `50` | Number of printed SBFL candidates considered before invalid block mappings are skipped. |
| `--top-k K` | `10` | Number of final entries copied into the compatibility `rankings` array. |
| `--source-mode MODE` | `snippets` | `snippets`, `full`, or `auto`. |
| `--max-source-chars N` | `240000` | Maximum characters used by rendered RTL source only. |
| `--snippet-radius N` | `30` | Requested context lines before and after candidate block lines. |
| `--patch PATH` | discovered | Override the `[DIFF]` path from `run.log`. |
| `--allow-unpatched-source` | off | Explicitly allow a run without a discovered or supplied patch. |
| `--test-info TEXT` | none | Inline failure description. Mutually exclusive with `--test-info-file`. |
| `--test-info-file PATH` | auto-discovered | Read the failure description from a file. |
| `--ranking-strategy STRATEGY` | `weighted` | `weighted`, `llm-only`, or `rrf`. |
| `--llm-weight VALUE` | `0.75` | LLM contribution in weighted and RRF modes; must be in `[0,1]`. |
| `--structured-output MODE` | `auto` | `auto`, `strict`, or `off`. |
| `--timeout SECONDS` | `180` | Timeout for one SDK request. |
| `--temperature VALUE` | omitted | Sent only when explicitly supplied, improving compatibility with models that reject it. |
| `--retries N` | `2` | Maximum transient request retries and maximum model-validation repairs. |
| `--retry-delay SECONDS` | `2` | Base request retry and validation repair delay. |
| `--output PATH` | `SBFL_RESULT/llm_rerank.json` | Output artifact path. Parent directories are created as needed. |
| `--save-prompt` | off | Save `OUTPUT.prompt.md` containing the resolved system and user prompts. |
| `--dry-run` | off | Validate inputs, apply the patch temporarily, and print the complete prompt without calling the API. |

`--top-k` cannot exceed `--candidate-count`. If missing `blocks.json` entries
reduce the valid candidate set below `--top-k`, reranking stops with an error.

## Patch reconstruction

Patch selection priority is:

1. Explicit `--patch PATH`.
2. The unique `[DIFF]` path in `SBFL_RESULT/run.log`.
3. No patch, only when `--allow-unpatched-source` is explicit.

The implementation then:

1. Verifies that the patch exists.
2. Parses both `---` and `+++` file headers.
3. Rejects absolute paths, `..` traversal, and changes outside `rtl/`.
4. Copies the supplied RTL tree to a temporary `workspace/rtl` directory.
5. Runs `git apply --check` before applying the diff.
6. If forward application fails, runs a reverse check to detect an already
   patched source tree.
7. Builds all source context from the temporary patched tree.
8. Removes the temporary workspace automatically.

Possible `patch_state` values in the output are:

| State | Meaning |
| --- | --- |
| `applied` | The diff was successfully applied to the temporary copy. |
| `already_patched` | The supplied RTL already contained the diff. |
| `not_available` | No patch was used because `--allow-unpatched-source` was explicit. |

If `run.log` declares a patch that is missing or incompatible, the command
fails even when `--allow-unpatched-source` is present. This prevents silently
analyzing clean or unrelated RTL.

## RTL source collection

Candidate module resolution first checks `RTL_SOURCE/<module>.sv`, then
recursively searches below `RTL_SOURCE`. No match is an error. Multiple matches
are also an error because silently choosing one could send the wrong source to
the model.

### `snippets` mode

This is the default. For every implicated module, the collector:

- Unions all candidate block lines.
- Expands each block by `--snippet-radius` lines.
- Merges overlapping and adjacent context naturally at module level.
- Sends each physical source line at most once per module.
- Removes blank or whitespace-only lines.
- Preserves original line numbers for every retained line.
- Preserves comments because they often encode RTL intent.
- Adds an omission marker only when non-blank code was skipped.
- Includes a candidate-to-line-range mapping before the code block.

If the requested snippets exceed `--max-source-chars`, the radius is reduced
one line at a time down to zero. Candidate block lines themselves are never
silently removed. If radius zero still exceeds the limit, the command fails and
asks for fewer candidates or a larger limit.

### `full` mode

All implicated module files are rendered with blank lines removed and original
line numbers retained. The command fails if the rendered source exceeds the
character limit.

### `auto` mode

The collector uses full files when they fit; otherwise it switches to adaptive
snippets.

`--max-source-chars` counts only rendered RTL source. Candidate JSON, failure
information, system instructions, schema instructions, and expected completion
tokens are additional. Use `--dry-run` when checking whether the complete prompt
fits a particular model's context window.

## Failure information

Failure information is selected in this order:

1. `--test-info TEXT`.
2. `--test-info-file PATH`.
3. `test_info.md` beside `result.log`.
4. `test_info.txt` beside `result.log`.
5. `test_info.json` beside `result.log`.
6. A generic architectural mismatch description.

The generic fallback allows experiments to run, but concrete mismatch data is
usually much more useful for causal reasoning. The tool intentionally does not
generate failure information from the mutation diff or `bug_info.json`, since
that would leak the evaluation answer.

## Prompt design

Prompt templates are package resources:

```text
src/ibex_sbfl_llm/prompts/
├── system.md
├── rerank.md
└── repair.md
```

The system prompt asks the model to distinguish root-cause logic from a block
that merely propagates an already incorrect value. It highlights control/data
predicates, state transitions, arithmetic, indexes, widths, signedness,
ready/valid handshakes, enables, and register updates. SBFL suspiciousness is
treated as prior evidence rather than ground truth.

The user prompt supplies failure information, the complete allowed candidate
set, compact candidate line ranges, and patched RTL context. The model is asked
to return one assessment for every candidate. The repair prompt is used only
after local validation rejects a response.

Patch contents, patch paths, `bug_info.json`, and known modified line numbers
are never included in the model prompt. Prompt SHA-256 values are recorded in
the result. `--save-prompt` is available when the exact resolved text is needed
for an experiment audit; saved prompts may contain a large amount of RTL.

## Responses API and structured model responses

All model calls use `client.responses.create`; there is no Chat Completions
request or fallback. The system prompt is sent through `instructions`, the
conversation is sent through `input`, and structured output is configured under
`text.format`. Requests set `store=False` because the prompt contains RTL and
failure information.

The response model requires:

```json
{
  "assessments": [
    {
      "candidate_id": "B018",
      "score": 0.97,
      "causal_role": "probable_root_cause",
      "key_lines": [853],
      "reason": "The feedback assignment makes the write enable depend on its own output."
    }
  ]
}
```

Allowed causal roles are:

- `probable_root_cause`
- `causal_upstream`
- `propagated_symptom`
- `weakly_related`
- `insufficient_evidence`

Local validation requires:

- Exactly one assessment for every candidate ID.
- No unknown or duplicate IDs.
- `score` in the inclusive range `[0,1]`.
- A valid causal role.
- A non-empty reason.
- `key_lines` contained in that candidate's block line set.
- No unrecognized fields in structured objects.

### Structured-output modes

| Mode | Behavior |
| --- | --- |
| `auto` | Try strict JSON Schema, then JSON object mode, then prompt-constrained JSON when the endpoint explicitly rejects the preceding response format. Pydantic validation always remains active. |
| `strict` | Require JSON Schema support and do not fall back. |
| `off` | Do not send `text.format`; parse and validate JSON from ordinary Responses API output text. |

Markdown JSON fences and a small amount of surrounding prose can be recovered
in fallback modes, but callers should still instruct compatible models to
return JSON only.

Transient connection failures, timeouts, HTTP 429 responses, and server errors
are retried with exponential backoff and jitter. Permanent API errors are not
retried. A syntactically valid API response that fails candidate or schema
validation triggers a repair turn containing the previous response and the
specific validation error. Repair turns resend the local input history and do
not depend on server-side conversation state or `previous_response_id`.

## API configuration

### Default OpenAI configuration

```bash
export OPENAI_API_KEY="your-api-key"

uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT --model MODEL
```

### Custom key variable

```bash
export MY_LLM_KEY="your-api-key"

uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT --model MODEL --api-key-env MY_LLM_KEY
```

### Custom Responses-compatible endpoint

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT \
  --model compatible-model \
  --api-base https://example.invalid/v1
```

### No-auth local endpoint

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT \
  --model local-model \
  --api-base http://127.0.0.1:8000/v1 \
  --api-key-env ''
```

In no-auth mode the SDK is configured without emitting an Authorization
header. Never place an API key directly in command history, source files, or a
rerank artifact.

## Ranking and score fusion

The model returns a semantic `llm_score` for every candidate. Numeric SBFL
suspiciousness is normalized within the candidate set:

```text
normalized_sbfl_score = suspiciousness / max_candidate_suspiciousness
```

If the maximum suspiciousness is zero, all normalized SBFL scores are zero.

### `weighted` strategy

The default strategy is:

```text
final_score = llm_weight * llm_score
            + (1 - llm_weight) * normalized_sbfl_score
```

With the default `--llm-weight 0.75`:

```text
final_score = 0.75 * llm_score + 0.25 * normalized_sbfl_score
```

### `llm-only` strategy

```text
final_score = llm_score
```

SBFL remains a deterministic tie-breaker.

### `rrf` strategy

The implementation uses `k = 60`:

```text
final_score = llm_weight / (60 + llm_rank)
            + (1 - llm_weight) / (60 + original_sbfl_rank)
```

RRF is useful when score calibration differs substantially across models.

Final ordering is deterministic:

1. `final_score`, descending.
2. `llm_score`, descending.
3. `normalized_sbfl_score`, descending.
4. Original SBFL rank, ascending.
5. Candidate ID, ascending.

## Output artifact

The output uses `schema_version: 2`. A shortened example is shown below:

```json
{
  "schema_version": 2,
  "tool_version": "0.1.0",
  "model": "MODEL",
  "inputs": {
    "result_log": "/absolute/path/result.log",
    "result_log_sha256": "...",
    "blocks_json": "/absolute/path/blocks.json",
    "blocks_json_sha256": "...",
    "rtl_source": "/absolute/path/rtl",
    "patch_path": "/absolute/path/bug.diff",
    "patch_sha256": "...",
    "patch_state": "applied",
    "patch_changed_files": ["rtl/ibex_id_stage.sv"],
    "patched_file_hashes": {
      "rtl/ibex_id_stage.sv": "..."
    },
    "test_info_source": "generic"
  },
  "config": {
    "candidate_count": 50,
    "top_k": 10,
    "source_mode": "snippets",
    "requested_source_mode": "snippets",
    "snippet_radius": 30,
    "effective_snippet_radius": 30,
    "max_source_chars": 240000,
    "source_chars": 188140,
    "ranking_strategy": "weighted",
    "llm_weight": 0.75,
    "structured_output": "auto",
    "temperature": null,
    "timeout": 180.0,
    "retries": 2,
    "retry_delay": 2.0
  },
  "request": {
    "api": "responses",
    "system_prompt_sha256": "...",
    "user_prompt_sha256": "...",
    "prompt_sha256": "...",
    "structured_output": "json_schema",
    "response_id": "...",
    "attempts": 1,
    "usage": {
      "input_tokens": 1234,
      "output_tokens": 567,
      "total_tokens": 1801
    },
    "elapsed_seconds": 12.34
  },
  "assessments": [
    {
      "candidate_id": "B018",
      "original_rank": 18,
      "suspiciousness": "0.140028",
      "module": "ibex_id_stage",
      "scope": "TOP.example.core.id_stage_i",
      "bid": 134,
      "lines": [851, 852, 853, 854],
      "block_type": "Always(COMB)",
      "llm_score": 0.97,
      "normalized_sbfl_score": 0.140028,
      "final_score": 0.762507,
      "causal_role": "probable_root_cause",
      "key_lines": [853],
      "reason": "...",
      "reranked_rank": 1
    }
  ],
  "rankings": [
    "Top-K complete assessment records"
  ],
  "raw_model_response": "..."
}
```

The two candidate arrays have different purposes:

- `assessments` contains every valid candidate, with LLM, normalized SBFL, and
  fused scores and a full reranked position.
- `rankings` contains only the configured Top-K complete records for backward
  compatibility and Top-K evaluation.

Compatibility fields such as top-level `candidate_count`, `top_k`,
`source_mode`, and `llm_elapsed_seconds` are retained for legacy readers.
Files are written by creating a temporary sibling file and atomically replacing
the destination.

## Dry-run and prompt inspection

Always run a dry-run when using a new dataset layout or context budget:

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT \
  --candidate-count 50 \
  --top-k 10 \
  --dry-run
```

The command still validates inputs, discovers and applies the patch in a
temporary workspace, resolves modules, and enforces the source budget. It then
prints the system prompt, user prompt, and metadata including:

- `patch_state`
- `patch_path`
- final `source_mode`
- effective snippet radius
- rendered source character count
- combined prompt SHA-256

It does not require `--model`, read an API key, call an endpoint, or write
`llm_rerank.json`. If `--save-prompt` is also present, the prompt sidecar is
written before the dry-run exits.

## Summary commands

### SBFL summary

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl summarize sbfl \
  verify_dataset \
  logs/reduce \
  -o sbfl_block_summary.tsv
```

The command recursively finds `status.txt` files below `logs_root`, resolves the
corresponding diff and `bug_info.json` below `bugset_root`, and compares modified
lines with SBFL blocks.

The SBFL TSV preserves these fields:

```text
bugset  diff  status  top-k  sus  elapsed_time  fuzzing_time
```

Metrics include Top-1, Top-5, Top-10, Top-20, MAR@10, average SBFL time, and
average fuzzing time.

### LLM summary

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl summarize llm \
  verify_dataset \
  logs/reduce \
  -o llm_rerank_summary.tsv
```

The LLM summary includes the original legacy columns followed by:

- `llm_score`
- `normalized_sbfl_score`
- `final_score`
- `causal_role`
- `ranking_strategy`
- `patch_state`
- `prompt_sha256`
- native Responses API `input_tokens`, `output_tokens`, and `total_tokens`
- legacy TSV aliases `prompt_tokens` and `completion_tokens`

It reports Top-1, Top-5, Top-10, MRR, MAR@10, improved/unchanged/worsened case
counts, and average LLM/SBFL/fuzzing times. Both legacy and schema-v2
`llm_rerank.json` files are accepted. Use `--rerank-filename` when an experiment
uses another artifact name.

### Line matching window

Both summary modes accept:

```text
--line-window N
```

`0` requires an exact source-line intersection. `1` also accepts one line before
or after each known modified line.

### SBFL tie rules

The summary preserves the previous experimental rules:

- Consecutive blocks with numerically equal suspiciousness share their average
  printed rank.
- If every printed block has the same suspiciousness, the result is treated as
  `over top-N` because the ordering carries no information.
- If a tie reaches the printed Top-N boundary, an earlier item in that boundary
  tie is treated as `over top-N`; the item printed exactly at Top-N remains
  `top-N`.

### Statistics from an existing TSV

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl stats sbfl \
  sbfl_block_summary.tsv

uv run --project tools/sbfl_llm --frozen ibex-sbfl stats llm \
  llm_rerank_summary.tsv
```

## Compatibility commands

Existing automation can continue to invoke:

```bash
python3 scripts/rerank_sbfl_with_llm.py rtl RESULT --model MODEL
python3 scripts/summarize_sbfl_blocks.py verify_dataset logs/reduce
python3 scripts/summarize_llm_rerank.py verify_dataset logs/reduce
```

The rerank wrapper performs single/parent detection and then calls the unified
project. The summary compatibility files contain no localization logic; they
replace themselves with the corresponding `uv run` command. Legacy
`--stats-only TSV` forms are translated to the new `stats` subcommands.

`scripts/run_args_sweep_sbfl.sh` continues to work through the SBFL summary
compatibility entry point.

## Reproducibility and security

- API keys are read only from the named environment variable and are never
  written to an artifact.
- The original RTL source is never patched in place.
- Patch and input SHA-256 values identify the exact experiment inputs.
- Patched RTL file hashes identify the source actually shown to the model.
- Prompt hashes identify resolved prompt content without embedding the full
  prompt in the JSON artifact.
- The raw response is saved for auditing model parsing and repairs.
- The model never receives the mutation diff, patch path, `bug_info.json`, or
  known ground-truth modified lines.
- `--save-prompt` should be used deliberately because the sidecar contains RTL
  source and failure information.
- Rerank artifacts contain absolute local paths by design; sanitize them before
  publishing if filesystem layout is sensitive.

## Common errors

### `cannot discover a [DIFF] patch`

`run.log` is missing or has no `[DIFF]` record. Supply `--patch PATH`. Use
`--allow-unpatched-source` only when analyzing a source tree that intentionally
has no mutation diff.

### `patch does not match RTL source`

The supplied `rtl/` is not the base tree expected by the diff, or the patch is
corrupt. Confirm the dataset revision and the `rtl_source` argument. A source
that already contains the diff is detected separately.

### `patch may only modify files below rtl/`

The diff references another directory or contains an unsafe path. This is
rejected before `git apply` runs.

### `no block ranking found`

`result.log` has no recognized `Suspiciousness of block:` section or no matching
ranking records.

### `none of the ranked blocks exists in blocks.json`

The `(scope, bid)` values in `result.log` do not match the supplied
`blocks.json`. Ensure both files came from the same run.

### `multiple source files found for module`

More than one `<module>.sv` exists below `rtl_source`. Supply a narrower RTL
root so the correct module is unambiguous.

### `candidate block code needs ... characters`

Candidate block lines alone exceed `--max-source-chars`. Reduce
`--candidate-count` or increase the limit.

### `candidate set mismatch`

The model omitted a candidate or returned an unknown candidate ID. The tool
will request a corrected response up to `--retries`; persistent failures stop
the run without writing a partial result.

### Structured output is rejected by a Responses-compatible endpoint

Keep the default `--structured-output auto` to allow controlled fallback. Use
`off` only when the endpoint rejects all Responses API `text.format` values.
Use `strict` when an experiment must guarantee JSON Schema enforcement by the
server. An endpoint without `/responses` support is an API error; the tool never
falls back to Chat Completions.

## Project layout

```text
tools/sbfl_llm/
├── pyproject.toml
├── uv.lock
├── README.md
├── README_CN.md
├── src/ibex_sbfl_llm/
│   ├── cli.py                 # command-line routing and validation
│   ├── models.py              # candidate and structured response models
│   ├── sbfl.py                # result/status parsing and rank rules
│   ├── patching.py            # safe temporary patch reconstruction
│   ├── snippets.py            # source lookup and context rendering
│   ├── prompting.py           # prompt loading and failure-info selection
│   ├── openai_client.py       # SDK call, fallback, retry, validation
│   ├── ranking.py             # score normalization and fusion
│   ├── rerank.py              # end-to-end rerank orchestration
│   ├── result_io.py           # legacy/v2 artifact validation
│   ├── summarize.py           # SBFL and LLM TSV/statistics
│   └── prompts/
│       ├── system.md
│       ├── rerank.md
│       └── repair.md
└── tests/
```

## Development and verification

Install development dependencies:

```bash
uv sync --project tools/sbfl_llm --all-groups --frozen
```

Run the project test suite from the Ibex repository root:

```bash
uv run --project tools/sbfl_llm --frozen pytest -q tools/sbfl_llm/tests
```

Run linting and formatting checks:

```bash
uv run --project tools/sbfl_llm --frozen ruff check tools/sbfl_llm
uv run --project tools/sbfl_llm --frozen ruff format --check tools/sbfl_llm
```

Build distributable artifacts:

```bash
uv build --project tools/sbfl_llm
```

The default tests mock the OpenAI client and incur no API cost. They cover patch
discovery and isolation, already-patched detection, unsafe paths, snippet
rendering, prompt leakage prevention, response validation, structured-output
fallback, score fusion, legacy/v2 result loading, SBFL tie rules, and an
end-to-end schema-v2 rerank.
