# 第 1 章：构建与运行时集成

## 1.1 FuseSoC 组成

`ibex_simple_system_sbfl.core` 定义 Verilator simple-system target，并链接：

- Ibex RTL 和 simple-system 仿真框架；
- Spike co-simulation adapter；
- `src/csrc/` 中的 SBFL C++ adapter；
- `IBEX_HOME/target/release/libcpusbfl.so`。

`ibex_sbfl_setup.core` 提供 pre-build hook：

1. `util/ibex_sbfl_setup_check.sh` 通过 `pkg-config` 检查 Spike 库；
2. `util/ibex_sbfl_build.sh` 在 `IBEX_HOME` 执行 Rust release 构建。

## 1.2 环境变量

```bash
export IBEX_HOME=/absolute/path/to/ibex
export PKG_CONFIG_PATH=/opt/spike-cosim/lib/pkgconfig:${PKG_CONFIG_PATH}
```

`IBEX_HOME` 同时用于定位 Cargo workspace、动态库和 FuseSoC 源文件。批量 runner 会把
它转换为绝对路径，并禁止临时 workdir 位于该目录内部。

## 1.3 构建命令

```bash
cd "$IBEX_HOME"
fusesoc --cores-root=. run \
  --target=sim \
  --setup \
  --build \
  lowrisc:ibex:ibex_simple_system_sbfl \
  --RV32E=0 \
  --RV32M=ibex_pkg::RV32MFast
```

输出通常为：

```text
target/release/libcpusbfl.so
build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system
```

## 1.4 参数层级

最终 executable 的根参数必须位于 command 前，generation 公共参数位于 mode 前：

```text
Vibex_simple_system [ROOT] generation [GENERATION] psbfl [MODE] -- [SIM]
Vibex_simple_system [ROOT] generation [GENERATION] wit-hw [MODE] -- [SIM]
Vibex_simple_system [ROOT] analysis [ANALYSIS] -- [SIM]
```

Ibex simple system 常用 simulator argument 为 `-c <cycle-limit>`。SBFL generation
还会根据 tracker 长度追加 `-I <instruction-limit>`。

## 1.5 兼容性要求

从 checkpoint 恢复或分析时，以下值必须和保存时完全一致：

- coverage 名称及顺序；
- state 名称及顺序；
- tracker window size；
- 对应 bug diff 构建出的 coverage point 布局。

因此批量 analysis 会重新应用原 diff 并构建对应仿真器，而不是使用未打补丁的公共 binary。

