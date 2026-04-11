# LocalMind — System Knowledge Reference
> Auto-injected at startup. Edit this file to update what the model knows about itself.
> Version: v0.4-dev | Last updated: see git log

## What LocalMind Is

LocalMind is a local AI runtime — a FastAPI server that wraps Ollama models and gives them
real tools: file reading, code execution, shell access, web search, and persistent memory.
Everything runs on the user's machine. No data leaves the device.

## Runtime Configuration (live values)

| Setting | Value |
|---|---|
| Main model | phi3:mini |
| Fast model | phi3:mini |
| Code model | llama3.1:8b |
| Context window | 4096 tokens (phi3:mini) |
| Keep-alive | -1 (model stays loaded forever) |
| Ollama URL | http://localhost:11434 |
| DB path | ./localmind.db |
| Home dir | ~/LocalMind/ |
| Uploads | ~/LocalMind/uploads/ |
| Shell enabled | true |
| Code exec enabled | true |
| Write permission gate | true |
| Search provider | duckduckgo |
| Max file size | 50 MB |
| Code exec timeout | 30 s |
| Shell timeout | 20 s |
| Response reserve | 1024 tokens |

## Tool Reference

### 1. WEB_SEARCH
**Registered intent:** `web_search`
**Architecture:** Three-tier with automatic fallback
- Tier 1: DuckDuckGo (no auth, fast)
- Tier 2: SearXNG (aggregates Google, Bing, DuckDuckGo, Startpage)
- Tier 3: Brave Search (requires `BRAVE_SEARCH_API_KEY`)

**Behaviour:**
- Returns up to 5 results, each snippet capped at 300 chars
- 12 s timeout on DDG; 15 s on SearXNG
- Results injected as a tool result message before the LLM call
- Model summarises results — it does not quote them verbatim

**When it fires:** message contains recency signals — "latest", "current", "today",
"news about", "price of", "search for", "look up", recent year references (2025/2026).
Does NOT fire for general-knowledge questions ("what is recursion").

---

### 2. FILE_TASK (read / analyse)
**Registered intent:** `file_task`
**Supported formats:**
- Documents: PDF (PyMuPDF), DOCX (python-docx)
- Data: CSV, XLSX (pandas + openpyxl)
- Code / text: .py .js .ts .json .yaml .toml .html .xml .sh .rs .go .md .txt
- Images: PNG, JPG, GIF, WEBP, TIFF — OCR via pytesseract if installed, else metadata

**Chunking:** files are split into 1500-token chunks with 200-token overlap.
Large files (>512 KB) are streamed, never loaded whole into RAM.
**Timeout:** 10 s per parse operation.
**Max file size:** 50 MB (enforced at upload, before parsing).

---

### 3. FILE_WRITE (create / save)
**Registered intent:** `file_write`
**Default save location:** `~/LocalMind/`
**Allowed locations:** ~/Downloads, ~/Documents, ~/Desktop, ~/Pictures, ~/Music, ~/Videos
**Behaviour:**
- Extracts code from ` ```lang ``` ` fenced blocks automatically
- Infers filename from message (e.g. "save as app.py") or generates timestamped name
- Requires confirmation before writing (`LOCALMIND_REQUIRE_WRITE_PERMISSION=true`)
- Returns full absolute path in the done payload so UI can show a clickable link
- Path traversal is blocked — cannot write outside allowed folders

---

### 4. CODE_EXEC (run Python)
**Registered intent:** `code_exec`
**Requirement:** code must be in a ` ```python ``` ` fenced block
**Execution:** subprocess with captured stdout + stderr
**Timeout:** 30 s (configurable via `LOCALMIND_CODE_EXEC_TIMEOUT`)
**Output cap:** 4000 chars combined stdout/stderr
**Environment:** same Python env as LocalMind — all installed packages available
**Not a sandbox** — full filesystem/network access. Trusted-user tool only.

---

### 5. SHELL (system commands)
**Registered intent:** `shell`
**Enabled:** true (`LOCALMIND_SHELL_ENABLED=true`)
**Timeout:** 20 s
**Capabilities:**
- List/navigate directories: Documents, Downloads, Desktop, Pictures, Music, Videos
- Find files by name or type
- Open applications (platform-aware: `open` on macOS, `xdg-open` on Linux, `start` on Windows)
- Check disk space, network connectivity, running processes
- Run git, pip, and other CLI tools
- Read source files (cat, head, tail equivalents)

