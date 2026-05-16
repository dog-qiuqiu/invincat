# Invincat CLI 项目架构说明

本文面向代码阅读和二次开发，按主流程、子包职责、模块职责三个层次说明 `invincat_cli` 的结构。它不替代用户文档，而是帮助快速判断“某个能力应该去哪个包、哪个文件看”。

## 运行主链路

Invincat CLI 的主链路可以理解为：

1. `invincat_cli.__main__` / `invincat_cli.main` 解析命令行并进入运行模式。
2. `invincat_cli.cli` 组装 CLI 参数、MCP、依赖检查、Textual 或非交互运行入口。
3. `invincat_cli.app` 创建 Textual 应用，组合 `app_runtime` 中的 mixin/handler。
4. `invincat_cli.agent` 创建 DeepAgents/LangGraph agent，注入工具、middleware、backend、system prompt。
5. `invincat_cli.textual_adapter` 将 agent stream、tool call、interrupt、todo、审批结果转换成 Textual UI 消息。
6. `invincat_cli.widgets` 渲染聊天、工具调用、审批、状态栏、选择器等 TUI 组件。
7. `invincat_cli.io`、`scheduler`、`memory`、`mcp`、`wecom`、`skills` 等子系统提供具体能力。

`/plan` 模式的核心路径是：

```text
command_handlers -> plan_handlers -> plan_mode.runtime/policy/handoff
                                      -> planner agent
                                      -> approve_plan
                                      -> main agent handoff
```

## 顶层入口

| 文件 | 职责 |
| --- | --- |
| `invincat_cli/__init__.py` | 包级公共入口，暴露 CLI main 等顶层能力。 |
| `invincat_cli/__main__.py` | `python -m invincat_cli` 的入口。 |
| `invincat_cli/py.typed` | 标记包支持 PEP 561 类型信息。 |

## `main`：传统 CLI 入口

| 模块 | 职责 |
| --- | --- |
| `main/__init__.py` | 主 CLI 入口和 argparse 驱动逻辑。 |
| `main/__main__.py` | `python -m invincat_cli.main` 入口。 |

## `cli`：命令行启动编排

| 模块 | 职责 |
| --- | --- |
| `cli/args.py` | 定义和解析命令行参数。 |
| `cli/dependencies.py` | 检查外部依赖、可选工具和运行前置条件。 |
| `cli/runtime.py` | 根据参数选择 Textual、非交互、server 等运行路径。 |
| `cli/textual.py` | 启动 Textual TUI 应用。 |
| `cli/stdin.py` | 处理 stdin 输入和一次性任务输入。 |
| `cli/mcp.py` | CLI 启动阶段的 MCP 参数和配置装配。 |
| `cli/acp.py` | ACP 相关启动入口和参数衔接。 |

## `app`：Textual 应用对象

| 模块 | 职责 |
| --- | --- |
| `app/__init__.py` | 定义 `DeepAgentsApp`，组合 runtime mixin、Textual 生命周期和 UI 事件。 |
| `app/app.tcss` | Textual 样式表。 |

## `app_runtime`：Textual App 的运行时逻辑

`app_runtime` 是 TUI 应用的主要控制层。它把大 App 类拆成纯函数、handler、mixin，避免所有逻辑堆在 `DeepAgentsApp` 里。

