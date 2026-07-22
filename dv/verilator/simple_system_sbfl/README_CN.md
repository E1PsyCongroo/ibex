# Ibex Simple System 的 CPU SBFL 集成

中文 | [English](README.md)

本目录将独立的 [`cpusbfl` Rust 项目](sbfl/README_CN.md)集成到 Ibex Verilator
Simple System 和 Spike 协同仿真中。宿主仿真器、coverage/state C ABI、FuseSoC
构建描述和 Ibex 实验流程都在这里维护，不放入通用 SBFL 项目。

## 集成目录

```text
dv/verilator/simple_system_sbfl/
├── ibex_simple_system_sbfl.core    # Verilator/FuseSoC 仿真 target
├── ibex_sbfl_setup.core            # 依赖检查和 Rust 构建 hook
├── util/                           # setup/build hook 脚本
├── src/csrc/                       # 仿真、Spike、coverage、state 桥接
├── sbfl/                           # 独立 CPU SBFL 项目
└── docs/                           # Ibex 专属技术文档
```

运行路径为：

```text
RISC-V ELF
  -> cpusbfl LibAFL executor
  -> simple_system_sbfl.cc::sim_main()
  -> Ibex RTL 与 Spike lockstep 比较
  -> Verilator coverage 和 Spike 体系结构状态
  -> cpusbfl selection 与 SBFL
  -> coverage point / RTL block 排名
```

## 环境依赖

集成环境需要：

- Rust 工具链和 `cargo-make`；
- Ibex 要求的 FuseSoC 和 Verilator；
- Ibex co-simulation 版本的 Spike；
- `riscv-riscv`、`riscv-disasm`、`riscv-fdt` 的 `pkg-config` 配置。

Spike 安装到 `/opt/spike-cosim` 时：

```bash
export PKG_CONFIG_PATH=/opt/spike-cosim/lib/pkgconfig:${PKG_CONFIG_PATH}
cargo install cargo-make
```

`IBEX_HOME` 必须指向 Ibex 仓库根目录。FuseSoC pre-build hook 会在该目录执行
`cargo make build-all`，并将 `target/release/libcpusbfl.so` 链接进仿真器。

## 构建

在 Ibex 仓库根目录执行：

```bash
export IBEX_HOME="$PWD"

fusesoc --cores-root=. run \
  --target=sim \
  --setup \
  --build \
  lowrisc:ibex:ibex_simple_system_sbfl \
  --RV32E=0 \
  --RV32M=ibex_pkg::RV32MFast
```

最终 executable 通常位于：

```text
build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system
```

```bash
SBFL_BIN=build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system
"$SBFL_BIN" --help
```

## 运行单次 generation

初始 ELF 必须能够复现 Ibex/Spike 差分错误。

```bash
"$SBFL_BIN" \
  --coverage verilator.branch,verilator.line \
  --state PCState,ArchIntRegState,CSRState \
  generation \
  --input examples/sw/benchmarks/coremark/coremark.elf \
  --output logs/manual \
  --max-iters 100 \
  --top-pass 10 \
  --selection diverse \
  --save-corpus logs/manual/saved_corpus \
  psbfl \
  --mutator-window-size 20 \
  --mutator-weight-strategy uniform \
  -- -c 5000000
```

根参数 `--coverage`/`--state` 位于 `generation` 前；generation 公共参数位于
`psbfl` 或 `wit-hw` 前；模式专属参数位于 mode 后。

## Bugset runner

Ibex 仓库的 `scripts/` 提供：

- [`run_bugset_psbfl.py`](../../../scripts/run_bugset_psbfl.py)：选择
  `GenerationMode::PSBFL`；
- [`run_bugset_withw.py`](../../../scripts/run_bugset_withw.py)：选择
  `GenerationMode::WitHW`；
- [`run_bugset_sbfl.py`](../../../scripts/run_bugset_sbfl.py)：通用兼容入口；
- [`rerun_bugset_corpus.py`](../../../scripts/rerun_bugset_corpus.py)：从旧 bugset run
  中每个 case 的 saved corpus 继续执行 generation；
- [`analyze_bugset_corpus.py`](../../../scripts/analyze_bugset_corpus.py)：重新构建旧日志
  中记录的 bug case，并对各自的 `saved_corpus` 执行 `analysis`。
- [`run_args_sweep_sbfl.py`](../../../scripts/run_args_sweep_sbfl.py)：并行运行
  PSBFL 参数组合并合并汇总结果。

这些入口统一调用 uv 管理的 Python 实现
[`tools/ibex_sbfl_batch`](../../../tools/ibex_sbfl_batch/README_CN.md)。

示例：

```bash
scripts/run_bugset_psbfl.py \
  --all verify_dataset \
  --input examples/sw/benchmarks/coremark/coremark.elf \
  --max-iters 100 \
  --jobs 4 \
  --save-corpus \
  --logs logs/psbfl
```

wrapper 的 `--save-corpus` 是布尔开关，每个 case 保存到
`<case_logdir>/saved_corpus`。应用 bug diff 前，workdir 只复制 `dv/`、`vendor/`、
`rtl/`、`shared/`、根目录 `.core` 文件、`Cargo.lock` 和 `Cargo.toml`。

## 重新分析 saved corpus

```bash
scripts/analyze_bugset_corpus.py \
  --input-logs logs/psbfl/<run-id> \
  --top-pass 50 \
  --selection diverse \
  --metric ochiai \
  --jobs 4 \
  --logs logs/analysis
```

analysis runner 读取原 `run_status.tsv` 和 `run.log`，提取 coverage/state/tracker
配置，重新构建每个 patched design，并将旧 checkpoint 作为 `analysis --input`。
结果写入新的日志树，不覆盖 checkpoint。使用 `--dry-run` 可以先检查计划。

## 文档

- [集成文档索引](docs/README.md)
- [构建和运行时集成](docs/01-build-and-runtime.md)
- [批量 generation 与 analysis runner](docs/02-batch-runners.md)
- [仿真器、coverage 和 state adapter](docs/03-simulator-adapter.md)
- [独立 CPU SBFL 技术文档](sbfl/docs/README.md)

通用 SBFL 算法、checkpoint 格式、CLI 语义和宿主 ABI 位于 `sbfl/`；Ibex、Spike、
FuseSoC 和 bugset 专属说明位于本目录。
