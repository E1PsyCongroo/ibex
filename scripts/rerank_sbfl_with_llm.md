# 使用大模型重排序 SBFL 结果

`rerank_sbfl_with_llm.py` 用大语言模型分析 SBFL（Spectrum-Based Fault
Localization）生成的可疑 block、对应的 SystemVerilog 源码及上下文，然后输出
经过模型重新排序的 Top-K 结果。

脚本只使用 Python 标准库，不需要安装额外 Python 包。模型服务需要提供与
OpenAI Chat Completions 兼容的 HTTP 接口。

## 1. 工作流程

脚本依次执行以下操作：

1. 从 `result.log` 的 `Suspiciousness of block:` 段读取 SBFL 排名。
2. 使用 `blocks.json` 中的 `(scope, bid)` 补充模块名、源码行号和 block 类型。
3. 在 RTL 目录中查找 `<module>.sv`。
4. 将候选 block、原始排名、可疑度和相关 RTL 源码组成提示词。
5. 调用 OpenAI-compatible `/chat/completions` 接口。
6. 校验模型返回的候选 ID，禁止模型添加不存在的 block、返回重复 block 或遗漏
   必需字段。
7. 将重新排序结果写入 JSON 文件。

模型只对原 SBFL 候选集合进行重排序，不会创建新的候选位置。

## 2. 环境要求

- Python 3.10 或更高版本。
- 可访问的 OpenAI-compatible Chat Completions 服务。
- SBFL 结果目录中同时存在 `result.log` 和 `blocks.json`。
- RTL 目录中存在候选模块对应的 `.sv` 文件。

使用需要鉴权的服务时，应通过环境变量提供 API key：

```bash
export OPENAI_API_KEY="your-api-key"
```

不要将 API key 直接写入脚本、命令行历史或提交到 Git。

## 3. 基本命令

在仓库根目录执行：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  <RTL源码目录> \
  <SBFL结果目录> \
  --model <模型名称>
```

使用仓库现有样本的命令如下：

```bash
export OPENAI_API_KEY="your-api-key"

python3 scripts/rerank_sbfl_with_llm.py \
  rtl \
  logs/reduce/2026-07-14-22-40-38_26692/0_103_ibex_id_stage \
  --model your-model-name
```

执行成功后，默认生成：

```text
logs/reduce/2026-07-14-22-40-38_26692/0_103_ibex_id_stage/llm_rerank.json
```

## 4. 位置参数

### `rtl_source`

RTL 源码根目录。脚本根据 `blocks.json` 的 `module` 字段查找
`<module>.sv`。

查找顺序为：

1. 首先检查 `<rtl_source>/<module>.sv`。
2. 如果不存在，则递归搜索 `<rtl_source>/**/<module>.sv`。
3. 找不到对应文件时终止执行。
4. 递归搜索得到多个同名文件时终止执行，避免把错误源码交给模型。

示例：

```text
rtl/
├── ibex_alu.sv
├── ibex_controller.sv
└── ibex_decoder.sv
```

### `sbfl_result`

可使用以下两种输入形式：

- 包含 `result.log` 和 `blocks.json` 的目录。
- `result.log` 文件本身，此时脚本自动读取同目录下的 `blocks.json`。

以下两个命令等价：

```bash
python3 scripts/rerank_sbfl_with_llm.py rtl logs/reduce/run/case --model your-model
```

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl \
  logs/reduce/run/case/result.log \
  --model your-model
```

## 5. 完整参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--model MODEL` | 无 | API 使用的模型名称。正常调用时必填；`--dry-run` 时可省略。 |
| `--api-base URL` | `$OPENAI_BASE_URL`，未设置时为 `https://api.openai.com/v1` | OpenAI-compatible API 根地址。 |
| `--api-key-env NAME` | `OPENAI_API_KEY` | 保存 API key 的环境变量名称。传入空字符串表示不发送鉴权头。 |
| `--candidate-count N` | `50` | 从 SBFL 原始排名中读取的候选 block 数量。 |
| `--top-k K` | `10` | 要求模型输出的最终候选数量。不能超过 `--candidate-count`。 |
| `--max-source-chars N` | `240000` | 发送给模型的 RTL 源码最大字符预算。 |
| `--snippet-radius N` | `30` | 使用源码片段时，在候选行前后各保留的行数。 |
| `--timeout SECONDS` | `180` | 单次 API 请求的超时时间，单位为秒。 |
| `--temperature VALUE` | 不发送 | 可选的采样温度。默认完全不发送该字段，以兼容不支持 temperature 的推理模型。 |
| `--retries N` | `2` | 首次请求失败后允许的重试次数，因此默认最多请求 3 次。 |
| `--retry-delay SECONDS` | `2` | 重试基础等待时间。第 N 次重试等待 `N × retry-delay` 秒。 |
| `--output PATH` | `<SBFL目录>/llm_rerank.json` | 自定义输出 JSON 路径。 |
| `--dry-run` | 关闭 | 只校验和解析输入并打印完整提示词，不调用 API，也不生成结果 JSON。 |

