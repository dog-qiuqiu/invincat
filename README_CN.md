# Invincat CLI

[English](README.md)

基于python实现的终端 AI 编程助手 — 在你的项目目录里直接与 AI 协作：读写文件、执行命令、浏览网页，跨会话保持记忆。

![](data/cli.png)

---

## 安装

**环境要求**：Python 3.11+

```bash
# 从 PyPI 安装
pip install invincat-cli
```

或从源码安装：

```bash
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

---

## 基本使用

直接在输入框输入问题或任务，按 `Enter` 发送。AI 会自动选择合适的工具完成任务：

```
搜索一下 LangGraph interrupt 的最新用法
```
---

## 非交互模式

通过 `-n` 参数以非交互方式运行单个任务，适合 CI、自动化脚本和管道场景：

```bash
# 运行单个任务后退出
invincat-cli -n "分析 src/ 目录并生成摘要"

# 静默模式：只输出 AI 响应文本（适合管道）
invincat-cli -n "总结这段代码" -q < code.py

# 通过管道传入内容
cat error.log | invincat-cli -n "分析这个错误的根因"

# 完整缓冲输出（非流式）
invincat-cli -n "生成测试用例" --no-stream
```

非交互模式默认禁用 Shell 工具，需通过 `--shell-allow-list` 显式启用：

```bash
# 允许常用的只读命令（安全默认值）
invincat-cli -n "检查项目依赖" -S recommended

