# LocalMind

> Make local AI work like Claude.

LocalMind is an open-source tool-use and retrieval layer that wraps any Ollama model and makes it behave like a capable AI assistant — reading files, searching the web, executing code, and remembering context across sessions. Free, local, private, forever.

## Why LocalMind

Local AI models are powerful but hobbled out of the box:

- They have stale knowledge (training cutoff 2023+)
- They cannot read files you give them
- They have no tools — just text in, text out

LocalMind fixes this by adding the same infrastructure that cloud AI products build silently around their models. The model doesn't get smarter. It gets equipped.

## Features

| Capability | Status |
|---|---|
| File reading (PDF, DOCX, TXT, CSV, code) | v0.1 |
| Web search (DuckDuckGo / Brave) | v0.2 |
| Cross-session memory | v0.3 |
| CLI + scripting support | v0.4 |
| Code execution (sandboxed) | v0.5 |
| File writing (with confirmation) | v0.5 |

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai) installed and running
- At least one model pulled: `ollama pull llama3.1:8b`

## Install

```bash
pip install localmind
```

## Run

```bash
# Launch the web UI (opens in your browser)
localmind start

# Ask a question from the CLI
localmind ask "What is the capital of Kenya?"

# Ask about a file
localmind ask "Summarise this document" --file report.pdf
```

## Recommended Models

| Profile | Model | Min RAM |
|---|---|---|
| Fast & Light | llama3.2:3b | 4 GB |
| Balanced | llama3.1:8b | 6 GB |
| Best for Code | qwen2.5-coder:7b | 6 GB |
| High Quality | deepseek-coder-v2:16b | 12 GB |

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full layer diagram and design decisions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Adding a new tool takes under two hours — drop a Python module in `tools/` and register it.

## License

MIT — free to use, fork, and extend.

---

Built by [Sky Tech Solutions](https://github.com/TisoneK) — Cheptais, Nairobi, Kenya.
