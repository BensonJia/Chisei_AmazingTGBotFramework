# Chisei: A Telegram LLM Bot

模块化 Telegram 聊天机器人，支持：

- Telegram 兼容层（消息处理、typing、reaction、emoji）
- OpenAI 风格接口 `/v1/chat/completions`（General/Summarizer/Verifier 三模型）
- SQLite 聊天存储（按私聊用户 / 群聊 ID 分类）
- 会话超长后调用 LLM 自动总结压缩
- 群聊中仅在被 `@BotUsername` 时触发回复
- `/teach`：基于最近 24 条消息构建时间逻辑与人物关系图

## 项目结构

- `bot.py`: 启动入口
- `app/config_loader.py`: 配置加载
- `app/telegram_adapter/`: Telegram 能力封装
- `app/llm_client.py`: LLM 调用
- `app/llm_router.py`: 三模型路由
- `app/storage/`: SQLite 数据层
- `app/services/memory_manager.py`: 记忆压缩
- `app/services/teach_service.py`: teach 流程
- `app/services/context_builder.py`: BFS关系+时间事件上下文
- `app/services/task_dispatcher.py`: 分session线程任务调度
- `app/orchestrator/bot_service.py`: 业务编排
- `config/service.yaml`: LLM 后端配置
- `config/bot.yaml`: Bot 设定
- `config/memory.yaml`: 记忆管理配置

## 依赖安装（conda: dllm）

```powershell
conda run -n dllm pip install -r requirements.txt
```

## 运行

```powershell
conda run -n dllm python bot.py
```

启用终端日志输出（同时仍写入本地 `bot.log`）：

```powershell
conda run -n dllm python bot.py --log
```

## 群聊命令

- `/RecordAll`: 每调用一次就切换当前群的 RecordAll 状态（ON/OFF）
- `/teach`: 对当前会话最近24条消息做语气校验 + 时间逻辑 + 人物关系抽取

说明：无论 RecordAll 开关状态如何，Bot 都只在群聊被 `@` 时回复。

## 配置说明

1. `config/service.yaml`

- `llm.general|summarizer|verifier`: 三套模型配置

2. `config/bot.yaml`

- `token_env`: 环境变量名，优先级高于 `token`
- `name`: Bot 名称
- `default_system_prompt`: 系统提示词
- `max_relation_depth`: BFS最大关系层数
- `max_events_context`: 上下文最大时间事件数
- `progress_feedback_enabled`: 是否发送 teach/对话中间进度提示
- `reply_style`: emoji/reaction 策略

3. `config/memory.yaml`

- `max_messages_per_conversation`: 每类会话最大消息条数
- `keep_recent_messages`: 压缩后保留的最近消息条数
- `time_logic_parse_retry_max/time_logic_parse_timeout_sec`: 时间逻辑解析重试参数
- `roles_logic_parse_retry_max/roles_logic_parse_timeout_sec`: 人物关系解析重试参数
- `summary_prompt`: 总结压缩 prompt

## 数据库

默认存储：`data/chat_history.db`

核心表：

- `conversations`
- `messages`
- `summaries`