| 模块 | 职责 |
| --- | --- |
| `state.py` | Textual 会话状态、队列消息、延迟动作、线程 payload 等 dataclass。 |
| `initialization.py` | 初始化 App 运行时字段、外部集成和内部状态。 |
| `runner.py` | 应用运行封装和主运行循环辅助。 |
| `layout.py` | 构建 Textual 页面布局。 |
| `bindings.py` | 键盘绑定定义。 |
| `textual_patch.py` | Textual 兼容性补丁。 |
| `terminal.py` | 终端状态、光标、显示相关辅助。 |
| `startup.py` | 启动阶段纯逻辑。 |
| `startup_handlers.py` | 启动、mount、预热、恢复线程等 App-bound handler。 |
| `services.py` | App 依赖服务初始化和持有。 |
| `server.py` | 本地 LangGraph server 运行时状态辅助。 |
| `server_events.py` | server 事件处理。 |
| `server_handlers.py` | server 启停、连接、恢复、错误处理。 |
| `agent.py` | agent turn 请求、线程 override、调度运行状态等纯决策。 |
| `agent_handlers.py` | 启动 agent turn、执行、异常处理、清理、队列后续处理。 |
| `turn_flow_mixins.py` | agent turn、消息流、worker 清理相关 App mixin。 |
| `delegate_mixins.py` | 将 App 方法委托到 runtime handler 的 mixin。 |
| `interaction_mixins.py` | 交互事件和 UI 事件 mixin。 |
| `approval.py` | 审批运行时纯函数、plan guard、shell auto-approve 判断。 |
| `approval_handlers.py` | 工具审批、ask_user、approve_plan UI 挂载和结果映射。 |
| `approval_plan_mixins.py` | `/plan`、审批、ask_user、输入路由的 App 方法层。 |
| `plan.py` | 兼容导出层，转发到 `plan_mode` 的 prompt/policy/handoff helper。 |
| `plan_handlers.py` | `/plan` 进入/退出、planner agent 创建、planner turn 后处理、handoff 执行。 |
| `command.py` | slash command 路由分类，包括 `/plan <task>`。 |
| `command_handlers.py` | slash command 的 App-bound 执行分发。 |
| `command_mixins.py` | 命令 handler 的 App mixin。 |
| `queueing.py` | 忙碌状态下命令是否可绕过队列的纯判断。 |
| `queue_handlers.py` | pending message 队列消费。 |
| `input_handlers.py` | chat input 根据 normal/shell/command 模式分发。 |
| `shell.py` | shell 命令运行的纯状态和安全辅助。 |
| `shell_handlers.py` | shell 任务启动、交互式 shell、清理和 kill。 |
| `action_handlers.py` | Textual action，例如打开编辑器。 |
| `deferred_handlers.py` | 忙碌结束后的延迟动作队列。 |
| `exit_handlers.py` | 退出前清理。 |
| `errors.py` | App 错误格式化、可重试错误识别。 |
| `help.py` | 帮助内容构造。 |
| `message_flow.py` | 消息挂载、裁剪、清屏、spinner。 |
| `memory.py` | App 层 memory/offload 纯逻辑。 |
| `memory_handlers.py` | token 统计、自动 offload、memory 通知、offload 命令。 |
| `model_args.py` | 模型命令参数解析辅助。 |
| `model_command.py` | `/model` 命令解析和用法提示。 |
| `model_runtime.py` | 模型切换运行时决策。 |
| `model_handlers.py` | 模型选择器、主模型/记忆模型切换、默认模型持久化。 |
| `reload.py` | `/reload` 报告构造和配置重载差异。 |
| `tokens.py` | token 消息和显示状态构造。 |
| `thread_runtime.py` | 线程切换/恢复的纯逻辑。 |
| `thread_handlers.py` | 线程选择、恢复、失败回滚。 |
| `thread_history.py` | 线程历史读取和展示辅助。 |
| `thread_links.py` | 线程链接/trace 链接辅助。 |
| `ui_actions.py` | UI action 绑定到 handler。 |
| `ui_handlers.py` | 主题、语言、MCP、memory、thread selector 等 UI 面板。 |
| `theme_prefs.py` | 主题偏好加载和保存。 |
| `update_handlers.py` | 更新检查、手动更新、auto-update 切换。 |
| `version.py` | 版本信息展示。 |
| `scheduler.py` | App 层 scheduler 纯逻辑。 |
| `schedule_handlers.py` | schedule 工具 payload 和 schedule manager UI。 |
| `scheduled_delivery.py` | 定时任务触发、超时、结果投递、WeCom delivery。 |
| `wecom.py` | App 层 WeCom 纯决策和消息格式。 |
| `wecom_handlers.py` | WeCom bot 命令、桥接、入站消息和文件发送。 |
| `skill.py` | App 层 skill 命令纯逻辑。 |
| `skill_handlers.py` | skill 命令执行和 discovery。 |

