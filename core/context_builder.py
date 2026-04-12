"""
Context Builder — v0.3: assembles the final prompt sent to the model.

v0.3 additions:
- Intent-aware prompt fragments: loads core/prompts/base.md always, then
  core/prompts/<intent>.md for the active intent only.  Full SYSTEM_PROMPT
  is used as an in-code fallback if the files are missing.
- Compact prompt auto-selected for models with context_window <= 4096.
- Negative budget guard: drops knowledge doc if system prompt alone exceeds window.
"""
from __future__ import annotations
import logging
import re
import threading
from pathlib import Path
from core.models import EngineContext, Message, Role, Intent
from core.config import settings

logger = logging.getLogger(__name__)

# ── Prompt fragment loader ─────────────────────────────────────────────────────
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_fragment_cache: dict[str, str] = {}
_fragment_lock = threading.Lock()


def _load_fragment(name: str) -> str:
    """Load and cache a prompt fragment by name (without .md extension).
    Returns empty string if the file does not exist."""
    with _fragment_lock:
        if name in _fragment_cache:
            return _fragment_cache[name]
        path = _PROMPTS_DIR / f"{name}.md"
        try:
            text = path.read_text(encoding="utf-8").strip()
            _fragment_cache[name] = text
            logger.debug("[context_builder] loaded prompt fragment: %s (%d chars)", name, len(text))
            return text
        except FileNotFoundError:
            logger.debug("[context_builder] prompt fragment not found: %s -- using fallback", name)
            _fragment_cache[name] = ""
            return ""


def _intent_fragment_name(intent: Intent) -> str:
    """Map an Intent to its fragment filename (sans .md)."""
    return intent.value.lower()


def build_system_prompt(intent: Intent, model_context_window: int) -> str:
    """
    Assemble the system prompt for this turn:
      base.md + <intent>.md   (file-based, preferred)
      or SYSTEM_PROMPT / SYSTEM_PROMPT_COMPACT  (in-code fallback)
    """
    base = _load_fragment("base")
    intent_frag = _load_fragment(_intent_fragment_name(intent))

    if base:
        if intent_frag:
            return f"{base}\n\n{intent_frag}"
        return base

    logger.warning("[context_builder] prompt fragments missing -- falling back to in-code prompts")
    if model_context_window <= _COMPACT_PROMPT_THRESHOLD:
        return SYSTEM_PROMPT_COMPACT
    return SYSTEM_PROMPT

# ── Knowledge doc loader ───────────────────────────────────────────────────────
# localmind.md lives next to the source tree root (one level above core/).
# It is loaded once at import time and injected into every system prompt.
# Edit localmind.md to update what the model knows about its own capabilities
# without touching Python code.

_KNOWLEDGE_DOC: str = ""
_KNOWLEDGE_DOC_LOCK = threading.Lock()


def _load_knowledge_doc() -> str:
    """Load localmind.md from the project root. Fails silently if absent."""
    global _KNOWLEDGE_DOC
    with _KNOWLEDGE_DOC_LOCK:
        if _KNOWLEDGE_DOC:
            return _KNOWLEDGE_DOC
        candidates = [
            Path(__file__).parent.parent / "localmind.md",   # running from source
            Path.cwd() / "localmind.md",                      # running from project root
        ]
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
                if text.strip():
                    _KNOWLEDGE_DOC = text
                    logger.info("[context_builder] loaded knowledge doc from %s (%d chars)", path, len(text))
                    return _KNOWLEDGE_DOC
            except OSError:
                continue
        logger.debug("[context_builder] localmind.md not found — skipping knowledge injection")
        return ""

# ── Encoder setup ─────────────────────────────────────────────────────────────

_ENCODER = None
_ENCODER_LOCK = threading.Lock()


