# 第 2 章：批量 generation 与 analysis

## 2.1 Runner 分工

| 脚本 | 职责 |
| --- | --- |
| `scripts/run_bugset_psbfl.py` | PSBFL 薄入口 |
| `scripts/run_bugset_withw.py` | WitHW 薄入口 |
| `scripts/run_bugset_sbfl.py` | 通用 generation 兼容入口 |
| `scripts/rerun_bugset_corpus.py` | 从旧 checkpoint 继续 generation |
| `scripts/analyze_bugset_corpus.py` | 从旧日志批量执行 checkpoint analysis |
| `scripts/run_args_sweep_sbfl.py` | 并行扫描 PSBFL 参数组合并合并结果 |

所有 Python 入口都只是薄启动器，公共参数、校验、workdir、构建、并发、日志及统计实现位于
uv 项目 [`tools/ibex_sbfl_batch`](../../../../tools/ibex_sbfl_batch/README_CN.md)。

## 2.2 Workdir 白名单

每个 case 的临时 workdir 只复制：

```text
dv/
vendor/
rtl/
shared/
IBEX_HOME/*.core
Cargo.lock
Cargo.toml
```

根目录 `.core` 文件单独复制并逐一检查。随后 runner 执行 `patch -p1`，再运行 FuseSoC
setup/build。默认完成后删除 workdir；`--keep-workdir` 可保留现场。

## 2.3 Generation

```bash
scripts/run_bugset_psbfl.py \
  --all verify_dataset \
  --input examples/sw/benchmarks/coremark/coremark.elf \
  --max-iters 100 \
  --jobs 4 \
  --save-corpus \
  --checkpoint-interval 25 \
  --logs logs/psbfl
```

`--input` 在 runner 切换 workdir 前转换为绝对路径。wrapper 中的 `--save-corpus` 不接收
文件名，而是把 checkpoint 固定保存到：

```text
<case_logdir>/saved_corpus
```

每个 run 生成 `run_status.tsv`，记录原 diff、case 目录、日志目录、workdir、状态和耗时。

## 2.4 从 saved corpus 继续 generation

```bash
scripts/rerun_bugset_corpus.py \
  --input-logs logs/psbfl/<run-id> \
  --max-iters 100 \
  --selection diverse \
  --no-save-corpus \
  --jobs 4 \
  --logs logs/rerun
```

runner 从旧 `run.log` 提取 generation mode、coverage、state、tracker window 及未被
命令行覆盖的 generation 参数，然后重新复制源码、应用 diff、构建并从各自的
`saved_corpus` 继续运行。默认会把新 checkpoint 保存到新 case logdir；
`--no-save-corpus` 同时关闭最终保存和周期保存。

## 2.5 Saved corpus analysis

```bash
scripts/analyze_bugset_corpus.py \
  --input-logs logs/psbfl/<run-id> \
  --top-pass 50 \
  --selection diverse \
  --selection-diversity-weight 0.4 \
  --selection-pool-factor 3 \
  --top-sus 20 \
  --metric ochiai \
  --jobs 4 \
  --logs logs/analysis
```

analysis runner 对每个旧 case：

1. 从 `run_status.tsv` 找到原 diff 和 case logdir；
2. 从 `run.log` 提取 coverage、state、tracker window 和旧 analysis 参数；
3. 检查 `<old_case_logdir>/saved_corpus`；
4. 重建 patched simulator；
5. 执行 `analysis --input <saved_corpus>`；
6. 将结果写入新的 analysis run 目录。

命令行 analysis 参数覆盖从旧日志继承的值。旧 checkpoint 不会被修改。执行前可使用：

```bash
scripts/analyze_bugset_corpus.py \
  --input-logs logs/psbfl/<run-id> \
  --selection diverse \
  --dry-run
```

## 2.6 状态与日志

常见状态包括 `OK`、`COPY_FAIL`、`APPLY_FAIL`、`BUILD_FAIL`、`ANALYSIS_FAIL`、
`CORPUS_MISSING` 和 `CONFIG_FAIL`。单个 case 失败不会阻止其他并发 case，最终退出码由
汇总中的失败数量决定。
