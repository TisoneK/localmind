# LocalMind — Architecture

## Overview

LocalMind is a layered system. Each layer has one responsibility and communicates only with the layer directly above or below it. No layer knows about the user interface. No layer calls the model directly except the adapter.

```
┌─────────────────────────────────────────────────┐
│                   SURFACE                        │
│          Web UI (React)  │  CLI (Typer)          │
└──────────────┬───────────┴───────┬───────────────┘
               │                   │
┌──────────────▼───────────────────▼───────────────┐
│                  API LAYER                        │
│          FastAPI  ·  SSE streaming                │
└──────────────────────┬────────────────────────────┘
                       │
┌──────────────────────▼────────────────────────────┐
│                 CORE ENGINE                        │
│   Intent Router → Tool Dispatcher → Context        │
│                   Builder → Response Assembler     │
└──────┬──────────────┬──────────────┬──────────────┘
       │              │              │
┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼──────────────┐
│ TOOL        │ │ STORAGE   │ │ ADAPTER             │
│ REGISTRY    │ │ SQLite    │ │ Ollama              │
│ file_reader │ │ ChromaDB  │ │ localhost:11434      │
│ web_search  │ └───────────┘ └─────────────────────┘
│ code_exec   │
│ memory      │
│ file_writer │
└─────────────┘
```

## Layers

### Surface
Web UI (React + Vite, served by FastAPI) and CLI (Typer). Both are thin wrappers — all intelligence is in the engine. Adding a new surface requires zero changes to any other layer.

### API Layer
FastAPI application. Handles HTTP routing, multipart file uploads, and SSE streaming. Routes live in `api/routes/` — one file per resource.

### Core Engine
The central coordinator (`core/engine.py`). Orchestrates:
1. **Intent Router** — classifies each message (file task, web search, code exec, memory, chat)
2. **Tool Dispatcher** — calls the relevant tool and collects the result
3. **Context Builder** — assembles system prompt + memory + tool result + history within token budget
4. **Response Assembler** — streams model tokens back to the surface

### Tool Registry
Five isolated modules in `tools/`. Each tool exposes a single `async run()` function. Tools share no state. A broken tool cannot crash the engine.

| Tool | Module | Risk | Milestone |
|---|---|---|---|
| File reader | `tools/file_reader.py` | LOW | v0.1 |
| Web search | `tools/web_search.py` | LOW | v0.2 |
| Memory | `tools/memory.py` | LOW | v0.3 |
| Code executor | `tools/code_executor/` | MEDIUM | v0.5 |
| File writer | `tools/file_writer.py` | HIGH | v0.5 |

### Storage Layer
- **SQLite** (`storage/db.py`) — session history, conversation logs. File-based, zero config.
- **ChromaDB** (`storage/vector.py`) — cross-session semantic memory. Local vector store. v0.3.

### Adapter Layer
Translates core engine calls into runtime-specific API calls. One file per runtime. The engine calls `BaseAdapter.chat()` — it never talks to Ollama directly.

```
adapters/
  base.py      ← interface all adapters implement
  ollama.py    ← wraps Ollama OpenAI-compatible API
  # future: lmstudio.py, llamacpp.py
```

## Request Flow

```
User message
     │
     ▼
Intent Router        classify(message, has_attachment)
     │
     ▼
Tool Dispatcher      dispatch(intent, message)
     │
     ▼
Context Builder      build(context, model_context_window)
     │               = system + memory + tool_result + file + history
     ▼
Ollama Adapter       POST /v1/chat/completions (streaming)
     │
     ▼
Response Assembler   yield StreamChunk → SSE → UI
     │
     ▼
SessionStore         append(session_id, message + response)
```

## Directory Structure

```
localmind/
├── core/                   Core engine
│   ├── config.py           Pydantic settings (from .env)
│   ├── models.py           Shared data models
│   ├── intent_router.py    Message classification
│   ├── context_builder.py  Prompt assembly + token management
│   └── engine.py           Main orchestrator
│
├── tools/                  Tool modules
│   ├── __init__.py         Registry + dispatcher
│   ├── base.py             ToolResult, ToolRisk re-exports
│   ├── file_reader.py      PDF/DOCX/TXT/CSV parsing
│   ├── web_search.py       DuckDuckGo / Brave search
│   ├── memory.py           Persistent fact storage (v0.3)
│   ├── file_writer.py      File creation/editing (v0.5)
│   └── code_executor/      Sandboxed code execution
│       ├── __init__.py
│       ├── detector.py     Language detection
│       ├── sandbox.py      Subprocess isolation
│       └── runner.py       Tool entry point
│
├── adapters/               Model runtime adapters
│   ├── base.py             BaseAdapter interface
│   ├── __init__.py         Registry (get_adapter)
│   └── ollama.py           Ollama OpenAI-compat wrapper
│
├── api/                    FastAPI application
│   ├── app.py              App factory + CORS + static
│   └── routes/
│       ├── chat.py         POST /api/chat (SSE)
│       ├── sessions.py     GET/DELETE /api/sessions
│       ├── models.py       GET /api/models
│       └── health.py       GET /api/health
│
├── storage/                Persistence layer
│   ├── db.py               SQLite session store
│   ├── vector.py           ChromaDB vector store
│   └── migrations/         Schema migrations
│
├── cli/                    Command-line interface
│   ├── main.py             Typer app + command registration
│   └── commands/
│       ├── ask.py          localmind ask "..."
│       ├── chat.py         localmind chat (REPL)
│       ├── start.py        localmind start (web UI)
│       ├── models.py       localmind models
│       ├── health.py       localmind health
│       └── sessions.py     localmind sessions
│
├── ui/                     React frontend (Vite)
│   ├── src/
│   │   ├── pages/          Route-level components
│   │   ├── components/     Reusable UI components
│   │   ├── hooks/          Custom React hooks
│   │   └── lib/            API client, utilities
│   └── package.json
│
├── tests/                  pytest suite
│   ├── conftest.py         Shared fixtures
│   ├── core/               Engine, router, context builder tests
│   ├── tools/              Tool unit tests
│   ├── adapters/           Adapter tests (mocked)
│   ├── api/                FastAPI integration tests
│   └── storage/            DB and vector store tests
│
└── docs/
    ├── ARCHITECTURE.md     This file
    ├── PRD.md              Product requirements
    └── stories/            Dev story definitions
        └── v0.1-story-1-file-reader.md
```

## Design Principles

1. **One direction of dependency** — surfaces → engine → tools/adapter. Never upward.
2. **Tools are isolated** — each tool is a self-contained module. No shared state.
3. **No framework lock-in** — no LangChain. Plain Python. Readable in one hour.
4. **Local by default** — no data leaves the machine except web search (opt-in).
5. **Adapter pattern** — switching from Ollama to LM Studio = one new file.
6. **Confirmations for writes** — file writer requires explicit user confirmation, enforced in the tool.

## Extension Points

### Add a new tool
1. Create `tools/my_tool.py` with `async def run(input_data, context) -> ToolResult`
2. Register in `tools/__init__.py` with an intent mapping
3. Write tests in `tests/tools/test_my_tool.py`

### Add a new adapter
1. Create `adapters/my_runtime.py` subclassing `BaseAdapter`
2. Register in `adapters/__init__.py`
3. Set `LOCALMIND_ADAPTER=my_runtime` in `.env`

### Add a new surface
Call `Engine.process(message, session_id, file=...)` and handle the `StreamChunk` iterator.