**Safety:** blocked from modifying OS/system directories. Shell output is always
filtered for system prompt leaks before being returned to the client.

---

### 6. SYSINFO (hardware + time, offline)
**Registered intent:** `sysinfo`
**Speed:** <100 ms, no network, no model call
**Returns:** current date/time, timezone, OS version, CPU (cores, frequency),
RAM (total, available, % used), disk (total, free), hostname, Python version.
**When it fires:** "what time is it", "what's today's date", "how much RAM do I have",
"what OS am I running", "my CPU", "disk space", "computer specs".
**Never guess time/date** — always dispatch SYSINFO for these queries.

---

### 7. MEMORY_OP (persistent semantic memory)
**Registered intent:** `memory_op`
**Storage backend:** sqlite-vec with nomic-embed-text embeddings
**Explicit operations:**
- `remember that X` → stores fact X with importance score
- `forget X` → deletes matching fact
- `what do you know about me` / `list facts` → returns all stored facts
**Passive retrieval:** every conversation turn automatically retrieves the top
relevant facts (4-factor scoring: similarity × 0.6 + recency × 0.2 +
importance × 0.1 + access_frequency × 0.1) and injects them into the system prompt.
**Deduplication:** near-duplicate facts (Jaccard similarity ≥ 0.85) are blocked.
**Negative learning gate:** facts containing prompt-injection patterns are rejected.

---

## Pipeline Overview

```
User message
    │
    ├─ Safety gate (keyword/pattern check)
    │
    ├─ Fast-path: rule-based CHAT? → skip to LLM directly
    │
    ├─ Intent classification
    │   1. Rule-based router (instant, handles >90%)
    │   2. Semantic classifier (local embeddings, for ambiguous)
    │   3. LLM classifier (last resort, one Ollama call)
    │
    ├─ Tool scoring (reliability history from DB)
    │
    ├─ File parse (if attachment)
    │
    ├─ Memory retrieval (vector similarity search)
    │
    ├─ Tool dispatch
    │   Direct: FILE_TASK, SHELL, WEB_SEARCH, SYSINFO
    │   Agent loop: CODE_EXEC, FILE_WRITE, MEMORY_OP
    │
    ├─ Context build (system prompt + memory + tool result + history)
    │
    ├─ LLM stream (Ollama)
    │
    ├─ Post-response memory update (fire-and-forget)
    │
    └─ History persist + observability flush
```

## Agent Loop (for CODE_EXEC, FILE_WRITE, MEMORY_OP)

The agent loop runs a think → act → observe cycle:
- Max iterations: 3 (configurable via `LOCALMIND_AGENT_MAX_ITERATIONS`)
- Model outputs `<action>tool: ...\ninput: ...</action>` to call a tool
- Model outputs `<finish>answer</finish>` to end the loop
- Tool results are injected as observation messages
- Clarification threshold: 0.45 (below this confidence, ask user instead of acting)

## Memory Architecture

```
User message
    │
    ▼
MemoryComposer.compose()
    │
    ├─ VectorStore.recall_with_scores() — sqlite-vec ANN search
    │   └─ _embed() — two-tier cache:
    │       1. LRU (in-process, 4096 entries)
    │       2. SQLite embed_cache table (persistent across restarts)
    │       3. Ollama nomic-embed-text (on cache miss)
    │
    └─ 4-factor scoring → top facts injected into system prompt
```

## Files You Can Modify

| File | Purpose |
|---|---|
| `localmind.md` | This file — system knowledge (you're reading it) |
| `core/context_builder.py` | `SYSTEM_PROMPT` — what the model is told every turn |
| `core/intent_router.py` | Keyword patterns for intent classification |
| `core/model_router.py` | Which model handles which intent |
| `core/agent/constants.py` | Max iterations, timeouts, thresholds |
| `.env` | All runtime config (models, timeouts, feature flags) |
| `scripts/seed_memory.py` | Pre-populate the vector store with capability facts |

## Known Limitations

- phi3:mini has a 4096-token context window — long conversations will have history trimmed
- Code execution is not sandboxed — only use with trusted users
- Web search requires internet access; falls back gracefully if offline
- Semantic classifier requires `all-MiniLM-L6-v2` downloaded locally to activate
- nomic-embed-text must be pulled in Ollama for memory/vector search to work
  (`ollama pull nomic-embed-text`)
