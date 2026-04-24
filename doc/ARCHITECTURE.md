# Invincat Agent Architecture

> This document describes Invincat's internal design for developers who want to understand the system deeply. It focuses on agent architecture, context management, and memory mechanisms — not code-level implementation details (see source comments for those).

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [Overall Architecture](#2-overall-architecture)
3. [Runtime Modes](#3-runtime-modes)
4. [Middleware Stack](#4-middleware-stack)
5. [Multi-Agent Design](#5-multi-agent-design)
6. [Context Management](#6-context-management)
7. [Short and Long-Term Memory](#7-short-and-long-term-memory)
8. [Local Context Awareness](#8-local-context-awareness)
9. [Tool System](#9-tool-system)
10. [Session Persistence](#10-session-persistence)

---

## 1. Design Philosophy

Invincat's core goal is to give LLMs **sustained working capacity** — not just single-turn Q&A. The architecture is built around three fundamental problems:

| Problem | Solution |
|---------|----------|
| Context windows are finite; long tasks exceed them | Three-layer progressive context compression |
| Models cannot remember user preferences across sessions | Two-layer persistent memory system |
| Complex tasks require planning and delegation | Multi-agent collaboration with specialized sub-agents |

The entire system is built on **LangGraph**. Every model interaction is a state-machine transition; all conversation state is persisted in checkpoints, making execution resumable and history auditable.

---

## 2. Overall Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Interface Layer                      │
│  Interactive TUI (Textual)    │  Non-interactive (-n)    │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│               Middleware Stack (Orchestration Layer)      │
│  ConfigurableModel → Token → MicroCompact → Memory →     │
│  Skills → LocalContext → Shell → Summarization           │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│                Main Agent (ReAct Loop)                    │
│  LangGraph StateGraph  ·  Tool calls  ·  Checkpoint      │
└───┬──────────┬─────────────┬──────────────┬─────────────┘
    │          │             │              │
 Planner    Memory Agent  Async Sub-      Tool Set
 Sub-Agent  (background)  Agents         (File/Shell/Web/MCP)
            (task tool)
```

---

## 3. Runtime Modes

The system supports two modes that share the same agent core and middleware stack:

### 3.1 Interactive TUI

A full-screen terminal UI built on **Textual**, with:

- Token-level streaming output
- Tool call visualization (expand/collapse)
- Tool approval dialogs (HITL)
- Status bar: token usage, active model, memory update state

### 3.2 Non-interactive Mode

Launched with `-n "task description"` for CI, automation scripts, and pipeline integration:

```bash
# Basic usage
invincat-cli -n "analyze src/ and generate a report"

# Quiet mode: only agent response goes to stdout (for piping)
invincat-cli -n "summarize this code" -q < code.py

# Fully buffered (non-streaming) output
invincat-cli -n "generate test cases" --no-stream
```

Shell tools are disabled by default in non-interactive mode; use `--shell-allow-list` to authorize:

```bash
# Allow safe read-only shell commands
invincat-cli -n "check dependencies" --shell-allow-list recommended

# Allow all shell commands (use with caution)
invincat-cli -n "run tests" --shell-allow-list all -y
```

---

## 4. Middleware Stack

The middleware stack is the core orchestration layer. Every feature module is mounted as middleware. On each model call, middlewares execute in registration order.

### Registration Order and Responsibilities

```
Request direction (→ model):
  ConfigurableModelMiddleware    Runtime model switching
  TokenStateMiddleware           Track context window usage
  MicroCompactMiddleware         Rule-based old-output compression (zero LLM cost)
  AskUserMiddleware              Inject ask_user tool into tool set
  ApprovePlanMiddleware          Handle /plan approval flow
  RefreshableMemoryMiddleware    Load long-term memory from memory_*.json; inject into prompt
  MemoryAgentMiddleware          Post-turn async memory extraction (write side)
  SkillsMiddleware               Load and execute skill files
  LocalContextMiddleware         Inject git state, project structure, runtimes
  ShellAllowListMiddleware       Shell command allow-list validation
  SummarizationToolMiddleware    Auto/manual context compression (LLM summary)

Response direction (← model):
  (Reverse order, state write-back)
```

### Design Principles

- **Side-effect-free first**: MicroCompact runs at the front of the stack — it modifies only the input to the current model call without touching the checkpoint.
- **Read before write**: RefreshableMemoryMiddleware (reads long-term memory) is registered before MemoryAgentMiddleware (writes long-term memory).
- **Awareness layer last**: LocalContextMiddleware is registered near the end so it can observe the complete tool set and agent state.

---

## 5. Multi-Agent Design

The system has four distinct agent roles.

### 5.1 Main Agent

- A ReAct loop built on LangGraph `StateGraph`
- Has access to the full tool set (file ops, shell, web search, MCP tools, sub-agent delegation)
- Conversation state is persisted to LangGraph checkpoint after every turn
- The direct recipient of user input and primary task executor

### 5.2 Planner Sub-Agent (`/plan` command)

A dedicated planning agent with a strictly limited tool set: only `write_todos` and `ask_user`.

```
User types /plan <task>
    │
    ▼
Planner sub-agent generates structured todo list
    │
    ▼
ask_user dialog: [Approve and execute] [Refine] [Cancel]
    │
    ├─ Approve → Returns <<PLAN_APPROVED>> marker
    │             Main agent syncs todos and begins execution
    │
    └─ Refine/Cancel → Stop; main agent executes nothing
```

The planner design ensures:
- **Execution and planning are decoupled**: no filesystem access, no commands during the planning phase
- **Full user confirmation window**: execution always requires explicit approval
- **Controlled step granularity**: each step maps to one focused unit of work (1–5 minutes)

### 5.3 Memory Agent (Background Async)

After each conversation turn completes, the memory agent runs asynchronously in the background:

```
Main agent turn ends
    │
    ▼
[Throttle check] Does it pass throttle gates?
    │
    ├─ No → Skip
    │
    └─ Yes → Build incremental slice (messages since last extraction cursor)
               │
               ▼
             Standalone LLM call (lightweight model)
               │
               ▼
             Returns structured operations JSON:
             { "operations": [create/update/archive/noop] }
               │
               ▼
             Validate + atomically write memory_*.json
               │
               ▼
             Invalidate memory_contents → reload next turn
```

Memory agent output is **never rendered in the chat UI** — it is completely transparent to the user. The status bar shows `Updating memory...` only during extraction.

### 5.4 Async Sub-Agents (`task` tool)

The main agent can delegate self-contained subtasks to sub-agents via the `task` tool:

```
Main agent calls task("analyze /src", subagent_type="code-analyst")
    │
    ▼
Sub-agent instance (independent LangGraph graph)
    - Has its own subset of tools
    - Executes independently
    - Returns result to main agent as a ToolMessage
```

Sub-agents are suited for: decomposing large tasks, parallelizing independent subtasks, and offloading tedious intermediate steps that don't need the main agent's direct attention.

---

## 6. Context Management

Context management is one of Invincat's core differentiators. The system uses **three progressive compression layers**, moving from lossless to lossy and from zero-cost to LLM-based.

### 6.1 Layer 1: MicroCompact (Zero-cost, Pre-call)

**Trigger**: Before every model call, automatic, zero LLM cost (< 1ms).

**How it works**:

```
Raw message list
    │
    ▼
Group into "tool call groups"
(each AI message + its corresponding ToolMessages = one group)
    │
    ▼
Determine dynamic retention window (keep the most recent N groups intact)
    │
    ├─ Inside window → keep as-is
    │
    └─ Outside window → two-level compression by distance:
         near cutoff → cleared-light  (head/tail signals preserved)
         far from cutoff → cleared-heavy (concise summary only)
```

**Compressible tool types**: `read_file`, `edit_file`, `write_file`, `execute`, `grep`, `glob`, `ls`, `web_search`, `fetch_url`

**Key constraint**: MicroCompact **only modifies the messages sent to the model** for this call. It never writes the checkpoint; complete history is always preserved in the checkpoint.

**Tuning** (environment variables):

```bash
INVINCAT_MICRO_COMPACT_KEEP_RECENT_GROUPS=3       # Unconditionally kept recent groups
INVINCAT_MICRO_COMPACT_DYNAMIC_GROUP_FACTOR=12    # Dynamic window factor
INVINCAT_MICRO_COMPACT_MAX_KEEP_RECENT_GROUPS=8   # Dynamic window upper bound
INVINCAT_MICRO_COMPACT_LIGHT_NEAR_CUTOFF_GROUPS=2 # Groups eligible for light compression
INVINCAT_MICRO_COMPACT_MIN_COMPRESS_CHARS=240     # Minimum chars threshold for compression
```

### 6.2 Layer 2: Auto Summarization (LLM-based, Threshold-triggered)

**Trigger**: When context window usage exceeds ~80%, triggered automatically.

**How it works**:

```
Context exceeds threshold
    │
    ▼
Determine cutoff (keep most recent N messages / M tokens)
    │
    ▼
Call LLM to produce a structured summary of old messages
(format: Task Goal / Completed Steps / Modified Files / Key Findings / Open Issues)
    │
    ▼
Old messages → single summary message (replaces state in checkpoint)
    │
    ▼
Raw messages archived to ~/.invincat/conversation_history/{thread_id}.md
```

**UI feedback**: Status bar token count turns orange above 70%, red above 90%; shows tokens freed after completion.

### 6.3 Layer 3: Manual Offload (`/offload`)

User-triggered via `/offload` or `/compact`. Follows the same summarization flow as auto-compression but without a threshold requirement. Suited for:
- Manually controlling context cadence
- Proactively cleaning up history before starting a large new task
- More predictable compression quality (user controls when it runs)

---

## 7. Short and Long-Term Memory

### 7.1 Short-term Memory

Short-term memory is the current session's conversation history, managed by the LangGraph checkpoint:

- The full `AgentState` is persisted automatically after every user turn
- Contains the complete `messages` list (AIMessage, HumanMessage, ToolMessage)
- Isolated per `thread_id` (`/clear` creates a new thread)
- Crash-resumable: process restart loads from the last checkpoint

Short-term memory's lifetime is tied to the thread. Use `/threads` to switch between historical threads.

### 7.2 Long-term Memory

Long-term memory persists across sessions, with structured JSON stores as the single source of truth:

| Dimension | Detail |
|-----------|--------|
| User memory | `~/.invincat/{assistant_id}/memory_user.json` |
| Project memory | `{project_root}/.invincat/memory_project.json` |
| Scope difference | user: cross-project (coding style, preferences); project: current repo only (architecture conventions, tech stack) |
| Data format | Each item has `id`, `section`, `content`, `status`, `confidence`, timestamps |
| Item status | `active` (injected into prompt) or `archived` (retained but not injected) |

**Write path (MemoryAgentMiddleware)**:

```
Turn ends, passes throttle gates
    │
    ▼
Build incremental slice (messages since last extraction cursor)
    │
    ▼
LLM extraction → returns operations list:
  create  → add new memory item
  update  → update existing item content
  archive → mark stale item as archived
  noop    → no change needed
    │
    ▼
Validate (anti-over-archive, field length limits, path whitelist)
    │
    ▼
Atomic write (tmp + os.replace, corruption-safe)
```

**Read path (RefreshableMemoryMiddleware)**:

```
Start of each turn
    │
    ▼
Load memory_user.json + memory_project.json
    │
    ▼
Filter: active items only
Group by section; sort by updated_at descending (most recent first)
    │
    ▼
Inject as <agent_memory> block into system message
    │
    Budget caps:
    Per-scope render cap: 4K chars
    Total injection cap:  8K chars
```

**Safety guards**:
- Operation count and field length limits
- Conflict guard: same id touched multiple times in one batch is rejected
- Archive-ratio guard: blocks over-aggressive archive batches
- Empty-wipe guard: prevents turning a non-empty active memory set fully inactive in one write
- Write path whitelist
- Corrupt store: auto-backup + safe-structure recovery

---

## 8. Local Context Awareness

`LocalContextMiddleware` runs a local detection script at the start of each turn and injects key environment information into the system prompt:

| Detection | Content |
|-----------|---------|
| Git state | Current branch, staged/unstaged files, recent commits |
| Project structure | Top-level directory tree |
| Runtime environment | Python/Node/Go/Rust version etc. |
| Test command | Auto-detected from Makefile / pyproject.toml / package.json |
| MCP servers | Connected MCP servers and tool list |

**Caching strategy**: Uses the checkpoint `cutoff_index` (message count) as the cache key. Detection only re-runs when new messages have arrived, avoiding shell command overhead on every model call.

This mechanism lets the model perceive the project's current state without extra tool calls, significantly reducing boilerplate like "first run git status, then start working."

---

## 9. Tool System

### 9.1 Built-in Tools

| Category | Tools |
|----------|-------|
| File ops | `read_file`, `edit_file` (exact diff-replace), `write_file`, `grep`, `glob`, `ls` |
| Shell | `execute` (gated by HITL and allow-list) |
| Web | `web_search` (Tavily), `fetch_url` |
| Agent collaboration | `task` (delegate to sub-agent), `ask_user` (human confirmation) |
| Planning | `write_todos` (create/update structured task list) |

### 9.2 MCP Tools

Tool set dynamically extended via Model Context Protocol:

- **Auto-discovery**: Loads `~/.claude/claude_desktop_config.json` and project-level `.mcp.json` at startup
- **Explicit config**: Specify a config file with `--mcp-config`
- **Tool namespace**: MCP tools are registered as `server_name__tool_name` to avoid collisions with built-in tools
- **Inspect connected MCP**: Run `/mcp` to view all MCP servers and tools loaded in the current session

```bash
# Disable all MCP tool loading
invincat-cli --no-mcp

# Specify MCP config file
invincat-cli --mcp-config ~/.config/my-mcp.json
```

### 9.3 Skill System

Skills are reusable workflow templates defined in Markdown files, loaded by `SkillsMiddleware`:

```
/skill:web-research  Search for LangGraph best practices
/skill:code-review   Check code quality in src/
```

| Location | Path | Scope |
|----------|------|-------|
| Built-in skills | Installed with package | Global |
| Global custom | `~/.invincat/agent/skills/` | Cross-project |
| Project-level | `.invincat/skills/` | Current project only |

### 9.4 Tool Approval (HITL)

All write and execution tool calls require user approval by default, through the Human-in-the-Loop mechanism:

- **Interactive mode**: Approval dialog with approve/reject options
- **Auto-approve mode**: `Shift+Tab` to toggle, or pass `-y` at startup
- **Shell allow-list**: Use `--shell-allow-list` for selective auto-approval of specific commands; others still require confirmation

---

## 10. Session Persistence

### 10.1 Thread Model

Each conversation session corresponds to a unique `thread_id`. Sessions are fully isolated:

```
thread_id = "__default_thread__" (default)
    │
    ├─ /clear → generates new thread_id, old thread preserved in checkpoint
    │
    └─ /threads → lists all historical threads, select one to switch
```

### 10.2 Storage Layers

| Layer | Contents | Location |
|-------|----------|----------|
| LangGraph Checkpoint | Full AgentState (messages + private fields) | Local SQLite / filesystem |
| Conversation History | Raw offloaded messages (Markdown) | `~/.invincat/conversation_history/{thread_id}.md` |
| Long-term Memory | Cross-session structured memory | `memory_user.json` / `memory_project.json` |
| Skills | Workflow templates | `~/.invincat/agent/skills/` / `.invincat/skills/` |

### 10.3 External Hook System

Configure external hooks in `~/.invincat/hooks.json` to trigger custom scripts on key events:

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

- Hooks execute concurrently in a background thread pool with a 5-second timeout per hook
- Hooks receive a JSON event payload via stdin
- An empty or missing `events` list subscribes to all events
- Hook failures are logged only — they never affect the main execution flow

---

## Related Documents

- [Memory Design (Chinese)](./MEMORY_DESIGN.md)
- [Memory Design (English)](./MEMORY_DESIGN_EN.md)
- [README (Chinese)](../README_CN.md)
- [README (English)](../README.md)
