# Training Trace Capture and Export

本文件说明当前 `three_u_0240` 分支中，面向训练/蒸馏的数据积累方案。

## 目标

将 `chat` / `chatbot` / `agent` / `agentbot` 的一次完整问答，按 **turn（轮次）** 独立落库，便于：

- 构建 SFT 训练集
- 构建 RL prompt bank
- 作为 DPO / GRPO 的 teacher anchor
- 回溯单轮输入、检索上下文、think、最终回答

## 当前落库模型

数据库新增表：`chat_trace_turn`

代码位置：

- `api/db/db_models.py` -> `ChatTraceTurn`
- `api/db/services/chat_trace_service.py`

关键字段：

- `session_id`: 会话 ID
- `turn_no`: 当前会话中的第几轮
- `dialog_id`: chat / chatbot / agent / agentbot 对应的配置对象 ID
- `source`: `chat` / `chatbot` / `agent` / `agentbot`
- `request_question`: 用户问题
- `request_payload`: 原始请求体
- `history_snapshot`: 本轮执行前的消息快照
- `model_input_messages`: 实际送入模型的消息快照
- `system_prompt_rendered`: 参数替换后的系统提示词
- `knowledge_text`: 拼接后的知识库文本
- `retrieval_snapshot`: 检索结果快照
- `think_content`: `<think>...</think>` 部分
- `answer_content`: 最终回答正文
- `full_response`: `think + answer`
- `response_reference`: 最终引用结果
- `timing_metrics`: 时延与 token 估算
- `status`: `running` / `done` / `error`

## 覆盖范围

目前已接入：

- `/api/v1/chats/<chat_id>/completions`
- `/api/v1/chatbots/<dialog_id>/completions`
- `/api/v1/agents/<agent_id>/completions`
- `/api/v1/agentbots/<agent_id>/completions`

说明：

- `chat` / `chatbot` 走 `dialog_service.async_chat()`，可抓到提示词拼接、知识拼接、检索结果、think、answer。
- `agent` / `agentbot` 当前先抓外层输入输出、reference、会话轮次；Canvas 内部节点级推理暂未拆成训练样本。

## 多轮策略

当前实现不是“一问一 session”，而是：

- `session_id` 表示一段完整会话
- `turn_no` 表示该会话里的单轮问答
- `chat_trace_turn` 一行对应一次问答

因此，多轮训练样本的最小单位是单条 `chat_trace_turn`，而不是整段 `conversation.message`。

## 自动建表

`chat_trace_turn` 不需要手工单独迁移脚本。

本项目会在初始化时调用：

```python
from api.db.db_models import init_database_tables
init_database_tables()
```

该函数会扫描 `db_models.py` 中的 `DataBaseModel` 子类并自动创建缺失表。

如果只是更新代码、未重启服务，可手工执行一次：

```bash
python -c "from api.db.db_models import init_database_tables; init_database_tables()"
```

## 导出脚本

脚本位置：

- `tools/export_chat_trace_turns.py`

默认导出格式：

```bash
python tools/export_chat_trace_turns.py --format sft_answer --output /tmp/sft_answer.jsonl
```

支持的导出模式：

- `training_record`
- `sft_answer`
- `sft_cot`
- `rl_prompt_bank`
- `dpo_seed`
- `grpo_seed`

常用示例：

```bash
python tools/export_chat_trace_turns.py --format sft_answer --output /tmp/sft_answer.jsonl
python tools/export_chat_trace_turns.py --format sft_cot --require-think --output /tmp/sft_cot.jsonl
python tools/export_chat_trace_turns.py --format rl_prompt_bank --output /tmp/rl_prompt_bank.jsonl
python tools/export_chat_trace_turns.py --format dpo_seed --output /tmp/dpo_seed.jsonl
python tools/export_chat_trace_turns.py --format grpo_seed --output /tmp/grpo_seed.jsonl
```

按对象过滤：

```bash
python tools/export_chat_trace_turns.py --format sft_answer --dialog-id <dialog_id> --output /tmp/dialog_sft.jsonl
python tools/export_chat_trace_turns.py --format grpo_seed --session-id <session_id> --output /tmp/session_grpo.jsonl
```

## 训练建议

### 1. SFT + RL

主数据集：

- `sft_answer`

辅助数据集：

- `sft_cot`

RL 输入池：

- `rl_prompt_bank`

建议：

- 先用 `sft_answer` 作为主训练集
- 再少量混入 `sft_cot`
- RL 阶段基于 `rl_prompt_bank` 生成候选并打分

### 2. DPO / GRPO

当前数据库只保存 teacher 正样本，因此：

- `dpo_seed` 只导出 `prompt + chosen + chosen_cot`
- `grpo_seed` 只导出 `prompt + teacher + empty candidates`

后续需要再补：

- 学生模型采样结果
- 或弱模型 / 高温采样结果

再组装成正式的 DPO `chosen/rejected` 和 GRPO `candidate groups`。

## 注意事项

- `think_content` 质量强依赖教师模型，不建议直接全量作为唯一监督信号。
- 主蒸馏目标仍应优先放在 `answer_content`。
- 如果未来要做更强的 agent 蒸馏，需要把 Canvas 内部节点 trace 再细化成可训练样本。
