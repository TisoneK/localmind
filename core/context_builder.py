"""
Context Builder — assembles the final prompt sent to the model.

Responsibilities:
- Combine system prompt + memory + tool results + conversation history
- Manage token budget so we never exceed the model's context window
- Trim oldest history first when over budget
- Never truncate mid-sentence

Token counting uses tiktoken with cl100k_base (reasonable approximation for
most models). Exact counts vary by model but the approximation is close enough
for budget management.
"""
from __future__ import annotations
import tiktoken
from core.models import EngineContext, Message, Role
from core.config import settings

_ENCODER = None

def _get_encoder():
    global _ENCODER
    if _ENCODER is None:
        try:
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Fallback: rough token estimate (1 token ≈ 4 chars) when tiktoken unavailable
            class _FallbackEncoder:
                def encode(self, text: str) -> list:
                    return list(range(len(text) // 4))
            _ENCODER = _FallbackEncoder()
    return _ENCODER

SYSTEM_PROMPT = """You are LocalMind, a helpful AI assistant running entirely on the user's local machine.
You have access to tools that let you read files, search the web, run code, and remember things across conversations.
You are honest about what you know and what you don't know.
When you use a tool, you explain what you found before giving your answer.
You never make up file contents, search results, or code outputs — you only report what the tools actually returned.
Keep responses clear and concise. Use markdown formatting when it helps readability."""


def _count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text))


def _messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += _count_tokens(m.get("content", ""))
        total += 4  # per-message overhead (role + separators)
    return total


def build(context: EngineContext, model_context_window: int = 8192) -> list[dict]:
    """
    Assemble the list of messages to send to the model.

    Args:
        context: The fully populated EngineContext.
        model_context_window: The model's maximum context window in tokens.

    Returns:
        A list of message dicts in OpenAI chat format.
    """
    available_tokens = (
        model_context_window
        - settings.localmind_response_reserve_tokens
        - _count_tokens(SYSTEM_PROMPT)
        - 16  # buffer
    )

    messages: list[dict] = []

    # System prompt is always first and never trimmed
    system_parts = [SYSTEM_PROMPT]

    # Inject memory facts
    if context.memory_facts:
        facts_text = "\n".join(f"- {f}" for f in context.memory_facts)
        system_parts.append(f"\n\nThings you remember about the user:\n{facts_text}")

    # Inject tool results into the system prompt context block
    if context.tool_result:
        tool_text = (
            f"\n\nTool result from [{context.tool_result.source}]:\n"
            f"{context.tool_result.content}"
        )
        system_parts.append(tool_text)

    # Inject file attachment info
    if context.file_attachment and context.file_attachment.chunks:
        chunks_text = "\n\n---\n\n".join(context.file_attachment.chunks)
        file_text = (
            f"\n\nFile attached by user: {context.file_attachment.filename}\n"
            f"Content:\n{chunks_text}"
        )
        system_parts.append(file_text)

    system_content = "".join(system_parts)
    messages.append({"role": "system", "content": system_content})

    # Build history — trim oldest messages first if over budget
    history_messages = [
        {"role": m.role.value, "content": m.content}
        for m in context.history
        if m.role in (Role.USER, Role.ASSISTANT)
    ]

    # Current user message
    current_message = {"role": "user", "content": context.message}

    # Fit history within token budget
    system_tokens = _count_tokens(system_content) + 4
    current_tokens = _count_tokens(current_message["content"]) + 4
    remaining = available_tokens - system_tokens - current_tokens

    fitted_history: list[dict] = []
    for msg in reversed(history_messages):
        msg_tokens = _count_tokens(msg["content"]) + 4
        if remaining - msg_tokens < 0:
            break
        fitted_history.insert(0, msg)
        remaining -= msg_tokens

    messages.extend(fitted_history)
    messages.append(current_message)

    return messages
