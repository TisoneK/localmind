"""
Agent Loop — v0.4: think → act → observe → reflect → adjust

Improvements over v0.3:
- Live thought streaming with XML tag sanitization (fixes blank thinking UI)
- Per-tool retry budget with exponential backoff (transient failures no longer fatal)
- Tool output injection filtering before re-entering context (closes prompt injection gap)
- Intent history window fed into context (improves follow-up message accuracy)
- Structured step cards via AgentTrace surfaced to caller
- AgentStep now records retry_count and latency_ms per tool call
- Observation log truncation per entry (prevents context window bloat)
- Reflection parser hardened with .lower() normalization
- Inline imports eliminated (all imports at module level)
- adapter parameter renamed to active_adapter for clarity
"""
from __future__ import annotations
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from core.models import Intent, StreamChunk, ToolResult
from core.filters import _filter_system_leaks, _filter_tool_injection, _filter_code_output
from tools import dispatch

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3
CLARIFICATION_THRESHOLD = 0.45

# Max characters kept per observation entry in the running log.
# Full observation is stored in AgentStep for trace/debug purposes.
OBS_LOG_MAX_CHARS = 800

# Retry config per tool call
TOOL_MAX_RETRIES = 2
TOOL_RETRY_BASE_DELAY = 0.5  # seconds; doubles each retry

AGENT_INTENTS = {
    Intent.WEB_SEARCH,
    Intent.CODE_EXEC,
    Intent.SHELL,
    Intent.FILE_WRITE,
    Intent.MEMORY_OP,
    # Intent.SYSINFO excluded — instant offline tool, no loop needed
}

_ACTION_PATTERN = re.compile(
    r"<action>\s*tool:\s*(\w+)\s*input:\s*(.*?)\s*</action>",
    re.DOTALL | re.IGNORECASE,
)
_FINISH_PATTERN = re.compile(r"<finish>(.*?)</finish>", re.DOTALL | re.IGNORECASE)
_REFLECT_PATTERN = re.compile(r"<reflect>(.*?)</reflect>", re.DOTALL | re.IGNORECASE)
_CLARIFY_PATTERN = re.compile(r"<clarify>(.*?)</clarify>", re.DOTALL | re.IGNORECASE)

