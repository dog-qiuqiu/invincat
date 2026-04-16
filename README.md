![](data/invincat.jpg)
----------------------------------------

# 项目介绍

基于 deepagents-cli 二次开发的 Agent CLI 工具，提供强大的 AI 编程助手功能。

![](data/cli.png)

# 安装

```bash
git clone https://github.com/dog-qiuqiu/invincat.git
cd invincat
pip install -e .
```

# 快速开始

安装完成后，运行以下命令启动 CLI：

```bash
invincat-cli
```

首次使用需要配置 API Key，请参考 [模型配置](#模型配置) 章节。

# 模型配置

## 打开模型界面

CLI 中执行以下命令打开模型管理界面：

```
/model
```

## 注册新模型

按下 `Ctrl+N` 注册新模型，按需填写以下信息：

- **提供商名称**: 根据接口类型选择，例如 `openai`、`anthropic` 等
- **模型名称**: 模型的具体名称，如 `gpt-4o`、`claude-3-opus` 等
- **API Key**: 环境变量名称或直接填写 API Key
- **Base URL**: API 端点地址（可选）
- **最大输入 Tokens**: 模型的上下文窗口大小（可选）

![](data/model.png)

模型提交后，在模型列表中选择新注册的模型即可生效。

## 支持的提供商

| 提供商 | 说明 |
|--------|------|
| `openai` | OpenAI 官方 API 或兼容接口（如 DeepSeek、智谱等） |
| `anthropic` | Anthropic Claude API |
| `google_genai` | Google Generative AI |
| `openrouter` | OpenRouter 聚合服务 |

# 斜杠命令

CLI 提供丰富的斜杠命令，输入 `/` 后按 `Tab` 键可查看所有可用命令。

## 会话管理

| 命令 | 说明 |
|------|------|
| `/clear` | 清除聊天并开始新对话 |
| `/threads` | 浏览并恢复之前的对话 |
| `/quit` 或 `/q` | 退出应用程序 |

## 模型与配置

| 命令 | 说明 |
|------|------|
| `/model` | 切换或配置模型 |
| `/theme` | 切换颜色主题 |
| `/language` | 切换界面语言（中文/英文） |
| `/reload` | 重新加载配置 |
| `/tokens` | 查看 token 使用情况 |

## 工具与功能

| 命令 | 说明 |
|------|------|
| `/mcp` | 显示活动的 MCP 服务器和工具 |
| `/editor` | 在外部编辑器中打开提示 |
| `/offload` 或 `/compact` | 释放上下文窗口空间 |
| `/remember` | 从对话中更新记忆和技能 |
| `/auto-memory` | 配置自动记忆更新 |
| `/skill-creator` | 创建有效代理技能的指南 |

## 其他

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/version` | 显示版本 |
| `/docs` | 在浏览器中打开文档 |
| `/feedback` | 提交错误报告或功能请求 |
| `/changelog` | 在浏览器中打开更新日志 |
| `/trace` | 在 LangSmith 中打开当前对话 |

# 快捷键

## 全局快捷键

| 快捷键 | 功能 |
|--------|------|
| `Escape` | 中断当前操作 |
| `Ctrl+C` | 退出或中断 |
| `Ctrl+D` | 退出应用程序 |
| `Ctrl+T` 或 `Shift+Tab` | 切换自动批准模式 |
| `Ctrl+O` | 切换工具输出显示 |
| `Ctrl+X` | 在外部编辑器中编辑 |

## 输入框快捷键

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` / `Alt+Enter` / `Ctrl+Enter` | 插入换行符 |
| `Tab` | 自动补全命令/文件路径 |
| `@` | 引用文件内容 |

## Shell 模式

输入 `!` 开头的命令可直接执行 shell 命令：

```
!ls -la
!git status
```

按 `Esc` 退出 shell 模式。

# 开发与测试

## 运行测试

项目使用 pytest 进行测试：

```bash
# 安装测试依赖
pip install -e ".[test]"

# 运行所有测试
pytest

# 运行单元测试
pytest tests/unit/

# 运行特定测试文件
pytest tests/unit/test_file_ops.py -v

# 生成测试覆盖率报告
pytest --cov=invincat_cli --cov-report=html
```

## 代码质量检查

项目使用 ruff 进行代码格式化和检查：

```bash
# 检查代码格式
ruff check .

# 自动修复格式问题
ruff check --fix .

# 格式化代码
ruff format .
```

## 项目结构

重构后的项目结构更加模块化：

```
invincat_cli/
├── app/                    # 应用主逻辑（拆分自 app.py）
│   ├── __init__.py
│   ├── core.py           # 核心应用类
│   ├── handlers.py       # 事件处理器
│   ├── commands.py       # 斜杠命令实现
│   └── utils.py          # 应用工具函数
├── config/               # 配置模块（拆分自 config.py）
│   ├── __init__.py
│   ├── settings.py      # Settings 类
│   ├── bootstrap.py     # 环境引导
│   ├── models.py        # 模型创建
│   └── utils.py         # 配置工具
├── widgets/             # UI 组件
│   ├── messages/        # 消息组件（拆分自 messages.py）
│   │   ├── __init__.py
│   │   ├── base.py     # 基础消息类
│   │   ├── user.py     # 用户消息
│   │   ├── assistant.py # 助手消息
│   │   ├── tool_call.py # 工具调用消息
│   │   └── diff.py     # 差异显示消息
│   └── ...             # 其他组件
├── file_ops.py          # 文件操作模块（已重构）
└── ...                 # 其他模块
```

# 配置文件

配置文件位于 `~/.invincat/config.toml`，包含以下内容：

```toml
[models]
recent = "openai:gpt-4o"  # 最近使用的模型

[models.providers.openai]
models = ["gpt-4o", "gpt-4-turbo"]

[models.providers.openai.params]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[general]
language = "zh"  # 界面语言: zh (中文) / en (英文)
```

# 环境变量

| 变量名 | 说明 |
|--------|------|
| `OPENAI_API_KEY` | OpenAI API Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `GOOGLE_API_KEY` | Google API Key |
| `OPENROUTER_API_KEY` | OpenRouter API Key |
| `TAVILY_API_KEY` | Tavily 搜索 API Key |

# 技能系统

技能是可复用的工作流程，存储在以下位置：

- 内置技能: `invincat_cli/built_in_skills/`
- 用户技能: `~/.invincat/agent/skills/`
- 项目技能: `.invincat/skills/`

使用 `/skill:<技能名>` 执行技能，例如：

```
/skill:web-research 搜索主题
```

# 记忆系统

Agent 可以记住对话中的重要信息，存储在：

- 全局记忆: `~/.invincat/agent/AGENTS.md`
- 项目记忆: `.invincat/AGENTS.md`

## 手动更新

使用 `/remember` 命令可以主动保存信息到记忆中。

## 自动记忆更新

Agent 内置自动记忆更新机制，无需手动操作即可将重要信息保存到记忆文件。

### 工作原理

自动记忆通过中间件实现，在系统提示词中周期性注入"记忆检查提示"，引导模型自主评估是否需要将对话中的重要信息保存到 AGENTS.md：

1. **周期性检查**：每隔一定轮数的对话后，自动提示模型检查是否有值得保存的信息
2. **退出标记**：退出 CLI 时写入标记文件，下次启动新会话时提前触发记忆检查，确保跨会话信息不丢失
3. **零额外开销**：提示注入到现有系统提示词中，不增加额外的 API 调用

### 项目级记忆文件生成条件

当在 Git 仓库内运行 CLI 时，系统会自动为模型提供项目级记忆文件路径：

- **检测条件**：必须在 Git 仓库内（存在 `.git` 目录）
- **路径位置**：`{project_root}/.invincat/AGENTS.md`
- **生成逻辑**：
  1. 系统在自动记忆提示中包含项目级路径
  2. 模型根据对话内容自主判断是否需要创建/更新
  3. 当模型使用 `write_file` 工具时，系统会自动创建父目录

### 配置

在 CLI 中执行 `/auto-memory` 命令打开配置界面，可交互式调整以下选项：

- **自动记忆**：启用/禁用自动记忆更新
- **检查间隔**：每隔多少轮对话触发一次记忆检查（可选 5/10/15/20/30）
- **退出标记**：退出时是否写入标记，下次会话提前触发检查

也可以直接在 `~/.invincat/config.toml` 中手动配置：

```toml
[auto_memory]
enabled = true    # 启用/禁用自动记忆更新（默认: true）
interval = 10     # 每隔多少轮对话触发一次记忆检查（默认: 10）
on_exit = true    # 退出时是否写入标记，下次启动提前检查（默认: true）
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | `true` | 是否启用自动记忆更新 |
| `interval` | int | `10` | 触发记忆检查的对话轮数间隔，最小为 1 |
| `on_exit` | bool | `true` | 退出时是否写入标记文件，使下次会话提前触发检查 |

# 常见问题

## 模型切换后不生效？

确保已正确配置 API Key 环境变量，或检查 `config.toml` 中的配置是否正确。

## 如何使用自定义 API 端点？

在模型配置中填写 Base URL 即可，例如使用 DeepSeek：

```
提供商: openai
模型名称: deepseek-chat
Base URL: https://api.deepseek.com/v1
API Key: DEEPSEEK_API_KEY (环境变量名)
```

## 如何恢复之前的对话？

使用 `/threads` 命令浏览历史对话，选择需要恢复的会话即可。

# 许可证

MIT License

# 参考
https://docs.langchain.com/oss/python/deepagents/cli/overview