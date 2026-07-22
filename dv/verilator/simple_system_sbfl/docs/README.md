# Ibex CPU SBFL 集成文档

本目录只描述 Ibex 宿主集成。通用 SBFL 算法、CLI、checkpoint 和 Rust 模块设计见
[`../sbfl/docs/`](../sbfl/docs/README.md)。

## 章节

| 章节 | 内容 |
| --- | --- |
| [01 构建与运行时](01-build-and-runtime.md) | FuseSoC target、Rust build hook、Spike 依赖和最终链接关系 |
| [02 批量 runner](02-batch-runners.md) | bugset workdir、generation、saved corpus 和批量 analysis |
| [03 仿真器 adapter](03-simulator-adapter.md) | `sim_main`、Verilator coverage、Spike state 和 C ABI 实现 |

## 文档边界

- `../sbfl/`：独立 Rust 项目，只记录可被不同宿主复用的行为。
- 当前目录：Ibex/Spike/Verilator/FuseSoC 和 Ibex 仓库脚本。
- 仓库根 `scripts/`：可执行的 bugset generation/analysis runner。

