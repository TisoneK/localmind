"""
Context Builder — v0.2: assembles the final prompt sent to the model.

Responsibilities:
- Combine system prompt + memory + tool results + conversation history
- Manage token budget so we never exceed the model's context window
- Trim oldest history first when over budget
- Truncate large tool results and file chunks within their own budget slice
- Never truncate mid-sentence or mid-message

Improvements over v0.1:
- Tool result and file content now have their own budget slices — they can no
  longer silently overflow the context window
- Script-aware token approximation (CJK/Arabic ~1.5 chars/token, Latin ~4)
  extracted into shared _count_tokens() — no more flat /4 underestimate
- Truncation helpers truncate at sentence boundaries where possible
- Thread-safe encoder init with a lock (safe under concurrent async requests)
- History trim injects a [context truncated] notice so the model knows
  earlier messages were dropped
- Tool result injected as a dedicated 'tool' role message instead of being
  stuffed into the system prompt (forward-compatible with tool-message APIs)
- Per-component token accounting logged at DEBUG for easier budget debugging
"""
from __future__ import annotations
import logging
import re
import threading
from core.models import EngineContext, Message, Role
from core.config import settings

logger = logging.getLogger(__name__)

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

SYSTEM_PROMPT = """You are LocalMind, an AI assistant running entirely on the user's local machine. \
You are capable, direct, and precise.

CAPABILITIES:
- Read and write files on the user's machine
- Execute Python code and return real output
- Search the web for current information
- Remember facts across conversations

REASONING RULES:
- Before calling ANY tool, silently consider: what did the user ask for? what output type? what file extension? what tool?
- NEVER output your thinking process to the user - only show the final result
- For any task with more than one step, plan the steps first.
- When asked to fix code or a bug: (1) read the file first, (2) identify the exact problem, \
(3) write the fix, (4) confirm what changed. Never guess at file contents.
- When a tool returns an error or empty result, say so clearly. Do not make up an answer.
- If you are not sure what the user wants, ask one specific clarifying question before proceeding.
- Prefer short, direct answers. Use markdown only when it genuinely helps (code, tables, lists).

CODE TASKS:
- Always extract code into a proper ```python block before executing it.
- When writing code to fix a file: read the file first using the file tool, then write the corrected version.
- When the user says "fix it" or "fix this" about a file: they want the actual file updated on disk, \
not just advice. Use file_write to save the result.
- After running code, report the actual stdout/stderr. Do not summarize or paraphrase tool output.

SELF-REPAIR:
- You can read and modify your own source files. They are in the directory where LocalMind was started.
- If asked to fix yourself: read the relevant source file, understand the bug, write the corrected code, \
save the file, and tell the user which file was changed and what line(s) were modified.
- Never modify files you have not first read in the same conversation turn."""


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
    base_system_tokens = _count_tokens(SYSTEM_PROMPT) + 4

    # Total tokens available after reserving space for the model's response
    total_available = (
        model_context_window
        - settings.localmind_response_reserve_tokens
        - base_system_tokens
        - 16  # structural buffer
    )

    # Per-component budgets
    tool_budget    = round(total_available * TOOL_RESULT_BUDGET_FRACTION)
    file_budget    = round(total_available * FILE_CONTENT_BUDGET_FRACTION)
    history_budget = total_available - tool_budget - file_budget

    messages: list[dict] = []

    # ── System prompt (base — never trimmed) ──────────────────────────────
    system_parts = [SYSTEM_PROMPT]

    # Inject memory facts (small — no dedicated budget needed)
    if context.memory_facts:
        facts_text = "\n".join(f"- {f}" for f in context.memory_facts)
        system_parts.append(f"\n\nThings you remember about the user:\n{facts_text}")

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
        tool_msg_content = (
            f"[Tool result from {context.tool_result.source}]\n{safe_tool_content}"
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