# Offline LLM reranking

这个工具重新使用已保存的 `llm_rerank.json` 和
`llm_rerank.json.prompt.md`，无需再次调用模型：

- candidate 及原始 suspiciousness 从 prompt 读取；
- LLM score 只从 JSON 的 `raw_model_response` 读取；
- 通过 `--llm-weight` 调整 LLM 与 SBFL 分数权重，`1.0` 即为纯 LLM 排序；
- 分数组合直接使用原始 suspiciousness，不做归一化；
- 在输入目录中写出独立的参数结果，然后复用 `ibex-sbfl-batch` 的 stats
  逻辑输出 Top-1/5/10、MRR 和 MAR@10。

## 用法

```bash
uv run --project tools/ibex_sbfl_rerank ibex-sbfl-rerank \
  logs/claude-opus-4.8/reduce_20 \
  --llm-weight 0.5
```

纯 LLM 排序：

```bash
uv run --project tools/ibex_sbfl_rerank ibex-sbfl-rerank \
  logs/claude-opus-4.8/reduce_20 \
  --llm-weight 1.0
```

默认输出文件类似 `llm_rerank.w0.5.json` 和 `llm_rerank.w1.json`。

每个 case 的结果写在原 `llm_rerank.json` 旁边；汇总 TSV 写在输入根目录。
工具从当前目录或输入路径的父目录自动寻找 `verify_dataset`，也可以通过
`--bugset-root` 显式指定。`--top-k` 可覆盖源 JSON 的 Top-K，`--summary` 可指定
汇总 TSV 路径。
