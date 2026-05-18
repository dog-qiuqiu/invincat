# Invincat CLI Architecture Guide

This document is for code reading and extension work. It explains `invincat_cli` at three levels: the main runtime flow, package responsibilities, and module responsibilities. It is not a user manual; its goal is to help contributors quickly decide which package and file to inspect for a given capability.

## Main Runtime Flow

The main Invincat CLI flow can be read as:

1. `invincat_cli.__main__` / `invincat_cli.main` parses CLI arguments and enters the selected runtime mode.
2. `invincat_cli.cli` assembles CLI arguments, MCP settings, dependency checks, and Textual or non-interactive entry points.
3. `invincat_cli.app` creates the Textual application and combines mixins/handlers from `app_runtime`.
4. `invincat_cli.agent` creates the DeepAgents/LangGraph agent and injects tools, middleware, backend, and system prompt.
5. `invincat_cli.textual_adapter` converts agent streams, tool calls, interrupts, todos, and approval results into Textual UI messages.
6. `invincat_cli.widgets` renders TUI components such as chat messages, tool calls, approvals, status bars, and selectors.
7. Subsystems such as `invincat_cli.io`, `scheduler`, `memory`, `mcp`, `wecom`, and `skills` provide concrete capabilities.

The core `/plan` mode path is:

```text
command_handlers -> plan_handlers -> plan_mode.runtime/policy/handoff
                                      -> planner agent
                                      -> approve_plan
                                      -> main agent handoff
```

## Top-Level Entry Points

| File | Responsibility |
| --- | --- |
| `invincat_cli/__init__.py` | Package-level public entry point, including the CLI main entry. |
| `invincat_cli/__main__.py` | Entry point for `python -m invincat_cli`. |
| `invincat_cli/py.typed` | Marks the package as PEP 561 typed. |

## `main`: Legacy CLI Entry

| Module | Responsibility |
| --- | --- |
| `main/__init__.py` | Main CLI entry and argparse-driven logic. |
| `main/__main__.py` | Entry point for `python -m invincat_cli.main`. |

## `cli`: Command-Line Startup Orchestration

| Module | Responsibility |
| --- | --- |
| `cli/args.py` | Defines and parses command-line arguments. |
| `cli/dependencies.py` | Checks external dependencies, optional tools, and runtime prerequisites. |
| `cli/runtime.py` | Chooses Textual, non-interactive, server, or other runtime paths from parsed arguments. |
| `cli/textual.py` | Starts the Textual TUI application. |
| `cli/stdin.py` | Handles stdin input and one-shot task input. |
| `cli/mcp.py` | Assembles MCP arguments and configuration during CLI startup. |
| `cli/acp.py` | ACP-related startup entry and argument wiring. |

## `app`: Textual Application Object

| Module | Responsibility |
| --- | --- |
| `app/__init__.py` | Defines `DeepAgentsApp`, combining runtime mixins, Textual lifecycle hooks, and UI events. |
| `app/app.tcss` | Textual stylesheet. |

## `app_runtime`: Textual App Runtime Logic

`app_runtime` is the main control layer for the TUI application. It splits the large App class into pure functions, handlers, and mixins so not every behavior lives directly in `DeepAgentsApp`.

