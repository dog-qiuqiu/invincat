# Invincat CLI

[Architecture](doc/ARCHITECTURE_EN.md) | [中文架构](doc/ARCHITECTURE.md)

[![CI](https://github.com/dog-qiuqiu/invincat/actions/workflows/ci.yml/badge.svg)](https://github.com/dog-qiuqiu/invincat/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/dog-qiuqiu/invincat/branch/main/graph/badge.svg)](https://codecov.io/gh/dog-qiuqiu/invincat)
[![PyPI](https://img.shields.io/pypi/v/invincat-cli.svg)](https://pypi.org/project/invincat-cli/)
[![Python](https://img.shields.io/pypi/pyversions/invincat-cli.svg)](https://pypi.org/project/invincat-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

![](data/banner_en.png)

A Python-based terminal AI programming assistant — collaborate with AI directly in your project directory: read/write files, execute commands, browse the web, and maintain memory across sessions.

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