## `plan_mode`：严格规划模式领域层

| 模块 | 职责 |
| --- | --- |
| `plan_mode/models.py` | `PlanModeStatus`、`PlanStep`、`PlanSession`、`PlanDrift`、`PlanTurnResolution`。 |
| `plan_mode/prompts.py` | planning-only planner prompt、approve prompt、planner runtime input 构造。 |
| `plan_mode/policy.py` | planner allow-list、todo/step normalize、fingerprint、drift detection。 |
| `plan_mode/runtime.py` | 根据 planner checkpoint 解析结果：approved/rejected/drift/noop。 |
| `plan_mode/handoff.py` | 构造 approved plan handoff prompt，包含原始请求、refinement、approved plan、执行规则。 |

## `agent`：Agent 构造和 Agent 级中间件

| 模块 | 职责 |
| --- | --- |
| `agent/__init__.py` | `create_cli_agent`，组装 model、backend、tools、middleware、subagents、checkpointer。 |
| `agent/catalog.py` | 发现、列出和加载 async subagents。 |
| `agent/middleware.py` | shell allow-list、memory file guard 等 agent middleware。 |
| `agent/prompt.py` | 构造主 agent system prompt，注入模式、时间、工作目录等。 |
| `agent/subagents.py` | 定义内置子 agent 规格，例如只读调研型 `researcher`。 |
| `agent/system_prompt.md` | 主 agent 的基础系统提示词。 |
| `agent/tool_descriptions.py` | HITL 审批时展示的工具描述和 interrupt 配置。 |

## `middleware`：LangChain/LangGraph Middleware

| 模块 | 职责 |
| --- | --- |
| `middleware/approve_plan.py` | `approve_plan` 工具和 interrupt 协议。 |
| `middleware/ask_user.py` | `ask_user` 工具和问题交互协议。 |
| `middleware/auto_memory.py` | 自动记忆刷新 middleware。 |
| `middleware/micro_compact.py` | 规则化压缩旧消息/工具结果，降低上下文占用。 |
| `middleware/plan_agent.py` | planner agent 可见工具过滤、运行时 allow-list、旧 API 兼容导出。 |
| `middleware/token_state.py` | 将 token 状态放入 graph state。 |

## `textual_adapter`：Agent Stream 到 Textual UI 的适配层

| 模块 | 职责 |
| --- | --- |
| `textual_adapter/__init__.py` | 适配层公共导出和调试初始化。 |
| `execution.py` | 主 streaming 执行循环，处理 messages/updates/custom stream。 |
| `ui_adapter.py` | Textual UI 回调适配对象，连接 agent runtime 和 App UI。 |
| `input.py` | 将用户输入、文件 mention、多媒体输入转成 agent message content。 |
| `message_stream.py` | 处理 assistant 文本、token、tool call stream。 |
| `update_stream.py` | 解析 updates stream 中的 HITL、ask_user、approve_plan interrupt。 |
| `interrupt_flow.py` | interrupt 结束后构造 LangGraph resume payload。 |
| `tool_calls.py` | streaming tool call block 的 UI 展示。 |
| `tool_results.py` | tool result 回填、成功/失败状态和文件操作展示。 |
| `turn_cleanup.py` | turn 结束后的 token 持久化、文本 flush、interrupt cleanup。 |
| `reporting.py` | token 和清理报告的兼容导出/辅助。 |
| `validation.py` | HITL、ask_user、approve_plan payload 的 Pydantic TypeAdapter 缓存。 |
| `utils.py` | adapter 通用工具函数。 |

## `widgets`：Textual UI 组件