| Module | Responsibility |
| --- | --- |
| `state.py` | Dataclasses for Textual session state, queued messages, deferred actions, thread payloads, and related state. |
| `initialization.py` | Initializes App runtime fields, external integrations, and internal state. |
| `runner.py` | Application run wrapper and main run-loop helpers. |
| `layout.py` | Builds the Textual page layout. |
| `bindings.py` | Defines keyboard bindings. |
| `textual_patch.py` | Textual compatibility patches. |
| `terminal.py` | Terminal state, cursor, and display helpers. |
| `startup.py` | Pure startup-stage logic. |
| `startup_handlers.py` | App-bound handlers for startup, mount, warmup, and thread recovery. |
| `services.py` | Initializes and holds App dependency services. |
| `server.py` | Local LangGraph server runtime state helpers. |
| `server_events.py` | Server event handling. |
| `server_handlers.py` | Server start/stop, connection, recovery, and error handling. |
| `agent.py` | Pure decisions for agent turn requests, thread overrides, and scheduler run state. |
| `agent_handlers.py` | Starts agent turns, runs execution, handles exceptions, cleans up, and processes queued follow-ups. |
| `turn_flow_mixins.py` | App mixins for agent turns, message flow, and worker cleanup. |
| `delegate_mixins.py` | Mixins that delegate App methods to runtime handlers. |
| `interaction_mixins.py` | Mixins for interaction and UI events. |
| `approval.py` | Pure approval runtime helpers, plan guards, and shell auto-approval decisions. |
| `approval_handlers.py` | Mounts tool approval, `ask_user`, and `approve_plan` UI; maps approval results. |
| `approval_plan_mixins.py` | App method layer for `/plan`, approvals, `ask_user`, and input routing. |
| `plan.py` | Compatibility export layer that forwards to `plan_mode` prompt/policy/handoff helpers. |
| `plan_handlers.py` | Enters/exits `/plan`, creates the planner agent, handles planner turn results, and executes handoff. |
| `command.py` | Slash-command routing classification, including `/plan <task>`. |
| `command_handlers.py` | App-bound execution dispatcher for slash commands. |
| `command_mixins.py` | App mixins for command handlers. |
| `queueing.py` | Pure decisions for whether commands can bypass the queue while busy. |
| `queue_handlers.py` | Consumes pending message queues. |
| `input_handlers.py` | Dispatches chat input according to normal, shell, or command mode. |
| `shell.py` | Pure shell-command state and safety helpers. |
| `shell_handlers.py` | Starts shell tasks, handles interactive shell sessions, cleanup, and kill. |
| `action_handlers.py` | Textual actions such as opening an editor. |
| `deferred_handlers.py` | Deferred action queue processed after busy work finishes. |
| `exit_handlers.py` | Cleanup before exit. |
| `errors.py` | App error formatting and retryable-error recognition. |
| `help.py` | Builds help content. |
| `message_flow.py` | Message mounting, pruning, clearing, and spinner handling. |
| `memory.py` | Pure App-layer memory/offload logic. |
| `memory_handlers.py` | Token statistics, auto offload, memory notifications, and offload commands. |
| `model_args.py` | Model command argument parsing helpers. |
| `model_command.py` | `/model` command parsing and usage text. |
| `model_runtime.py` | Runtime decisions for model switching. |
| `model_handlers.py` | Model selector, main/memory model switching, and default model persistence. |
| `reload.py` | `/reload` report construction and configuration diffing. |
| `tokens.py` | Token messages and display state construction. |
| `thread_runtime.py` | Pure logic for thread switching and recovery. |
| `thread_handlers.py` | Thread selection, recovery, and failure rollback. |
| `thread_history.py` | Thread history reading and display helpers. |
| `thread_links.py` | Thread link and trace link helpers. |
| `ui_actions.py` | Binds UI actions to handlers. |
| `ui_handlers.py` | UI panels for theme, language, MCP, memory, thread selector, and related features. |
| `theme_prefs.py` | Loads and saves theme preferences. |
| `update_handlers.py` | Update checks, manual update flow, and auto-update toggles. |
| `version.py` | Version information display. |
| `scheduler.py` | Pure App-layer scheduler logic. |
| `schedule_handlers.py` | Schedule tool payload handling and schedule manager UI. |
| `scheduled_delivery.py` | Scheduled-task triggers, timeouts, result delivery, and WeCom delivery. |
| `wecom.py` | Pure App-layer WeCom decisions and message formatting. |
| `wecom_handlers.py` | WeCom bot commands, bridge handling, inbound messages, and file sending. |
| `skill.py` | Pure App-layer skill command logic. |
| `skill_handlers.py` | Skill command execution and discovery. |

## `plan_mode`: Strict Planning Domain Layer

| Module | Responsibility |
| --- | --- |
| `plan_mode/models.py` | `PlanModeStatus`, `PlanStep`, `PlanSession`, `PlanDrift`, and `PlanTurnResolution`. |
| `plan_mode/prompts.py` | Planning-only planner prompt, approval prompt, and planner runtime input construction. |
| `plan_mode/policy.py` | Planner allow-list, todo/step normalization, fingerprints, and drift detection. |
| `plan_mode/runtime.py` | Resolves planner checkpoints into approved, rejected, drift, or noop results. |
| `plan_mode/handoff.py` | Builds the approved-plan handoff prompt with the original request, refinements, approved plan, and execution rules. |

## `agent`: Agent Construction and Agent-Level Middleware

