# Invincat

[![CI](https://github.com/dog-qiuqiu/invincat/actions/workflows/ci.yml/badge.svg)](https://github.com/dog-qiuqiu/invincat/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/dog-qiuqiu/invincat/branch/main/graph/badge.svg)](https://codecov.io/gh/dog-qiuqiu/invincat)
[![PyPI](https://img.shields.io/pypi/v/invincat-cli.svg)](https://pypi.org/project/invincat-cli/)
[![Python](https://img.shields.io/pypi/pyversions/invincat-cli.svg)](https://pypi.org/project/invincat-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

![](data/banner_en.png)

Invincat is a terminal-native AI coding assistant for local repositories. It can inspect and edit files, run shell commands with approval, use web/MCP tools, keep long-term memory, plan before execution, run scheduled tasks, and bridge remote WeCom messages into a project session.

## Features

- Work directly from your project directory in a terminal UI.
- Read, edit, and create files with approval-gated tool execution.
- Run shell commands under configurable safety controls.
- Use `/plan` to review and approve an execution plan before implementation.
- Keep user and project memory across sessions.
- Create recurring or one-shot scheduled tasks in natural language.
- Extend capabilities through MCP tools, skills, and WeCom bot daemon integration.

## Quick Start

```bash
pip install invincat-cli
cd /path/to/your/project
invincat-cli
```

On first launch, run `/model` to configure a provider and model.

## Installation

Requires Python 3.11+.

```bash
pip install invincat-cli
```

Install from source:

```bash
git clone https://github.com/dog-qiuqiu/invincat.git
cd invincat
pip install -e .
```

## Start

Run Invincat from your project directory:

```bash
cd /path/to/your/project
invincat-cli
```

## Model Configuration

After the first launch, run `/model` to open the model manager.

- Press `Ctrl+N` to register a model.
- Fill in the provider, model name, API key, and optional base URL.
- Select a model and press `Enter` to activate it.

You can also provide credentials through environment variables such as:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GOOGLE_API_KEY="..."
export DEEPSEEK_API_KEY="..."
export OPENROUTER_API_KEY="..."
```

Example for DeepSeek:

```bash
export DEEPSEEK_API_KEY="sk-..."
```

Then register a model in `/model` with:

| Field | Value |
| --- | --- |
| Provider | `openai` |
| Model | `deepseek-v4-flash` |
| API Key | `DEEPSEEK_API_KEY` |
| BASE URL | `https://api.deepseek.com` |

Invincat supports a primary model for normal work and an optional memory model for post-turn memory extraction. If no memory model is configured, memory extraction uses the current primary model.

## Basic Commands

| Command | Description |
| --- | --- |
| `/model` | Configure and switch models. |
| `/plan` | Enter plan-first mode and approve a checklist before execution. |
| `/memory` | Open the memory manager. |
| `/schedule` | Open the scheduled task manager. |
| `/mcp` | View connected MCP servers and tools. |
| `/threads` | Browse and resume conversation threads. |
| `/help` | Show command help. |

## Documentation

- [Architecture guide](doc/ARCHITECTURE_EN.md)
- [中文架构说明](doc/ARCHITECTURE.md)

## WeCom Bot Daemon

Configure a robot on the enterprise WeChat side to obtain the "Bot ID" and "Secret":

https://developer.work.weixin.qq.com/document/path/101463

Invincat can run a foreground WeCom bot daemon for project-scoped remote turns and scheduled-task delivery.

Configure the bot credentials first:

```bash
export WECOM_BOT_ID="your_bot_id"
export WECOM_BOT_SECRET="your_bot_secret"
```

`WECOM_WS_URL` is optional and defaults to `wss://openws.work.weixin.qq.com`.
Set it only when you need to override the WeCom websocket endpoint.

Start the daemon from the project directory:

```bash
cd /path/to/your/project
invincat-cli wecombot
```

For a lightweight background process, run it with `nohup`:

```bash
cd /path/to/your/project
mkdir -p .invincat
nohup invincat-cli wecombot > wecombot.nohup.log 2>&1 &
```

Stop it by stopping the foreground process or killing the background process.

## Development

Install development dependencies:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run lint checks:

```bash
ruff check invincat_cli tests
```

## License

MIT License.
