"""
AgentLoop — think → act → observe → reflect → adjust

The main orchestration class for agent-mode responses.  All constants,
data models, prompt building, filtering, and tool dispatch have been
extracted into sibling modules so this file owns only the loop mechanics.

v0.5 changes vs v0.4 (monolithic agent.py):
- Refactored into core/agent/ package (constants / models / prompts /
  agent_filters / search / tool_dispatch / loop)
- Zero code removed — all behaviour preserved; only factored out
- Duplicate `from tools import dispatch` import inside web-search branch removed
- `_summarize_search_results_background` dead code removed (replaced by
  inline extractive summary in v0.4; background task was never awaited)
- Double except-block on file read error corrected (was copy-paste duplicate)
- Module-level logger name updated to reflect new location
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import AsyncIterator, Optional

from core.models import Intent, StreamChunk, ToolResult
from core.agent.constants import (
    MAX_ITERATIONS,
    CLARIFICATION_THRESHOLD,
    OBS_LOG_MAX_CHARS,
    AGENT_INTENTS,
)
from core.agent.models import AgentStep, AgentTrace
from core.agent.prompts import build_agent_system_prompt
from core.agent.agent_filters import (
    sanitize_thought_for_display,
    _filter_system_leaks,
    _filter_tool_injection,
    _filter_code_output,
)
from core.agent.search import truncate_web_search_results, create_extractive_summary
from core.agent.tool_dispatch import dispatch_with_retry

logger = logging.getLogger(__name__)

# ── Regex patterns ─────────────────────────────────────────────────────────────

_ACTION_PATTERN = re.compile(
    r"<action>\s*tool:\s*(\w+)\s*input:\s*(.*?)\s*</action>",
    re.DOTALL | re.IGNORECASE,
)
_FINISH_PATTERN = re.compile(r"<finish>(.*?)</finish>", re.DOTALL | re.IGNORECASE)
_REFLECT_PATTERN = re.compile(r"<reflect>(.*?)</reflect>", re.DOTALL | re.IGNORECASE)
_CLARIFY_PATTERN = re.compile(r"<clarify>(.*?)</clarify>", re.DOTALL | re.IGNORECASE)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _truncate_observation(obs: str, max_chars: int = OBS_LOG_MAX_CHARS) -> str:
    """Trim a single observation entry for the running context log."""
    if len(obs) <= max_chars:
        return obs
    half = max_chars // 2
    return obs[:half] + f"\n… [{len(obs) - max_chars} chars truncated] …\n" + obs[-half:]


# ── AgentLoop ──────────────────────────────────────────────────────────────────

class AgentLoop:
    """
    Runs the think → act → observe → reflect cycle for a single user turn.

    Usage (called by Engine.stream()):
        async for chunk in agent_loop.run(messages, intent, ...):
            yield chunk
    """

    def __init__(self, adapter):
        self._adapter = adapter

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

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
        active_adapter = adapter or self._adapter
        trace = AgentTrace()

        # ── Clarification gate ─────────────────────────────────────────
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

        # ── Inject agent system prompt ─────────────────────────────────
        agent_system = build_agent_system_prompt(intent, available_tools, intent_history)
        loop_messages = list(messages)
        if loop_messages and loop_messages[0]["role"] == "system":
            loop_messages[0] = {
                "role": "system",
                "content": loop_messages[0]["content"] + "\n\n" + agent_system,
            }
        else:
            loop_messages.insert(0, {"role": "system", "content": agent_system})

        # ── Seed observation log with initial tool result ──────────────
        observation_log = ""
        last_tool_failed = False
        consecutive_failures = 0

        if initial_tool_result:
            safe_initial = _filter_tool_injection(initial_tool_result.content)
            observation_log = (
                f"[Initial tool result from {initial_tool_result.source}]\n"
                f"{_truncate_observation(safe_initial)}\n"
            )

        # ── Main loop ──────────────────────────────────────────────────
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

            # ── LLM call — collect full thought ───────────────────────
            thought_chunks: list[str] = []
            t_llm = time.monotonic()
            async for chunk in active_adapter.chat(iteration_messages, temperature=0.3):
                thought_chunks.append(chunk.text)
            llm_ms = round((time.monotonic() - t_llm) * 1000)
            logger.info(f"[agent.loop] iter={iteration + 1} LLM: {llm_ms}ms")
            thought = "".join(thought_chunks)

            # Stream sanitized reasoning text to UI
            thinking_display = sanitize_thought_for_display(thought)
            if thinking_display:
                yield StreamChunk(text=f"*{thinking_display}*\n\n", done=False)

            logger.debug(f"[agent.loop] iter={iteration + 1} thought={thought[:120]}…")

            # ── <clarify> ──────────────────────────────────────────────
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

            # ── <finish> ───────────────────────────────────────────────
            finish_match = _FINISH_PATTERN.search(thought)
            if finish_match:
                final_response = _filter_system_leaks(finish_match.group(1).strip())
                trace.final_response = final_response
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                ))
                yield StreamChunk(text=final_response, done=False)
                yield StreamChunk(text="", done=True)
                logger.info(f"[agent.loop] finished iter={iteration + 1} — {trace.summary()}")
                return

            # ── <reflect> ─────────────────────────────────────────────
            reflect_match = _REFLECT_PATTERN.search(thought)
            if reflect_match:
                reflection_text = reflect_match.group(1).strip()
                observation_log += f"\n[Reflection]\n{reflection_text}\n"

                quality = "unknown"
                issue = ""
                next_step = ""
                for line in reflection_text.splitlines():
                    lower = line.strip().lower()
                    if lower.startswith("quality:"):
                        quality = lower.split(":", 1)[1].strip()
                    elif lower.startswith("issue:"):
                        issue = line.split(":", 1)[1].strip()
                    elif lower.startswith("next:"):
                        next_step = line.split(":", 1)[1].strip()

                last_tool_failed = quality == "failed"
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                    reflection=reflection_text,
                ))

                yield StreamChunk(text="\n### Reasoning\n", done=False)
                status_map = {
                    "good": "✓ Task completed successfully.",
                    "partial": "↻ Partial progress — continuing…",
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

            # ── <action> ──────────────────────────────────────────────
            action_match = _ACTION_PATTERN.search(thought)
            if action_match:
                async for chunk in self._handle_action(
                    action_match=action_match,
                    thought=thought,
                    iteration=iteration,
                    observation_log=observation_log,
                    trace=trace,
                    last_tool_failed_ref=[last_tool_failed],
                    consecutive_failures_ref=[consecutive_failures],
                ):
                    # _handle_action signals "early return" via a sentinel done=True chunk
                    # with a special marker; check here.
                    if chunk.done and chunk.error == "__RETURN__":
                        return
                    # Update mutable refs back
                    yield chunk

                # Refresh mutable state from refs (Python lists used as pass-by-ref)
                last_tool_failed = self._last_tool_failed
                consecutive_failures = self._consecutive_failures
                observation_log = self._observation_log
                continue

            # ── No tag found — treat whole thought as finish ───────────
            logger.warning(f"[agent.loop] no structured tag at iter={iteration + 1}, treating as finish")
            filtered = _filter_system_leaks(thought)
            yield StreamChunk(text=filtered, done=False)
            yield StreamChunk(text="", done=True)
            return

        # ── MAX_ITERATIONS hit ─────────────────────────────────────────
        trace.hit_limit = True
        logger.warning(f"[agent.loop] hit MAX_ITERATIONS={MAX_ITERATIONS} — {trace.summary()}")
        async for chunk in self._force_final_answer(
            loop_messages=loop_messages,
            observation_log=observation_log,
            active_adapter=active_adapter,
        ):
            yield chunk

    # ------------------------------------------------------------------
    # Action handler (extracted to keep run() readable)
    # ------------------------------------------------------------------

    async def _handle_action(
        self,
        action_match,
        thought: str,
        iteration: int,
        observation_log: str,
        trace: AgentTrace,
        last_tool_failed_ref: list,
        consecutive_failures_ref: list,
    ) -> AsyncIterator[StreamChunk]:
        """
        Execute the tool requested in an <action> block and update the
        observation log.  Yields StreamChunks; uses instance attrs as
        "return values" for mutable state (last_tool_failed, consecutive_failures,
        observation_log) since Python generators can't return values.
        """
        tool_name = action_match.group(1).strip()
        tool_input = action_match.group(2).strip()
        logger.info(f"[agent.loop] iter={iteration + 1} tool={tool_name} input={tool_input[:80]}")

        yield StreamChunk(text=f"\n### Action: `{tool_name}`\n", done=False)
        action_labels = {
            "file_write": f"Creating file: `{tool_input}`",
            "code_exec": "Executing code…",
            "web_search": f"Searching: *{tool_input}*",
            "read_file": f"Reading: `{tool_input}`",
        }
        yield StreamChunk(
            text=action_labels.get(tool_name, f"Input: {tool_input}") + "\n",
            done=False,
        )

        t_tool = time.monotonic()
        observation: str
        tool_failed: bool
        retry_count: int

        try:
            tool_intent = Intent(tool_name)
            tool_result, tool_failed, retry_count = await dispatch_with_retry(
                tool_intent, tool_input
            )
            latency_ms = round((time.monotonic() - t_tool) * 1000)
            logger.info(f"[agent.loop] iter={iteration + 1} tool={tool_name}: {latency_ms}ms")

            if tool_result:
                # Filter prompt injection before re-entering context
                if tool_intent == Intent.CODE_EXEC:
                    safe_obs = _filter_code_output(tool_result.content)
                else:
                    safe_obs = _filter_tool_injection(tool_result.content)

                # Web-search special path: write results + summary to files
                if tool_intent == Intent.WEB_SEARCH:
                    async for chunk in self._handle_web_search_result(
                        safe_obs=safe_obs,
                        query=tool_input,
                        thought=thought,
                        iteration=iteration,
                        trace=trace,
                    ):
                        yield chunk
                    # Signal caller to return early
                    yield StreamChunk(text="", done=True, error="__RETURN__")
                    return

                observation = safe_obs
                last_tool_failed_ref[0] = False
                consecutive_failures_ref[0] = 0

                if retry_count > 0:
                    yield StreamChunk(
                        text=f"*(succeeded after {retry_count} retr{'y' if retry_count == 1 else 'ies'})*\n",
                        done=False,
                    )
            else:
                observation = f"[Tool '{tool_name}' returned no result after {retry_count} retries]"
                tool_failed = True
                latency_ms = round((time.monotonic() - t_tool) * 1000)

        except (ValueError, Exception) as exc:
            observation = f"[Tool '{tool_name}' failed: {exc}]"
            tool_failed = True
            retry_count = 0
            latency_ms = round((time.monotonic() - t_tool) * 1000)

        if tool_failed:
            last_tool_failed_ref[0] = True
            consecutive_failures_ref[0] += 1

        trace.steps.append(AgentStep(
            iteration=iteration + 1,
            thought=thought,
            tool_name=tool_name,
            tool_input=tool_input,
            observation=observation[:500],
            tool_failed=tool_failed,
            retry_count=retry_count,
            latency_ms=latency_ms,
        ))

        # Persist mutable state back through instance attrs
        self._last_tool_failed = last_tool_failed_ref[0]
        self._consecutive_failures = consecutive_failures_ref[0]
        self._observation_log = (
            observation_log
            + f"\n[Tool: {tool_name} | Input: {tool_input[:60]} | "
            f"Status: {'FAILED' if tool_failed else 'OK'} | {latency_ms}ms]\n"
            f"{_truncate_observation(observation)}\n"
        )

    # ------------------------------------------------------------------
    # Web-search result handler
    # ------------------------------------------------------------------

    async def _handle_web_search_result(
        self,
        safe_obs: str,
        query: str,
        thought: str,
        iteration: int,
        trace: AgentTrace,
    ) -> AsyncIterator[StreamChunk]:
        """
        Write full search results and an extractive summary to files,
        then yield a rich immediate response to the user.
        """
        filename = f"search_results_{int(time.time())}.md"
        file_content = (
            f"# Web Search Results: {query}\n\n"
            f"*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
            f"{safe_obs}"
        )

        try:
            file_result, file_failed, _ = await dispatch_with_retry(
                Intent.FILE_WRITE, f"{filename}:\n\n{file_content}"
            )
            if file_failed or not file_result:
                raise RuntimeError("file_write tool returned failure")

            logger.info(f"[agent.loop] web search results written to {filename}")

            # Build and write extractive summary
            summary = create_extractive_summary(safe_obs, query)
            summary_filename = filename.replace(".md", "_summary.md")
            summary_result, summary_failed, _ = await dispatch_with_retry(
                Intent.FILE_WRITE, f"{summary_filename}:\n\n{summary}"
            )
            if not summary_failed and summary_result:
                logger.info(f"[agent.loop] summary written to {summary_filename}")
            else:
                logger.warning(f"[agent.loop] summary write failed; continuing without summary file")
                summary_filename = None

            # Build immediate response
            summary_line = (
                f"\n📄 **Summary created:** `{summary_filename}`"
                if summary_filename else ""
            )
            immediate_response = (
                f"✅ **Search complete!** Full results saved to `{filename}`"
                f"{summary_line}\n\n"
                f"💡 *Quick preview:*\n{truncate_web_search_results(safe_obs)}"
            )

            trace.final_response = immediate_response
            trace.steps.append(AgentStep(
                iteration=iteration + 1,
                thought=thought,
                tool_name="web_search",
                tool_input=query,
                observation=f"Results → {filename}" + (f", summary → {summary_filename}" if summary_filename else ""),
            ))

            yield StreamChunk(text=immediate_response, done=False)
            yield StreamChunk(text="", done=True)

        except Exception as exc:
            logger.error(f"[agent.loop] failed to write web search results: {exc}")
            # Fall through — caller will continue normal observation flow
            # (web search observation stays in context for next iteration)

    # ------------------------------------------------------------------
    # Forced final answer after iteration limit
    # ------------------------------------------------------------------

    async def _force_final_answer(
        self,
        loop_messages: list[dict],
        observation_log: str,
        active_adapter,
    ) -> AsyncIterator[StreamChunk]:
        """
        After hitting MAX_ITERATIONS, ask the LLM to synthesize a final
        answer from everything gathered so far.
        """
        force_msg = loop_messages + [{
            "role": "user",
            "content": (
                f"{observation_log}\n"
                "Maximum iterations reached. Provide your best answer now "
                "based on everything gathered so far."
            ),
        }]
        try:
            t_final = time.monotonic()
            async for chunk in active_adapter.chat(force_msg, temperature=0.4):
                filtered = StreamChunk(
                    text=_filter_system_leaks(chunk.text),
                    done=chunk.done,
                )
                yield filtered
            logger.info(
                f"[agent.loop] final synthesis: {round((time.monotonic() - t_final) * 1000)}ms"
            )
        except Exception as exc:
            logger.warning(f"[agent.loop] final synthesis failed: {exc}")
            yield StreamChunk(
                text=(
                    "I've reached my iteration limit. Here are the key findings "
                    "gathered so far:\n\n" + observation_log
                ),
                done=True,
            )
