# Ibex SBFL + LLM 缺陷定位工具

[English documentation](README.md)

`ibex-sbfl-llm` 是一个使用 `uv` 管理的 Python 项目，用于利用大语言模型对
Ibex 的频谱缺陷定位（Spectrum-Based Fault Localization，SBFL）结果进行后处理。
它会还原 SBFL 运行时实际使用的含缺陷 RTL，将紧凑且带原始行号的
SystemVerilog 上下文通过 OpenAI Responses API 发送给模型，为每个候选 block 打分，生成
确定性的融合排序，并汇总 SBFL 与 LLM 的缺陷定位指标。

本项目取代了原来的三个独立脚本：

- `scripts/rerank_sbfl_with_llm.py`
- `scripts/summarize_sbfl_blocks.py`
- `scripts/summarize_llm_rerank.py`

这些文件仍作为兼容入口保留，但具体实现、提示词、测试和依赖管理都已迁移到本项目。

## 为什么必须还原 patch

SBFL 在临时 Ibex 工作目录中运行，运行前会先应用 mutation diff。SBFL 结束后，临时
工作目录通常会被删除。如果直接将仓库中干净的 `rtl/` 交给大模型，模型将看不到
SBFL 实际分析的缺陷代码。

本项目会从 `run.log` 中读取 `[DIFF]`，将 `rtl/` 复制到临时目录，并只在临时副本中
应用 diff，然后从 patched RTL 构造提示词。调用者提供的源码目录不会被修改。patch
信息和已知真值会记录在结果中用于审计，但不会发送给模型。

## 主要功能

- 从 `result.log` 的 `Suspiciousness of block:` 段解析 block 排名。
- 使用 `blocks.json` 中的 `(scope, bid)` 补充模块名、block 类型和源码行。
- 从 `run.log` 的 `[DIFF]` 自动获取缺陷注入 diff，也支持 `--patch` 显式覆盖。
- 只在隔离的临时 RTL 副本中应用 diff。
- 自动识别输入 RTL 已经包含 patch 的情况。
- 拒绝不安全路径以及与输入 RTL 不匹配的 patch。
- 按模块合并和去重 snippets，删除空白行并保留原始源码行号。
- 将冗长的候选行号压缩成连续区间。
- 将系统提示词、用户提示词和修复提示词独立存放在 Markdown 文件中。
- 使用官方 `openai` Python 库的 `client.responses.create`，并支持配置实现
  Responses API 的兼容 `base_url`。
- 优先请求严格 JSON Schema 输出，并使用 Pydantic 校验所有响应。
- 要求模型为每个候选打分，而不是只返回 Top-K。
- 支持纯 LLM 排序、加权分数融合和 Reciprocal Rank Fusion（RRF）。
- 输出版本化、可审计的 `llm_rerank.json`。
- 生成 SBFL/LLM 汇总 TSV，同时保留原有排名统计规则。
- 同时读取旧版 rerank 结果和 schema v2 结果。
- 提供 API mock、patch、prompt、排序、解析和端到端测试。

## 整体流程

```text
result.log + blocks.json
          |
          v
     解析 SBFL 候选
          |
          v
run.log -> 获取 [DIFF] -> 复制 rtl/ -> 在临时工作区应用 patch
                                         |
                                         v
                              构造并去重 RTL snippets
                                         |
失败信息 --------------------------------+
                                         |
                                         v
                               调用 Responses API
                                         |
                                         v
                              校验所有候选评估结果
                                         |
                                         v
                               融合 LLM 与 SBFL 证据
                                         |
                                         v
                llm_rerank.json + 汇总 TSV + 统计指标
```

## 环境要求

