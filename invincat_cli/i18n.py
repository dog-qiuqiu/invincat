"""Internationalization (i18n) support for deepagents-cli.

This module provides comprehensive language localization support, enabling
users to switch between English and Chinese languages throughout the application.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class Language(StrEnum):
    """Supported languages for the CLI interface."""

    EN = "en"
    """English (default)"""

    ZH = "zh"
    """Chinese (Simplified)"""


DEFAULT_LANGUAGE = Language.EN
"""Default language when not configured."""


TRANSLATIONS: dict[str, dict[str, str]] = {
    Language.EN: {
        "welcome.tips.1": "Use @ to reference files and / for commands",
        "welcome.tips.2": "Try /threads to resume a previous conversation",
        "welcome.tips.3": "Use /offload when your conversation gets long",
        "welcome.tips.4": "Use /mcp to see your loaded tools and servers",
        "welcome.tips.6": "Use /model to switch models mid-conversation",
        "welcome.tips.7": "Press ctrl+x to compose prompts in your external editor",
        "welcome.tips.8": "Press ctrl+u to delete to the start of the line in the chat input",
        "welcome.tips.9": "Use /skill:<name> to invoke a skill directly",
        "welcome.tips.10": "Type /update to check for and install updates",
        "welcome.tips.11": "Use /theme to customize the CLI colors and style",
        "welcome.tips.12": "Use /skill-creator to build reusable agent skills",
        "welcome.connecting": "Connecting...",
        "welcome.resuming": "Resuming...",
        "welcome.ready": "Ready",
        "welcome.current": "current",
        "approval.approve": "Approve",
        "approval.reject": "Reject",
        "approval.auto_approve": "Auto-approve",
        "approval.expand_command": "Expand command",
        "approval.shell_command": "Shell command",
        "approval.tool_call": "Tool call",
        "approval.decision_required": "Decision required",
        "approval.warning_deceptive": "Warning: Potentially deceptive text",
        "approval.more_warnings": "+{count} more warning(s)",
        "status.thinking": "Thinking",
        "status.memory_updating": "Updating memory...",
        "status.memory_updated": "Memory updated: {path}",
        "status.memory_updated_n": "Memory updated: {n} files",
        "status.awaiting_decision": "Awaiting decision",
        "status.esc_to_interrupt": "esc to interrupt",
        "status.offloading": "Offloading",
        "status.shell_mode": "SHELL",
        "status.cmd_mode": "CMD",
        "status.plan_mode": "PLAN",
        "status.tokens": "Token",
        "status.loading": "Loading...",
        "status.ready": "Ready",
        "status.error": "Error",
        "status.success": "Success",
        "status.cancelled": "Cancelled",
        "status.interrupted": "Interrupted",
        "command.clear": "Clear chat and start new thread",
        "command.editor": "Open prompt in external editor ($EDITOR)",
        "command.mcp": "Show active MCP servers and tools",
        "command.memory": "Open memory manager to inspect memory stores",
        "command.wecombot_start": "Start WeCom bot bridge",
        "command.wecombot_status": "Show WeCom bot bridge status",
        "command.wecombot_stop": "Stop WeCom bot bridge",
        "command.model": "Switch or configure model (--model-params, --default)",
        "command.offload": "Free up context window space by offloading older messages",
        "command.plan": "/plan - Enter plan mode, then describe your task",
        "command.exit_plan": "/exit-plan - Exit plan mode and return to main agent",
        "plan.entered": "Plan mode ON. Describe your task and I'll create a plan.",
        "plan.exited": "Plan mode OFF.",
        "plan.already_on": "Plan mode is already ON. Use /exit-plan to leave plan mode.",
        "plan.not_on": "Plan mode is already OFF.",
        "plan.approved_no_execute": "Plan approved. Exited plan mode. Handing off the approved checklist to the main agent for execution.",
        "plan.handoff_started": "Executing approved plan with the main agent...",
        "plan.handoff_prompt_preview": "Approved plan handoff prompt (for audit):",
        "plan.refine_prompt": "Plan not approved. Tell me what to change, and I'll regenerate the plan.",
        "plan.auto_reject_non_plan_tool": "Rejected non-plan tool call in /plan mode: {tools}. Continue refining the plan.",
        "plan.agent_not_configured": "Agent not configured.",
        "plan.planner_unavailable": "Planner agent is unavailable.",
        "plan.approval_no_valid_todos": "Planner approval succeeded but no valid todo list was found; please regenerate the plan.",
        "plan.ready_no_valid_todos": "Planner marked plan-ready but no valid todo list was found; please regenerate the plan.",
        "plan.blocked_tool_error": "Blocked by /plan policy: this tool is not allowed in planner mode.",
        "approve.prompt": "Press 'y' to approve and execute, or 'n' to refine the plan",
        "approve.approved": "Plan approved.",
        "approve.rejected": "Plan rejected. Please provide feedback to refine the plan.",
        "approve.tool_result_approved": "Plan approved",
        "ask_user.tool_result_answered": "User answered",
        "memory.viewer.loading": "Loading memory...",
        "memory.viewer.help": "1 user · 2 project · tab switch · ↑↓ nav · r refresh · a archived · s sort · d delete · esc close",
        "memory.viewer.delete.confirm": "Delete {item_id} — press [bold yellow]d[/bold yellow] again to confirm, navigate away to cancel.",
        "memory.viewer.delete.success": "Deleted: {item_id}",
        "memory.viewer.delete.error": "Delete failed: {error}",
        "memory.viewer.delete.no_selection": "No item selected.",
        "memory.viewer.title": "Memory Manager · Scope: {scope} · {sort}",
        "memory.viewer.sort.score_desc": "Score ↓",
        "memory.viewer.sort.score_asc": "Score ↑",
        "memory.viewer.sort.last_scored_desc": "Last Scored ↓",
        "memory.viewer.sort.last_scored_asc": "Last Scored ↑",
        "memory.viewer.summary": "Scopes: {valid}/{total} valid · path: {path} · total={items_total} active={active} archived={archived} latest={latest}",
        "memory.viewer.summary_unavailable": "Scopes: {valid}/{total} valid · Current scope unavailable: {scope}",
        "memory.viewer.no_scope_configured": "No memory store configured for current scope.",
        "memory.viewer.no_stores_configured": "No memory stores configured.",
        "memory.viewer.label.scope": "scope",
        "memory.viewer.label.path": "path",
        "memory.viewer.label.status": "status",
        "memory.viewer.label.id": "id",
        "memory.viewer.label.section": "section",
        "memory.viewer.label.tier": "tier",
        "memory.viewer.label.score": "score",
        "memory.viewer.label.content": "content",
        "memory.viewer.label.score_reason": "score_reason",
        "memory.viewer.label.last_scored_at": "last_scored_at",
        "memory.viewer.status.missing": "missing",
        "memory.viewer.status.invalid": "invalid ({error})",
        "memory.viewer.status.ok": "ok",
        "memory.viewer.no_visible_items": "(no visible items)",
        "command.skill_creator": "Guide for creating effective agent skills",
        "command.threads": "Browse and resume previous threads",
        "command.trace": "Open current thread in LangSmith",
        "command.tokens": "Token usage",
        "command.reload": "Reload config from environment variables and .env",
        "command.theme": "Switch color theme",
        "command.update": "Check for and install updates",
        "command.auto_update": "Toggle automatic updates on or off",
        "command.changelog": "Open changelog in browser",
        "command.version": "Show version",
        "command.feedback": "Submit a bug report or feature request",
        "command.docs": "Open documentation in browser",
        "command.quit": "Quit the application",
        "command.help": "Show help message",
        "command.unknown": "Unknown command: {command}",
        "command.language": "Switch interface language",
        "help.title": "Commands",
        "help.interactive_features": "Interactive Features",
        "help.submit": "Submit your message",
        "help.insert_newline": "Insert newline",
        "help.open_editor": "Open prompt in external editor",
        "help.toggle_auto_approve": "Toggle auto-approve mode",
        "help.autocomplete_files": "Auto-complete files and inject content",
        "help.slash_commands": "Slash commands (/help, /clear, /quit)",
        "help.shell_commands": "Switch to shell mode, press 'esc' to exit shell mode",
        "help.docs": "Docs",
        "error.missing_dependencies": "Missing required CLI dependencies!",
        "error.install_required": "The following packages are required to use the deepagents CLI:",
        "error.install_command": "Please install them with:",
        "error.install_all": "Or install all dependencies:",
        "error.ripgrep_not_found": "ripgrep (rg) not found. Install it for faster code search.",
        "error.tavily_not_configured": "Tavily API key not configured. Please set TAVILY_API_KEY environment variable.",
        "error.package_not_installed": "Required package not installed: {package}. Install with: pip install 'deepagents[cli]'",
        "error.config_read_failed": "Failed to read configuration file",
        "error.config_write_failed": "Failed to write configuration file",
        "error.language_save_failed": "Failed to save language preference",
        "success.language_changed": "Language changed to {language}",
        "success.config_reloaded": "Configuration reloaded successfully",
        "success.new_thread": "Started new thread: {thread_id}",
        "success.theme_changed": "Theme changed to {theme}",
        "success.update_available": "Update available: {version}",
        "success.up_to_date": "Already up to date",
        "notification.press_escape": "Press Escape to cancel",
        "notification.select_option": "Select an option",
        "notification.loading_please_wait": "Loading, please wait...",
        "language.select_title": "Select Language",
        "language.english": "English",
        "language.chinese": "中文 (Chinese)",
        "language.current": "current",
        "language.preview": "preview",
        "language.select": "select",
        "language.cancel": "cancel",
        "theme.select_title": "Select Theme",
        "theme.preview": "preview",
        "theme.select": "select",
        "theme.cancel": "cancel",
        "thread.select_title": "Select Thread",
        "thread.no_threads": "No threads found",
        "thread.loading": "Loading threads...",
        "thread.created": "Created",
        "thread.updated": "Updated",
        "thread.last_used": "Last Used",
        "model.select_title": "Select Model",
        "model.loading": "Loading models...",
        "model.no_models": "No models available",
        "model.current": "current",
        "model.default": "default",
        "model.set_default": "Set as default",
        "model.clear_default": "Clear default",
        "mcp.title": "MCP Servers and Tools",
        "mcp.no_servers": "No MCP servers configured",
        "mcp.tools_loaded": "tools loaded",
        "mcp.server": "Server",
        "mcp.tools": "Tools",
        "tokens.title": "Token Usage",
        "tokens.total": "Total",
        "tokens.prompt": "Prompt",
        "tokens.completion": "Completion",
        "tokens.approximate": "approximate",
        "tokens.usage_with_limit": "{used} / {limit} tokens ({pct}%)",
        "tokens.usage_simple": "{used} tokens used",
        "tokens.system_tools_fixed": "├ System prompt + tools: ~{tokens} (fixed)",
        "tokens.conversation": "└ Conversation: ~{tokens}",
        "tokens.no_usage_yet": "No token usage yet",
        "tokens.context_window": "{limit} token context window",
        "update.checking": "Checking for updates...",
        "update.installing": "Installing update...",
        "update.restart_required": "Restart the CLI to use the new version",
        "auto_update.enabled": "Automatic updates enabled",
        "auto_update.disabled": "Automatic updates disabled",
        "approval_menu.title": "Approval Required",
        "approval_menu.approve_short": "Approve",
        "approval_menu.reject_short": "Reject",
        "approval_menu.auto_approve_short": "Auto",
        "approval_menu.expand_short": "Expand",
        "approval_menu.collapse_short": "Collapse",
        "chat_input.placeholder": "Type a message... (@file /command)",
        "chat_input.shell_mode": "Shell mode",
        "chat_input.command_mode": "Command mode",
        "file_ops.file_not_found": "File not found: {path}",
        "file_ops.directory_not_found": "Directory not found: {path}",
        "file_ops.permission_denied": "Permission denied: {path}",
        "search.no_results": "No results found",
        "search.searching": "Searching...",
        "search.results_count": "{count} results found",
        "model.title": "Select Model",
        "model.current_model": "current: {model}",
        "model.filter_placeholder": "Type to filter or enter provider:model...",
        "model.target_primary": "Primary",
        "model.target_memory": "Memory",
        "model.target_short": "target: {target}",
        "model.navigate": "navigate",
        "model.select_action": "select",
        "model.set_default_action": "set default",
        "model.cancel_action": "cancel",
        "model.no_credentials": "No credentials",
        "model.profile_params": "Profile parameters",
        "model.default_model": "Default model",
        "model.loading": "Loading models…",
        "model.no_matching": "No matching models",
        "model.profile_not_available": "Model profile not available :(",
        "model.no_selected": "No model selected",
        "model.could_not_load": "Could not load profile details",
        "model.load_error": "Could not load model list. Check provider packages and config.toml.",
        "model.default_cleared": "Default cleared",
        "model.default_set_to": "Default set to {spec}",
        "model.default_target_cleared": "{target} default cleared",
        "model.default_target_set_to": "{target} default set to {spec}",
        "model.default_primary_only": "Default applies only to the primary model. Use '/model 2 <provider:model>' for session-level memory model switching.",
        "model.failed_clear": "Failed to clear default",
        "model.failed_save": "Failed to save default",
        "model.failed_target_clear": "Failed to clear {target} default",
        "model.failed_target_save": "Failed to save {target} default",
        "model.register_action": "register",
        "model.register_title": "Register New Model",
        "model.register_provider_label": "Provider Name",
        "model.register_provider_placeholder": "e.g. ollama, deepseek, openrouter",
        "model.register_model_label": "Model Name",
        "model.register_model_placeholder": "e.g. qwen3:4b, deepseek-chat",
        "model.register_apikey_label": "API Key Environment Variable",
        "model.register_apikey_hint": "Leave empty for providers without API keys (e.g. Ollama)",
        "model.register_apikey_placeholder": "e.g. DEEPSEEK_API_KEY",
        "model.register_baseurl_label": "Base URL",
        "model.register_baseurl_placeholder": "e.g. https://api.deepseek.com/v1",
        "model.register_max_input_tokens_label": "Max Input Tokens",
        "model.register_max_input_tokens_placeholder": "e.g. 128000",
        "model.register_error_max_input_tokens_integer": "Max input tokens must be an integer",
        "model.register_error_max_input_tokens_positive": "Max input tokens must be a positive number",
        "model.register_error_baseurl": "Base URL is required",
        "model.register_classpath_label": "Class Path (optional)",
        "model.register_classpath_hint": "For custom providers using a BaseChatModel subclass",
        "model.register_classpath_placeholder": "e.g. langchain_ollama:ChatOllama",
        "model.register_next_field": "next field",
        "model.register_submit": "submit",
        "model.register_error_provider": "Provider name is required",
        "model.register_error_model": "Model name is required",
        "model.register_error_colon": "Do not include ':' in provider or model name — use separate fields",
        "model.register_error_classpath": "Class path must be in 'module.path:ClassName' format",
        "model.register_error_save": "Failed to save configuration",
        "model.register_success": "Registered {spec}",
        "mcp.title": "MCP Servers",
        "mcp.servers_count": "{count} servers",
        "mcp.tools_count": "{count} tools",
        "mcp.no_servers_configured": "No MCP servers configured.\nUse `--mcp-config` to load servers.",
        "mcp.navigate": "navigate",
        "mcp.expand_collapse": "expand/collapse",
        "mcp.close": "close",
        "thread.title": "Select Thread",
        "thread.current_thread": "current: {thread_id}",
        "thread.filter_placeholder": "Type to filter threads...",
        "thread.navigate": "navigate",
        "thread.select_action": "select",
        "thread.focus_options": "focus options",
        "thread.toggle_option": "toggle option",
        "thread.delete_action": "delete",
        "thread.cancel_action": "cancel",
        "thread.showing_limit": "Showing last {limit} threads. Set DA_CLI_RECENT_THREADS to override.",
        "thread.column_thread_id": "Thread ID",
        "thread.column_agent": "Agent",
        "thread.column_messages": "Msgs",
        "thread.column_created": "Created",
        "thread.column_updated": "Updated",
        "thread.column_branch": "Branch",
        "thread.column_location": "Location",
        "thread.column_prompt": "Prompt",
        "thread.sort_by": "Sort by {field}",
        "thread.sort_updated": "Updated At",
        "thread.sort_created": "Created At",
        "thread.delete_confirm": "Delete thread {thread_id}?",
        "thread.delete_help": "Enter to confirm, Esc to cancel",
        "thread.relative_time": "Relative time",
        "thread.options": "Options",
        "thread.options_help": "Tab through sort and column toggles. Column visibility persists between sessions.",
        "thread.filter_placeholder": "Type to filter threads...",
        "ask.select": "Select",
        "ask.enter_continue": "Enter to continue",
        "ask.switch_question": "Tab/Shift+Tab switch question",
        "ask.cancel": "Esc to cancel",
        "ask.type_answer": "Type your answer...",
        "diff.no_changes": "No changes detected",
        "diff.truncated": "... (truncated)",
        "queue.discarded": "Queued message discarded",
        "queue.moved_to_input": "Queued message moved to input",
        "queue.discarded_input_not_empty": "Queued message discarded (input not empty)",
        "thread.sort_save_failed": "Could not save sort preference",
        "tool.details_not_available": "Tool details not available",
        "tool.no_changes": "No changes to display",
        "tool.removing": "Removing:",
        "tool.adding": "Adding:",
        "tool.more_lines": "... ({count} more lines)",
        "tool.more_chars": "... ({count} more chars)",
        "tool.plan_preview": "Plan Preview",
        "tool.interrupted_by_error": "Interrupted by error",
        "tool.interrupted_by_user": "Interrupted by user",
        "shell.command_completed": "Command completed",
        "shell.command_completed_no_output": "Command completed (no output)",
        "shell.command_timeout": "Command timed out ({seconds}s limit)",
        "shell.command_interrupted": "Command interrupted",
        "shell.command_not_found": "Command not found: {command}",
        "shell.command_failed": "Failed to run command: {error}",
        "shell.exit_code": "Exit code: {code}",
        "trace.no_active_session": "No active session.",
        "trace.resolve_failed": "Failed to resolve LangSmith thread URL.",
        "trace.not_configured": "LangSmith tracing is not configured. Set LANGSMITH_API_KEY and LANGSMITH_TRACING=true to enable.",
        "skill.usage": "Usage: /skill:<name> [args]",
        "skill.not_found": "Skill not found: {skill}",
        "skill.load_permission_error": "Could not load skill: {skill}. Permission error: {error}",
        "skill.load_filesystem_error": "Could not load skill: {skill}. Filesystem error: {error}",
        "skill.load_unexpected_error": "Error loading skill: {skill}. Unexpected error: {error}",
        "skill.content_unreadable": "Could not read content for skill: {skill}. Check that SKILL.md exists, is readable, and is UTF-8.",
        "skill.content_empty": "Skill '{skill}' has an empty SKILL.md file. Add instructions before invoking.",
        "offload.nothing_to_offload": "Nothing to offload — start a conversation first",
        "offload.cannot_while_running": "Cannot offload while agent is running",
        "offload.failed_read_state": "Failed to read state: {error}",
        "offload.failed": "Offload failed: {error}",
        "agent.not_configured_session": "Agent not configured for this session.",
        "agent.error": "Agent error: {error}",
        "queue.process_failed": "Failed to process queued message: {message}",
        "thread.resumed": "Resumed thread: {thread_id}",
        "thread.history_load_failed": "Could not load history: {error}",
        "thread.switch_no_active_agent": "Cannot switch threads: no active agent",
        "thread.switch_no_active_session": "Cannot switch threads: no active session",
        "thread.already_on": "Already on thread: {thread_id}",
        "model.switch_in_progress": "Model switch already in progress.",
        "model.switch_requires_server": "Model switching requires a server-backed session.",
        "model.missing_credentials": "Missing credentials: {detail}",
        "model.already_using": "Already using {model}",
        "model.switch_failed": "Failed to switch model: {error}",
        "model.preference_save_failed": "Model switched for this session, but could not save preference. Check permissions for ~/.invincat/",
        "model.switched_to": "Switched to {model}",
        "model.memory_switched_to": "Memory model switched to {model}",
        "loading.hint": "({duration}, esc to interrupt)",
        "loading.paused_at": "(paused at {duration})",
        "theme.select_title": "Select Theme",
        "theme.preview": "preview",
        "theme.select": "select",
        "theme.cancel": "cancel",
        "theme.current": "(current)",
        "message.no_results": "No results",
        "message.more_results": "{count} more results",
        "message.error": "Error:",
        "version.cli_line": "deepagents-cli version: {version}",
        "version.cli_unknown": "deepagents-cli version: unknown",
        "version.sdk_line": "deepagents (SDK) version: {version}",
        "version.sdk_unknown": "deepagents (SDK) version: unknown",
        "app.session_init_failed": "Session initialization failed. Some features may be unavailable.",
        "app.skill_scan_failed": "Could not scan skill directories. Some /skill: commands may be unavailable.",
        "app.skill_discovery_failed": "Skill discovery failed unexpectedly. /skill: commands may not work. Check logs for details.",
        "app.no_threads_agent": "No previous threads for '{agent}', starting new.",
        "app.no_threads": "No previous threads, starting new.",
        "app.thread_not_found": "Thread '{thread_id}' not found. Did you mean: {similar}?",
        "app.thread_not_found_simple": "Thread '{thread_id}' not found.",
        "app.thread_lookup_failed": "Could not look up thread history. Starting new session.",
        "app.updating_to": "Updating to v{version}...",
        "app.updated_to": "Updated to v{version}. Restart to use the new version.",
        "app.auto_update_failed": "Auto-update failed. Run manually: {command}",
        "app.update_available": "Update available: v{latest} (current: v{current}). Run: {command}\nEnable auto-updates: /auto-update",
        "app.update_available_upgrading": "Update available: v{latest} (current: v{current}). Upgrading...",
        "app.auto_update_failed_with_detail": "Auto-update failed{detail}\nRun manually: {command}",
        "app.update_failed_with_error": "Update failed: {error}",
        "app.update_failed": "Update failed unexpectedly.",
        "app.auto_update_not_available": "Auto-updates are not available for editable installs.",
        "app.auto_updates_enabled": "Auto-updates enabled.",
        "app.auto_updates_disabled": "Auto-updates disabled.",
        "app.auto_update_toggle_failed": "Auto-update toggle failed: {error}",
        "app.thread_switch_in_progress": "Thread switch in progress. Please wait.",
        "app.press_to_quit": "Press {shortcut} again to quit",
        "app.external_editor_failed": "External editor failed. Check $VISUAL/$EDITOR.",
        "app.model_switch_pending": "Model will switch after current task completes.",
        "app.theme_not_saved": "Theme applied for this session but could not be saved. Check logs for details.",
        "app.language_changed_to": "Language changed to {language}",
        "app.thread_switch_pending": "Thread will switch after current task completes.",
        "chat.attach_failed": "Could not attach {type}: {name}",
        "thread.delete_failed": "Failed to delete thread {thread_id}",
    },
    Language.ZH: {
        "welcome.tips.1": "使用 @ 引用文件，使用 / 执行命令",
        "welcome.tips.2": "尝试 /threads 恢复之前的对话",
        "welcome.tips.3": "对话过长时使用 /offload 释放上下文空间",
        "welcome.tips.4": "使用 /mcp 查看已加载的工具和服务器",
        "welcome.tips.6": "使用 /model 在对话中切换模型",
        "welcome.tips.7": "按 ctrl+x 在外部编辑器中编写提示",
        "welcome.tips.8": "按 ctrl+u 删除输入行首的内容",
        "welcome.tips.9": "使用 /skill:<名称> 直接调用技能",
        "welcome.tips.10": "输入 /update 检查并安装更新",
        "welcome.tips.11": "使用 /theme 自定义 CLI 颜色和样式",
        "welcome.tips.12": "使用 /skill-creator 创建可复用的代理技能",
        "welcome.connecting": "连接中...",
        "welcome.resuming": "恢复中...",
        "welcome.ready": "就绪",
        "welcome.current": "当前",
        "approval.approve": "人工批准",
        "approval.reject": "拒绝",
        "approval.auto_approve": "自动批准",
        "approval.expand_command": "展开命令",
        "approval.shell_command": "Shell 命令",
        "approval.tool_call": "工具调用",
        "approval.decision_required": "需要决定",
        "approval.warning_deceptive": "警告：可能存在欺骗性文本",
        "approval.more_warnings": "+{count} 个更多警告",
        "status.thinking": "思考中",
        "status.memory_updating": "记忆整理中...",
        "status.memory_updated": "记忆已更新: {path}",
        "status.memory_updated_n": "记忆已更新: {n} 个文件",
        "status.awaiting_decision": "等待决定",
        "status.esc_to_interrupt": "esc 中断",
        "status.offloading": "压缩中",
        "status.shell_mode": "SHELL",
        "status.cmd_mode": "CMD",
        "status.plan_mode": "PLAN",
        "status.tokens": "tokens",
        "status.loading": "加载中...",
        "status.ready": "就绪",
        "status.error": "错误",
        "status.success": "成功",
        "status.cancelled": "已取消",
        "status.interrupted": "已中断",
        "command.clear": "清除聊天并开始新对话",
        "command.editor": "在外部编辑器中打开提示 ($EDITOR)",
        "command.mcp": "显示活动的 MCP 服务器和工具",
        "command.memory": "打开记忆管理界面查看记忆状态",
        "command.wecombot_start": "启动企业微信机器人桥接",
        "command.wecombot_status": "查看企业微信机器人桥接状态",
        "command.wecombot_stop": "停止企业微信机器人桥接",
        "command.model": "切换或配置模型 (--model-params, --default)",
        "command.offload": "通过卸载旧消息释放上下文窗口空间",
        "command.plan": "/plan - 进入计划模式，然后描述你的任务",
        "command.exit_plan": "/exit-plan - 退出计划模式并返回主代理",
        "plan.entered": "计划模式已开启。描述你的任务，我会创建一个计划。",
        "plan.exited": "计划模式已关闭。",
        "plan.already_on": "计划模式已开启。使用 /exit-plan 退出计划模式。",
        "plan.not_on": "计划模式当前未开启。",
        "plan.approved_no_execute": "计划已通过并已退出计划模式。正在将已确认清单交给主代理开始执行。",
        "plan.handoff_started": "主代理正在执行已批准计划...",
        "plan.handoff_prompt_preview": "已批准计划交付提示词（审计可见）：",
        "plan.refine_prompt": "计划未通过。请告诉我需要调整的地方，我会重新生成计划。",
        "plan.auto_reject_non_plan_tool": "在 /plan 模式下已拒绝非计划工具调用：{tools}。请继续完善计划。",
        "plan.agent_not_configured": "代理未配置。",
        "plan.planner_unavailable": "计划代理当前不可用。",
        "plan.approval_no_valid_todos": "计划已确认，但未找到有效待办清单；请重新生成计划。",
        "plan.ready_no_valid_todos": "规划代理已标记计划就绪，但未找到有效待办清单；请重新生成计划。",
        "plan.blocked_tool_error": "已被 /plan 策略拦截：规划模式下不允许调用该工具。",
        "approve.prompt": "按 'y' 确认并执行，或按 'n' 完善计划",
        "approve.approved": "计划已确认。",
        "approve.rejected": "计划已拒绝，请提供反馈以完善计划。",
        "approve.tool_result_approved": "计划已确认",
        "ask_user.tool_result_answered": "用户已回复",
        "memory.viewer.loading": "正在加载记忆...",
        "memory.viewer.help": "1 用户 · 2 项目 · Tab 切换 · ↑↓ 导航 · r 刷新 · a 归档 · s 排序 · d 删除 · Esc 关闭",
        "memory.viewer.delete.confirm": "删除 {item_id} — 再次按 [bold yellow]d[/bold yellow] 确认，移动光标取消。",
        "memory.viewer.delete.success": "已删除：{item_id}",
        "memory.viewer.delete.error": "删除失败：{error}",
        "memory.viewer.delete.no_selection": "未选中任何条目。",
        "memory.viewer.title": "记忆管理 · 范围：{scope} · {sort}",
        "memory.viewer.sort.score_desc": "评分 ↓",
        "memory.viewer.sort.score_asc": "评分 ↑",
        "memory.viewer.sort.last_scored_desc": "最近评分 ↓",
        "memory.viewer.sort.last_scored_asc": "最近评分 ↑",
        "memory.viewer.summary": "范围：{valid}/{total} 可用 · 路径：{path} · 总计={items_total} 活跃={active} 归档={archived} 最近更新={latest}",
        "memory.viewer.summary_unavailable": "范围：{valid}/{total} 可用 · 当前范围不可用：{scope}",
        "memory.viewer.no_scope_configured": "当前范围未配置记忆存储。",
        "memory.viewer.no_stores_configured": "未配置任何记忆存储。",
        "memory.viewer.label.scope": "范围",
        "memory.viewer.label.path": "路径",
        "memory.viewer.label.status": "状态",
        "memory.viewer.label.id": "编号",
        "memory.viewer.label.section": "分组",
        "memory.viewer.label.tier": "层级",
        "memory.viewer.label.score": "分数",
        "memory.viewer.label.content": "内容",
        "memory.viewer.label.score_reason": "评分理由",
        "memory.viewer.label.last_scored_at": "最近评分时间",
        "memory.viewer.status.missing": "缺失",
        "memory.viewer.status.invalid": "无效（{error}）",
        "memory.viewer.status.ok": "正常",
        "memory.viewer.no_visible_items": "（无可见条目）",
        "command.skill_creator": "创建有效代理技能的指南",
        "command.threads": "浏览并恢复之前的对话",
        "command.trace": "在 LangSmith 中打开当前对话",
        "command.tokens": "Token 使用情况",
        "command.reload": "从环境变量和 .env 重新加载配置",
        "command.theme": "切换颜色主题",
        "command.update": "检查并安装更新",
        "command.auto_update": "开启或关闭自动更新",
        "command.changelog": "在浏览器中打开更新日志",
        "command.version": "显示版本",
        "command.feedback": "提交错误报告或功能请求",
        "command.docs": "在浏览器中打开文档",
        "command.quit": "退出应用程序",
        "command.help": "显示帮助信息",
        "command.unknown": "未知命令：{command}",
        "command.language": "切换界面语言",
        "help.title": "命令",
        "help.interactive_features": "交互功能",
        "help.submit": "提交消息",
        "help.insert_newline": "插入换行符",
        "help.open_editor": "在外部编辑器中打开提示",
        "help.toggle_auto_approve": "切换自动批准模式",
        "help.autocomplete_files": "自动补全文件并注入内容",
        "help.slash_commands": "斜杠命令 (/help, /clear, /quit)",
        "help.shell_commands": "切换到shell模式, 退出shell模式请按'esc'",
        "help.docs": "文档",
        "error.missing_dependencies": "缺少必需的 CLI 依赖项！",
        "error.install_required": "以下包是使用 deepagents CLI 所需的：",
        "error.install_command": "请使用以下命令安装：",
        "error.install_all": "或安装所有依赖项：",
        "error.ripgrep_not_found": "未找到 ripgrep (rg)。请安装它以获得更快的代码搜索。",
        "error.tavily_not_configured": "Tavily API 密钥未配置。请设置 TAVILY_API_KEY 环境变量。",
        "error.package_not_installed": "未安装必需的包：{package}。使用以下命令安装：pip install 'deepagents[cli]'",
        "error.config_read_failed": "读取配置文件失败",
        "error.config_write_failed": "写入配置文件失败",
        "error.language_save_failed": "保存语言首选项失败",
        "success.language_changed": "语言已更改为 {language}",
        "success.config_reloaded": "配置重新加载成功",
        "success.new_thread": "已开始新对话：{thread_id}",
        "success.theme_changed": "主题已更改为 {theme}",
        "success.update_available": "有可用更新：{version}",
        "success.up_to_date": "已是最新版本",
        "notification.press_escape": "按 Escape 取消",
        "notification.select_option": "选择一个选项",
        "notification.loading_please_wait": "加载中，请稍候...",
        "language.select_title": "选择语言",
        "language.english": "English (英语)",
        "language.chinese": "中文 (Chinese)",
        "language.current": "当前",
        "language.preview": "预览",
        "language.select": "选择",
        "language.cancel": "取消",
        "theme.select_title": "选择主题",
        "theme.preview": "预览",
        "theme.select": "选择",
        "theme.cancel": "取消",
        "thread.select_title": "选择对话",
        "thread.no_threads": "未找到对话",
        "thread.loading": "加载对话中...",
        "thread.created": "创建时间",
        "thread.updated": "更新时间",
        "thread.last_used": "最后使用",
        "model.select_title": "选择模型",
        "model.loading": "加载模型中...",
        "model.no_models": "没有可用的模型",
        "model.current": "当前",
        "model.default": "默认",
        "model.set_default": "设为默认",
        "model.clear_default": "清除默认",
        "mcp.title": "MCP 服务器和工具",
        "mcp.no_servers": "未配置 MCP 服务器",
        "mcp.tools_loaded": "个工具已加载",
        "mcp.server": "服务器",
        "mcp.tools": "工具",
        "tokens.title": "Token 使用情况",
        "tokens.total": "总计",
        "tokens.prompt": "提示",
        "tokens.completion": "完成",
        "tokens.approximate": "约",
        "tokens.usage_with_limit": "{used} / {limit} Token ({pct}%)",
        "tokens.usage_simple": "已使用 {used} Token",
        "tokens.system_tools_fixed": "├ 系统提示词 + 工具：约 {tokens}（固定）",
        "tokens.conversation": "└ 对话内容：约 {tokens}",
        "tokens.no_usage_yet": "暂无 token 使用数据",
        "tokens.context_window": "上下文窗口 {limit} Token",
        "update.checking": "检查更新中...",
        "update.installing": "安装更新中...",
        "update.restart_required": "重启 CLI 以使用新版本",
        "auto_update.enabled": "自动更新已启用",
        "auto_update.disabled": "自动更新已禁用",
        "approval_menu.title": "需要批准",
        "approval_menu.approve_short": "批准",
        "approval_menu.reject_short": "拒绝",
        "approval_menu.auto_approve_short": "自动",
        "approval_menu.expand_short": "展开",
        "approval_menu.collapse_short": "折叠",
        "chat_input.placeholder": "输入消息... (@文件 /命令)",
        "chat_input.shell_mode": "Shell 模式",
        "chat_input.command_mode": "命令模式",
        "file_ops.file_not_found": "文件未找到：{path}",
        "file_ops.directory_not_found": "目录未找到：{path}",
        "file_ops.permission_denied": "权限被拒绝：{path}",
        "search.no_results": "未找到结果",
        "search.searching": "搜索中...",
        "search.results_count": "找到 {count} 个结果",
        "model.title": "选择模型",
        "model.current_model": "当前: {model}",
        "model.filter_placeholder": "输入筛选或输入 provider:model...",
        "model.target_primary": "主模型",
        "model.target_memory": "副模型",
        "model.target_short": "目标：{target}",
        "model.navigate": "导航",
        "model.select_action": "选择",
        "model.set_default_action": "设为默认",
        "model.cancel_action": "取消",
        "model.no_credentials": "无凭证",
        "model.profile_params": "配置参数",
        "model.default_model": "默认模型",
        "model.loading": "加载模型中…",
        "model.no_matching": "没有匹配的模型",
        "model.profile_not_available": "模型配置不可用 :(",
        "model.no_selected": "未选择模型",
        "model.could_not_load": "无法加载配置详情",
        "model.load_error": "无法加载模型列表。请检查提供商包和 config.toml。",
        "model.default_cleared": "已清除默认",
        "model.default_set_to": "默认已设置为 {spec}",
        "model.default_target_cleared": "已清除{target}默认",
        "model.default_target_set_to": "已将{target}默认设置为 {spec}",
        "model.default_primary_only": "默认模型仅适用于主模型。请使用 '/model 2 <provider:model>' 在当前会话切换记忆模型。",
        "model.failed_clear": "清除默认失败",
        "model.failed_save": "保存默认失败",
        "model.failed_target_clear": "清除{target}默认失败",
        "model.failed_target_save": "保存{target}默认失败",
        "model.register_action": "注册",
        "model.register_title": "注册新模型",
        "model.register_provider_label": "提供商名称",
        "model.register_provider_placeholder": "例如 ollama, deepseek, openrouter",
        "model.register_model_label": "模型名称",
        "model.register_model_placeholder": "例如 qwen3:4b, deepseek-chat",
        "model.register_apikey_label": "API Key 环境变量",
        "model.register_apikey_hint": "无 API Key 的提供商请留空（如 Ollama）",
        "model.register_apikey_placeholder": "例如 DEEPSEEK_API_KEY",
        "model.register_baseurl_label": "基础 URL",
        "model.register_baseurl_placeholder": "例如 https://api.deepseek.com/v1",
        "model.register_max_input_tokens_label": "最大输入 Tokens",
        "model.register_max_input_tokens_placeholder": "例如 128000",
        "model.register_error_max_input_tokens_integer": "最大输入 Tokens 必须是整数",
        "model.register_error_max_input_tokens_positive": "最大输入 Tokens 必须是正数",
        "model.register_error_baseurl": "Base URL 不能为空",
        "model.register_classpath_label": "Class Path（可选）",
        "model.register_classpath_hint": "用于自定义 BaseChatModel 子类的提供商",
        "model.register_classpath_placeholder": "例如 langchain_ollama:ChatOllama",
        "model.register_next_field": "下一字段",
        "model.register_submit": "提交",
        "model.register_error_provider": "提供商名称不能为空",
        "model.register_error_model": "模型名称不能为空",
        "model.register_error_colon": "提供商和模型名称中不要包含 ':'，请分别填写",
        "model.register_error_classpath": "Class Path 必须使用 'module.path:ClassName' 格式",
        "model.register_error_save": "保存配置失败",
        "model.register_success": "已注册 {spec}",
        "mcp.title": "MCP 服务器",
        "mcp.servers_count": "{count} 个服务器",
        "mcp.tools_count": "{count} 个工具",
        "mcp.no_servers_configured": "未配置 MCP 服务器。\n使用 `--mcp-config` 加载服务器。",
        "mcp.navigate": "导航",
        "mcp.expand_collapse": "展开/折叠",
        "mcp.close": "关闭",
        "thread.title": "选择对话",
        "thread.current_thread": "当前: {thread_id}",
        "thread.filter_placeholder": "输入筛选对话...",
        "thread.navigate": "导航",
        "thread.select_action": "选择",
        "thread.focus_options": "聚焦选项",
        "thread.toggle_option": "切换选项",
        "thread.delete_action": "删除",
        "thread.cancel_action": "取消",
        "thread.showing_limit": "显示最近 {limit} 个对话。设置 DA_CLI_RECENT_THREADS 覆盖。",
        "thread.column_thread_id": "对话 ID",
        "thread.column_agent": "代理",
        "thread.column_messages": "消息",
        "thread.column_created": "创建时间",
        "thread.column_updated": "更新时间",
        "thread.column_branch": "分支",
        "thread.column_location": "位置",
        "thread.column_prompt": "提示",
        "thread.sort_by": "按 {field} 排序",
        "thread.sort_updated": "更新时间",
        "thread.sort_created": "创建时间",
        "thread.delete_confirm": "删除对话 {thread_id}？",
        "thread.delete_help": "Enter 确认，Esc 取消",
        "thread.relative_time": "相对时间",
        "thread.options": "选项",
        "thread.options_help": "Tab 切换排序和列显示。列可见性在会话间保持。",
        "thread.filter_placeholder": "输入筛选对话...",
        "ask.select": "选择",
        "ask.enter_continue": "Enter 继续",
        "ask.switch_question": "Tab/Shift+Tab 切换问题",
        "ask.cancel": "Esc 取消",
        "ask.type_answer": "输入您的答案...",
        "diff.no_changes": "未检测到更改",
        "diff.truncated": "... (已截断)",
        "queue.discarded": "排队消息已丢弃",
        "queue.moved_to_input": "排队消息已移至输入框",
        "queue.discarded_input_not_empty": "排队消息已丢弃（输入框非空）",
        "thread.sort_save_failed": "无法保存排序偏好",
        "tool.details_not_available": "工具详情不可用",
        "tool.no_changes": "无更改可显示",
        "tool.removing": "删除中：",
        "tool.adding": "添加中：",
        "tool.more_lines": "... (还有 {count} 行)",
        "tool.more_chars": "... (还有 {count} 个字符)",
        "tool.plan_preview": "计划预览",
        "tool.interrupted_by_error": "因错误中断",
        "tool.interrupted_by_user": "已被用户中断",
        "shell.command_completed": "命令执行完成",
        "shell.command_completed_no_output": "命令执行完成（无输出）",
        "shell.command_timeout": "命令执行超时（{seconds} 秒限制）",
        "shell.command_interrupted": "命令已中断",
        "shell.command_not_found": "未找到命令：{command}",
        "shell.command_failed": "命令执行失败：{error}",
        "shell.exit_code": "退出码：{code}",
        "trace.no_active_session": "当前无活动会话。",
        "trace.resolve_failed": "无法解析 LangSmith 线程链接。",
        "trace.not_configured": "LangSmith tracing 未配置。请设置 LANGSMITH_API_KEY 和 LANGSMITH_TRACING=true 后重试。",
        "skill.usage": "用法：/skill:<name> [args]",
        "skill.not_found": "未找到技能：{skill}",
        "skill.load_permission_error": "无法加载技能：{skill}。权限错误：{error}",
        "skill.load_filesystem_error": "无法加载技能：{skill}。文件系统错误：{error}",
        "skill.load_unexpected_error": "加载技能出错：{skill}。异常：{error}",
        "skill.content_unreadable": "无法读取技能内容：{skill}。请检查 SKILL.md 是否存在、可读且为 UTF-8 编码。",
        "skill.content_empty": "技能“{skill}”的 SKILL.md 为空。请先补充说明后再调用。",
        "offload.nothing_to_offload": "暂无可压缩内容，请先开始对话",
        "offload.cannot_while_running": "代理运行中，暂时无法压缩上下文",
        "offload.failed_read_state": "读取状态失败：{error}",
        "offload.failed": "压缩失败：{error}",
        "agent.not_configured_session": "当前会话未配置代理。",
        "agent.error": "代理错误：{error}",
        "queue.process_failed": "处理排队消息失败：{message}",
        "thread.resumed": "已恢复对话：{thread_id}",
        "thread.history_load_failed": "加载历史失败：{error}",
        "thread.switch_no_active_agent": "无法切换对话：当前无活动代理",
        "thread.switch_no_active_session": "无法切换对话：当前无活动会话",
        "thread.already_on": "已在该对话：{thread_id}",
        "model.switch_in_progress": "模型切换进行中，请稍候。",
        "model.switch_requires_server": "切换模型需要服务端会话支持。",
        "model.missing_credentials": "缺少凭据：{detail}",
        "model.already_using": "当前已使用 {model}",
        "model.switch_failed": "切换模型失败：{error}",
        "model.preference_save_failed": "本次会话已切换模型，但无法保存偏好。请检查 ~/.invincat/ 的权限。",
        "model.switched_to": "已切换到 {model}",
        "model.memory_switched_to": "记忆模型已切换到 {model}",
        "loading.hint": "({duration}, esc 中断)",
        "loading.paused_at": "（已暂停于 {duration}）",
        "theme.select_title": "选择主题",
        "theme.preview": "预览",
        "theme.select": "选择",
        "theme.cancel": "取消",
        "theme.current": "(当前)",
        "message.no_results": "无结果",
        "message.more_results": "还有 {count} 个结果",
        "message.error": "错误：",
        "version.cli_line": "deepagents-cli 版本：{version}",
        "version.cli_unknown": "deepagents-cli 版本：unknown",
        "version.sdk_line": "deepagents（SDK）版本：{version}",
        "version.sdk_unknown": "deepagents（SDK）版本：unknown",
        "app.session_init_failed": "会话初始化失败。某些功能可能不可用。",
        "app.skill_scan_failed": "无法扫描技能目录。某些 /skill: 命令可能不可用。",
        "app.skill_discovery_failed": "技能发现意外失败。/skill: 命令可能无法工作。请查看日志了解详情。",
        "app.no_threads_agent": "'{agent}' 没有之前的对话，开始新对话。",
        "app.no_threads": "没有之前的对话，开始新对话。",
        "app.thread_not_found": "对话 '{thread_id}' 未找到。您是指：{similar}？",
        "app.thread_not_found_simple": "对话 '{thread_id}' 未找到。",
        "app.thread_lookup_failed": "无法查找对话历史。开始新会话。",
        "app.updating_to": "正在更新到 v{version}...",
        "app.updated_to": "已更新到 v{version}。请重启以使用新版本。",
        "app.auto_update_failed": "自动更新失败。请手动运行：{command}",
        "app.update_available": "有可用更新：v{latest}（当前：v{current}）。运行：{command}\n启用自动更新：/auto-update",
        "app.update_available_upgrading": "有可用更新：v{latest}（当前：v{current}），正在升级...",
        "app.auto_update_failed_with_detail": "自动更新失败{detail}\n请手动运行：{command}",
        "app.update_failed_with_error": "更新失败：{error}",
        "app.update_failed": "更新意外失败。",
        "app.auto_update_not_available": "可编辑安装不支持自动更新。",
        "app.auto_updates_enabled": "自动更新已启用。",
        "app.auto_updates_disabled": "自动更新已禁用。",
        "app.auto_update_toggle_failed": "自动更新切换失败：{error}",
        "app.thread_switch_in_progress": "对话切换进行中。请稍候。",
        "app.press_to_quit": "再次按 {shortcut} 退出",
        "app.external_editor_failed": "外部编辑器失败。请检查 $VISUAL/$EDITOR。",
        "app.model_switch_pending": "模型将在当前任务完成后切换。",
        "app.theme_not_saved": "主题已应用于本次会话，但无法保存。请查看日志了解详情。",
        "app.language_changed_to": "语言已更改为 {language}",
        "app.thread_switch_pending": "对话将在当前任务完成后切换。",
        "chat.attach_failed": "无法附加 {type}：{name}",
        "thread.delete_failed": "删除对话 {thread_id} 失败",
    },
}


class I18n:
    """Internationalization manager for the CLI.

    This class manages language preferences and provides translation services
    for all user-facing text in the application.

    Attributes:
        current_language: The currently active language.
    """

    def __init__(self, language: Language = DEFAULT_LANGUAGE) -> None:
        """Initialize the i18n manager.

        Args:
            language: The initial language to use.
        """
        self._language = language
        self._translations = TRANSLATIONS

    @property
    def language(self) -> Language:
        """Get the current language."""
        return self._language

    @language.setter
    def language(self, value: Language) -> None:
        """Set the current language.

        Args:
            value: The language to set.
        """
        if value not in Language:
            logger.warning(
                "Invalid language '%s', falling back to default '%s'",
                value,
                DEFAULT_LANGUAGE,
            )
            value = DEFAULT_LANGUAGE
        self._language = value
        logger.debug("Language changed to: %s", value)

    def t(self, key: str, **kwargs: Any) -> str:
        """Translate a key to the current language.

        Args:
            key: The translation key (e.g., "welcome.ready").
            **kwargs: Format arguments for string interpolation.

        Returns:
            The translated string, or the key if not found.
        """
        translations = self._translations.get(self._language, {})
        text = translations.get(key)

        if text is None:
            translations = self._translations.get(DEFAULT_LANGUAGE, {})
            text = translations.get(key, key)
            if text == key:
                logger.warning("Translation key not found: %s", key)

        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError) as e:
                logger.warning(
                    "Failed to format translation key '%s' with args %s: %s",
                    key,
                    kwargs,
                    e,
                )
                return text

        return text

    def get_tip(self, index: int) -> str:
        """Get a welcome tip by index.

        Args:
            index: The tip index (1-13).

        Returns:
            The translated tip text.
        """
        return self.t(f"welcome.tips.{index}")

    def get_all_tips(self) -> list[str]:
        """Get all welcome tips.

        Returns:
            List of all translated tip texts.
        """
        language_tips = self._translations.get(self._language, {})
        fallback_tips = self._translations.get(DEFAULT_LANGUAGE, {})
        tips: list[str] = []
        for i in range(1, 13):
            key = f"welcome.tips.{i}"
            tip = language_tips.get(key) or fallback_tips.get(key)
            if tip:
                tips.append(tip)
        return tips

    def get_language_name(self, language: Language) -> str:
        """Get the display name for a language.

        Args:
            language: The language to get the name for.

        Returns:
            The display name of the language.
        """
        if language == Language.EN:
            return self.t("language.english")
        elif language == Language.ZH:
            return self.t("language.chinese")
        return language.value


_i18n_instance: I18n | None = None


def get_i18n() -> I18n:
    """Get the global i18n instance.

    Returns:
        The global I18n instance.
    """
    global _i18n_instance
    if _i18n_instance is None:
        _i18n_instance = I18n()
    return _i18n_instance


def set_language(language: Language) -> None:
    """Set the global language.

    Args:
        language: The language to set.
    """
    i18n = get_i18n()
    i18n.language = language


def t(key: str, **kwargs: Any) -> str:
    """Translate a key using the global i18n instance.

    This is a convenience function that wraps get_i18n().t().

    Args:
        key: The translation key.
        **kwargs: Format arguments for string interpolation.

    Returns:
        The translated string.
    """
    return get_i18n().t(key, **kwargs)


def load_language_from_config(config_path: Path | None = None) -> Language:
    """Load language preference from config file.

    Args:
        config_path: Path to config file. Defaults to ~/.invincat/config.toml.

    Returns:
        The configured language, or default if not configured.
    """
    import tomllib

    if config_path is None:
        try:
            config_path = Path.home() / ".invincat" / "config.toml"
        except RuntimeError:
            logger.debug("Could not determine home directory")
            return DEFAULT_LANGUAGE

    if not config_path.exists():
        logger.debug("Config file not found at %s, using default language", config_path)
        return DEFAULT_LANGUAGE

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)

        lang_value = data.get("general", {}).get("language")
        if lang_value:
            try:
                return Language(lang_value)
            except ValueError:
                logger.warning(
                    "Invalid language value '%s' in config, using default",
                    lang_value,
                )
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to read language from config: %s", e)

    return DEFAULT_LANGUAGE


def save_language_to_config(
    language: Language, config_path: Path | None = None
) -> bool:
    """Save language preference to config file.

    Args:
        language: The language to save.
        config_path: Path to config file. Defaults to ~/.invincat/config.toml.

    Returns:
        True if save succeeded, False otherwise.
    """
    import tomllib
    import tomli_w

    if config_path is None:
        try:
            config_path = Path.home() / ".invincat" / "config.toml"
        except RuntimeError:
            logger.error("Could not determine home directory for config path")
            return False

    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            logger.warning("Failed to read existing config, will overwrite: %s", e)
            data = {}

    if "general" not in data:
        data["general"] = {}

    data["general"]["language"] = language.value

    try:
        with config_path.open("wb") as f:
            tomli_w.dump(data, f)
        logger.debug("Saved language preference to %s", config_path)
        return True
    except OSError as e:
        logger.error("Failed to save language preference: %s", e)
        return False