| 模块 | 职责 |
| --- | --- |
| `messages.py` | 用户、助手、系统、错误、队列等消息 widget。 |
| `assistant_message.py` | assistant 消息渲染。 |
| `message_data.py` | 消息数据模型。 |
| `message_store.py` | UI 消息窗口存储和裁剪。 |
| `message_styles.py` | 消息样式常量。 |
| `chat_input.py` | 聊天输入组件。 |
| `chat_text_area.py` | 输入文本区域。 |
| `chat_input_completion.py` | 输入补全状态。 |
| `chat_input_paths.py` | 输入中的路径识别。 |
| `chat_input_styles.py` | 输入组件样式。 |
| `chat_completion.py` | chat completion UI 辅助。 |
| `autocomplete.py` | 自动补全基础组件。 |
| `autocomplete_slash.py` | slash command 补全。 |
| `autocomplete_files.py` | 文件路径补全。 |
| `autocomplete_file_utils.py` | 文件补全工具。 |
| `autocomplete_shell.py` | shell 输入补全。 |
| `autocomplete_shell_utils.py` | shell 补全工具函数。 |
| `tool_call_message.py` | 工具调用消息。 |
| `tool_call_output.py` | 工具输出渲染。 |
| `tool_renderers.py` | 不同工具输出到 widget 的 renderer 注册。 |
| `tool_widgets.py` | 工具审批、计划审批、工具结果 widget。 |
| `approval.py` | 通用工具审批菜单。 |
| `approve.py` | plan approval 展示。 |
| `ask_user.py` | ask_user 问题表单。 |
| `diff.py` | 文件 diff 展示。 |
| `history.py` | 历史消息显示。 |
| `loading.py` | loading/spinner widget。 |
| `status.py` | 状态栏主体。 |
| `status_model_label.py` | 状态栏模型标签。 |
| `status_styles.py` | 状态栏样式。 |
| `welcome.py` | 欢迎页/banner。 |
| `output_formatters.py` | 输出格式化。 |
| `model_selector*.py` | 模型选择器、选项、动作、展示和样式。 |
| `model_register*.py` | 模型注册 UI 和样式。 |
| `theme_selector.py` | 主题选择器。 |
| `language_selector.py` | 语言选择器。 |
| `mcp_viewer.py` | MCP server/tool 查看器。 |
| `memory_viewer*.py` | memory viewer、store、排序、模型、样式。 |
| `schedule_manager.py` | 定时任务管理 UI。 |
| `skill_message.py` | skill 相关消息。 |
| `thread_selector*.py` | 线程选择器、数据、布局、选项、渲染、动作、样式。 |
| `thread_delete_confirm.py` | 删除线程确认弹窗。 |
| `_links.py` | Textual clickable link 辅助。 |

## `io`：本地 I/O、文件操作和媒体处理

| 模块 | 职责 |
| --- | --- |
| `input.py` | 输入构建、文件 mention 解析、多媒体 tracker 协作。 |
| `output.py` | CLI 输出辅助。 |
| `clipboard.py` | 剪贴板读写。 |
| `editor.py` | 外部编辑器打开和内容回收。 |
| `file_mentions.py` | 用户输入中的文件路径/mention 提取。 |
| `pasted_paths.py` | 粘贴路径识别。 |
| `file_ops.py` | 读写/编辑文件工具的高层封装。 |
| `file_op_models.py` | 文件操作数据模型。 |
| `file_op_paths.py` | 文件路径校验、展示和归一化。 |
| `file_op_content.py` | 文件内容读写辅助。 |
| `file_op_diff.py` | 文件 diff 生成。 |
| `file_op_approval.py` | 文件操作审批描述。 |
| `file_op_tracker.py` | 文件工具调用记录和结果追踪。 |
| `media_tracker.py` | 多媒体输入跟踪。 |
| `media_utils.py` | 图片/媒体解析和 MIME 辅助。 |

