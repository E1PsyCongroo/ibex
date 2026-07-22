# Ibex SBFL 公共库

`ibex_sbfl_common` 是 Ibex SBFL 工具的无业务依赖共享层，只包含 artifact 解析、
suspiciousness 排名计算和 rerank result schema 校验，不依赖 batch runner、统计、
OpenAI 或 Pydantic。

`tools/ibex_sbfl_batch` 和 `tools/ibex_sbfl_llm` 均依赖这个 uv 项目。

```bash
uv sync --project tools/ibex_sbfl_common --all-groups --frozen
uv run --project tools/ibex_sbfl_common --all-groups --frozen \
  pytest -q tools/ibex_sbfl_common/tests
```
