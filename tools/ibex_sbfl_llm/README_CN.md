# Ibex SBFL LLM 重排序器

`ibex_sbfl_llm` 只保存定位流程中与模型交互相关的部分：读取 SBFL block 候选、收集
patched RTL 上下文、调用 OpenAI 兼容 API、校验结构化响应，并生成 rerank JSON。

批量 generation、checkpoint analysis、汇总 TSV 和统计功能独立保存在
[`tools/ibex_sbfl_batch`](../ibex_sbfl_batch/README_CN.md)。

## 环境

```bash
uv sync --project tools/ibex_sbfl_llm --all-groups --frozen
uv run --project tools/ibex_sbfl_llm --frozen ibex-sbfl --help
```

锁文件包含轻量的本地 `ibex-sbfl-common` 依赖，用于通用 SBFL artifact 解析。
LLM 与 batch 项目不再互相依赖。

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
  --ranking-strategy weighted \
  --output logs/sbfl/<case-logdir>/llm_rerank.json
```

主要参数：

- `--source-mode snippets|full|auto`：选择 RTL 上下文策略；
- `--candidate-count`：限制发送给模型的 SBFL 候选数量；
- `--top-k`：设置通过校验的 rerank 结果数量；
- `--ranking-strategy weighted|llm-only|rrf`：组合 SBFL 与模型分数；
- `--structured-output auto|strict|off`：控制响应 schema；
- `--dry-run --save-prompt`：只准备输入，不调用模型。

默认要求 RTL 源码与传入 patch 匹配。只有在源码已经应用 patch 且已独立确认时，才应
使用 `--allow-unpatched-source`。

## 输出

输出 JSON 记录解析后的输入、配置、prompt hash、模型信息、token 使用量、延迟、候选
评估和最终排名。批处理项目使用该 schema 生成 LLM 汇总与统计：

```bash
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch summarize llm \
  verify_dataset logs/sbfl -o llm_rerank_summary.tsv
```

## 开发

```bash
uv run --project tools/ibex_sbfl_llm --all-groups --frozen \
  pytest -q tools/ibex_sbfl_llm/tests
uv run --project tools/ibex_sbfl_llm --all-groups --frozen \
  ruff check tools/ibex_sbfl_llm/src/ibex_ibex_sbfl_llm tools/ibex_sbfl_llm/tests
```