## `config` 和 `model_config`：配置与模型配置

| 模块 | 职责 |
| --- | --- |
| `config/__init__.py` | 全局 settings、常量、模型创建相关公共导出。 |
| `config/settings_model.py` | settings 数据结构和环境变量映射。 |
| `config/runtime.py` | 运行时配置加载。 |
| `config/bootstrap.py` | 初始配置/bootstrap。 |
| `config/display.py` | 配置展示。 |
| `config/langsmith.py` | LangSmith 配置和链接构造。 |
| `config/model_factory.py` | 模型实例创建。 |
| `config/paths.py` | 配置路径、agent 目录、项目目录。 |
| `config/session.py` | session 配置。 |
| `model_config/types.py` | 模型配置类型。 |
| `model_config/profiles.py` | 模型 profile 解析和管理。 |
| `model_config/persistence.py` | TOML 配置持久化。 |
| `model_config/thread_preferences.py` | 线程级模型偏好。 |
| `configurable_model/__init__.py` | 通过 LangGraph runtime context 在调用级切换模型。 |

## `mcp`：MCP 集成

| 模块 | 职责 |
| --- | --- |
| `mcp/models.py` | MCP server/tool 数据模型。 |
| `mcp/config_loader.py` | MCP 配置读取。 |
| `mcp/loader.py` | MCP server 加载和连接。 |
| `mcp/tools.py` | MCP tool 包装、信任和展示。 |
| `mcp/trust.py` | MCP server 信任策略。 |

## `memory`：长期记忆子系统

| 模块 | 职责 |
| --- | --- |
| `memory/agent.py` | memory agent middleware。 |
| `memory/agent_runtime.py` | memory agent 运行时。 |
| `memory/agent_extraction.py` | 从对话中抽取记忆操作。 |
| `memory/agent_store.py` | memory agent store 封装。 |
| `memory/prompts.py` | memory 抽取 prompt。 |
| `memory/signals.py` | 记忆更新信号。 |
| `memory/store_core.py` | memory store 基础读写。 |
| `memory/store_ops.py` | memory store 操作。 |
| `memory/store_mutations.py` | memory mutation 应用。 |
| `memory/store_validation.py` | store 结构校验和恢复。 |

## `scheduler`：定时任务系统

| 模块 | 职责 |
| --- | --- |
| `scheduler/models.py` | `ScheduledTask`、`TaskRun`、delivery/report spec。 |
| `scheduler/parser.py` | 自然语言/简写时间表达式到 cron/once 的解析。 |
| `scheduler/runner.py` | 定时轮询、due run 计算、任务注入。 |
| `scheduler/store.py` | scheduler store 主类。 |
| `scheduler/store_db.py` | SQLite 连接和 running row 健康检查。 |
| `scheduler/store_run_ops.py` | run 历史操作。 |
| `scheduler/store_serialization.py` | task/run 行序列化。 |
| `scheduler/store_views.py` | cwd scoped / filtered store view。 |
| `scheduler/schema.py` | SQLite schema 和迁移。 |
| `scheduler/payloads.py` | schedule tool payload 构造和更新应用。 |
| `scheduler/tool.py` | `ScheduleMiddleware`，向 agent 暴露定时任务工具。 |
| `scheduler/tool_factories.py` | create/update/list/cancel/run_now 工具工厂。 |
| `scheduler/tool_constants.py` | schedule 工具常量。 |
| `scheduler/tool_validation.py` | schedule tool 参数校验。 |
| `scheduler/display.py` | 定时任务展示文本。 |
| `scheduler/delivery.py` | 结果投递抽象。 |
| `scheduler/wecom_delivery.py` | WeCom 定时任务投递纯逻辑。 |

## `wecom`：企业微信长连接和无头运行