# 允许所有 shell 命令并自动批准（慎用）
invincat-cli -n "运行测试套件" -S all -y
```

---

### 命令模式（`/` 前缀）

```
/clear
/threads
/model
... ...
```

按 `Tab` 自动补全可用命令。完整命令列表见[斜杠命令](#斜杠命令)。

---

## 任务规划

```
/plan <任务描述>
```

在执行前生成结构化计划。规划子 Agent 会将任务拆解为有序步骤并展示给你确认，只有在你批准后主 Agent 才会开始执行。

```
/plan 为 user_service.py 添加 JWT 认证，更新相关测试
```

弹窗提供三个选项：**批准并执行** / **精化** / **取消**。

---

## 引用文件

在消息中用 `@` 引用文件，AI 会读取并理解其内容：

```
@src/main.py 这个文件有没有潜在的性能问题？
```
---

## 工具批准

AI 执行文件写入、shell 命令、网络请求等操作时，默认会暂停等待确认：


**自动批准模式**：按`Shift+Tab` 切换，开启后所有工具调用自动通过，适合信任的任务场景。状态栏会显示 `AUTO` 标志。

> ⚠️ 建议在熟悉任务内容后再开启自动批准。

## 输入换行

在输入框中按 `Ctrl+J` 可以换行，适合输入较长的代码或段落。

---

## 上下文管理

### 微压缩

每次模型调用前自动运行的轻量级压缩，**无需 LLM 参与**，耗时 <1ms。

**工作原理**：将对话消息按"工具调用组"分组，保留**动态最近窗口**，并对更旧的大体积工具输出执行两级压缩：

- `cleared-light`：靠近保留边界的轻压缩，占位符保留头尾信号
- `cleared-heavy`：更旧内容的重压缩，占位符仅保留简短摘要

**可压缩的工具输出**：
| 工具 | 压缩效果 |
|------|---------|
| `read_file` | 文件内容 → 轻/重占位符 |
| `edit_file` | diff 输出 → 轻/重占位符 |
| `write_file` | 写入结果 → 轻/重占位符 |
| `execute` | shell 输出 → 轻/重占位符 |
| `grep`/`glob`/`ls` | 搜索/列表输出 → 轻/重占位符 |
| `web_search`/`fetch_url` | 网页内容 → 轻/重占位符 |

**不会压缩**：agent/subagent 结果、`ask_user` 响应、MCP 工具输出、`compact_conversation` 结果。

可通过环境变量调节微压缩行为：

```bash
INVINCAT_MICRO_COMPACT_KEEP_RECENT_GROUPS=3
INVINCAT_MICRO_COMPACT_DYNAMIC_GROUP_FACTOR=12
INVINCAT_MICRO_COMPACT_MAX_KEEP_RECENT_GROUPS=8
INVINCAT_MICRO_COMPACT_LIGHT_NEAR_CUTOFF_GROUPS=2
INVINCAT_MICRO_COMPACT_MIN_COMPRESS_CHARS=240
```

> 💡 微压缩只影响发送给模型的上下文，不修改持久化状态，完整历史仍保存在检查点中。

### 自动压缩

当上下文窗口使用量超过 **80%** 时，系统自动将较旧的消息压缩为摘要，释放空间，无需手动操作。状态栏 token 计数超过 70% 变橙色、90% 变红色作为预警。

### 手动压缩

```
/offload
```

或等效的 `/compact`。执行后显示压缩了多少消息、释放了多少 token。

## 记忆系统

AI 可以在会话之间记住你的偏好、项目约定和重要信息。

### 记忆文件

| 类型 | 路径 | 适用范围 |
|------|------|---------|
| 全局记忆存储 | `~/.invincat/{assistant_id}/memory_user.json`（默认：`~/.invincat/agent/memory_user.json`） | 所有项目通用（编码风格、个人偏好）|
| 项目记忆存储 | `{项目根目录}/.invincat/memory_project.json` | 仅当前 Git 仓库（架构约定、技术栈）|

`AGENTS.md` 已从运行时记忆注入链路中弃用，当前以 `memory_*.json` 为唯一真源。

### 手动更新记忆

```
/remember
```

触发 AI 主动整理对话中值得保存的内容，写入记忆文件。

### 自动记忆更新

记忆更新会在“非 trivial 且任务完成”的回合后触发，并结合以下机制控制频率：

- 增量提取：默认只消费同一线程中“自上次提取后新增”的消息
- 游标失效回退：若历史被压缩/重放导致游标失效，会自动回退一次全量提取
- 按轮次间隔节流
- 关键词早触发（偏好/规则/约定）
- 时间与文件冷却保护

可通过环境变量调节行为：

```bash
INVINCAT_MEMORY_CONTEXT_MESSAGES=0
INVINCAT_MEMORY_MIN_TURN_INTERVAL=2
INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS=8
INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS=5
```

`INVINCAT_MEMORY_CONTEXT_MESSAGES=0` 表示对“自上次记忆提取后的增量消息”
不设上限；设置为正整数则只取该增量中的最近 N 条消息。

### 记忆设计文档

- [Memory Design（中文）](./MEMORY_DESIGN.md)
- [Memory Design（English）](./MEMORY_DESIGN_EN.md)

---

## 架构文档

如需深入了解 Invincat 的 Agent 架构设计（多 Agent 协作、三层上下文管理、长短期记忆机制等），参见：

- [架构设计（中文）](./ARCHITECTURE_CN.md)
- [Architecture Design（English）](./ARCHITECTURE.md)

### 记忆管理界面

```
/memory
```

打开全屏记忆管理界面，实时查看 memory store：

- `user` / `project` 双页面展示（`1` / `2`，或 `Tab` 切换）
- 每条记忆突出显示关键字段（`status`、`id`、`section`、`content`）
- 支持 `r` 刷新、`a` 显示/隐藏 archived、`Esc` 关闭

---

## Hook 系统

通过 `~/.invincat/hooks.json` 配置外部 Hook，在 Agent 事件发生时触发自定义脚本（通知、日志、与外部系统集成等）：

```json
{
  "hooks": [
    {
      "command": ["bash", "~/.invincat/notify.sh"],
      "events": ["session.start", "turn.end"]
    }
  ]
}
```

- 每个 Hook 通过 stdin 接收 JSON 格式的事件 payload（包含 `event` 字段及上下文信息）
- `events` 字段缺失或为空列表时，订阅全部事件
- Hook 在后台并发执行，超时 5 秒，失败仅记录日志，不影响主流程

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

### 开始新对话

```
/clear
```

清除当前对话，开始新会话（旧会话仍保存，可通过 `/threads` 找回）。

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
| `/memory` | 打开全屏记忆管理界面（实时查看 user/project） |

### 规划

| 命令 | 说明 |
|------|------|
| `/plan <任务>` | 生成结构化任务计划，用户确认后再执行 |

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

**Q: 非交互模式下能执行 shell 命令吗？**
可以，但需要显式授权：`-S recommended`（安全只读命令）、`-S "git,pytest"`（指定命令列表）或 `-S all`（所有命令，慎用）。

**Q: 如何集成到 CI 管道中？**
使用 `-n "任务" -q` 组合：`-n` 进入非交互模式，`-q` 确保只有 AI 响应文本输出到 stdout，其余状态信息输出到 stderr，适合管道处理。