| Module | Responsibility |
| --- | --- |
| `agent/__init__.py` | `create_cli_agent`; assembles model, backend, tools, middleware, subagents, and checkpointer. |
| `agent/catalog.py` | Discovers, lists, and loads async subagents. |
| `agent/middleware.py` | Agent middleware such as shell allow-listing and memory file guards. |
| `agent/prompt.py` | Builds the main agent system prompt, injecting mode, time, working directory, and other context. |
| `agent/subagents/` | Defines and registers built-in subagents, such as the codebase `explorer`, implementation `worker`, read-only `researcher`, and document-focused `document-worker`. |
| `agent/system_prompt.md` | Base system prompt for the main agent. |
| `agent/tool_descriptions.py` | Tool descriptions and interrupt configuration shown during HITL approval. |

## `middleware`: LangChain/LangGraph Middleware

| Module | Responsibility |
| --- | --- |
| `middleware/approve_plan.py` | `approve_plan` tool and interrupt protocol. |
| `middleware/ask_user.py` | `ask_user` tool and question interaction protocol. |
| `middleware/auto_memory.py` | Automatic memory refresh middleware. |
| `middleware/file_management.py` | Safe project-scoped file management tools such as `file_info`, `mkdir`, `move_file`, `copy_file`, and `delete_file`. |
| `middleware/micro_compact.py` | Rule-based compaction of old messages/tool results to reduce context usage. |
| `middleware/plan_agent.py` | Planner-agent visible tool filtering, runtime allow-listing, and legacy API compatibility exports. |
| `middleware/token_state.py` | Stores token state in graph state. |

## `textual_adapter`: Agent Stream to Textual UI Adapter

| Module | Responsibility |
| --- | --- |
| `textual_adapter/__init__.py` | Public exports and debug initialization for the adapter layer. |
| `execution.py` | Main streaming execution loop; handles messages, updates, and custom streams. |
| `ui_adapter.py` | Textual UI callback adapter connecting agent runtime to App UI. |
| `input.py` | Converts user input, file mentions, and media inputs into agent message content. |
| `message_stream.py` | Handles assistant text, token, and tool-call streams. |
| `update_stream.py` | Parses HITL, `ask_user`, and `approve_plan` interrupts from update streams. |
| `interrupt_flow.py` | Builds LangGraph resume payloads after interrupts complete. |
| `tool_calls.py` | UI display for streaming tool-call blocks. |
| `tool_results.py` | Fills in tool results, success/failure state, and file-operation display. |
| `turn_cleanup.py` | Persists token state, flushes text, and cleans interrupt state at turn end. |
| `reporting.py` | Compatibility exports/helpers for token and cleanup reports. |
| `validation.py` | Pydantic TypeAdapter caches for HITL, `ask_user`, and `approve_plan` payloads. |
| `utils.py` | Shared adapter utility functions. |

## `widgets`: Textual UI Components

| Module | Responsibility |
| --- | --- |
| `messages.py` | User, assistant, system, error, queue, and related message widgets. |
| `assistant_message.py` | Assistant message rendering. |
| `message_data.py` | Message data models. |
| `message_store.py` | UI message window storage and pruning. |
| `message_styles.py` | Message style constants. |
| `chat_input.py` | Chat input component. |
| `chat_text_area.py` | Input text area. |
| `chat_input_completion.py` | Input completion state. |
| `chat_input_paths.py` | Path recognition inside input. |
| `chat_input_styles.py` | Input component styles. |
| `chat_completion.py` | Chat completion UI helpers. |
| `autocomplete.py` | Base autocomplete component. |
| `autocomplete_slash.py` | Slash-command autocomplete. |
| `autocomplete_files.py` | File path autocomplete. |
| `autocomplete_file_utils.py` | File autocomplete helpers. |
| `autocomplete_shell.py` | Shell input autocomplete. |
| `autocomplete_shell_utils.py` | Shell autocomplete helpers. |
| `tool_call_message.py` | Tool-call messages. |
| `tool_call_output.py` | Tool output rendering. |
| `tool_renderers.py` | Renderer registry for different tool outputs. |
| `tool_widgets.py` | Widgets for tool approval, plan approval, and tool results. |
| `approval.py` | Generic tool approval menu. |
| `approve.py` | Plan approval display. |
| `ask_user.py` | `ask_user` question form. |
| `diff.py` | File diff display. |
| `history.py` | History message display. |
| `loading.py` | Loading/spinner widget. |
| `status.py` | Main status bar. |
| `status_model_label.py` | Model label in the status bar. |
| `status_styles.py` | Status bar styles. |
| `welcome.py` | Welcome page/banner. |
| `output_formatters.py` | Output formatting. |
| `model_selector*.py` | Model selector, options, actions, display, and styles. |
| `model_register*.py` | Model registration UI and styles. |
| `theme_selector.py` | Theme selector. |
| `language_selector.py` | Language selector. |
| `mcp_viewer.py` | MCP server/tool viewer. |
| `memory_viewer*.py` | Memory viewer, store, sorting, models, and styles. |
| `schedule_manager.py` | Scheduled-task manager UI. |
| `skill_message.py` | Skill-related messages. |
| `thread_selector*.py` | Thread selector, data, layout, option, render, action, and style modules. |
| `thread_delete_confirm.py` | Thread deletion confirmation dialog. |
| `_links.py` | Textual clickable-link helpers. |