| 模块 | 职责 |
| --- | --- |
| `wecom/protocol.py` | WeCom frame 构造、解析、内容安全截断。 |
| `wecom/bridge.py` | WeCom 长连接桥接客户端。 |
| `wecom/session.py` | WeCom turn 进度和用户可见错误。 |
| `wecom/turn.py` | 将 WeCom 入站消息适配成本地 CLI turn。 |
| `wecom/media.py` | 入站媒体下载、解密、上传、文件工具发送。 |
| `wecom/file.py` | agent 侧 WeCom 文件发送 middleware/tool。 |
| `wecom/headless.py` | 后台 daemon 的无头消息处理器。 |
| `wecom/headless_stream.py` | 无头 streaming 输出节流。 |
| `wecom/headless_schedule.py` | 无头模式 schedule payload 持久化。 |
| `wecom/daemon_config.py` | daemon 配置模型。 |
| `wecom/daemon_constants.py` | daemon 常量。 |
| `wecom/daemon_state.py` | daemon state 文件、lock、存活检测。 |
| `wecom/daemon_control.py` | Unix socket 控制 RPC。 |
| `wecom/daemon_process.py` | fork/stdio/启动状态处理。 |
| `wecom/daemon_runtime.py` | daemon 主 async runtime。 |
| `wecom/daemon_scheduler.py` | daemon 中的 scheduler 运行和投递。 |
| `wecom/daemon.py` | daemon lifecycle 兼容/聚合入口。 |

## `server`、`remote`、`sessions`

| 模块 | 职责 |
| --- | --- |
| `server/config.py` | LangGraph server 配置。 |
| `server/graph.py` | server graph 入口。 |
| `server/manager.py` | server 进程管理和 session。 |
| `server/app_server.py` | App server 集成。 |
| `server/app_config.py` | server app 配置。 |
| `server/app_env.py` | server 环境变量。 |
| `server/app_health.py` | server 健康检查。 |
| `server/app_network.py` | server 网络端口/URL。 |
| `remote/client.py` | RemoteAgent client。 |
| `remote/helpers.py` | remote 调用辅助。 |
| `remote/messages.py` | remote message 转换。 |
| `sessions/__init__.py` | 线程管理公共入口和 checkpoint persistence。 |
| `sessions/cache.py` | 线程列表缓存。 |
| `sessions/checkpoints.py` | checkpoint 查询。 |
| `sessions/commands.py` | 线程相关 CLI 命令。 |
| `sessions/format.py` | 线程展示格式。 |
| `sessions/queries.py` | 线程查询。 |

## `skills` 与内置技能

| 模块 | 职责 |
| --- | --- |
| `skills/load.py` | skill 目录发现、metadata 加载。 |
| `skills/commands.py` | skill 子命令总入口。 |
| `skills/commands_list.py` | list 命令。 |
| `skills/commands_create.py` | create 命令。 |
| `skills/commands_info.py` | info 命令。 |
| `skills/commands_delete.py` | delete 命令。 |
| `built_in_skills/skill-creator/SKILL.md` | 内置 skill creator 指令。 |
| `built_in_skills/skill-creator/scripts/init_skill.py` | 初始化 skill 目录。 |
| `built_in_skills/skill-creator/scripts/quick_validate.py` | 快速校验 skill。 |

## 其他基础设施包