查看脚本自身的参数帮助：

```bash
python3 scripts/rerank_sbfl_with_llm.py --help
```

## 6. API 地址和鉴权

### 使用默认环境变量

```bash
export OPENAI_API_KEY="your-api-key"

python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model-name
```

### 使用自定义 API 地址

可以用环境变量设置服务地址：

```bash
export OPENAI_BASE_URL="https://example.com/v1"
export OPENAI_API_KEY="your-api-key"

python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model-name
```

也可以在命令行中设置：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model-name \
  --api-base "https://example.com/v1"
```

如果 `--api-base` 没有以 `/chat/completions` 结尾，脚本会自动追加该路径。

### 使用其他 API key 环境变量

```bash
export MY_LLM_API_KEY="your-api-key"

python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model-name \
  --api-key-env MY_LLM_API_KEY
```

### 使用无需鉴权的本地服务

对不需要 Bearer token 的本地 OpenAI-compatible 服务，可以传入空的环境变量名：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model local-model \
  --api-base "http://127.0.0.1:8000/v1" \
  --api-key-env ''
```

## 7. 候选数量和 Top-K

默认配置从 `result.log` 读取原始 Top 50，并要求模型输出重新排序后的 Top 10：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model \
  --candidate-count 50 \
  --top-k 10
```

如果只想在原始 Top 20 中选择最终 Top 5：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model \
  --candidate-count 20 \
  --top-k 5
```

注意：

- `candidate-count` 和 `top-k` 必须为正整数。
- `top-k` 不能大于 `candidate-count`。
- 如果部分 SBFL block 在 `blocks.json` 中不存在，脚本会跳过并打印警告。
- 跳过无效 block 后，剩余候选少于 `top-k` 时会终止执行。

## 8. RTL 源码收集策略

脚本优先把所有候选涉及的完整 `.sv` 文件交给模型，并为每行添加源码行号。

如果完整源码总字符数超过 `--max-source-chars`，脚本自动切换为片段模式：

- 以 `blocks.json` 中候选 block 的 `lines` 为中心。
- 默认保留每个候选行前后各 30 行。
- 相邻或重叠片段会自动合并。
- 不连续片段之间使用 `...` 标记。

例如，限制源码字符数并缩小片段范围：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model \
  --max-source-chars 120000 \
  --snippet-radius 15
```

如果片段模式仍超过字符预算，脚本会报错。可以采用以下方式处理：

- 增大 `--max-source-chars`。
- 减小 `--candidate-count`。
- 减小 `--snippet-radius`。

`--max-source-chars` 只统计 RTL 源码字符，不包含候选 JSON、系统提示词和格式说明。
实际发送给模型的总上下文会略大于这个数值。

## 9. Dry-run 检查

第一次使用新日志或新模型前，建议先执行：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --dry-run
```

该模式会：

- 检查输入路径。
- 解析 `result.log` 和 `blocks.json`。
- 检查候选模块源码是否存在。
- 执行源码预算和片段选择逻辑。
- 将系统提示词和完整用户提示词打印到标准输出。

该模式不会：

- 检查 API key。
- 调用模型接口。
- 创建 `llm_rerank.json`。

因为 RTL 源码可能很长，dry-run 输出也可能非常大。可以将候选数临时调小以便人工检查：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --candidate-count 10 \
  --top-k 5 \
  --dry-run
