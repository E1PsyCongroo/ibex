# Ibex SBFL Common

`ibex_sbfl_common` is the dependency-neutral shared layer for Ibex SBFL tooling. It
contains artifact parsing, suspiciousness rank evaluation, and rerank-result
schema validation. It has no batch-runner, statistics, OpenAI, or Pydantic
dependency.

Both `tools/ibex_sbfl_batch` and `tools/ibex_sbfl_llm` depend on this uv project.

```bash
uv sync --project tools/ibex_sbfl_common --all-groups --frozen
uv run --project tools/ibex_sbfl_common --all-groups --frozen \
  pytest -q tools/ibex_sbfl_common/tests
```
