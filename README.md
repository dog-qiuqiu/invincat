![](data/invincat.jpg)

# Invincat CLI

基于 deepagents-cli 二次开发的终端 AI 编程助手，提供强大的 AI 交互能力。

![](data/cli.png)

## 特性

- **交互式终端界面** - 基于 Textual 框架的现代化 TUI 界面
- **多模型支持** - 支持 OpenAI、Anthropic、Google GenAI、OpenRouter 等多种模型提供商
- **智能文件操作** - 内置文件读取、编辑、创建等工具，支持代码差异对比
- **Shell 命令执行** - 输入 `!` 前缀直接执行 shell 命令
- **技能系统** - 可扩展的技能工作流，存储在本地供复用
- **记忆系统** - 自动/手动保存对话记忆，跨会话保持上下文
- **多语言界面** - 支持中文/英文界面切换
- **会话管理** - 支持多会话切换和历史记录浏览

## 安装

### 环境要求

- Python 3.11+
- pip 或 pipx

### 安装步骤

```bash
git clone https://github.com/dog-qiuqiu/invincat.git
cd invincat
pip install -e .
```

## 快速开始

安装完成后，运行以下命令启动 CLI：

```bash
invincat-cli
```

首次启动需要配置 API Key，按 `Ctrl+N` 或执行 `/model` 命令打开模型配置界面。

## 模型配置

### 添加新模型

1. 在 CLI 中执行 `/model` 打开模型管理界面
2. 按下 `Ctrl+N` 注册新模型
3. 填写以下信息：
   - **提供商名称**: 如 `openai`、`anthropic`、`google_genai`、`openrouter`
   - **模型名称**: 如 `gpt-4o`、`claude-3-opus`
   - **API Key**: 环境变量名称或直接填写 API Key
   - **Base URL**: API 端点地址（可选）
   - **最大输入 Tokens**: 上下文窗口大小（可选）

![](data/model.png)

4. 提交后在模型列表中选择即可生效

### 支持的提供商

| 提供商 | 说明 |
|--------|------|
| `openai` | OpenAI 官方 API 或兼容接口（如 DeepSeek、智谱等） |
| `anthropic` | Anthropic Claude API |
| `google_genai` | Google Generative AI |
| `openrouter` | OpenRouter 聚合服务 |

### 环境变量

| 变量名 | 说明 |
|--------|------|
| `OPENAI_API_KEY` | OpenAI API Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `GOOGLE_API_KEY` | Google API Key |
| `OPENROUTER_API_KEY` | OpenRouter API Key |
| `TAVILY_API_KEY` | Tavily 搜索 API Key |

## 斜杠命令

输入 `/` 后按 `Tab` 键可查看所有可用命令。

### 会话管理

| 命令 | 说明 |
|------|------|
| `/clear` | 清除聊天并开始新对话 |
| `/threads` | 浏览并恢复之前的对话 |
| `/quit` 或 `/q` | 退出应用程序 |

### 模型与配置

| 命令 | 说明 |
|------|------|
| `/model` | 切换或配置模型 |
| `/theme` | 切换颜色主题 |
| `/language` | 切换界面语言（中文/英文） |
| `/reload` | 重新加载配置 |
| `/tokens` | 查看 token 使用情况 |

### 工具与功能

| 命令 | 说明 |
|------|------|
| `/mcp` | 显示活动的 MCP 服务器和工具 |
| `/editor` | 在外部编辑器中打开提示 |
| `/offload` 或 `/compact` | 释放上下文窗口空间 |
| `/remember` | 从对话中更新记忆和技能 |
| `/auto-memory` | 配置自动记忆更新 |
| `/skill-creator` | 创建有效代理技能的指南 |

### 其他

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/version` | 显示版本 |
| `/docs` | 在浏览器中打开文档 |
| `/feedback` | 提交错误报告或功能请求 |
| `/changelog` | 在浏览器中打开更新日志 |
| `/trace` | 在 LangSmith 中打开当前对话 |

## 快捷键

### 全局快捷键

| 快捷键 | 功能 |
|--------|------|
| `Escape` | 中断当前操作 |
| `Ctrl+C` | 退出或中断 |
| `Ctrl+D` | 退出应用程序 |
| `Ctrl+T` 或 `Shift+Tab` | 切换自动批准模式 |
| `Ctrl+O` | 切换工具输出显示 |
| `Ctrl+X` | 在外部编辑器中编辑 |

### 输入框快捷键

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` / `Alt+Enter` / `Ctrl+Enter` | 插入换行符 |
| `Tab` | 自动补全命令/文件路径 |
| `@` | 引用文件内容 |

### Shell 模式

输入 `!` 开头的命令可直接执行 shell 命令：

```
!ls -la
!git status
```

按 `Esc` 退出 shell 模式。

## 技能系统

技能是可复用的工作流程模板。

### 技能位置

- 内置技能: `invincat_cli/built_in_skills/`
- 用户技能: `~/.invincat/agent/skills/`
- 项目技能: `.invincat/skills/`

