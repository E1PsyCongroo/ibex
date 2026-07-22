# Ibex SBFL 批处理工具

`ibex_sbfl_batch` 负责所有不调用 LLM 的 Ibex SBFL 非交互工作流：

- PSBFL 与 WitHW bugset generation；
- 并行执行 PSBFL 参数扫描；
- 从 `saved_corpus` 继续 generation；
- 重建 patched simulator 后执行 checkpoint analysis；
- 生成 SBFL/LLM rerank 汇总 TSV 并计算统计指标。

LLM 请求、prompt 和 rerank 核心实现仍保存在 `tools/ibex_sbfl_llm`；通用 artifact
解析来自 [`tools/ibex_sbfl_common`](../ibex_sbfl_common/README_CN.md)。

## 环境

```bash
uv sync --project tools/ibex_sbfl_batch --all-groups --frozen
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch --help
```

仓库根目录的 Python 脚本是兼容入口，会自动调用该 uv 项目。
FuseSoC 与 Edalize 固定为 Ibex Python 工具环境使用的版本，确保 FuseSoC pre-build
脚本调用的 `python3` 可以导入 Edalize。

## 常用命令

```bash
# Generation
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch generation psbfl \
  --all verify_dataset --max-iters 100 --save-corpus

# 从 checkpoint 继续 generation
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch rerun \
  --input-logs logs/psbfl/<run-id> --max-iters 100 --no-save-corpus

# 分析 checkpoint
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch analysis \
  --input-logs logs/psbfl/<run-id> --selection diverse

# 参数扫描
scripts/run_args_sweep_sbfl.py --all verify_dataset \
  --top-pass 10,20 --mutator-window-size 5,10 \
  --mutator-weight-strategy uniform,tail_linear --sweep-jobs 2 \
  -- --max-iters 20

# 汇总与统计
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch summarize sbfl \
  verify_dataset logs/psbfl -o sbfl_summary.tsv
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch stats sbfl \
  sbfl_summary.tsv
```

`--` 后的参数原样传给 simulator。`analysis --dry-run` 只解析旧日志并输出计划命令，
不会复制源码、应用 patch 或构建。

每个命令启动时都会用 `[INFO]` 输出原始 CLI argv，以及解析后的全部 execution 与
工作流配置字段，包括路径、selection 和 mode 参数、布尔开关、rerun/analysis
继承值及 simulator 参数。

## Workdir 与输出

每个执行 workdir 只包含 `dv/`、`vendor/`、`rtl/`、`shared/`、根目录
`*.core`、`Cargo.lock` 和 `Cargo.toml`。每次运行创建带时间戳的日志目录以及
`run_status.tsv`。runner 会跟踪子进程组，以便中断时结束正在运行的 patch、构建和
SBFL 进程。

## 开发

```bash
uv run --project tools/ibex_sbfl_batch --all-groups --frozen \
  pytest -q tools/ibex_sbfl_batch/tests
uv run --project tools/ibex_sbfl_batch --all-groups --frozen ruff check tools/ibex_sbfl_batch
uv run --project tools/ibex_sbfl_batch --all-groups --frozen ruff format --check tools/ibex_sbfl_batch
```