## `io`: Local I/O, File Operations, and Media Handling

| Module | Responsibility |
| --- | --- |
| `input.py` | Input construction, file mention parsing, and media tracker coordination. |
| `output.py` | CLI output helpers. |
| `clipboard.py` | Clipboard reading and writing. |
| `editor.py` | Opens an external editor and collects edited content. |
| `file_mentions.py` | Extracts file paths/mentions from user input. |
| `pasted_paths.py` | Recognizes pasted paths. |
| `file_ops.py` | High-level wrapper for read/write/edit file tools. |
| `file_op_models.py` | File-operation data models. |
| `file_op_paths.py` | File path validation, display, and normalization. |
| `file_op_content.py` | File content read/write helpers. |
| `file_op_diff.py` | File diff generation. |
| `file_op_approval.py` | File-operation approval descriptions. |
| `file_op_tracker.py` | File-tool call recording and result tracking. |
| `media_tracker.py` | Media input tracking. |
| `media_utils.py` | Image/media parsing and MIME helpers. |

## `config` and `model_config`: Configuration and Model Configuration

| Module | Responsibility |
| --- | --- |
| `config/__init__.py` | Public exports for global settings, constants, and model creation. |
| `config/settings_model.py` | Settings data structures and environment variable mapping. |
| `config/runtime.py` | Runtime configuration loading. |
| `config/bootstrap.py` | Initial configuration/bootstrap. |
| `config/display.py` | Configuration display. |
| `config/langsmith.py` | LangSmith configuration and link construction. |
| `config/model_factory.py` | Model instance creation. |
| `config/paths.py` | Configuration paths, agent directories, and project directories. |
| `config/session.py` | Session configuration. |
| `model_config/types.py` | Model configuration types. |
| `model_config/profiles.py` | Model profile parsing and management. |
| `model_config/persistence.py` | TOML configuration persistence. |
| `model_config/thread_preferences.py` | Thread-level model preferences. |
| `configurable_model/__init__.py` | Switches models per call through LangGraph runtime context. |

## `mcp`: MCP Integration

| Module | Responsibility |
| --- | --- |
| `mcp/models.py` | MCP server/tool data models. |
| `mcp/config_loader.py` | MCP configuration loading. |
| `mcp/loader.py` | MCP server loading and connection. |
| `mcp/tools.py` | MCP tool wrapping, trust handling, and display. |
| `mcp/trust.py` | MCP server trust policy. |

## `memory`: Long-Term Memory Subsystem

| Module | Responsibility |
| --- | --- |
| `memory/agent.py` | Memory agent middleware. |
| `memory/agent_runtime.py` | Memory agent runtime. |
| `memory/agent_extraction.py` | Extracts memory operations from conversations. |
| `memory/agent_store.py` | Memory agent store wrapper. |
| `memory/prompts.py` | Memory extraction prompts. |
| `memory/signals.py` | Memory update signals. |
| `memory/store_core.py` | Basic memory store reads and writes. |
| `memory/store_ops.py` | Memory store operations. |
| `memory/store_mutations.py` | Applies memory mutations. |
| `memory/store_validation.py` | Store structure validation and recovery. |

## `scheduler`: Scheduled Task System