```

## 10. 提示词内容

脚本的提示词要求模型以 RTL 验证和处理器调试工程师的视角分析候选位置，并综合：

- SBFL 原始排名和 suspiciousness。
- block 类型、层次 scope、模块和行号。
- 对应 SystemVerilog 源码。
- 控制流和数据流中的因果关系。
- 边界条件、错误谓词、状态机跳转、位宽和符号问题。
- ready/valid 等握手逻辑。
- 相关分支之间的不一致行为。

模型被要求优先选择可能的根因，而不是只选择下游症状位置。提示词同时规定模型只能返回
JSON，并只能引用候选列表中提供的 `candidate_id`。

## 11. 模型响应校验

期望的模型原始响应格式为：

```json
{
  "rankings": [
    {
      "candidate_id": "B017",
      "reason": "该状态转移会在下游尚未 ready 时提前结束操作"
    },
    {
      "candidate_id": "B003",
      "reason": "该译码分支的控制信号组合与相邻操作不一致"
    }
  ]
}
```

脚本会校验：

- 顶层必须为 JSON 对象。
- 必须包含 `rankings` 数组。
- 数组长度必须严格等于 `top-k`。
- 每一项必须包含有效的 `candidate_id` 和非空 `reason`。
- `candidate_id` 必须来自当前 SBFL 候选列表。
- 同一个 `candidate_id` 不能重复出现。

脚本也能解析包裹在 JSON Markdown 代码块中的内容，或从少量附加文本中提取最外层
JSON 对象。返回内容仍不合法时会按 `--retries` 配置重试，并把校验错误反馈给模型要求
修正格式。

## 12. 输出文件格式

默认输出文件为 SBFL 结果目录下的 `llm_rerank.json`。示例结构：

```json
{
  "model": "your-model-name",
  "result_log": "/absolute/path/to/result.log",
  "blocks_json": "/absolute/path/to/blocks.json",
  "rtl_source": "/absolute/path/to/rtl",
  "candidate_count": 50,
  "top_k": 10,
  "source_mode": "full_files",
  "rankings": [
    {
      "original_rank": 17,
      "scope": "TOP.example.core.controller_i",
      "bid": 135,
      "suspiciousness": "0.192450",
      "candidate_id": "B017",
      "module": "ibex_controller",
      "lines": [398, 399],
      "block_type": "Branch",
      "reranked_rank": 1,
      "reason": "模型给出的技术判断"
    }
  ],
  "raw_model_response": "模型返回的原始文本"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `model` | 本次调用使用的模型名称。 |
| `result_log` | 输入 `result.log` 的绝对路径。 |
| `blocks_json` | 输入 `blocks.json` 的绝对路径。 |
| `rtl_source` | RTL 根目录的绝对路径。 |
| `candidate_count` | 成功映射到 `blocks.json` 的有效候选数量。 |
| `top_k` | 最终输出的重排序数量。 |
| `source_mode` | `full_files` 表示使用完整文件；`snippets` 表示使用候选附近片段。 |
| `original_rank` | SBFL 原始排名。 |
| `reranked_rank` | 大模型给出的新排名。 |
| `suspiciousness` | SBFL 原始可疑度，按日志中的字符串保存。 |
| `candidate_id` | 稳定候选 ID，例如原始 Top 17 对应 `B017`。 |
| `module`、`scope`、`bid` | block 的模块、实例层次和 block ID。 |
| `lines`、`block_type` | `blocks.json` 中记录的源码行和 block 类型。 |
| `reason` | 模型对该候选位置的技术分析。 |
| `raw_model_response` | 模型原始回复，便于审计和复现。 |

写文件时脚本先生成同目录临时文件，再原子替换目标文件，避免中途失败留下不完整 JSON。

使用自定义输出路径：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model \
  --output results/case_001_rerank.json
```

输出文件的父目录必须已经存在。

## 13. 超时和重试

网络不稳定或模型偶尔返回错误格式时，可以增加超时和重试次数：

```bash
python3 scripts/rerank_sbfl_with_llm.py \
  rtl path/to/sbfl-result \
  --model your-model \
  --timeout 300 \
  --retries 4 \
  --retry-delay 3
```

这个例子最多执行 5 次请求：首次请求加 4 次重试。重试前依次等待 3、6、9、12 秒。

以下情况会进入重试：

- HTTP/API 请求失败。
- API 响应结构中没有 `choices[0].message.content`。
- 模型返回空内容。
- 模型返回的 JSON 或排名不符合约束。

## 14. 常见错误

### `no block ranking found`

`result.log` 中不存在可识别的 `Suspiciousness of block:` 段。检查日志是否完整，以及
排名行是否符合以下形式：

```text
top-1: Block(scope: TOP.example, bid: 7) with suspicious '0.900000'
```

### `required input file does not exist`

SBFL 结果目录中缺少 `result.log` 或 `blocks.json`，或者输入路径错误。

### `cannot find source for module`

`blocks.json` 中引用的模块在 RTL 目录中没有对应的 `<module>.sv`。检查 RTL 路径是否
指向正确版本的源码。

### `multiple source files found`

RTL 目录中存在多个同名 `<module>.sv`。应将 `rtl_source` 缩小到唯一源码树，避免模型
读取错误版本。

### `API key environment variable ... is not set`

设置对应环境变量，或者对无需鉴权的本地服务使用：

```bash
--api-key-env ''
```

### `LLM API returned HTTP ...`

检查以下内容：

- API 地址是否正确。
- 模型名称是否存在。
- API key 是否有效。
- 服务是否支持 `/chat/completions`。
- 服务端上下文长度是否足以容纳当前源码。

### `model returned ... rankings; expected ...`

模型没有严格返回指定数量的候选。脚本会自动重试；如果持续失败，可以换用指令遵循
能力更强的模型，或减少 `--top-k`。

### 源码超过 `--max-source-chars`

减小 `--candidate-count` 或 `--snippet-radius`，也可以增大源码字符预算。增大预算前应
确认模型的上下文窗口和 API 成本。

## 15. 测试

运行脚本对应的单元测试：

```bash
python3 -m unittest scripts/test_rerank_sbfl_with_llm.py
```

测试覆盖：

- `result.log` block 排名解析。
- `blocks.json` 信息合并。
- 模型排名结果校验。
- 未知候选 ID 拒绝逻辑。
- 源码超出预算时的片段降级逻辑。
