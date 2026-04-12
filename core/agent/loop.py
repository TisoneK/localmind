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
import json
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
    AGENT_THINKING_MIN_CHARS,
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

# ── JSON parsing helpers ───────────────────────────────────────────────────────

def _parse_agent_response(text: str) -> Optional[dict]:
    """
    Parse a single-line JSON agent response.

    The model is instructed to output exactly one JSON object per iteration:
      {"action": {"tool": "...", "input": "..."}}
      {"finish": {"answer": "..."}}
      {"reflect": {"quality": "...", "issue": "...", "next": "..."}}

    Scans the full text for the first valid JSON object so that minor preamble
    or whitespace from the model does not break parsing.  Returns None if no
    valid agent JSON is found.
    """
    # Try each line; the model should emit exactly one JSON line.
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and any(k in obj for k in ("action", "finish", "reflect")):
                return obj
        except json.JSONDecodeError:
            continue

    # Fallback: try the whole text stripped (model sometimes adds newlines inside JSON)
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and any(k in obj for k in ("action", "finish", "reflect")):
                return obj
        except json.JSONDecodeError:
            pass

    return None


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

        # ── Pre-flight: verify Ollama is reachable before entering loop ────
        # Without this check, an unreachable or cold-loading Ollama causes
        # each iteration to wait the full ollama_timeout (default 120s) before
        # failing.  A 5-second health probe here converts a 300s+ hang into an
        # immediate, actionable error message.
        # Warmup first to ensure model is loaded, then health check
        if hasattr(active_adapter, "warmup"):
            await active_adapter.warmup()
            
        if hasattr(active_adapter, "health_check"):
            try:
                reachable = await active_adapter.health_check()
            except Exception:
                reachable = False
            if not reachable:
                msg = (
                    "Ollama is not reachable right now. "
                    "Make sure Ollama is running (`ollama serve`) and try again."
                )
                logger.error("[agent.loop] pre-flight failed — Ollama unreachable")
                yield StreamChunk(text=msg, done=False)
                yield StreamChunk(text="", done=True)
                return

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

        # Per-turn tool call cache: (tool_name, normalised_input) → observation text.
        # Used for deduplication — if the model re-emits an identical action we
        # return the cached result immediately rather than re-dispatching.
        _call_cache: dict[tuple[str, str], str] = {}

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
                f"Output one JSON object: action, finish, or reflect."
            )
            iteration_messages = loop_messages + [{"role": "user", "content": context_msg}]

            # ── LLM call — collect full response ──────────────────────
            thought_chunks: list[str] = []
            t_llm = time.monotonic()
            async for chunk in active_adapter.chat(iteration_messages, temperature=0.3):
                thought_chunks.append(chunk.text)
            llm_ms = round((time.monotonic() - t_llm) * 1000)
            logger.info(f"[agent.loop] iter={iteration + 1} LLM: {llm_ms}ms")
            thought = "".join(thought_chunks)

            # Stream sanitized reasoning text to UI before parsing
            thinking_display = sanitize_thought_for_display(thought)
            if thinking_display and len(thinking_display) >= AGENT_THINKING_MIN_CHARS:
                yield StreamChunk(text=f"*{thinking_display}*\n\n", done=False)

            logger.debug(f"[agent.loop] iter={iteration + 1} thought={thought[:120]}…")

            # ── Parse structured JSON response ─────────────────────────
            parsed = _parse_agent_response(thought)

            # ── reflect ───────────────────────────────────────────────
            if parsed and "reflect" in parsed:
                ref = parsed["reflect"] if isinstance(parsed["reflect"], dict) else {}
                quality = str(ref.get("quality", "unknown")).lower()
                issue   = str(ref.get("issue", ""))
                next_step = str(ref.get("next", ""))
                reflection_text = f"quality: {quality}\nissue: {issue}\nnext: {next_step}"

                observation_log += f"\n[Reflection]\n{reflection_text}\n"
                last_tool_failed = quality == "failed"

                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                    reflection=reflection_text,
                ))

                yield StreamChunk(text="\n### Reasoning\n", done=False)
                status_map = {
                    "good":    "✓ Task completed successfully.",
                    "partial": "↻ Partial progress — continuing…",
                    "failed":  "✗ Previous approach failed — trying alternative.",
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

            # ── finish ────────────────────────────────────────────────
            if parsed and "finish" in parsed:
                fin = parsed["finish"] if isinstance(parsed["finish"], dict) else {}
                final_response = _filter_system_leaks(str(fin.get("answer", "")).strip())
                trace.final_response = final_response
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                ))
                yield StreamChunk(text=final_response, done=False)
                yield StreamChunk(text="", done=True)
                logger.info(f"[agent.loop] finished iter={iteration + 1} — {trace.summary()}")
                return

            # ── action ────────────────────────────────────────────────
            if parsed and "action" in parsed:
                act = parsed["action"] if isinstance(parsed["action"], dict) else {}
                tool_name  = str(act.get("tool", "")).strip()
                tool_input = str(act.get("input", "")).strip()

                # Semantic validation: reject empty or degenerate inputs before dispatch.
                # A structurally valid JSON action with a useless input (empty string,
                # pure punctuation, fewer than 3 chars) is guaranteed to produce a bad
                # tool result and wastes an Ollama call.  Feed back a clear error so the
                # model can correct itself rather than silently dispatching garbage.
                if not tool_input or len(tool_input.strip("?. \t")) < 3:
                    observation = (
                        f"[Action rejected: input for '{tool_name}' is empty or too short. "
                        f"Provide a specific, meaningful input and try again.]"
                    )
                    logger.warning(
                        f"[agent.loop] rejected degenerate input for tool={tool_name!r}: {tool_input!r}"
                    )
                    last_tool_failed = True
                    consecutive_failures += 1
                    observation_log += f"\n[Tool: {tool_name} | Status: REJECTED (bad input)]\n{observation}\n"
                    trace.steps.append(AgentStep(
                        iteration=iteration + 1, thought=thought,
                        tool_name=tool_name, tool_input=tool_input,
                        observation=observation, tool_failed=True,
                        retry_count=0, latency_ms=0,
                    ))
                    continue

                # Deduplication: skip a tool call we've already made this turn.
                # The model sometimes re-emits an identical action when it loses
                # track of what it has already observed.  Returning the cached
                # result is cheaper and prevents infinite loops on slow models.
                call_fingerprint = (tool_name, tool_input.lower().strip())
                if call_fingerprint in _call_cache:
                    cached_obs = _call_cache[call_fingerprint]
                    logger.info(
                        f"[agent.loop] dedup hit for tool={tool_name!r} — returning cached result"
                    )
                    observation_log += (
                        f"\n[Tool: {tool_name} | Input: {tool_input[:60]} | Status: CACHED]\n"
                        f"{_truncate_observation(cached_obs)}\n"
                    )
                    trace.steps.append(AgentStep(
                        iteration=iteration + 1, thought=thought,
                        tool_name=tool_name, tool_input=tool_input,
                        observation=f"[cached] {cached_obs[:200]}",
                        tool_failed=False, retry_count=0, latency_ms=0,
                    ))
                    continue

                # Guard: reject tool names not in the explicit allowlist.
                from core.agent.constants import AGENT_ALLOWED_TOOLS
                if tool_name not in AGENT_ALLOWED_TOOLS:
                    allowed_list = ", ".join(sorted(AGENT_ALLOWED_TOOLS))
                    observation = (
                        f"[Tool '{tool_name}' is not available. "
                        f"Available tools: {allowed_list}]"
                    )
                    logger.warning(f"[agent.loop] rejected tool name: {tool_name!r}")
                    last_tool_failed = True
                    consecutive_failures += 1
                    observation_log += (
                        f"\n[Tool: {tool_name} | Status: BLOCKED (not in allowlist)]\n"
                        f"{observation}\n"
                    )
                    trace.steps.append(AgentStep(
                        iteration=iteration + 1, thought=thought,
                        tool_name=tool_name, tool_input=tool_input,
                        observation=observation, tool_failed=True,
                        retry_count=0, latency_ms=0,
                    ))
                    continue

                logger.info(f"[agent.loop] iter={iteration + 1} tool={tool_name} input={tool_input[:80]}")

                yield StreamChunk(text=f"\n### Action: `{tool_name}`\n", done=False)
                action_labels = {
                    "file_write": f"Creating file: `{tool_input}`",
                    "code_exec":  "Executing code…",
                    "web_search": f"Searching: *{tool_input}*",
                    "read_file":  f"Reading: `{tool_input}`",
                }
                yield StreamChunk(
                    text=action_labels.get(tool_name, f"Input: {tool_input}") + "\n",
                    done=False,
                )

                t_tool = time.monotonic()
                tool_failed = False
                retry_count = 0
                observation = ""

                try:
                    tool_intent = Intent(tool_name)
                    tool_result, tool_failed, retry_count = await dispatch_with_retry(
                        tool_intent, tool_input
                    )
                    latency_ms = round((time.monotonic() - t_tool) * 1000)

                    if tool_result:
                        if tool_intent == Intent.CODE_EXEC:
                            safe_obs = _filter_code_output(tool_result.content)
                        else:
                            safe_obs = _filter_tool_injection(tool_result.content)

                        # Prepend structured error context so the LLM can reason
                        # about *why* a tool failed rather than just seeing a message.
                        if not tool_result.success and tool_result.error_type:
                            safe_obs = (
                                f"[Tool '{tool_name}' returned error_type={tool_result.error_type!r}]\n"
                                + safe_obs
                            )

                        # Web-search: stream result immediately, write files in background.
                        if tool_intent == Intent.WEB_SEARCH:
                            async for chunk in self._handle_web_search_result(
                                safe_obs=safe_obs,
                                query=tool_input,
                                thought=thought,
                                iteration=iteration,
                                trace=trace,
                            ):
                                yield chunk
                            return  # web search is always terminal in agent loop

                        observation = safe_obs
                        _call_cache[call_fingerprint] = safe_obs  # store for deduplication
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

                except (ValueError, Exception) as exc:
                    observation = f"[Tool '{tool_name}' failed: {exc}]"
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
                    observation=observation[:500],
                    tool_failed=tool_failed,
                    retry_count=retry_count,
                    latency_ms=latency_ms,
                ))
                observation_log += (
                    f"\n[Tool: {tool_name} | Input: {tool_input[:60]} | "
                    f"Status: {'FAILED' if tool_failed else 'OK'} | {latency_ms}ms]\n"
                    f"{_truncate_observation(observation)}\n"
                )
                continue

            # ── No valid JSON — treat whole response as finish ─────────
            logger.warning(f"[agent.loop] no structured JSON at iter={iteration + 1}, treating as finish")
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
        Stream search results to the user immediately, then write result files
        as a background task.

        The caller is responsible for returning after iterating this method.
        Web search is always terminal in the agent loop — the results are the answer.
        """
        filename = f"search_results_{int(time.time())}.md"
        summary_filename = filename.replace(".md", "_summary.md")

        file_content = (
            f"# Web Search Results: {query}\n\n"
            f"*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
            f"{safe_obs}"
        )
        summary = create_extractive_summary(safe_obs, query)

        # Fire-and-forget: write both files in the background.
        async def _write_files() -> None:
            try:
                await dispatch_with_retry(
                    Intent.FILE_WRITE, f"{filename}:\n\n{file_content}"
                )
                logger.info(f"[agent.loop] web search results written to {filename}")
            except Exception as exc:
                logger.warning(f"[agent.loop] search result file write failed: {exc}")
            try:
                await dispatch_with_retry(
                    Intent.FILE_WRITE, f"{summary_filename}:\n\n{summary}"
                )
                logger.info(f"[agent.loop] summary written to {summary_filename}")
            except Exception as exc:
                logger.warning(f"[agent.loop] summary file write failed: {exc}")

        asyncio.create_task(_write_files())

        immediate_response = (
            f"✅ **Search complete!** Full results saved to `{filename}`\n"
            f"📄 **Summary created:** `{summary_filename}`\n\n"
            f"💡 *Quick preview:*\n{truncate_web_search_results(safe_obs)}"
        )

        trace.final_response = immediate_response
        trace.steps.append(AgentStep(
            iteration=iteration + 1,
            thought=thought,
            tool_name="web_search",
            tool_input=query,
            observation=f"Results → {filename}, summary → {summary_filename}",
        ))

        yield StreamChunk(text=immediate_response, done=False)
        yield StreamChunk(text="", done=True)

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
