# LocalMind — Product Requirements

## Problem

Local AI models running through Ollama hit two walls immediately:
- **Stale knowledge** — training cutoff 2023+, no current information
- **No file access** — models cannot read PDFs, code, or documents

These are infrastructure problems, not model quality problems.

## Vision

An open-source tool-use and retrieval layer that wraps any Ollama model and makes it behave like a capable AI assistant. Free, local, private, forever.

> "The goal is not to match frontier model intelligence. It is to give local models the same infrastructure that frontier models rely on."

## Target Users

| User | Need |
|---|---|
| Developer | Local AI that fits into existing workflow — code review, file ops, automation |
| Non-technical user | Simple chat UI that reads documents, no setup required |
| Student / researcher | Free alternative to ChatGPT that reads papers |

## Core Capabilities

| # | Capability | Status |
|---|---|---|
| 1 | File reading (PDF, DOCX, TXT, CSV, code) | v0.1 |
| 2 | Web search (DuckDuckGo / Brave) | v0.2 |
| 3 | Cross-session memory (SQLite + ChromaDB) | v0.3 |
| 4 | CLI + scripting support | v0.4 |
| 5 | Code execution (sandboxed Python + JS) | v0.5 |
| 6 | File writing (with confirmation UX) | v0.5 |

## Non-Goals

- Replacing frontier model intelligence
- Another generic Ollama chat UI
- Cloud service or SaaS
- Fine-tuning or modifying models

## Architecture Reference

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Roadmap

### v0.1 — File Reading MVP
**Acceptance criteria:** User opens Web UI, uploads a PDF, asks a question, gets an accurate answer sourced from the document. Runs fully locally, no internet required.

Tasks:
1. FastAPI project structure per ARCHITECTURE.md
2. Ollama adapter — wrap `/v1/chat/completions`, handle streaming
3. File reader tool — parse PDF/DOCX/TXT, chunk by token count
4. Context builder — system prompt + file chunks + history, respect context window
5. React UI — file upload, chat input, streaming response
6. Wire FastAPI routes — `POST /api/chat` with optional file
7. pytest tests for file_reader and context_builder
8. README with install and run instructions

### v0.2 — Web Search
- DuckDuckGo search tool
- Auto-trigger on time-sensitive queries
- Source citation in responses

### v0.3 — Memory
- Session history persistence (SQLite — already scaffolded)
- Cross-session fact store (ChromaDB — already scaffolded)
- Memory viewer in UI

### v0.4 — CLI
- `localmind ask`, `localmind chat`, `localmind start`
- Pipe support: `cat report.pdf | localmind ask "summarise"`
- Config file (YAML)

### v0.5 — Code Execution + File Write
- Sandboxed Python/JS execution
- File write with confirmation UX
- Diff preview for edits

## Competitive Position

| Tool | File Read | Web Search | Code Exec | Lightweight |
|---|---|---|---|---|
| Open WebUI | ✓ | ✗ | ✗ | ✗ Heavy |
| AnythingLLM | ✓ | ✓ | ✗ | ✗ Complex |
| Msty | ✗ | ✗ | ✗ | ✓ |
| **LocalMind** | ✓ | ✓ | ✓ | ✓ |

## Model Profiles

| Profile (UI label) | Model | Min RAM |
|---|---|---|
| Fast & Light | llama3.2:3b | 4 GB |
| Balanced | llama3.1:8b | 6 GB |
| Best for Code | qwen2.5-coder:7b | 6 GB |
| High Quality | deepseek-coder-v2:16b | 12 GB |
| Writing Focused | mistral:7b | 6 GB |