# Strip structured XML tags from raw thought before displaying to user
_TAG_STRIP_PATTERN = re.compile(
    r"<(action|finish|reflect|clarify)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class AgentStep:
    iteration: int
    thought: str
    tool_name: Optional[str]
    tool_input: Optional[str]
    observation: Optional[str]
    reflection: Optional[str] = None
    tool_failed: bool = False
    retry_count: int = 0
    latency_ms: int = 0


@dataclass
class AgentTrace:
    steps: list[AgentStep] = field(default_factory=list)
    final_response: str = ""
    iterations_used: int = 0
    hit_limit: bool = False
    clarification_issued: bool = False

    def summary(self) -> str:
        """Human-readable one-line trace summary for logs."""
        return (
            f"iters={self.iterations_used} steps={len(self.steps)} "
            f"hit_limit={self.hit_limit} clarified={self.clarification_issued}"
        )


def _build_agent_system_prompt(
    intent: Intent,
    available_tools: list[dict],
    intent_history: Optional[list[str]] = None,
) -> str:
    tool_list = "\n".join(
        f"  - {t['intent']}: {t['description']}"
        for t in available_tools
    )
    intent_ctx = ""
    if intent_history:
        intent_ctx = (
            f"\nRecent intent history (most recent last): {', '.join(intent_history)}\n"
            "Use this as a prior when interpreting follow-up messages.\n"
        )
    return f"""You are LocalMind's reasoning agent. You have tools available and MUST use them — never simulate or guess tool results.

Available tools:
{tool_list}
{intent_ctx}
STRICT RULES:
1. To use a tool: output ONLY an <action> block. Nothing else on that iteration.
2. To deliver your final answer: output ONLY a <finish> block. Nothing else.
3. NEVER output <reflect> tags in a <finish> block. Reflection is internal only.
4. NEVER fabricate tool results. If a tool fails, say so in <finish>.
5. For FILE_WRITE: always use the write_file tool — never just show code in <finish>.
6. For time/date/specs: use sysinfo tool — never guess or use training data.
7. After every tool result, decide: is this enough to answer? If yes → <finish>. If no → next <action>.

FORMAT:

Use a tool:
<action>
tool: <tool_name_from_list>
input: <exact input for the tool>
</action>

Deliver answer (plain markdown, no XML tags inside):
<finish>
Your complete answer here.
</finish>

Internal reflection (NEVER shown to user, use sparingly):
<reflect>
quality: good|partial|failed
issue: what went wrong
next: what to try instead
</reflect>

Current intent: {intent.value}
Max iterations: {MAX_ITERATIONS}
"""


def _sanitize_thought_for_display(thought: str) -> str:
    """Strip structured XML tags from raw LLM thought before showing to user."""
    cleaned = _TAG_STRIP_PATTERN.sub("", thought).strip()
    return cleaned


def _truncate_observation(obs: str, max_chars: int = OBS_LOG_MAX_CHARS) -> str:
    if len(obs) <= max_chars:
        return obs
    half = max_chars // 2
    return obs[:half] + f"\n... [{len(obs) - max_chars} chars truncated] ...\n" + obs[-half:]


async def _dispatch_with_retry(
    tool_intent: Intent,
    tool_input: str,
    max_retries: int = TOOL_MAX_RETRIES,
    base_delay: float = TOOL_RETRY_BASE_DELAY,
) -> tuple[Optional[ToolResult], bool, int]:
    """
    Dispatch a tool with exponential backoff retries.
    Returns (result, failed, retry_count).
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = await dispatch(tool_intent, tool_input)
            return result, False, attempt
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"[agent] tool={tool_intent.value} attempt={attempt+1} failed: {e} "
                    f"— retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

    logger.error(f"[agent] tool={tool_intent.value} exhausted retries: {last_error}")
    return None, True, max_retries


class AgentLoop:
    def __init__(self, adapter):
        self._adapter = adapter

    async def run(
        self,
        messages: list[dict],
        intent: Intent,
        initial_tool_result: Optional[ToolResult],
        available_tools: list[dict],
        confidence: float = 1.0,
        adapter=None,
        intent_history: Optional[list[str]] = None,
    ) -> AsyncIterator[StreamChunk]:
        # Renamed from `adapter` parameter to avoid shadowing self._adapter
        active_adapter = adapter or self._adapter
        trace = AgentTrace()

        # ── Clarification gate ────────────────────────────────────────────
        if confidence < CLARIFICATION_THRESHOLD and intent in AGENT_INTENTS:
            clarification = (
                f"I want to make sure I understand — it looks like you want me to "
                f"use the **{intent.value.replace('_', ' ')}** tool, but I'm not very confident. "
                f"Could you confirm, or rephrase what you'd like me to do?"
            )
            trace.clarification_issued = True
            yield StreamChunk(text=clarification, done=False)
            yield StreamChunk(text="", done=True)
            return

        # ── Build system prompt with intent history ───────────────────────
        agent_system = _build_agent_system_prompt(intent, available_tools, intent_history)
        loop_messages = list(messages)
        if loop_messages and loop_messages[0]["role"] == "system":
            loop_messages[0] = {
                "role": "system",
                "content": loop_messages[0]["content"] + "\n\n" + agent_system,
            }
        else:
            loop_messages.insert(0, {"role": "system", "content": agent_system})

        observation_log = ""
        last_tool_failed = False
        consecutive_failures = 0

        if initial_tool_result:
            # Filter injection from initial tool result before it enters context
            safe_initial = _filter_tool_injection(initial_tool_result.content)
            observation_log = (
                f"[Initial tool result from {initial_tool_result.source}]\n"
                f"{_truncate_observation(safe_initial)}\n"
            )

        for iteration in range(MAX_ITERATIONS):
            trace.iterations_used = iteration + 1

            failure_hint = ""
            if last_tool_failed and consecutive_failures >= 2:
                failure_hint = (
                    "\nWARNING: Multiple tool failures. Consider providing your "
                    "best answer from existing context instead of retrying."
                )

            context_msg = (
                f"{observation_log}{failure_hint}\n"
                f"Iteration {iteration + 1}/{MAX_ITERATIONS}. "
                f"Think, reflect if needed, then act or finish."
            )
            iteration_messages = loop_messages + [{"role": "user", "content": context_msg}]

            # ── Collect thought, stream sanitized version live ────────────
            thought_chunks: list[str] = []
            try:
                async for chunk in asyncio.wait_for(
                    active_adapter.chat(iteration_messages, temperature=0.3),
                    timeout=30.0  # 30 second timeout per LLM call
                ):
                    thought_chunks.append(chunk.text)
            except asyncio.TimeoutError:
                logger.warning(f"[agent] LLM call timed out at iteration {iteration+1}")
                thought = "finish: I apologize, but I'm taking too long to respond. Let me provide my best answer based on what I've gathered so far."
                thought_chunks = [thought]
            thought = "".join(thought_chunks)

            # Stream the reasoning text (tags stripped) so UI shows thinking
            thinking_display = _sanitize_thought_for_display(thought)
            if thinking_display:
                yield StreamChunk(text=f"*{thinking_display}*\n\n", done=False)

            logger.debug(f"[agent] iter={iteration+1} thought={thought[:100]}...")

            # ── Clarify ───────────────────────────────────────────────────
            clarify_match = _CLARIFY_PATTERN.search(thought)
            if clarify_match:
                question = clarify_match.group(1).strip()
                trace.clarification_issued = True
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                    reflection="clarification issued",
                ))
                yield StreamChunk(text=question, done=False)
                yield StreamChunk(text="", done=True)
                return

            # ── Finish ────────────────────────────────────────────────────
            finish_match = _FINISH_PATTERN.search(thought)
            if finish_match:
                final_response = finish_match.group(1).strip()
                final_response = _filter_system_leaks(final_response)
                trace.final_response = final_response
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                ))
                yield StreamChunk(text=final_response, done=False)
                yield StreamChunk(text="", done=True)
                logger.info(f"[agent] finished at iteration {iteration+1} — {trace.summary()}")
                return

            # ── Reflect ───────────────────────────────────────────────────
            reflect_match = _REFLECT_PATTERN.search(thought)
            if reflect_match:
                reflection_text = reflect_match.group(1).strip()
                observation_log += f"\n[Reflection]\n{reflection_text}\n"

                # Hardened parser: normalize case before matching
                quality = "unknown"
                issue = ""
                next_step = ""
                for line in reflection_text.splitlines():
                    line_lower = line.strip().lower()
                    if line_lower.startswith("quality:"):
                        quality = line_lower.split(":", 1)[1].strip()
                    elif line_lower.startswith("issue:"):
                        issue = line.split(":", 1)[1].strip()
                    elif line_lower.startswith("next:"):
                        next_step = line.split(":", 1)[1].strip()

                last_tool_failed = quality == "failed"

                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                    reflection=reflection_text,
                ))

                # Stream structured reflection card
                yield StreamChunk(text=f"\n### Reasoning\n", done=False)
                status_map = {
                    "good": "✓ Task completed successfully.",
                    "partial": "↻ Partial progress — continuing...",
                    "failed": "✗ Previous approach failed — trying alternative.",
                }
                yield StreamChunk(
                    text=status_map.get(quality, f"Status: {quality}") + "\n",
                    done=False,
                )
                if issue:
                    yield StreamChunk(text=f"**Problem:** {issue}\n", done=False)
                if next_step:
                    yield StreamChunk(text=f"**Next:** {next_step}\n", done=False)
                continue

            # ── Action ────────────────────────────────────────────────────
            action_match = _ACTION_PATTERN.search(thought)
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_input = action_match.group(2).strip()
                logger.info(f"[agent] iter={iteration+1} tool={tool_name} input={tool_input[:80]}")

                yield StreamChunk(text=f"\n### Action: `{tool_name}`\n", done=False)
                action_labels = {
                    "file_write": f"Creating file: `{tool_input}`",
                    "code_exec": "Executing code...",
                    "web_search": f"Searching: *{tool_input}*",
                    "read_file": f"Reading: `{tool_input}`",
                }
                yield StreamChunk(
                    text=action_labels.get(tool_name, f"Input: {tool_input}") + "\n",
                    done=False,
                )

                t_tool = time.monotonic()
                try:
                    tool_intent = Intent(tool_name)
                    tool_result, tool_failed, retry_count = await _dispatch_with_retry(
                        tool_intent, tool_input
                    )
                    latency_ms = round((time.monotonic() - t_tool) * 1000)

                    if tool_result:
                        # Filter prompt injection from tool output before re-entering context
                        if tool_intent == Intent.CODE_EXEC:
                            safe_obs = _filter_code_output(tool_result.content)
                        else:
                            safe_obs = _filter_tool_injection(tool_result.content)
                        observation = safe_obs
                        last_tool_failed = False
                        consecutive_failures = 0
                        if retry_count > 0:
                            yield StreamChunk(
                                text=f"*(succeeded after {retry_count} retr{'y' if retry_count == 1 else 'ies'})*\n",
                                done=False,
                            )
                    else:
                        observation = f"[Tool '{tool_name}' returned no result after {retry_count} retries]"
                        tool_failed = True
                        latency_ms = round((time.monotonic() - t_tool) * 1000)

                except (ValueError, Exception) as e:
                    observation = f"[Tool '{tool_name}' failed: {e}]"
                    tool_failed = True
                    retry_count = 0
                    latency_ms = round((time.monotonic() - t_tool) * 1000)

                if tool_failed:
                    last_tool_failed = True
                    consecutive_failures += 1

                trace.steps.append(AgentStep(
                    iteration=iteration + 1,
                    thought=thought,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    observation=observation[:500],  # cap trace storage
                    tool_failed=tool_failed,
                    retry_count=retry_count,
                    latency_ms=latency_ms,
                ))

                # Truncate before adding to running context to control window growth
                observation_log += (
                    f"\n[Tool: {tool_name} | Input: {tool_input[:60]} | "
                    f"Status: {'FAILED' if tool_failed else 'OK'} | {latency_ms}ms]\n"
                    f"{_truncate_observation(observation)}\n"
                )
                continue

            # ── No structured tag found — treat as finish ─────────────────
            logger.warning(f"[agent] no tag at iteration {iteration+1}, treating as finish")
            filtered_thought = _filter_system_leaks(thought)
            yield StreamChunk(text=filtered_thought, done=False)
            yield StreamChunk(text="", done=True)
            return

        # ── Hit MAX_ITERATIONS ────────────────────────────────────────────
        trace.hit_limit = True
        logger.warning(f"[agent] hit MAX_ITERATIONS={MAX_ITERATIONS} — {trace.summary()}")
        force_msg = loop_messages + [{
            "role": "user",
            "content": (
                f"{observation_log}\n"
                "Maximum iterations reached. Provide your best answer now "
                "based on everything gathered so far."
            )
        }]
        try:
            async for chunk in asyncio.wait_for(
                active_adapter.chat(force_msg, temperature=0.4),
                timeout=30.0  # 30 second timeout
            ):
                filtered_chunk = StreamChunk(
                    text=_filter_system_leaks(chunk.text),
                    done=chunk.done,
                )
                yield filtered_chunk
        except asyncio.TimeoutError:
            logger.warning("[agent] Final LLM call timed out")
            yield StreamChunk(
                text="I apologize, but I'm unable to complete this request due to time constraints. Please try rephrasing your question or breaking it into smaller parts.",
                done=True
            )