| Module | Responsibility |
| --- | --- |
| `scheduler/models.py` | `ScheduledTask`, `TaskRun`, delivery specs, and report specs. |
| `scheduler/parser.py` | Parses natural-language/shorthand time expressions into cron/once schedules. |
| `scheduler/runner.py` | Polling loop, due-run calculation, and task injection. |
| `scheduler/store.py` | Main scheduler store class. |
| `scheduler/store_db.py` | SQLite connection and running-row health checks. |
| `scheduler/store_run_ops.py` | Run history operations. |
| `scheduler/store_serialization.py` | Task/run row serialization. |
| `scheduler/store_views.py` | CWD-scoped and filtered store views. |
| `scheduler/schema.py` | SQLite schema and migrations. |
| `scheduler/payloads.py` | Schedule tool payload construction and update application. |
| `scheduler/tool.py` | `ScheduleMiddleware`, exposing scheduled-task tools to the agent. |
| `scheduler/tool_factories.py` | Tool factories for create/update/list/cancel/run_now. |
| `scheduler/tool_constants.py` | Schedule tool constants. |
| `scheduler/tool_validation.py` | Schedule tool argument validation. |
| `scheduler/display.py` | Scheduled-task display text. |
| `scheduler/delivery.py` | Result delivery abstractions. |
| `scheduler/wecom_delivery.py` | Pure logic for WeCom scheduled-task delivery. |

## `wecom`: WeCom Long Connection and Headless Runtime

| Module | Responsibility |
| --- | --- |
| `wecom/protocol.py` | WeCom frame construction, parsing, and safe content truncation. |
| `wecom/bridge.py` | WeCom long-connection bridge client. |
| `wecom/session.py` | WeCom turn progress and user-visible errors. |
| `wecom/turn.py` | Adapts inbound WeCom messages into local CLI turns. |
| `wecom/media.py` | Inbound media download/decryption/upload and file-tool sending. |
| `wecom/file.py` | Agent-side WeCom file sending middleware/tool. |
| `wecom/headless.py` | Headless message handler for the background daemon. |
| `wecom/headless_stream.py` | Throttles headless streaming output. |
| `wecom/headless_schedule.py` | Persists schedule payloads in headless mode. |
| `wecom/daemon_config.py` | Daemon configuration models. |
| `wecom/daemon_constants.py` | Daemon constants. |
| `wecom/daemon_state.py` | Daemon state files, locks, and liveness checks. |
| `wecom/daemon_control.py` | Unix socket control RPC. |
| `wecom/daemon_process.py` | Fork, stdio, and startup-state handling. |
| `wecom/daemon_runtime.py` | Main async daemon runtime. |
| `wecom/daemon_scheduler.py` | Scheduler runtime and delivery inside the daemon. |
| `wecom/daemon.py` | Compatibility/aggregation entry for daemon lifecycle. |

## `server`, `remote`, and `sessions`

| Module | Responsibility |
| --- | --- |
| `server/config.py` | LangGraph server configuration. |
| `server/graph.py` | Server graph entry point. |
| `server/manager.py` | Server process management and sessions. |
| `server/app_server.py` | App server integration. |
| `server/app_config.py` | Server app configuration. |
| `server/app_env.py` | Server environment variables. |
| `server/app_health.py` | Server health checks. |
| `server/app_network.py` | Server network ports and URLs. |
| `remote/client.py` | RemoteAgent client. |
| `remote/helpers.py` | Remote call helpers. |
| `remote/messages.py` | Remote message conversion. |
| `sessions/__init__.py` | Public thread-management entry and checkpoint persistence. |
| `sessions/cache.py` | Thread list cache. |
| `sessions/checkpoints.py` | Checkpoint queries. |
| `sessions/commands.py` | Thread-related CLI commands. |
| `sessions/format.py` | Thread display formatting. |
| `sessions/queries.py` | Thread queries. |

## `skills` and Built-In Skills

| Module | Responsibility |
| --- | --- |
| `skills/load.py` | Skill directory discovery and metadata loading. |
| `skills/commands.py` | Main entry for skill subcommands. |
| `skills/commands_list.py` | `list` command. |
| `skills/commands_create.py` | `create` command. |
| `skills/commands_info.py` | `info` command. |
| `skills/commands_delete.py` | `delete` command. |
| `built_in_skills/skill-creator/SKILL.md` | Built-in skill creator instructions. |
| `built_in_skills/skill-creator/scripts/init_skill.py` | Initializes a skill directory. |
| `built_in_skills/skill-creator/scripts/quick_validate.py` | Quickly validates a skill. |

## Other Infrastructure Packages