| 子包/模块 | 职责 |
| --- | --- |
| `commands/registry.py` | slash command 元数据、描述 key、bypass 等级。 |
| `core/ask_user_types.py` | ask_user 问题和结果类型。 |
| `core/cli_context.py` | 传入 LangGraph runtime 的上下文。 |
| `core/debug.py` | debug logging 配置。 |
| `core/env_vars.py` | 环境变量常量。 |
| `core/session_stats.py` | token、请求、耗时统计。 |
| `core/version.py` | 版本号和固定 URL。 |
| `hooks/__init__.py` | 外部 hook 配置和事件分发。 |
| `i18n/__init__.py` | 语言选择、翻译函数、语言持久化。 |
| `i18n/translations.py` | 翻译表聚合。 |
| `i18n/catalog/en.py` | 英文翻译。 |
| `i18n/catalog/zh.py` | 中文翻译。 |
| `integrations/sandbox_provider.py` | sandbox provider 协议。 |
| `integrations/sandbox_factory.py` | sandbox 创建入口。 |
| `integrations/sandbox_factory_lifecycle.py` | sandbox 生命周期管理。 |
| `integrations/sandbox_cloud_providers.py` | 云 sandbox provider 选择。 |
| `integrations/sandbox_agentcore.py` | AWS Bedrock AgentCore sandbox。 |
| `integrations/sandbox_langsmith.py` | LangSmith sandbox。 |
| `langsmith_links/__init__.py` | LangSmith project URL 查询。 |
| `local_context/__init__.py` | local context middleware。 |
| `local_context/script.py` | 项目环境探测脚本内容。 |
| `local_context/mcp.py` | MCP server 信息摘要。 |
| `models/deepseek_chat_openai.py` | DeepSeek OpenAI-compatible adapter。 |
| `models/testing.py` | 测试模型。 |
| `non_interactive/state.py` | 非交互模式状态。 |
| `non_interactive/stream.py` | 非交互 streaming 执行。 |
| `offload/__init__.py` | `/offload` 核心逻辑。 |
| `presentation/formatting.py` | CLI 展示格式。 |
| `presentation/glyphs.py` | 符号/glyph。 |
| `presentation/help.py` | 帮助展示辅助。 |
| `presentation/tool_display.py` | 工具展示格式。 |
| `project_utils/__init__.py` | 项目根检测和项目上下文。 |
| `shell_security/__init__.py` | shell allow-list 解析和命令安全判断。 |
| `theme/colors.py` | 主题颜色模型。 |
| `theme/registry.py` | 主题注册和加载。 |
| `theme/__init__.py` | 主题 facade。 |
| `thread_config/__init__.py` | 线程选择器偏好持久化。 |
| `tools/__init__.py` | 自定义工具：web search、fetch URL 等。 |
| `unicode_security/args.py` | URL/参数字符串遍历。 |
| `unicode_security/dangerous.py` | 危险 Unicode 字符检测。 |
| `unicode_security/models.py` | Unicode 安全结果类型。 |
| `unicode_security/url.py` | URL 安全检查。 |
| `unicode_security/__init__.py` | Unicode security facade。 |
| `update_check/__init__.py` | PyPI 更新检查、缓存、自动更新。 |

## 常见改动入口

| 要改的能力 | 优先查看 |
| --- | --- |
| 主 agent 创建、工具、middleware | `agent/__init__.py`、`agent/middleware.py`、`agent/tool_descriptions.py` |
| Textual App 生命周期 | `app/__init__.py`、`app_runtime/startup_handlers.py`、`app_runtime/initialization.py` |
| agent streaming/UI 适配 | `textual_adapter/execution.py`、`message_stream.py`、`interrupt_flow.py` |
| `/plan` 行为 | `plan_mode/*`、`app_runtime/plan_handlers.py`、`middleware/plan_agent.py` |
| slash command | `commands/registry.py`、`app_runtime/command.py`、`command_handlers.py` |
| 工具审批 | `app_runtime/approval.py`、`approval_handlers.py`、`widgets/approval.py` |
| 文件操作 | `io/file_ops.py`、`io/file_op_*`、`textual_adapter/tool_results.py` |
| 记忆 | `memory/*`、`middleware/auto_memory.py`、`app_runtime/memory_handlers.py` |
| 定时任务 | `scheduler/*`、`app_runtime/scheduled_delivery.py`、`schedule_handlers.py` |
| WeCom | `wecom/*`、`app_runtime/wecom*.py` |
| 模型选择 | `model_config/*`、`config/model_factory.py`、`app_runtime/model_handlers.py` |
| MCP | `mcp/*`、`widgets/mcp_viewer.py` |
| UI 组件 | `widgets/*` |
