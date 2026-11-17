# 批次执行、重复调用与历史记录：对齐 DeepSeek Harness

实现日期：2026-11-05。本文记录针对三个可疑点的修复，以及“模型把工具协议写进正文”
的恢复路径。范围是应用源码与离线回归；真实模型服务和 SOLIDWORKS 仍需目标机验收。

## 1. 批次不再短路

此前 `backend/agent.py` 在一条调用失败后设置 `batch_failure`，随后把同批其他调用标成
`skipped_after_failure`，即使只是 `sw_help` 或只读查询。真实用户会看到“整批失败”，
也无法区分哪些调用根本没执行。

现在采用 Harness `executeToolBatch` 的语义：

- 每条调用独立执行，失败只是一条 `tool_result`，不会取消同批其他调用。
- 只有任务级停止才结束后续调用：取消、批准被拒/过期、执行结果未知
  （`DecisionStopped` / `ExecutionHalted`）。
- 模型猜错的工具名不再导致整轮回复被丢弃。未知名称得到自己的
  `not_executed/unregistered_tool` 结果，其他只读调用照常执行；同批若含未注册工具，
  可能修改 CAD 的已知调用标为 `unavailable_batch` 并跳过，避免在计划不完整时推进模型。
- 每条 `tool_result` 带 `outcome`、`executed`、`category`，以及 `batch_id`、
  `batch_index`、`batch_total`。
- 同一批超过一条调用时，额外发出一条 `status` 汇总：成功 / 失败 / 未执行数量及逐项名称。
- `not_executed` 只用于真正的预执行拒绝（预算用尽、缺少用途说明、注册表变化等），
  不再用于“同批前面失败了”。

对应代码：`backend/agent.py` 的 `execute()`。

## 2. 重复失败改为建议，不拦截

此前相同指纹的失败调用在“状态未变化”时直接返回 `identical_failed_call`。状态缓存只覆盖
文档/连接字段；SW 被用户启动但执行器尚未重新附加时，缓存仍可能显示“未变化”，
于是真实重试被护栏吃掉。

现在采用 Harness `repeat-tool-reminder` 的建议式语义：

- 调用照常执行；只有任务级停止才不执行。
- 状态未变化且命中重复阈值（2/3/5/8 次）时，在结果 JSON 中附加
  `repeat_of_failed_call`、`state_unchanged`、`previous_error`、`repeat_count` 和
  `repeat_notice`。
- 提示明确说明“这是建议，不是工具失败”，并要求模型换方法或参数。
- `sw_connect` 成功、成功的非只读操作或状态变化都会清除记录。

因此用户启动 SW 后再次调用 `model_info`，会得到真实的 `NOT_CONNECTED` 或成功结果，
而不是应用伪造的失败。

## 3. recentOps 窗口与完整历史

`sw_status.recentOps` 是上游执行器的有界窗口（最近 1000 条）。应用现在：

- 用 `backend/execution_history.py` 投影状态：保留首 5 + 尾 45 条，给出
  `recentOpsTotal`、`recentOpsTruncated`、`recentOpsWindow.omitted`、`operationLog`。
- 不再让 18,000 字符截断随意把 `recentOps` 整段切掉。
- 新增只读本地工具 `executor_history(offset, limit, name)`，按最新在前分页读取
  `executor/operations.jsonl`；`offset` 跳过最新条目，返回 `next_offset`。
- `vbaEnabled=false` 且执行器没有返回 `vbaNote` 时，应用补上解释：
  执行器进程未设置 `SWMCP_VBA=on`，需要在执行器设置中开启并重连，`vba_run` 未被广告。

`operations.jsonl` 是追加日志，跨执行器重启保留；`recentOps` 只是窗口，不是完整历史。

## 4. 正文工具协议的恢复

模型把工具调用写进正文时，应用先尝试恢复，而不是直接判整批失败。新增覆盖：

- DSML：完整 `<|DSML|tool_calls>` / `<|DSML|function_calls>` / 短 `calls`，
  容忍全角竖线、空格、缺失外层闭合（只要每个 `invoke` 完整）。
- XML：`<tool_calls>`、`<function_calls>`、单数 `<tool_call>` / `<tool_use>` /
  `<function_call>`，以及只出现 `<invoke>` 的情况。
- JSON：完整 `{"tool_calls":[...]}`、`function_call`、裸 `{name, arguments}`、
  顶层调用数组、围栏代码块、正文中的平衡 JSON 片段，以及 `tool`/`parameters` 别名。
- 函数写法：整段或围栏内保守匹配 `tool(kw=value, ...)`，仅关键字参数，名称必须在
  本轮广告表中。
- 推理通道：正文没有可恢复包络时，尝试从 `reasoning` 恢复完整包络。

恢复仍是原子的：每个调用都必须有完整包络和合法身份；已知名称还要通过 JSON 参数和
Schema 校验。恢复成功后从可见正文中移除标记。

未知工具名不再走整轮重生成：完整的正文调用被恢复后，未知名称得到
`unregistered_tool` 的逐调用结果，其他只读调用继续。只有调用包络本身不完整、
调用 ID 非法或正文混有无法恢复的协议文本时，才整轮重生成。

同时删除了提示词里的字面 JSON 调用信封。此前 `protocol_examples()` 会把
`{"role":"assistant","tool_calls":[...]}` 原样放进系统提示，模型容易照抄进正文；
现在只描述当前协议的“原生工具通道”，不再提供可复制的调用对象。

## 5. 离线回归

- `tests/test_textual_recovery_and_batch.py`：批次不短路、重复失败建议式、DSML/JSON 数组/
  单数标签/函数写法恢复、推理通道恢复、预算耗尽丢弃。
- `tests/test_output_guard.py`、`tests/test_case_protocol_repair.py`：未知工具逐调用
  `unregistered_tool`、同批修改标 `unavailable_batch`、后续发现并重试、批准卡片仍生效。
- `tests/test_execution_history.py`：状态窗口投影、VBA 解释、日志分页、名称过滤、
  Agent 实际调用 `executor_history`。
- `tests/test_protocol_consistency.py`：协议示例不再含字面信封；原生协议的未知工具结果
  与历史配对继续通过。

全部离线测试：`630 passed`（2026-11-05）。未调用真实模型、未启动 SOLIDWORKS。
