# 第 3 章：仿真器、coverage 与 state adapter

## 3.1 C++ 组件

`src/csrc/` 中的主要文件为：

| 文件 | Ibex 集成职责 |
| --- | --- |
| `simple_system_sbfl.cc` | 提供 `sim_main`，建立并重置 Verilator 仿真上下文 |
| `spike_cosim.cc` | 驱动 Spike，与 Ibex retire/memory 行为比较 |
| `cosim_stats.cc` | 实现 Rust 需要的 coverage/state C ABI |
| `coverage.cc` | 导出并解析 Verilator coverage 数据 |
| `state_tracker.cc` | 从 Spike 状态构造 PC、整数寄存器和 CSR 序列 |

通用 C ABI 的 Rust 侧定义和数据模型见
[`sbfl/docs/03-data-and-ffi.md`](../sbfl/docs/03-data-and-ffi.md)。

## 3.2 单次仿真生命周期

1. Rust 将 `BytesInput` 写入临时 ELF；
2. `sim_main` 使用 `-E <elf>` 启动 simple system；
3. adapter 重置 Verilator controller 和 `CosimStats`；
4. Ibex 与 Spike lockstep 执行；
5. 退休指令推进 state tracker；
6. Verilator 在执行结束时导出 coverage；
7. Rust 通过 `update_stats_cover/state` 复制数据；
8. 差分匹配返回 0，不匹配返回非零。

## 3.3 Coverage

当前集成注册 `verilator` coverage provider，并支持：

```text
verilator.line
verilator.branch
verilator.expr
verilator.toggle
```

Point metadata 包含 filename、line、column、type 和 hierarchy。RTL block 映射依赖
line 与 hierarchy 和本次构建使用的 RTL 源文件一致。

## 3.4 State

当前集成从 Spike 暴露：

- `PCState`；
- `ArchIntRegState`；
- `CSRState`。

C++ 与 Rust 通过 `memcpy` 传输数组，因此字段顺序、宽度、对齐和序列长度必须一致。
修改 Spike state 采样或 CSR 结构时，需要同时更新两侧定义。

## 3.5 重置和并发约束

Rust 使用 in-process executor，同一进程内会重复调用 `sim_main`。adapter 必须保证每次
输入前清除覆盖率、状态序列和仿真器全局状态。Bugset 并发通过独立进程和独立 workdir
实现，不在单个 Verilator 进程中并行执行多个输入。
