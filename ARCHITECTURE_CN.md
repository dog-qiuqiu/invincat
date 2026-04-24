# Invincat Agent 架构设计

> 本文档面向希望理解 Invincat 内部设计的开发者，从 Agent 架构、上下文管理、记忆机制三个维度进行深度阐述。代码层细节请参考对应模块的源码注释。

---

## 目录

1. [设计理念](#1-设计理念)
2. [整体架构](#2-整体架构)
3. [运行模式](#3-运行模式)
4. [Middleware 栈](#4-middleware-栈)
5. [多 Agent 设计](#5-多-agent-设计)
6. [上下文管理](#6-上下文管理)
7. [长短期记忆](#7-长短期记忆)
8. [本地上下文感知](#8-本地上下文感知)
9. [工具系统](#9-工具系统)
10. [会话持久化](#10-会话持久化)

---

## 1. 设计理念

Invincat 的核心设计目标是：**让大语言模型具备持续工作能力**，而非仅进行单轮问答。为此，架构围绕三个核心问题展开：

| 问题 | 解决方案 |
|------|---------|
| 上下文窗口有限，长任务会超限 | 三层递进式上下文压缩 |
| 模型无法跨会话记住用户偏好 | 双层持久化记忆系统 |
| 复杂任务需要规划与分工 | 多 Agent 协作与专用子 Agent |

整个系统基于 **LangGraph** 构建，每次与模型的交互都是一个状态机转换；所有对话状态均持久化在 checkpoint 中，中断可恢复，历史可回溯。

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      用户界面层                           │
│  Interactive TUI (Textual)    │  Non-interactive (-n)    │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│                   Middleware 栈（编排层）                  │
│  ConfigurableModel → Token → MicroCompact → Memory →     │
│  Skills → LocalContext → Shell → Summarization           │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│                    主 Agent（ReAct Loop）                  │
│  LangGraph StateGraph  ·  工具调用  ·  checkpoint 持久化   │
└───┬──────────┬─────────────┬──────────────┬─────────────┘
    │          │             │              │
  规划子       记忆 Agent    异步子 Agent    工具集
  Agent       （后台）       （task 工具）   （文件/Shell/Web/MCP）
```

---

## 3. 运行模式

系统支持两种运行模式，共用同一个 Agent 核心与 Middleware 栈：

### 3.1 交互模式（Interactive TUI）

基于 **Textual** 框架构建全屏终端界面，支持：

- 消息实时流式渲染（token 级别）
- 工具调用可视化（展开/折叠）
- 工具审批弹窗（HITL）
- 状态栏显示 token 用量、当前模型、memory 更新状态

### 3.2 非交互模式（Non-interactive）

通过 `-n "任务描述"` 启动，适用于 CI、自动化脚本、管道集成：

```bash
# 基本用法
invincat-cli -n "分析 src/ 目录并生成报告"

# 静默模式：仅输出 agent 响应文本，用于管道
invincat-cli -n "总结这段代码" -q < code.py

# 完全缓冲（非流式）输出
invincat-cli -n "生成测试用例" --no-stream
```

非交互模式默认禁用 Shell 工具，需通过 `--shell-allow-list` 显式授权：

```bash
# 允许安全的只读 shell 命令
invincat-cli -n "检查依赖" --shell-allow-list recommended

# 允许全部 shell 命令（慎用）
invincat-cli -n "运行测试" --shell-allow-list all -y
```

---

## 4. Middleware 栈

Middleware 栈是 Invincat 架构的核心编排层，所有功能模块通过中间件形式挂载。每次模型调用前后，中间件按注册顺序依次执行。

### 注册顺序与职责

```
请求方向（→ 模型）:
  ConfigurableModelMiddleware    运行时动态切换模型
  TokenStateMiddleware           追踪上下文窗口使用量
  MicroCompactMiddleware         规则压缩旧工具输出（零 LLM 开销）
  AskUserMiddleware              向工具集注入 ask_user 工具
  ApprovePlanMiddleware          处理 /plan 规划审批流程
  RefreshableMemoryMiddleware    从 memory_*.json 读取长期记忆并注入提示
  MemoryAgentMiddleware          回合结束后触发记忆异步提取（写入端）
  SkillsMiddleware               加载并执行技能文件
  LocalContextMiddleware         注入 git 状态、项目结构、运行时信息
  ShellAllowListMiddleware       Shell 命令白名单校验
  SummarizationToolMiddleware    上下文自动/手动压缩（LLM 摘要）

响应方向（← 模型）:
  （逆序执行，完成状态回写）
```

### 设计原则

- **无副作用前置**：MicroCompact 在最靠前的位置运行，只修改本次模型调用的输入消息，不写 checkpoint。
- **读先于写**：RefreshableMemoryMiddleware（读长期记忆）早于 MemoryAgentMiddleware（写长期记忆）注册。
- **感知层最后**：LocalContextMiddleware 在尽量靠后的位置注入，确保其看到完整的工具集与状态。

---

## 5. 多 Agent 设计

系统内存在四种 Agent 角色，各司其职。

### 5.1 主 Agent

- 基于 LangGraph `StateGraph` 的 ReAct 循环
- 持有完整工具集（文件操作、Shell、Web 搜索、MCP 工具、子 Agent 委托）
- 每轮对话状态持久化到 LangGraph checkpoint
- 是用户交互的直接接收者和任务执行者

### 5.2 规划子 Agent（`/plan` 命令）

专门用于任务规划，工具集被严格限制为 `write_todos` 和 `ask_user`。

```
用户输入 /plan <任务>
    │
    ▼
规划子 Agent 生成结构化 todo 列表
    │
    ▼
ask_user 弹窗：[批准并执行] [精化] [取消]
    │
    ├─ 批准 → 返回 <<PLAN_APPROVED>> 标记
    │           主 Agent 将 todo 同步到自身状态并开始执行
    │
    └─ 精化/取消 → 停止，主 Agent 不执行任何操作
```

规划子 Agent 的设计确保：
- **执行与规划解耦**：规划阶段不接触文件系统，不执行任何命令
- **用户有完整确认窗口**：执行前必须经过显式批准
- **步骤粒度可控**：每个步骤对应一个明确的工作单元（1～5 分钟）

### 5.3 记忆 Agent（后台异步）

每个对话回合完成后，记忆 Agent 在后台异步执行：

```
主 Agent 回合结束
    │
    ▼
[节流检查] 通过节流门槛？
    │
    ├─ 否 → 跳过
    │
    └─ 是 → 构建增量对话切片（仅上次提取后的新消息）
              │
              ▼
            独立 LLM 调用（轻量模型）
              │
              ▼
            返回结构化操作 JSON
            { "operations": [create/update/archive/noop] }
              │
              ▼
            校验 + 原子写 memory_*.json
              │
              ▼
            标记 memory_contents 失效 → 下轮重载
```

记忆 Agent 的输出**不渲染到对话界面**，对用户完全透明。状态栏仅在提取期间显示 `Updating memory...`。

### 5.4 异步子 Agent（`task` 工具）

主 Agent 可通过 `task` 工具将自包含的子任务委托给子 Agent 执行：

```
主 Agent 调用 task("分析 /src 目录", subagent_type="code-analyst")
    │
    ▼
子 Agent 实例（独立 LangGraph 图）
    - 拥有独立工具子集
    - 独立执行，互不干扰
    - 结果以 ToolMessage 形式返回主 Agent
```

子 Agent 适用于：大型任务分解、并行独立子任务、不需要主 Agent 直接关注的繁琐中间步骤。

---

## 6. 上下文管理

上下文管理是 Invincat 区别于普通聊天界面的核心能力。系统采用**三层递进式压缩**，从无损到有损、从低成本到高成本逐层介入。

### 6.1 第一层：微压缩（MicroCompact）

**触发时机**：每次模型调用前，自动运行，零 LLM 开销（< 1ms）。

**工作原理**：

```
原始消息列表
    │
    ▼
按"工具调用组"分组
（每个 AI 消息 + 其对应的所有 ToolMessage 为一组）
    │
    ▼
确定动态保留窗口（保留最近 N 组完整）
    │
    ├─ 窗口内 → 保持原样
    │
    └─ 窗口外 → 按距离分两级压缩：
         near cutoff → cleared-light（保留头尾信号）
         far from cutoff → cleared-heavy（仅保留简短摘要）
```

**可压缩工具类型**：`read_file`、`edit_file`、`write_file`、`execute`、`grep`、`glob`、`ls`、`web_search`、`fetch_url`

**关键约束**：微压缩**只修改发送给模型的消息**，不写 checkpoint，不影响持久化历史。完整历史始终保存在 checkpoint 中。

**调节参数**（环境变量）：

```bash
INVINCAT_MICRO_COMPACT_KEEP_RECENT_GROUPS=3       # 无条件保留的最近组数
INVINCAT_MICRO_COMPACT_DYNAMIC_GROUP_FACTOR=12    # 动态窗口因子
INVINCAT_MICRO_COMPACT_MAX_KEEP_RECENT_GROUPS=8   # 动态窗口上限
INVINCAT_MICRO_COMPACT_LIGHT_NEAR_CUTOFF_GROUPS=2 # light 压缩组数
INVINCAT_MICRO_COMPACT_MIN_COMPRESS_CHARS=240     # 最小压缩字符阈值
```

### 6.2 第二层：自动摘要（Auto Summarization）

**触发时机**：上下文窗口使用量超过约 80% 时自动触发（无需用户操作）。

**工作原理**：

```
上下文超过阈值
    │
    ▼
确定截断位置（保留最近 N 条消息 / M tokens）
    │
    ▼
对旧消息调用 LLM 生成结构化摘要
（使用结构化摘要指令，包含：任务目标 / 完成步骤 / 修改文件 / 关键发现 / 待处理问题）
    │
    ▼
旧消息 → 单条摘要消息（替换 checkpoint 中的状态）
    │
    ▼
原始消息归档至 ~/.invincat/conversation_history/{thread_id}.md
```

**UI 反馈**：状态栏 token 计数超过 70% 变橙色，超过 90% 变红色；压缩完成后显示释放的 token 数量。

### 6.3 第三层：手动卸载（/offload）

用户主动运行 `/offload` 或 `/compact` 时触发，执行流程与自动摘要完全相同，但不受阈值约束，适用于：
- 手动管理上下文节奏
- 在开始新的大型任务前主动清理历史
- 压缩质量更可控（用户可在完整上下文时触发）

---

## 7. 长短期记忆

### 7.1 短期记忆（Short-term Memory）

短期记忆等同于当前会话的对话历史，由 LangGraph checkpoint 管理：

- 每个用户 turn 后自动持久化整个 `AgentState`
- 包含完整的 `messages` 列表（AIMessage、HumanMessage、ToolMessage）
- 通过 `thread_id` 隔离不同会话（`/clear` 创建新 thread）
- 支持断点续跑：进程崩溃后重启，从 checkpoint 恢复到上次状态

短期记忆的生命周期与 thread 绑定。通过 `/threads` 可在历史 thread 间切换。

### 7.2 长期记忆（Long-term Memory）

长期记忆跨会话持久化，以结构化 JSON store 为唯一真源：

| 维度 | 详情 |
|------|------|
| 用户记忆 | `~/.invincat/{assistant_id}/memory_user.json` |
| 项目记忆 | `{project_root}/.invincat/memory_project.json` |
| 作用域区别 | user：跨项目通用（编码风格、交流偏好）；project：仅当前仓库（架构约定、技术栈）|
| 数据格式 | 每条记忆有 `id`、`section`、`content`、`status`、`confidence`、时间戳等字段 |
| 条目状态 | `active`（注入提示词）或 `archived`（归档，不注入） |

**写入流程（MemoryAgentMiddleware）**：

```
回合结束，通过节流检查
    │
    ▼
构建增量切片（自上次提取游标后的新消息）
    │
    ▼
LLM 提取 → 返回操作列表：
  create  → 新增记忆条目
  update  → 更新已有条目内容
  archive → 将过时条目标记为 archived
  noop    → 无需变更
    │
    ▼
校验（防过度 archive、防字段超限、防路径越权）
    │
    ▼
原子写盘（tmp + os.replace，防损坏）
```

**读取流程（RefreshableMemoryMiddleware）**：

```
每轮对话开始前
    │
    ▼
读取 memory_user.json + memory_project.json
    │
    ▼
过滤：仅保留 active 条目
按 section 分组，组内按 updated_at 降序（最新优先）
    │
    ▼
注入 <agent_memory> 块到系统提示
    │
    预算控制：
    单 scope 上限：4K 字符
    总注入上限：8K 字符
```

**安全保护**：
- 操作数量与字段长度限制
- 同轮次对同一 id 的冲突操作拒绝
- 过高归档比例拦截（防止 LLM 批量错误 archive）
- 防"全量清空活跃记忆"保护
- 写入路径白名单
- 损坏 store 自动备份并恢复为安全结构

---

## 8. 本地上下文感知

`LocalContextMiddleware` 在每个对话回合开始时，通过运行本地检测脚本，将当前工作环境的关键信息注入系统提示：

| 检测项 | 内容 |
|--------|------|
| Git 状态 | 当前分支、暂存/未暂存文件、最近提交 |
| 项目结构 | 根目录下的顶层目录树 |
| 运行时环境 | Python/Node/Go/Rust 等版本 |
| 测试命令 | 从 Makefile / pyproject.toml / package.json 自动识别 |
| MCP 服务器 | 已连接的 MCP 服务器与工具列表 |

**缓存策略**：以 checkpoint 的 `cutoff_index`（消息数）为 key 缓存，消息数不变则不重新检测，避免每次调用都触发 Shell 命令。

这一机制使模型无需额外工具调用就能感知项目的即时状态，显著减少"先执行 git status 再开始工作"这类冗余步骤。

---

## 9. 工具系统

### 9.1 内置工具

| 类别 | 工具 |
|------|------|
| 文件操作 | `read_file`、`edit_file`（精确 diff 替换）、`write_file`、`grep`、`glob`、`ls` |
| Shell | `execute`（受 HITL 和白名单控制）|
| Web | `web_search`（Tavily）、`fetch_url` |
| Agent 协作 | `task`（委托子 Agent）、`ask_user`（人机确认） |
| 规划 | `write_todos`（创建/更新结构化任务列表）|

### 9.2 MCP 工具

通过 Model Context Protocol 动态扩展工具集：

- **自动发现**：启动时自动加载 `~/.claude/claude_desktop_config.json` 和项目级 `.mcp.json`
- **显式配置**：通过 `--mcp-config` 参数指定配置文件
- **工具命名空间**：MCP 工具以 `server_name__tool_name` 格式注册，避免与内置工具冲突
- **查看已连接 MCP**：运行 `/mcp` 查看当前会话加载的所有 MCP 服务器和工具

```bash
# 禁用全部 MCP 工具加载
invincat-cli --no-mcp

# 指定 MCP 配置文件
invincat-cli --mcp-config ~/.config/my-mcp.json
```

### 9.3 技能系统（Skills）

技能是可复用的工作流模板，以 Markdown 文件定义，由 `SkillsMiddleware` 加载：

```
/skill:web-research 搜索 LangGraph 最佳实践
/skill:code-review  检查 src/ 目录代码质量
```

| 位置 | 路径 | 作用域 |
|------|------|--------|
| 内置技能 | 随包安装 | 全局 |
| 全局自定义 | `~/.invincat/agent/skills/` | 跨项目 |
| 项目级 | `.invincat/skills/` | 仅当前项目 |

### 9.4 工具审批（HITL）

所有写入、执行类工具调用默认需要用户审批，通过 Human-in-the-Loop 机制实现：

- **交互模式**：弹出审批弹窗，支持批准/拒绝
- **自动批准模式**：`Shift+Tab` 切换，或启动时传入 `-y`
- **Shell 白名单**：通过 `--shell-allow-list` 设置只对特定命令自动批准，其余仍需确认

---

## 10. 会话持久化

### 10.1 Thread 模型

每个对话会话对应一个唯一的 `thread_id`。会话之间完全隔离：

```
thread_id = "__default_thread__"（默认）
    │
    ├─ /clear → 生成新 thread_id，旧 thread 保留在 checkpoint
    │
    └─ /threads → 列出所有历史 thread，选择后切换
```

### 10.2 存储层次

| 层次 | 内容 | 位置 |
|------|------|------|
| LangGraph Checkpoint | 完整 AgentState（消息 + 私有字段） | 本地 SQLite / 文件系统 |
| Conversation History | 被 offload 的原始消息（Markdown 格式） | `~/.invincat/conversation_history/{thread_id}.md` |
| Long-term Memory | 跨会话结构化记忆 | `memory_user.json` / `memory_project.json` |
| Skills | 工作流模板 | `~/.invincat/agent/skills/` / `.invincat/skills/` |

### 10.3 外部 Hook 系统

通过 `~/.invincat/hooks.json` 配置外部 Hook，在关键事件发生时触发自定义脚本：

```json
{
  "hooks": [
    {
      "command": ["bash", "notify.sh"],
      "events": ["session.start", "turn.end"]
    }
  ]
}
```

- Hook 在后台线程池中并发执行，每个 Hook 有 5 秒超时
- Hook 通过 stdin 接收 JSON 格式的事件 payload
- `events` 字段为空时订阅全部事件
- 失败只记录日志，不影响主流程

---

## 相关文档

- [Memory Design（中文）](./MEMORY_DESIGN.md)
- [Memory Design（English）](./MEMORY_DESIGN_EN.md)
- [README（中文）](./README_CN.md)
- [README（English）](./README.md)