### 使用技能

```
/skill:web-research 搜索主题
/skill:code-review 检查代码
```

## 记忆系统

Agent 可以记住对话中的重要信息。

### 记忆文件位置

- 全局记忆: `~/.invincat/agent/AGENTS.md`
- 项目记忆: `{project_root}/.invincat/AGENTS.md`（仅在 Git 仓库中生效）

### 手动更新记忆

使用 `/remember` 命令主动保存信息到记忆中。

### 自动记忆更新

Agent 内置自动记忆更新机制：

1. **周期性检查**: 每隔一定轮数的对话后，自动提示模型检查是否有值得保存的信息
2. **退出标记**: 退出 CLI 时写入标记文件，下次启动新会话时提前触发记忆检查
3. **零额外开销**: 提示注入到现有系统提示词中，不增加额外的 API 调用

### 配置自动记忆

执行 `/auto-memory` 命令打开配置界面，可调整以下选项：

- **自动记忆**: 启用/禁用自动记忆更新
- **检查间隔**: 每隔多少轮对话触发一次（5/10/15/20/30）
- **退出标记**: 退出时是否写入标记

也可在 `~/.invincat/config.toml` 中手动配置：

```toml
[auto_memory]
enabled = true    # 启用/禁用自动记忆更新（默认: true）
interval = 10     # 检查间隔轮数（默认: 10）
on_exit = true    # 退出时写入标记（默认: true）
```

## 项目结构

```
invincat_cli/
├── __init__.py              # 包入口
├── __main__.py              # CLI 入口点
├── main.py                  # 主程序
├── app.py                   # 应用主逻辑
├── agent.py                 # Agent 核心
├── config.py                # 配置管理
├── model_config.py          # 模型配置
├── server.py                # 服务器相关
├── sessions.py              # 会话管理
├── tools.py                 # 工具集
├── file_ops.py              # 文件操作
├── mcp_tools.py             # MCP 工具集成
├── clipboard.py             # 剪贴板操作
├── editor.py                # 外部编辑器
├── auto_memory.py           # 自动记忆
├── offload.py               # 上下文卸载
├── output.py                # 输出格式化
├── formatting.py            # 格式化工具
├── input.py                 # 输入处理
├── ui.py                    # UI 相关
├── theme.py                 # 主题管理
├── i18n.py                  # 国际化
├── subagents.py             # 子代理
├── hooks.py                 # 钩子系统
├── command_registry.py      # 命令注册
├── remote_client.py         # 远程客户端
├── server_manager.py        # 服务器管理
├── server_graph.py          # 服务器图
├── token_state.py           # Token 状态
├── local_context.py         # 本地上下文
├── project_utils.py         # 项目工具
├── media_utils.py           # 媒体工具
├── unicode_security.py      # Unicode 安全
├── update_check.py          # 更新检查
├── non_interactive.py       # 非交互模式
├── mcp_trust.py             # MCP 信任
├── _env_vars.py             # 环境变量
├── _cli_context.py          # CLI 上下文
├── _debug.py                # 调试
├── _server_config.py        # 服务器配置
├── _session_stats.py        # 会话统计
├── _testing_models.py       # 测试模型
├── _ask_user_types.py       # 用户输入类型
├── _version.py              # 版本信息
├── built_in_skills/         # 内置技能
│   ├── remember/
│   └── skill-creator/
├── integrations/            # 集成
│   ├── sandbox_factory.py
│   └── sandbox_provider.py
├── skills/                  # 技能加载
│   ├── __init__.py
│   ├── commands.py
│   └── load.py
├── widgets/                 # UI 组件
│   ├── __init__.py
│   ├── messages.py
│   ├── chat_input.py
│   ├── status.py
│   ├── loading.py
│   ├── welcome.py
│   ├── diff.py
│   ├── approval.py
│   ├── ask_user.py
│   ├── autocomplete.py
│   ├── auto_memory_config.py
│   ├── history.py
│   ├── language_selector.py
│   ├── mcp_viewer.py
│   ├── model_selector.py
│   ├── theme_selector.py
│   ├── thread_selector.py
│   ├── tool_renderers.py
│   ├── tool_widgets.py
│   ├── message_store.py
│   ├── _links.py
│   └── app.tcss             # 样式
└── system_prompt.md          # 系统提示词
```

## 配置文件

配置文件位于 `~/.invincat/config.toml`：

```toml
[models]
recent = "openai:gpt-4o"

[models.providers.openai]
models = ["gpt-4o", "gpt-4-turbo"]

[models.providers.openai.params]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[general]
language = "zh"  # 界面语言: zh / en
```

## 开发与测试

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行单元测试
pytest tests/unit/

# 运行特定测试
pytest tests/unit/test_file_ops.py -v

# 生成覆盖率报告
pytest --cov=invincat_cli --cov-report=html
```

### 代码质量检查

```bash
# 检查代码格式
ruff check .

# 自动修复格式问题
ruff check --fix .

# 格式化代码
ruff format .
```