| Package/Module | Responsibility |
| --- | --- |
| `commands/registry.py` | Slash-command metadata, description keys, and bypass levels. |
| `core/ask_user_types.py` | `ask_user` question and result types. |
| `core/cli_context.py` | Context passed into LangGraph runtime. |
| `core/debug.py` | Debug logging configuration. |
| `core/env_vars.py` | Environment variable constants. |
| `core/session_stats.py` | Token, request, and duration statistics. |
| `core/version.py` | Version number and fixed URLs. |
| `hooks/__init__.py` | External hook configuration and event dispatch. |
| `i18n/__init__.py` | Language selection, translation functions, and language persistence. |
| `i18n/translations.py` | Translation table aggregation. |
| `i18n/catalog/en.py` | English translations. |
| `i18n/catalog/zh.py` | Chinese translations. |
| `integrations/sandbox_provider.py` | Sandbox provider protocol. |
| `integrations/sandbox_factory.py` | Sandbox creation entry point. |
| `integrations/sandbox_factory_lifecycle.py` | Sandbox lifecycle management. |
| `integrations/sandbox_cloud_providers.py` | Cloud sandbox provider selection. |
| `integrations/sandbox_agentcore.py` | AWS Bedrock AgentCore sandbox. |
| `integrations/sandbox_langsmith.py` | LangSmith sandbox. |
| `langsmith_links/__init__.py` | LangSmith project URL lookup. |
| `local_context/__init__.py` | Local context middleware. |
| `local_context/script.py` | Project environment detection script content. |
| `local_context/mcp.py` | MCP server information summary. |
| `models/deepseek_chat_openai.py` | DeepSeek OpenAI-compatible adapter. |
| `models/testing.py` | Testing model. |
| `non_interactive/state.py` | Non-interactive mode state. |
| `non_interactive/stream.py` | Non-interactive streaming execution. |
| `offload/__init__.py` | Core `/offload` logic. |
| `presentation/formatting.py` | CLI display formatting. |
| `presentation/glyphs.py` | Symbols/glyphs. |
| `presentation/help.py` | Help display helpers. |
| `presentation/tool_display.py` | Tool display formatting. |
| `project_utils/__init__.py` | Project root detection and project context. |
| `shell_security/__init__.py` | Shell allow-list parsing and command safety decisions. |
| `theme/colors.py` | Theme color models. |
| `theme/registry.py` | Theme registration and loading. |
| `theme/__init__.py` | Theme facade. |
| `thread_config/__init__.py` | Thread selector preference persistence. |
| `tools/__init__.py` | Custom tools such as web search and URL fetch. |
| `unicode_security/args.py` | URL/argument string traversal. |
| `unicode_security/dangerous.py` | Dangerous Unicode character detection. |
| `unicode_security/models.py` | Unicode security result types. |
| `unicode_security/url.py` | URL safety checks. |
| `unicode_security/__init__.py` | Unicode security facade. |
| `update_check/__init__.py` | PyPI update checks, caching, and auto-update. |

## Common Change Entry Points

| Capability | Start Here |
| --- | --- |
| Main agent creation, tools, middleware | `agent/__init__.py`, `agent/middleware.py`, `agent/tool_descriptions.py` |
| Textual App lifecycle | `app/__init__.py`, `app_runtime/startup_handlers.py`, `app_runtime/initialization.py` |
| Agent streaming/UI adaptation | `textual_adapter/execution.py`, `message_stream.py`, `interrupt_flow.py` |
| `/plan` behavior | `plan_mode/*`, `app_runtime/plan_handlers.py`, `middleware/plan_agent.py` |
| Slash commands | `commands/registry.py`, `app_runtime/command.py`, `command_handlers.py` |
| Tool approval | `app_runtime/approval.py`, `approval_handlers.py`, `widgets/approval.py` |
| File operations | `io/file_ops.py`, `io/file_op_*`, `textual_adapter/tool_results.py` |
| Memory | `memory/*`, `middleware/auto_memory.py`, `app_runtime/memory_handlers.py` |
| Scheduled tasks | `scheduler/*`, `app_runtime/scheduled_delivery.py`, `schedule_handlers.py` |
| WeCom | `wecom/*`, `app_runtime/wecom*.py` |
| Model selection | `model_config/*`, `config/model_factory.py`, `app_runtime/model_handlers.py` |
| MCP | `mcp/*`, `widgets/mcp_viewer.py` |
| UI components | `widgets/*` |
