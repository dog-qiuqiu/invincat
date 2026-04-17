![](data/invincat.jpg)

# Invincat CLI

终端 AI 编程助手 — 在你的项目目录里直接与 AI 协作：读写文件、执行命令、浏览网页，跨会话保持记忆。

![](data/cli.png)

---

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [配置模型](#配置模型)
- [基本使用](#基本使用)
- [输入模式](#输入模式)
- [引用文件](#引用文件)
- [工具批准](#工具批准)
- [上下文管理](#上下文管理)
- [记忆系统](#记忆系统)
- [技能系统](#技能系统)
- [会话管理](#会话管理)
- [斜杠命令](#斜杠命令)
- [快捷键参考](#快捷键参考)
- [命令行参数](#命令行参数)
- [配置文件](#配置文件)

---

## 安装

**环境要求**：Python 3.11+

```bash
# 推荐：使用 pipx 隔离安装
pipx install invincat-cli

# 或直接 pip
pip install invincat-cli

# 或从源码安装（开发模式）
git clone https://github.com/dog-qiuqiu/invincat.git
cd invincat
pip install -e .
```

---

## 快速开始

```bash
# 在你的项目目录中启动
cd ~/my-project
invincat-cli
```

首次启动后执行 `/model` 配置模型和 API Key，之后就可以直接开始对话。

```bash
# 恢复上次会话
invincat-cli -r

# 启动时直接发送消息
invincat-cli -m "帮我分析一下当前项目结构"

# 使用指定模型
invincat-cli -M claude-sonnet-4-6
```

---

## 配置模型

### 通过界面配置

执行 `/model` 命令打开模型管理界面：

![](data/model.png)

1. 按 `Ctrl+N` 注册新模型
2. 填写提供商、模型名称、API Key
3. 在列表中选中后按 `Enter` 切换生效

### 支持的提供商

| 提供商 | 示例模型 |
|--------|---------|
| `anthropic` | `claude-sonnet-4-6`、`claude-opus-4-7` |
| `openai` | `gpt-4o`、`o3` |
| `google_genai` | `gemini-2.0-flash`、`gemini-2.5-pro` |
| `openrouter` | 支持 OpenRouter 上的所有模型 |

OpenAI 兼容接口（DeepSeek、智谱、本地 Ollama 等）设置 `base_url` 即可接入。

### 环境变量

| 变量名 | 说明 |
|--------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `OPENAI_API_KEY` | OpenAI API Key |
| `GOOGLE_API_KEY` | Google API Key |
| `OPENROUTER_API_KEY` | OpenRouter API Key |
| `TAVILY_API_KEY` | Tavily 网页搜索 Key（可选）|

### 命令行快速指定

```bash
# 临时使用某个模型，不修改配置
invincat-cli -M gpt-4o

# 设为默认模型
invincat-cli --default-model anthropic:claude-sonnet-4-6
```

---

## 基本使用

直接在输入框输入问题或任务，按 `Enter` 发送。AI 会自动选择合适的工具完成任务：

```
你: 帮我找一下项目里所有未处理的 TODO 注释
你: 把 src/utils.py 里的 calculate_total 函数重构，加上类型注解
你: 搜索一下 LangGraph interrupt 的最新用法
```

**多行输入**：`Shift+Enter` / `Alt+Enter` / `Ctrl+Enter` 插入换行，`Enter` 发送。

**历史消息**：在输入框按 `↑` / `↓` 浏览历史输入记录，支持前缀过滤（先输入几个字再按 `↑` 可过滤匹配历史）。

---

## 输入模式

### 普通模式

默认模式，直接与 AI 对话。

### Shell 模式（`!` 前缀）

```
!ls -la
!git log --oneline -10
!docker ps
```

以 `!` 开头的内容直接作为 shell 命令执行，输出显示在对话中。按 `Esc` 退出 shell 模式。

### 命令模式（`/` 前缀）

```
/clear
/threads
/model
```

按 `Tab` 自动补全可用命令。完整命令列表见[斜杠命令](#斜杠命令)。

---

## 引用文件

在消息中用 `@` 引用文件，AI 会读取并理解其内容：

```
你: @src/main.py 这个文件有没有潜在的性能问题？
你: 参考 @docs/api-spec.md 帮我实现用户注册接口
你: @package.json 和 @requirements.txt 对比一下依赖差异
```

输入 `@` 后按 `Tab` 可模糊搜索项目文件。

---

## 工具批准

AI 执行文件写入、shell 命令、网络请求等操作时，默认会暂停等待确认：

```
┌─ Agent 请求执行 ─────────────────────────┐
│  write_file: src/auth.py                 │
│  [1] 批准   [2] 始终批准   [3] 拒绝       │
└──────────────────────────────────────────┘
```

| 操作 | 说明 |
|------|------|
| `y` / `1` / `Enter` | 批准本次 |
| `a` / `2` | 始终批准（本次会话内跳过所有确认）|
| `n` / `3` / `Esc` | 拒绝 |
| `e` | 展开查看完整命令内容 |

**自动批准模式**：按 `Ctrl+T` 或 `Shift+Tab` 切换，开启后所有工具调用自动通过，适合信任的任务场景。状态栏会显示 `AUTO` 标志。

> ⚠️ 建议在熟悉任务内容后再开启自动批准。

---

## 上下文管理

### 自动压缩

当上下文窗口使用量超过 **80%** 时，系统自动将较旧的消息压缩为摘要，释放空间，无需手动操作。状态栏 token 计数超过 70% 变橙色、90% 变红色作为预警。

### 手动压缩

```
/offload
```

或等效的 `/compact`。执行后显示压缩了多少消息、释放了多少 token。

### 查看 Token 使用情况

```
/tokens
```

显示当前会话的详细 token 分布（系统提示、历史消息、工具调用等）。

---

## 记忆系统

AI 可以在会话之间记住你的偏好、项目约定和重要信息。

### 记忆文件

| 类型 | 路径 | 适用范围 |
|------|------|---------|
| 全局记忆 | `~/.invincat/agent/AGENTS.md` | 所有项目通用（编码风格、个人偏好）|
| 项目记忆 | `{项目根目录}/.invincat/AGENTS.md` | 仅当前 Git 仓库（架构约定、技术栈）|

### 手动更新记忆

```
/remember
```

触发 AI 主动整理对话中值得保存的内容，写入记忆文件。

### 自动记忆更新

系统每隔一定轮数自动检查是否有新内容需要保存，或在检测到对话中出现"规范"、"约定"、"偏好"等关键信息时提前触发。

**配置自动记忆**：执行 `/auto-memory` 打开配置界面，或在 `~/.invincat/config.toml` 中手动设置：

```toml
[auto_memory]
enabled = true   # 启用自动记忆（默认: true）
interval = 10    # 每隔多少轮触发一次检查（默认: 10）
on_exit = true   # 退出时写标记，下次启动提前触发（默认: true）
```

---

## 技能系统

技能是预定义的工作流模板，可复用复杂任务步骤。

### 使用技能

```
/skill:web-research 搜索 LangGraph 最佳实践
/skill:code-review 检查 src/ 目录的代码质量
```

### 技能位置

| 位置 | 路径 | 说明 |
|------|------|------|
| 内置技能 | 随包安装 | `remember`、`skill-creator` |
| 全局自定义 | `~/.invincat/agent/skills/` | 跨项目可用 |
| 项目级 | `.invincat/skills/` | 仅当前项目可用 |

### 创建自定义技能

```
/skill-creator
```

启动交互式向导，引导你创建并保存新技能。

---

## 会话管理

### 查看和切换会话

```
/threads
```

打开会话浏览器，显示所有历史对话（时间、消息数、所在分支等）。

### 恢复会话

```bash
# 恢复最近一次会话
invincat-cli -r

# 恢复指定会话（从 /threads 中复制 ID）
invincat-cli -r abc123def456
```

### 开始新对话

```
/clear
```

清除当前对话，开始新会话（旧会话仍保存，可通过 `/threads` 找回）。

### 命令行管理会话

```bash
# 列出所有会话
invincat-cli threads list

# 列出指定分支的会话
invincat-cli threads list --branch main

# 删除会话
invincat-cli threads delete <thread-id>
```

---

## 斜杠命令

在输入框输入 `/` 后按 `Tab` 可查看并补全所有命令。

### 会话

| 命令 | 说明 |
|------|------|
| `/clear` | 清除当前对话，开始新会话 |
| `/threads` | 浏览并恢复历史会话 |
| `/quit` / `/q` | 退出程序 |

### 模型与界面

| 命令 | 说明 |
|------|------|
| `/model` | 切换或管理模型配置 |
| `/theme` | 切换颜色主题 |
| `/language` | 切换界面语言（中文 / 英文）|
| `/tokens` | 查看 token 使用详情 |

### 上下文与记忆

| 命令 | 说明 |
|------|------|
| `/offload` / `/compact` | 手动压缩上下文，释放 token |
| `/remember` | 手动触发记忆更新 |
| `/auto-memory` | 配置自动记忆行为 |

### 工具与扩展

| 命令 | 说明 |
|------|------|
| `/mcp` | 查看已连接的 MCP 服务器和工具 |
| `/editor` | 在外部编辑器中编辑当前输入 |
| `/skill-creator` | 创建新技能的交互向导 |

### 其他

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/version` | 显示版本号 |
| `/reload` | 重新加载配置文件 |
| `/trace` | 在 LangSmith 中打开当前对话（需配置）|

---

## 快捷键参考

### 全局

| 快捷键 | 功能 |
|--------|------|
| `Esc` | 中断 AI 响应 / 关闭弹窗 / 退出 shell 模式 |
| `Ctrl+C` | 中断或退出（双击确认退出）|
| `Ctrl+D` | 直接退出 |
| `Ctrl+T` / `Shift+Tab` | 切换自动批准模式 |
| `Ctrl+O` | 展开/折叠工具调用输出 |
| `Ctrl+X` | 在外部编辑器中编辑当前输入 |

### 输入框

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` / `Alt+Enter` | 插入换行 |
| `↑` / `↓` | 浏览历史输入 |
| `Tab` | 自动补全命令或文件路径 |
| `@` + `Tab` | 搜索并引用项目文件 |

### 工具批准弹窗

| 快捷键 | 功能 |
|--------|------|
| `y` / `1` / `Enter` | 批准 |
| `a` / `2` | 始终批准（本次会话）|
| `n` / `3` / `Esc` | 拒绝 |
| `↑` / `↓` / `j` / `k` | 在选项间导航 |
| `e` | 展开查看完整内容 |

---

## 命令行参数

```bash
invincat-cli [选项]
```

| 参数 | 说明 |
|------|------|
| `-r` / `--resume [ID]` | 恢复会话：`-r` 恢复最近，`-r <ID>` 恢复指定 |
| `-m TEXT` | 启动时自动发送的初始消息 |
| `-M MODEL` | 指定模型（如 `claude-sonnet-4-6`、`gpt-4o`）|
| `-a NAME` | 指定 Agent 名称（默认 `agent`）|
| `-n TEXT` | 非交互模式：执行单次任务后退出 |
| `--default-model MODEL` | 设置默认模型 |
| `--clear-default-model` | 清除默认模型设置 |
| `--model-params JSON` | 传入模型额外参数（如 `{"temperature":0.5}`）|

**子命令**：

```bash
invincat-cli threads list          # 列出所有会话
invincat-cli threads list -n 5     # 列出最近 5 条
invincat-cli threads delete <id>   # 删除指定会话
invincat-cli agents list           # 列出所有 Agent
invincat-cli update                # 检查并安装更新
```

---

## 配置文件

配置文件位于 `~/.invincat/config.toml`，首次启动自动创建。

```toml
[models]
# 最近使用的模型，格式 "提供商:模型名"
recent = "anthropic:claude-sonnet-4-6"

# 配置 OpenAI 提供商
[models.providers.openai]
models = ["gpt-4o", "o3"]

[models.providers.openai.params]
api_key_env = "OPENAI_API_KEY"
# 使用兼容接口（如 DeepSeek）可修改 base_url
# base_url = "https://api.deepseek.com/v1"

# 配置 Anthropic 提供商
[models.providers.anthropic]
models = ["claude-sonnet-4-6", "claude-opus-4-7"]

[models.providers.anthropic.params]
api_key_env = "ANTHROPIC_API_KEY"

[general]
language = "zh"  # 界面语言：zh / en

[auto_memory]
enabled = true
interval = 10
on_exit = true
```

### MCP 服务器

在配置文件中添加 MCP 服务器（符合 [MCP 协议](https://modelcontextprotocol.io) 的工具扩展）：

```toml
[[mcp_servers]]
name = "filesystem"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"]

[[mcp_servers]]
name = "github"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "ghp_xxx" }
```

配置后执行 `/mcp` 可查看已连接的 MCP 工具。

---

## 常见问题

**Q: 首次启动没有响应？**
需要先配置模型。执行 `/model` → 按 `Ctrl+N` 注册模型 → 填写 API Key。

**Q: 如何中断正在运行的任务？**
按 `Esc` 中断 AI 当前响应；如果 AI 正在等待工具批准，`Esc` 相当于拒绝。

**Q: 上下文太长导致响应变慢？**
执行 `/offload` 手动压缩历史，或等待系统自动压缩（使用量超过 80% 时触发）。

**Q: 如何让 AI 记住我的编码偏好？**
直接告诉 AI，例如"记住：我的项目使用 4 空格缩进，不加分号"，AI 会在适当时机自动保存到记忆文件。也可执行 `/remember` 手动触发保存。

**Q: 如何在不同项目间共享技能？**
将技能文件放在 `~/.invincat/agent/skills/` 目录下即可全局生效；放在 `.invincat/skills/` 则仅当前项目可用。