- Python 3.11 或更高版本。
- `PATH` 中可找到 [`uv`](https://docs.astral.sh/uv/)。
- `PATH` 中可找到 `git`；临时应用 patch 使用 `git apply`。
- 真正执行 rerank 时，需要 OpenAI Responses API 服务，或实现 `/responses` 的兼容服务。
- SBFL 结果目录中存在 `result.log` 和 `blocks.json`。
- 可读取的缺陷注入 diff，通常由 `run.log` 引用。
- 输入 RTL 目录下存在候选 block 对应的 SystemVerilog 模块。

`--dry-run`、汇总命令和默认测试套件不会访问任何外部服务。

## 安装

下面的命令均从 Ibex 仓库根目录执行。

创建或更新锁定的虚拟环境：

```bash
uv sync --project tools/sbfl_llm --all-groups --frozen
```

`uv.lock` 锁定完整依赖环境。虚拟环境位于 `tools/sbfl_llm/.venv/`，并已被
Git 忽略。

检查 CLI：

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl --help
```

## 输入目录结构

标准 rerank 输入目录如下：

```text
logs/reduce/RUN/CASE/
├── result.log
├── blocks.json
├── run.log
├── status.txt                 # 汇总命令使用
├── sbfl_time.txt              # 可选，SBFL 耗时
├── fuzzing_time.txt           # 可选，fuzzing 耗时
├── test_info.md               # 可选
├── test_info.txt              # 可选
└── test_info.json             # 可选
```

`result.log` 中需要存在类似下面的段落：

```text
Suspiciousness of block:
top-1: Block(scope: TOP.example.core.alu_i, bid: 17) with suspicious '1.000000'
```

`blocks.json` 必须是 JSON 数组。候选映射使用 `(scope, bid)`：

```json
[
  {
    "scope": "TOP.example.core.alu_i",
    "bid": 17,
    "module": "ibex_alu",
    "lines": [84, 85, 86, 87],
    "type": "Always(COMB)"
  }
]
```

自动发现 patch 时，`run.log` 中必须有且只有一个 `[DIFF]`：

```text
[DIFF] /absolute/path/to/verify_dataset/103/ibex_id_stage.sv.diff
```

## 快速开始

通过环境变量设置 API key：

```bash
export OPENAI_API_KEY="your-api-key"
```

对一个 SBFL 结果执行重排序：

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl \
  logs/reduce/RUN/CASE \
  --model MODEL
```

默认输出文件为：

```text
logs/reduce/RUN/CASE/llm_rerank.json
```

## Rerank 命令

```text
ibex-sbfl rerank [OPTIONS] RTL_SOURCE SBFL_RESULT
```

`SBFL_RESULT` 可以是结果目录，也可以直接是目录中的 `result.log`。

### Rerank 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--model MODEL` | 无 | 发送给 API 的模型标识。除 `--dry-run` 外必填。 |
| `--api-base URL` | `OPENAI_BASE_URL` 或 `https://api.openai.com/v1` | Responses API-compatible 根地址。末尾的 `/responses` 会被归一化，所有模型请求都使用 `/responses`。 |
| `--api-key-env NAME` | `OPENAI_API_KEY` | 保存 API key 的环境变量名。对明确无需鉴权的兼容服务可传空字符串。 |
| `--candidate-count N` | `50` | 从打印的 SBFL 排名中考虑的候选数；之后可能跳过无法映射的 block。 |
| `--top-k K` | `10` | 写入兼容 `rankings` 数组的最终候选数。 |
| `--source-mode MODE` | `snippets` | 可选 `snippets`、`full` 或 `auto`。 |
| `--max-source-chars N` | `240000` | 渲染 RTL 源码部分的最大字符数。 |
| `--snippet-radius N` | `30` | 每个候选 block 前后的期望上下文行数。 |
| `--patch PATH` | 自动发现 | 覆盖 `run.log` 中的 `[DIFF]`。 |
| `--allow-unpatched-source` | 关闭 | 显式允许在没有 patch 的情况下工作。 |
| `--test-info TEXT` | 无 | 直接传入失败描述，与 `--test-info-file` 互斥。 |
| `--test-info-file PATH` | 自动发现 | 从指定文件读取失败描述。 |
| `--ranking-strategy STRATEGY` | `weighted` | 可选 `weighted`、`llm-only` 或 `rrf`。 |
| `--llm-weight VALUE` | `0.75` | weighted 和 RRF 中的 LLM 权重，必须在 `[0,1]`。 |
| `--structured-output MODE` | `auto` | 可选 `auto`、`strict` 或 `off`。 |
| `--timeout SECONDS` | `180` | 单次 SDK 请求超时。 |
| `--temperature VALUE` | 不发送 | 仅在显式传入时发送，以兼容拒绝 temperature 的模型。 |
| `--retries N` | `2` | 瞬时请求错误的最大重试次数，也是模型格式修复的最大次数。 |
| `--retry-delay SECONDS` | `2` | 请求重试和格式修复的基础等待时间。 |
| `--output PATH` | `SBFL_RESULT/llm_rerank.json` | 输出路径；所需父目录会自动创建。 |
| `--save-prompt` | 关闭 | 保存包含完整系统/用户提示词的 `OUTPUT.prompt.md`。 |
| `--dry-run` | 关闭 | 校验输入、临时应用 patch 并打印完整提示词，但不调用 API。 |

`--top-k` 不能大于 `--candidate-count`。如果缺失的 `blocks.json` 映射导致有效
候选数少于 `--top-k`，命令会直接报错。

## Patch 还原策略

patch 的选择优先级为：

1. 显式传入的 `--patch PATH`。
2. `SBFL_RESULT/run.log` 中唯一的 `[DIFF]` 路径。
3. 仅当显式传入 `--allow-unpatched-source` 时允许不使用 patch。

选择 patch 后依次执行：

1. 检查 patch 文件是否存在。
2. 解析 `---` 和 `+++` 文件头。
3. 拒绝绝对路径、`..` 路径穿越以及 `rtl/` 之外的修改。
4. 将输入 RTL 复制到临时 `workspace/rtl`。
5. 先运行 `git apply --check`，成功后再应用 diff。
6. 正向检查失败时运行反向检查，以识别输入源码已经包含 patch 的情况。
7. 所有模型上下文都从临时 patched RTL 中读取。
8. 完成后自动删除临时工作目录。

输出中的 `patch_state` 可能为：

| 状态 | 含义 |
| --- | --- |
| `applied` | diff 已成功应用到临时副本。 |
| `already_patched` | 输入 RTL 已经包含该 diff。 |
| `not_available` | 因显式使用 `--allow-unpatched-source` 而未使用 patch。 |

如果 `run.log` 已声明 patch，但文件不存在或无法应用，即使同时传入
`--allow-unpatched-source` 也会报错。这可以防止工具静默分析干净或无关的 RTL。

## RTL 源码收集

解析候选模块时，工具先检查 `RTL_SOURCE/<module>.sv`，不存在时再递归搜索
`RTL_SOURCE`。完全找不到会报错；找到多个同名模块也会报错，因为自动选择可能把错误
源码发送给模型。

### `snippets` 模式

默认模式。针对每个相关模块，工具会：

- 合并所有候选 block 行。
- 在每个 block 前后扩展 `--snippet-radius` 行。
- 在模块层面自然合并重叠或相邻上下文。
- 每个模块中的一行物理源码最多发送一次。
- 删除空行和只包含空白的行。
- 为保留的每行代码保留原始行号。
- 保留注释，因为注释通常能说明 RTL 设计意图。
- 只有在跳过非空代码时才插入省略标记。
- 在源码前提供 candidate ID 到行号区间的映射。

如果 snippets 超过 `--max-source-chars`，上下文半径会逐行缩小到 0，但候选 block
自身的代码绝不会被静默删除。如果半径 0 仍超出限制，工具会要求减少候选数或增大
限制。

### `full` 模式

发送所有相关模块文件，删除空白行并保留原始行号。如果渲染后的源码超出字符限制，
直接报错。

### `auto` 模式

完整模块能够放入限制时使用完整文件，否则自动切换到自适应 snippets。

`--max-source-chars` 只统计渲染后的 RTL 源码。候选 JSON、失败信息、系统提示词、
schema 指令和模型输出 token 都不在该限制中。使用新模型或新数据集时，应通过
`--dry-run` 检查完整 prompt 是否适合模型的上下文窗口。

## 失败信息

失败信息按以下顺序选择：

1. `--test-info TEXT`。
2. `--test-info-file PATH`。
3. `result.log` 同目录的 `test_info.md`。
4. `result.log` 同目录的 `test_info.txt`。
5. `result.log` 同目录的 `test_info.json`。
6. 通用的架构状态不一致描述。

通用描述可以保证实验能够运行，但具体 mismatch 信息通常更有利于因果推理。工具不会
从 mutation diff 或 `bug_info.json` 生成失败描述，因为这样会向模型泄露评测答案。

## 提示词设计

提示词作为 package resource 独立存放：

```text
src/ibex_sbfl_llm/prompts/
├── system.md
├── rerank.md
└── repair.md
```

系统提示词要求模型区分真正生成错误值的根因逻辑，与只是传播上游错误值的下游 block。
重点检查控制/数据谓词、状态跳转、算术、索引、位宽、有符号性、ready/valid 握手、
enable 和寄存器更新。SBFL suspiciousness 只作为先验，而不是正确答案。

用户提示词包含失败信息、完整合法候选集合、压缩后的候选行号范围和 patched RTL。
模型必须为每个候选返回一个评估。只有本地校验拒绝响应后，才会使用修复提示词。

patch 内容、patch 路径、`bug_info.json` 和已知修改行号都不会发送给模型。输出会记录
prompt SHA-256。需要审计完整文本时可以使用 `--save-prompt`；保存的提示词可能包含
大量 RTL。

## Responses API 与结构化模型响应

所有模型调用都使用 `client.responses.create`，不存在 Chat Completions 请求或回退。
系统提示词通过 `instructions` 发送，对话通过 `input` 发送，结构化输出配置位于
`text.format`。由于提示词包含 RTL 和失败信息，请求显式设置 `store=False`。

模型响应格式为：

```json
{
  "assessments": [
    {
      "candidate_id": "B018",
      "score": 0.97,
      "causal_role": "probable_root_cause",
      "key_lines": [853],
      "reason": "反馈赋值使写使能依赖自身输出。"
    }
  ]
}
```

允许的 `causal_role`：

- `probable_root_cause`
- `causal_upstream`
- `propagated_symptom`
- `weakly_related`
- `insufficient_evidence`

本地校验要求：

- 每个候选 ID 必须恰好出现一次。
- 不允许未知或重复 ID。
- `score` 必须在闭区间 `[0,1]`。
- `causal_role` 必须是合法枚举。
- `reason` 不能为空。
- `key_lines` 必须属于该候选 block 的行号集合。
- 结构化对象中不允许未定义字段。

### 结构化输出模式

| 模式 | 行为 |
| --- | --- |
| `auto` | 先请求严格 JSON Schema；如果服务明确拒绝该格式，再依次降级到 JSON object 和 prompt 约束 JSON。所有阶段都保留 Pydantic 本地校验。 |
| `strict` | 必须支持 JSON Schema，不允许降级。 |
| `off` | 不发送 `text.format`，从普通 Responses API 输出文本中提取并校验 JSON。 |

降级模式可以解析 Markdown JSON 代码块和少量包围文本，但仍建议要求兼容模型只输出
JSON。

连接错误、超时、HTTP 429 和服务端错误会使用指数退避及随机抖动重试。永久 API 错误
不会重试。API 返回成功但 schema 或候选集合校验失败时，工具会开启修复轮次，将上一次
响应和具体校验错误反馈给模型。修复轮次会重发本地保存的输入历史，不依赖服务端会话状态
或 `previous_response_id`。

## API 配置

### 默认 OpenAI 配置

```bash
export OPENAI_API_KEY="your-api-key"

uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT --model MODEL
```

### 自定义 key 环境变量

```bash
export MY_LLM_KEY="your-api-key"

uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT --model MODEL --api-key-env MY_LLM_KEY
```

### 自定义 Responses-compatible 服务

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT \
  --model compatible-model \
  --api-base https://example.invalid/v1
```

### 无鉴权本地服务

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT \
  --model local-model \
  --api-base http://127.0.0.1:8000/v1 \
  --api-key-env ''
```

无鉴权模式下，SDK 不会发送 Authorization header。不要将 API key 直接放进命令历史、
源码或 rerank 输出文件。

## 排序与分数融合

模型为每个候选输出语义 `llm_score`。SBFL suspiciousness 在当前候选集合内归一化：

```text
normalized_sbfl_score = suspiciousness / max_candidate_suspiciousness
```

如果最大 suspiciousness 为 0，则所有归一化 SBFL 分数均为 0。

### `weighted` 策略

默认策略：

```text
final_score = llm_weight * llm_score
            + (1 - llm_weight) * normalized_sbfl_score
```

默认 `--llm-weight 0.75` 时：

```text
final_score = 0.75 * llm_score + 0.25 * normalized_sbfl_score
```

### `llm-only` 策略

```text
final_score = llm_score
```

SBFL 分数仍用于确定性 tie-break。

### `rrf` 策略

实现固定使用 `k = 60`：

```text
final_score = llm_weight / (60 + llm_rank)
            + (1 - llm_weight) / (60 + original_sbfl_rank)
```

不同模型的分数标定差异较大时，可以使用 RRF。

最终排序规则是确定性的：

1. `final_score` 降序。
2. `llm_score` 降序。
3. `normalized_sbfl_score` 降序。
4. 原始 SBFL rank 升序。
5. candidate ID 升序。

## 输出文件

新版输出使用 `schema_version: 2`。下面是缩略示例：

```json
{
  "schema_version": 2,
  "tool_version": "0.1.0",
  "model": "MODEL",
  "inputs": {
    "result_log": "/absolute/path/result.log",
    "result_log_sha256": "...",
    "blocks_json": "/absolute/path/blocks.json",
    "blocks_json_sha256": "...",
    "rtl_source": "/absolute/path/rtl",
    "patch_path": "/absolute/path/bug.diff",
    "patch_sha256": "...",
    "patch_state": "applied",
    "patch_changed_files": ["rtl/ibex_id_stage.sv"],
    "patched_file_hashes": {
      "rtl/ibex_id_stage.sv": "..."
    },
    "test_info_source": "generic"
  },
  "config": {
    "candidate_count": 50,
    "top_k": 10,
    "source_mode": "snippets",
    "requested_source_mode": "snippets",
    "snippet_radius": 30,
    "effective_snippet_radius": 30,
    "max_source_chars": 240000,
    "source_chars": 188140,
    "ranking_strategy": "weighted",
    "llm_weight": 0.75,
    "structured_output": "auto",
    "temperature": null,
    "timeout": 180.0,
    "retries": 2,
    "retry_delay": 2.0
  },
  "request": {
    "api": "responses",
    "system_prompt_sha256": "...",
    "user_prompt_sha256": "...",
    "prompt_sha256": "...",
    "structured_output": "json_schema",
    "response_id": "...",
    "attempts": 1,
    "usage": {
      "input_tokens": 1234,
      "output_tokens": 567,
      "total_tokens": 1801
    },
    "elapsed_seconds": 12.34
  },
  "assessments": [
    {
      "candidate_id": "B018",
      "original_rank": 18,
      "suspiciousness": "0.140028",
      "module": "ibex_id_stage",
      "scope": "TOP.example.core.id_stage_i",
      "bid": 134,
      "lines": [851, 852, 853, 854],
      "block_type": "Always(COMB)",
      "llm_score": 0.97,
      "normalized_sbfl_score": 0.140028,
      "final_score": 0.762507,
      "causal_role": "probable_root_cause",
      "key_lines": [853],
      "reason": "...",
      "reranked_rank": 1
    }
  ],
  "rankings": [
    "Top-K 完整评估记录"
  ],
  "raw_model_response": "..."
}
```

两个候选数组用途不同：

- `assessments` 包含所有有效候选、LLM 分数、归一化 SBFL 分数、融合分数和完整新排名。
- `rankings` 只包含配置的 Top-K 完整记录，用于向后兼容和 Top-K 评测。

顶层仍保留 `candidate_count`、`top_k`、`source_mode`、`llm_elapsed_seconds` 等兼容
字段。写文件时先创建同目录临时文件，再原子替换目标文件。

## Dry-run 与提示词检查

第一次使用新的数据集结构或上下文限制时，建议先运行：

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl rerank \
  rtl RESULT \
  --candidate-count 50 \
  --top-k 10 \
  --dry-run
```

该命令仍会校验输入、发现 patch、在临时工作区应用 patch、解析模块并检查源码预算，
然后打印系统提示词、用户提示词和以下元数据：

- `patch_state`
- `patch_path`
- 最终 `source_mode`
- 实际 snippet 半径
- 渲染源码字符数
- 完整 prompt SHA-256

它不要求 `--model`，不会读取 API key、调用服务或生成 `llm_rerank.json`。如果同时
传入 `--save-prompt`，dry-run 退出前仍会写入提示词 sidecar。

## 汇总命令

### SBFL 汇总

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl summarize sbfl \
  verify_dataset \
  logs/reduce \
  -o sbfl_block_summary.tsv
```

工具递归查找 `logs_root` 下的 `status.txt`，然后解析 `bugset_root` 中对应的 diff 和
`bug_info.json`，使用修改行与 SBFL block 的交集计算排名。

SBFL TSV 保持原字段：

```text
bugset  diff  status  top-k  sus  elapsed_time  fuzzing_time
```

统计指标包括 Top-1、Top-5、Top-10、Top-20、MAR@10、平均 SBFL 时间和平均
fuzzing 时间。

### LLM 汇总

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl summarize llm \
  verify_dataset \
  logs/reduce \
  -o llm_rerank_summary.tsv
```

LLM 汇总保留原字段，并在末尾追加：

- `llm_score`
- `normalized_sbfl_score`
- `final_score`
- `causal_role`
- `ranking_strategy`
- `patch_state`
- `prompt_sha256`
- Responses API 原生 `input_tokens`、`output_tokens` 和 `total_tokens`
- 旧 TSV 兼容别名 `prompt_tokens` 和 `completion_tokens`

统计包括 Top-1、Top-5、Top-10、MRR、MAR@10、提升/不变/变差案例数，以及平均
LLM/SBFL/fuzzing 时间。旧版和 schema v2 的 `llm_rerank.json` 都可以读取。
实验使用其他输出文件名时，可通过 `--rerank-filename` 指定。

### 行号匹配窗口

两种汇总模式都支持：

```text
--line-window N
```

`0` 要求源码行精确相交；`1` 还会接受已知修改行的前后一行。

### SBFL tie 规则

汇总器保留原有实验口径：

- 连续且数值相等的 suspiciousness 使用打印排名的平均值。
- 如果所有打印 block 的 suspiciousness 完全相同，则认为排序没有信息，结果记为
  `over top-N`。
- 如果 tie 延伸到打印的 Top-N 边界，边界 tie 中更早打印的候选记为 `over top-N`；
  恰好打印在 Top-N 的候选仍记为 `top-N`。

### 从已有 TSV 输出统计

```bash
uv run --project tools/sbfl_llm --frozen ibex-sbfl stats sbfl \
  sbfl_block_summary.tsv

uv run --project tools/sbfl_llm --frozen ibex-sbfl stats llm \
  llm_rerank_summary.tsv
```

## 兼容命令

现有自动化仍可使用：

```bash
python3 scripts/rerank_sbfl_with_llm.py rtl RESULT --model MODEL
python3 scripts/summarize_sbfl_blocks.py verify_dataset logs/reduce
python3 scripts/summarize_llm_rerank.py verify_dataset logs/reduce
```

兼容脚本不再包含缺陷定位实现。它们只负责定位本项目，然后将自身替换为相应的
`uv run` 命令。旧的 `--stats-only TSV` 形式会转换成新的 `stats` 子命令。

`scripts/run_args_sweep_sbfl.sh` 继续通过 SBFL 汇总兼容入口工作。

## 可复现性与安全性

- API key 只从指定环境变量读取，不会写入输出文件。
- 原始 RTL 永远不会被原地应用 patch。
- patch 和输入文件 SHA-256 用于标识准确的实验输入。
- patched RTL 文件哈希用于标识模型实际看到的源码。
- prompt 哈希可以标识完整提示词，而无需把长 prompt 写入 JSON。
- 原始模型响应会保存，便于审计解析和修复过程。
- 模型不会收到 mutation diff、patch 路径、`bug_info.json` 或真实修改行。
- `--save-prompt` 应谨慎使用，因为 sidecar 包含 RTL 和失败信息。
- rerank 输出会记录绝对本地路径；如果文件系统布局敏感，公开前应先清理。

## 常见错误

### `cannot discover a [DIFF] patch`

`run.log` 不存在或没有 `[DIFF]`。可以传入 `--patch PATH`。只有在确实不存在 mutation
diff 的场景下才应使用 `--allow-unpatched-source`。

### `patch does not match RTL source`

输入 `rtl/` 不是 diff 所基于的源码版本，或 patch 已损坏。请检查数据集版本和
`rtl_source`。输入源码已包含 patch 的情况会被单独识别。

### `patch may only modify files below rtl/`

diff 修改了其他目录或包含不安全路径，因此在执行 `git apply` 前被拒绝。

### `no block ranking found`

`result.log` 中没有可识别的 `Suspiciousness of block:` 段或 block 排名行。

### `none of the ranked blocks exists in blocks.json`

`result.log` 中的 `(scope, bid)` 与 `blocks.json` 不匹配。请确认两个文件来自同一次
SBFL 运行。

### `multiple source files found for module`

`rtl_source` 下存在多个同名 `<module>.sv`。请提供更精确的 RTL 根目录。

### `candidate block code needs ... characters`

候选 block 自身的代码已经超过 `--max-source-chars`。需要减少
`--candidate-count` 或增大限制。

### `candidate set mismatch`

模型遗漏候选或返回未知 ID。工具会在 `--retries` 限制内请求修复；持续失败时不会写入
部分结果。

### Responses-compatible 服务拒绝结构化输出

使用默认 `--structured-output auto` 可以允许受控降级。如果服务拒绝所有 Responses
API `text.format`，使用 `off`。如果实验必须由服务端强制 JSON Schema，则使用
`strict`。如果服务完全不支持 `/responses`，工具会直接报告 API 错误，绝不会回退到
Chat Completions。

## 项目结构

```text
tools/sbfl_llm/
├── pyproject.toml
├── uv.lock
├── README.md
├── README_CN.md
├── src/ibex_sbfl_llm/
│   ├── cli.py                 # CLI 路由与参数校验
│   ├── models.py              # 候选和结构化响应模型
│   ├── sbfl.py                # 结果/status 解析与排名规则
│   ├── patching.py            # 安全的临时 patch 还原
│   ├── snippets.py            # 源码查找与上下文渲染
│   ├── prompting.py           # 提示词与失败信息选择
│   ├── openai_client.py       # SDK 调用、降级、重试与校验
│   ├── ranking.py             # 分数归一化和融合
│   ├── rerank.py              # 端到端重排序流程
│   ├── result_io.py           # 旧版/v2 输出校验
│   ├── summarize.py           # SBFL/LLM TSV 与统计
│   └── prompts/
│       ├── system.md
│       ├── rerank.md
│       └── repair.md
└── tests/
```

## 开发与验证

安装开发依赖：

```bash
uv sync --project tools/sbfl_llm --all-groups --frozen
```

从 Ibex 仓库根目录运行项目测试：

```bash
uv run --project tools/sbfl_llm --frozen pytest -q tools/sbfl_llm/tests
```

运行 lint 和格式检查：

```bash
uv run --project tools/sbfl_llm --frozen ruff check tools/sbfl_llm
uv run --project tools/sbfl_llm --frozen ruff format --check tools/sbfl_llm
```

构建可发布文件：

```bash
uv build --project tools/sbfl_llm
```

默认测试使用 mock OpenAI client，不会产生 API 费用。测试覆盖 patch 自动发现与隔离、
already-patched 检测、不安全路径拒绝、snippet 渲染、提示词真值泄露检查、响应校验、
结构化输出降级、分数融合、旧版/v2 结果读取、SBFL tie 规则和 schema v2 端到端重排序。