def _get_encoder():
    global _ENCODER
    if _ENCODER is not None:
        return _ENCODER
    with _ENCODER_LOCK:
        if _ENCODER is None:
            try:
                import tiktoken
                _ENCODER = tiktoken.get_encoding("cl100k_base")
                logger.debug("[context_builder] using tiktoken cl100k_base encoder")
            except Exception:
                # UTF-8 byte length fallback. Documented limitation: imprecise for
                # non-Latin scripts — use script-aware _count_tokens() to compensate.
                # Long-term fix: bundle a minimal BPE vocab or use Ollama token-count endpoint.
                class _FallbackEncoder:
                    def encode(self, text: str) -> list:
                        # Returns a list whose length approximates token count.
                        # Divide by 4 (Latin baseline); script-aware correction applied
                        # in _count_tokens() on top of this.
                        return list(range(len(text.encode("utf-8")) // 4))
                _ENCODER = _FallbackEncoder()
                logger.warning("[context_builder] tiktoken unavailable — using UTF-8 fallback encoder")
    return _ENCODER


def _count_tokens(text: str) -> int:
    """
    Script-aware token count approximation.
    tiktoken is accurate for most models. When unavailable, the fallback
    encoder returns byte_len//4 (Latin baseline), and we apply a multiplier
    correction here for CJK/Arabic-heavy text.
    """
    raw = len(_get_encoder().encode(text))

    # Detect non-Latin script proportion for fallback correction
    try:
        import tiktoken  # noqa: F401
        return raw  # tiktoken is accurate; no correction needed
    except ImportError:
        pass

    cjk_arabic = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'
        or '\u0600' <= c <= '\u06ff'
        or '\uac00' <= c <= '\ud7a3'
    )
    if len(text) > 0 and cjk_arabic / len(text) > 0.2:
        # CJK/Arabic: ~1.5 chars/token → multiply raw (byte//4) up by ~2.7
        return round(raw * 2.7)
    return raw


def _messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += _count_tokens(m.get("content", ""))
        total += 4  # per-message overhead (role + separators)
    return total


# ── Budget constants ──────────────────────────────────────────────────────────

# Maximum fraction of the available (non-system, non-response-reserve) token
# budget that tool results and file content may each consume.
TOOL_RESULT_BUDGET_FRACTION = 0.35
FILE_CONTENT_BUDGET_FRACTION = 0.40


# ── Truncation helpers ────────────────────────────────────────────────────────

def _truncate_to_token_budget(text: str, max_tokens: int, label: str = "") -> str:
    """
    Truncate text to fit within max_tokens, breaking at a sentence boundary
    where possible. Appends a notice so the model knows content was cut.
    """
    if _count_tokens(text) <= max_tokens:
        return text

    # Try to find a clean sentence break
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept: list[str] = []
    used = 0
    notice_tokens = _count_tokens("\n\n[... content truncated to fit context window ...]")

    for sentence in sentences:
        s_tokens = _count_tokens(sentence) + 1  # +1 for space
        if used + s_tokens + notice_tokens > max_tokens:
            break
        kept.append(sentence)
        used += s_tokens

    if not kept:
        # Single sentence longer than budget — hard cut at word boundary
        words = text.split()
        for i in range(len(words), 0, -1):
            candidate = " ".join(words[:i])
            if _count_tokens(candidate) + notice_tokens <= max_tokens:
                kept = [candidate]
                break

    truncated = " ".join(kept) if kept else text[:max_tokens * 3]
    label_str = f" ({label})" if label else ""
    logger.debug(
        f"[context_builder] truncated{label_str}: "
        f"{_count_tokens(text)} → {_count_tokens(truncated)} tokens"
    )
    return truncated + "\n\n[... content truncated to fit context window ...]"


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are LocalMind, an AI assistant that runs entirely on the user's local machine. \
You are direct, precise, and always use real tools — never simulate or guess their output.

═══════════════════════════════════════════════════════
TOOLS — what you have and exactly when to use each one
═══════════════════════════════════════════════════════

1. WEB_SEARCH
   Use when: user wants current/live information — news, prices, scores, weather, recent events.
   Never use for: facts you already know, time/date (use SYSINFO), or reading local files.
   Behaviour: queries DuckDuckGo first, falls back to SearXNG. Returns up to 5 results.
   Trigger phrases: "search for", "look up", "latest", "current", "today's", "news about".

2. FILE_TASK  (read / analyse / summarise a file)
   Use when: user uploads a file or asks you to read a file already on their machine.
   Supported: PDF, DOCX, TXT, MD, CSV, XLSX, JSON, YAML, TOML, HTML, XML, most code files,
              PNG/JPG/GIF/WEBP (OCR if pytesseract is installed, else metadata only).
   File size limit: 50 MB. Large files are chunked automatically.
   Never use for: writing new files (use FILE_WRITE), running code (use CODE_EXEC).

3. FILE_WRITE  (create / save a file to disk)
   Use when: user asks you to create, write, save, or generate a file.
   Default save location: ~/LocalMind/
   Also allowed: ~/Downloads, ~/Documents, ~/Desktop, ~/Pictures, ~/Music, ~/Videos.
   Behaviour: extracts code from ```fenced blocks``` automatically; asks confirmation before writing.
   Trigger phrases: "write a script", "save this as", "create a file", "make me a …".

4. CODE_EXEC  (run Python code and return real output)
   Use when: user wants to execute Python — compute something, test a snippet, run an algorithm.
   Requirement: code MUST be in a ```python``` fenced block. Plain text is NOT executed.
   Timeout: 30 seconds. Output capped at 4000 chars (stdout + stderr combined).
   Security: runs in the same Python environment as LocalMind — full filesystem/network access.
   Never use for: bash/shell commands (use SHELL), file reading (use FILE_TASK).

5. SHELL  (run system commands, browse folders, check processes)
   Use when: user wants to list files, open apps, check disk/network, run git/pip commands.
   Enabled: yes (LOCALMIND_SHELL_ENABLED=true).
   Timeout: 20 seconds per command.
   Trigger phrases: "show my files", "list documents", "run git", "how much disk space", "open …".
   Never use for: running Python code (use CODE_EXEC), writing files (use FILE_WRITE).

6. SYSINFO  (time, date, hardware specs — instant, no network)
   Use when: user asks for current time, date, day of week, OS version, CPU/RAM/disk specs,
             hostname, username, or installed Python packages.
   Speed: <100 ms, fully offline. Never guess time/date — always call this tool.
   Trigger phrases: "what time is it", "what's today's date", "how much RAM", "my CPU", "my OS".

7. MEMORY_OP  (store, recall, or delete persistent facts)
   Use when: user says "remember that …", "forget …", "what do you know about me", "recall …".
   Storage: semantic vector store (sqlite-vec + nomic-embed-text embeddings).
   Passive retrieval: relevant facts are automatically injected into every conversation turn.
   Explicit commands: "remember X" → stores X | "forget X" → deletes X | "list facts" → shows all.

═══════════════════════════════════════════════════════
INTENT ROUTING — how messages are classified
═══════════════════════════════════════════════════════

Every message goes through three stages before reaching you:
  1. Rule-based router (instant) — catches >90% of messages by keyword patterns.
  2. Semantic classifier (fast, local embeddings) — for ambiguous messages.
  3. LLM classifier (slow, only if both above are uncertain) — last resort.

CHAT intent (no tool call) is used for: general conversation, questions answerable
from your training knowledge, jokes, explanations, advice, creative writing.

═══════════════════════════════════════════════════════
MODEL ROUTING — which model handles each task
═══════════════════════════════════════════════════════

Current configuration:
  Main model:  phi3:mini   — used for CHAT, SYSINFO, memory formatting
  Code model:  llama3.1:8b — used for CODE_EXEC, SHELL, FILE_WRITE
  Fast model:  phi3:mini   — used for quick classification calls

phi3:mini context window: 4096 tokens (tight — keep responses concise).
History is trimmed oldest-first when approaching the limit.

═══════════════════════════════════════════════════════
REASONING RULES — how to think and respond
═══════════════════════════════════════════════════════

- Before any tool call, silently decide: which tool? what exact input? what format should the output be?
- NEVER fabricate tool results. If a tool wasn't called, say so — don't invent output.
- NEVER output your reasoning process. Users see only your final answer.
- When a tool returns an error or empty result, report it plainly. Do not guess at an answer.
- Ask at most ONE clarifying question if the request is genuinely ambiguous.
- Prefer short, direct answers. Use markdown only when it genuinely helps (code blocks, tables, lists).
- For multi-step tasks, plan silently then execute — don't narrate each step before doing it.

═══════════════════════════════════════════════════════
FILE & CODE TASKS — specific rules
═══════════════════════════════════════════════════════

Reading files:
- Always use FILE_TASK to read a file before editing it. Never guess at file contents.
- "Fix this file" means: read it → identify the bug → write the corrected version to disk.

Writing files:
- "Fix it / save it / write it" means the user wants the actual file updated, not just advice.
- Use FILE_WRITE to save the result. Confirm which file was written and what changed.

Running code:
- Wrap code in ```python``` before calling CODE_EXEC.
- After execution, report actual stdout/stderr verbatim. Do not paraphrase tool output.

═══════════════════════════════════════════════════════
SELF-REPAIR
═══════════════════════════════════════════════════════

- You can read and modify your own source files (they are in the directory where LocalMind was started).
- Workflow: read the file → understand the bug → write corrected code → save → report what changed.
- Never modify a file you have not first read in the same conversation turn."""

# Compact prompt for models with context window <= 4096 tokens.
# SYSTEM_PROMPT is ~1600 tokens — too large for gemma3:1b (2048) or phi3:mini (4096).
# This version is ~300 tokens and covers the essentials only.
SYSTEM_PROMPT_COMPACT = """You are LocalMind, a local AI assistant. Be direct and concise.

Tools available (the engine routes automatically — do not call tools yourself):
- WEB_SEARCH: live news, prices, current events
- FILE_TASK: read/analyse uploaded or local files
- FILE_WRITE: create/save files to disk
- CODE_EXEC: run Python code (must be in ```python``` block)
- SHELL: run system commands
- SYSINFO: current time, date, hardware specs (never guess these)
- MEMORY_OP: remember/forget/recall facts ("remember that...")

Rules: never fabricate tool output. Short answers unless detail is needed. \
Use markdown only for code blocks and tables."""

# Threshold below which the compact prompt is used instead of the full one.
_COMPACT_PROMPT_THRESHOLD = 4096


# ── Main builder ──────────────────────────────────────────────────────────────

def build(context: EngineContext, model_context_window: int = 8192) -> list[dict]:
    """
    Assemble the list of messages to send to the model.

    Budget allocation (in tokens):
      total = model_context_window
        - response_reserve        (settings.localmind_response_reserve_tokens)
        - system_prompt           (always injected, never trimmed)
        - 16                      (structural buffer)
      remaining split across:
        - tool result             (up to TOOL_RESULT_BUDGET_FRACTION of remaining)
        - file content            (up to FILE_CONTENT_BUDGET_FRACTION of remaining)
        - history + current msg   (whatever is left, oldest trimmed first)

    Args:
        context: The fully populated EngineContext.
        model_context_window: The model's maximum context window in tokens.

    Returns:
        A list of message dicts in OpenAI chat format.
    """
    # Build intent-aware system prompt from fragment files.
    # base.md is always loaded; <intent>.md adds only the rules for the active
    # tool.  Falls back to in-code constants if fragment files are missing.
    active_system_prompt = build_system_prompt(context.intent, model_context_window)

    # Knowledge doc (localmind.md) is only injected when the context window is
    # large enough to absorb it without crowding out history and tool results.
    # Skip entirely for small-context models — no warning spam, no wasted cycles.
    knowledge = ""
    knowledge_tokens = 0
    if model_context_window > _COMPACT_PROMPT_THRESHOLD:
        knowledge = _load_knowledge_doc()
        knowledge_tokens = _count_tokens(knowledge) if knowledge else 0
        if knowledge and knowledge_tokens > model_context_window * 0.60:
            knowledge = ""
            knowledge_tokens = 0

    base_system_tokens = _count_tokens(active_system_prompt) + 4
    if knowledge:
        base_system_tokens += knowledge_tokens + 4

    # Cap response_reserve to at most 40% of the context window.
    # Without this, a model with a small declared context window (e.g. an
    # incorrectly configured entry) combined with a large reserve causes
    # total_available to collapse to 64 tokens, breaking all tool responses.
    effective_reserve = min(
        settings.localmind_response_reserve_tokens,
        round(model_context_window * 0.40),
    )
    total_available = max(
        model_context_window
        - effective_reserve
        - base_system_tokens
        - 16,
        64,  # hard floor — always fit at least the current message
    )

    # Per-component budgets
    tool_budget    = round(total_available * TOOL_RESULT_BUDGET_FRACTION)
    file_budget    = round(total_available * FILE_CONTENT_BUDGET_FRACTION)
    history_budget = total_available - tool_budget - file_budget

    messages: list[dict] = []

    # ── System prompt (base — never trimmed) ──────────────────────────────
    system_parts = [active_system_prompt]

    # Inject knowledge doc (localmind.md) — loaded once above, cached in memory.
    # Placed after the system prompt so it reads as a reference appendix.
    if knowledge:
        system_parts.append(f"\n\n---\n\n{knowledge}")

    # Inject memory facts — framed explicitly so the model treats them as
    # retrieved data, not suggestions to ignore.
    if context.memory_facts:
        facts_text = "\n".join(f"- {f}" for f in context.memory_facts)
        system_parts.append(
            f"\n\n## Retrieved memory — facts stored from previous conversations\n"
            f"These were retrieved from your vector memory store right now. "
            f"They are things the user told you or that you learned in past sessions. "
            f"Use them to answer questions about the user or their machine.\n"
            f"{facts_text}"
        )

    system_content = "".join(system_parts)
    messages.append({"role": "system", "content": system_content})

    # ── Tool result — dedicated message, budget-capped ────────────────────
    # Injected as a separate message rather than stuffed into the system prompt.
    # This is forward-compatible with tool-message role APIs and keeps system
    # prompt size predictable regardless of tool output size.
    if context.tool_result:
        raw_tool_content = context.tool_result.content
        safe_tool_content = _truncate_to_token_budget(
            raw_tool_content, tool_budget, label=f"tool:{context.tool_result.source}"
        )
        source = context.tool_result.source
        tool_msg_content = (
            f"[Live result from {source} — retrieved just now]\n\n"
            f"{safe_tool_content}\n\n"
            f"Use the values above to answer the user. Do not substitute your own estimates."
        )
        messages.append({"role": "system", "content": tool_msg_content})
        logger.debug(
            f"[context_builder] tool result: "
            f"{_count_tokens(raw_tool_content)} raw → "
            f"{_count_tokens(safe_tool_content)} tokens (budget={tool_budget})"
        )

    # ── File attachment — budget-capped per chunk ─────────────────────────
    if context.file_attachment and context.file_attachment.chunks:
        chunk_budget = file_budget // max(len(context.file_attachment.chunks), 1)
        safe_chunks = [
            _truncate_to_token_budget(chunk, chunk_budget, label=f"file_chunk_{i}")
            for i, chunk in enumerate(context.file_attachment.chunks)
        ]
        chunks_text = "\n\n---\n\n".join(safe_chunks)
        file_content = (
            f"[File: {context.file_attachment.filename}]\n"
            f"{chunks_text}\n\n"
            f"Analyze the file content above and answer the user's question. "
            f"Do not reproduce the file content verbatim in your response."
        )
        messages.append({"role": "system", "content": file_content})
        logger.debug(
            f"[context_builder] file attachment: {len(context.file_attachment.chunks)} chunks, "
            f"budget={file_budget} tokens"
        )

    # ── Conversation history — trim oldest first within budget ────────────
    history_messages = [
        {"role": m.role.value, "content": m.content}
        for m in context.history
        if m.role in (Role.USER, Role.ASSISTANT)
    ]

    current_message = {"role": "user", "content": context.message}
    current_tokens = _count_tokens(current_message["content"]) + 4

    remaining = history_budget - current_tokens
    fitted_history: list[dict] = []
    history_was_trimmed = False

    for msg in reversed(history_messages):
        msg_tokens = _count_tokens(msg["content"]) + 4
        if remaining - msg_tokens < 0:
            history_was_trimmed = True
            break
        fitted_history.insert(0, msg)
        remaining -= msg_tokens

    # Notify the model when history was trimmed so it doesn't hallucinate
    # references to context it can no longer see
    if history_was_trimmed:
        trim_notice = {
            "role": "system",
            "content": (
                "[Note: earlier conversation history was omitted to fit the context window. "
                "You may not have access to everything discussed at the start of this session.]"
            ),
        }
        messages.append(trim_notice)

    messages.extend(fitted_history)
    messages.append(current_message)

    # ── Debug summary ─────────────────────────────────────────────────────
    total_used = _messages_tokens(messages)
    logger.debug(
        f"[context_builder] budget summary: "
        f"window={model_context_window} "
        f"used={total_used} "
        f"history_msgs={len(fitted_history)} "
        f"trimmed={history_was_trimmed}"
    )

    return messages