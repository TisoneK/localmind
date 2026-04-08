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

MAX_ITERATIONS = 2
CLARIFICATION_THRESHOLD = 0.45

# Max characters kept per observation entry in the running log.
# Full observation is stored in AgentStep for trace/debug purposes.
OBS_LOG_MAX_CHARS = 100  # Extreme reduction for local LLM

# Retry config per tool call
TOOL_MAX_RETRIES = 2
TOOL_RETRY_BASE_DELAY = 0.5  # seconds; doubles each retry

# Web search result truncation to prevent context bloat
WEB_SEARCH_MAX_CHARS_PER_RESULT = 100  # Extreme reduction
WEB_SEARCH_MAX_RESULTS = 1  # Only 1 result to minimize context

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


def _truncate_web_search_results(search_content: str) -> str:
    """
    Truncate web search results to prevent context bloat and LLM hanging.
    
    Takes formatted search results like:
    1. **Title**
       URL
       Description...
    
    And truncates each result to WEB_SEARCH_MAX_CHARS_PER_RESULT chars,
    keeping only WEB_SEARCH_MAX_RESULTS results.
    """
    lines = search_content.split('\n')
    truncated_results = []
    current_result = []
    result_count = 0
    
    for line in lines:
        # Detect new result (starts with number and **)
        if line.strip().startswith(tuple(f"{i}." for i in range(1, 10))):
            # Save previous result if exists
            if current_result and result_count < WEB_SEARCH_MAX_RESULTS:
                result_text = '\n'.join(current_result)
                if len(result_text) > WEB_SEARCH_MAX_CHARS_PER_RESULT:
                    result_text = result_text[:WEB_SEARCH_MAX_CHARS_PER_RESULT] + "..."
                truncated_results.append(result_text)
                result_count += 1
            
            # Start new result
            current_result = [line]
        else:
            # Add to current result
            current_result.append(line)
    
    # Don't forget the last result
    if current_result and result_count < WEB_SEARCH_MAX_RESULTS:
        result_text = '\n'.join(current_result)
        if len(result_text) > WEB_SEARCH_MAX_CHARS_PER_RESULT:
            result_text = result_text[:WEB_SEARCH_MAX_CHARS_PER_RESULT] + "..."
        truncated_results.append(result_text)
    
    return '\n'.join(truncated_results)


async def _summarize_search_results_background(filename: str, original_query: str) -> None:
    """
    Background task to summarize search results after file is written.
    Uses extractive summarization (no LLM) for instant results.
    """
    import asyncio
    import os
    from pathlib import Path
    
    try:
        # Look for any file that starts with the expected filename in the correct LocalMind directory
        from core.config import settings
        localmind_dir = Path(settings.localmind_home)
        
        # Write test file to verify background task is running
        test_file = localmind_dir / filename.replace('.md', '_test.txt')
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(f"Background task started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Wait a bit to ensure file is fully written
        await asyncio.sleep(2)
        
        # Find the actual search results file (might have different timestamp)
        base_name = filename.replace('.md', '')
        search_files = list(localmind_dir.glob(f"{base_name}*.md"))
        
        if not search_files:
            logger.error(f"[agent] No search results file found matching pattern: {base_name}*.md")
            return
            
        # Use the most recent file
        search_file = max(search_files, key=lambda f: f.stat().st_mtime)
        logger.info(f"[agent] Found search results file: {search_file}")
        
        # Read the search results file
        try:
            content = search_file.read_text(encoding='utf-8')
            logger.info(f"[agent] Background task successfully read {search_file}")
        except Exception as read_error:
            logger.error(f"[agent] Failed to read search results file {search_file}: {read_error}")
            return
        except Exception as read_error:
            logger.error(f"[agent] Failed to read search results file {search_file}: {read_error}")
            return
        
        # Extractive summarization - pull key headlines and first sentences
        lines = content.split('\n')
        summary_points = []
        
        logger.info(f"[agent] Background processing {len(lines)} lines from search results")
        
        for i, line in enumerate(lines):
            line = line.strip()
            # Look for result titles (start with number and **)
            if line.startswith(tuple(f"{i}." for i in range(1, 10))) and '**' in line:
                # Extract the title
                parts = line.split('**')
                if len(parts) >= 2:
                    title = parts[1].split('**')[0].strip()
                    if title and len(summary_points) < 5:
                        summary_points.append(f"• {title}")
                        logger.info(f"[agent] Extracted title: {title}")
            
            # Look for URLs and descriptions
            elif line.startswith('https://') and len(summary_points) > 0:
                # Add as a detail to the last point
                if summary_points:
                    summary_points[-1] += f" ([Source]({line}))"
                    logger.info(f"[agent] Added source URL to last point")
        
        logger.info(f"[agent] Extracted {len(summary_points)} summary points")
        
        # Create extractive summary
        try:
            if summary_points:
                summary = f"# Quick Summary: {original_query}\n\n*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n\n" + "\n".join(summary_points[:5])
                logger.info(f"[agent] Created summary with {len(summary_points)} points")
            else:
                # Fallback: just use first few lines
                summary = f"# Quick Summary: {original_query}\n\n*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n\nKey findings from search results:\n" + "\n".join(lines[:10])
                logger.info(f"[agent] Created fallback summary with {len(lines)} lines")
            
            # Write summary to a separate file in LocalMind directory
            summary_filename = filename.replace('.md', '_summary.md')
            summary_file = localmind_dir / summary_filename
            summary_file.write_text(summary, encoding='utf-8')
            
            logger.info(f"[agent] Background extractive summary completed: {summary_file}")
            logger.info(f"[agent] Summary file size: {len(summary)} characters")
            
        except Exception as summary_error:
            logger.error(f"[agent] Failed to create summary: {summary_error}")
            # Write error summary
            error_summary = f"# Summary Error\n\nFailed to create summary for: {original_query}\n\nError: {summary_error}"
            error_filename = filename.replace('.md', '_error.md')
            error_file = localmind_dir / error_filename
            error_file.write_text(error_summary, encoding='utf-8')
            logger.error(f"[agent] Error summary written to: {error_file}")
        
    except Exception as e:
        logger.error(f"[agent] Background summarization failed: {e}")


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
            t_llm_start = time.monotonic()
            async for chunk in active_adapter.chat(iteration_messages, temperature=0.3):
                thought_chunks.append(chunk.text)
            t_llm_end = time.monotonic()
            llm_time_ms = round((t_llm_end - t_llm_start) * 1000)
            logger.info(f"[agent] iter={iteration+1} LLM call: {llm_time_ms}ms")
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
                    logger.info(f"[agent] iter={iteration+1} tool={tool_name}: {latency_ms}ms")

                    if tool_result:
                        # Filter prompt injection from tool output before re-entering context
                        if tool_intent == Intent.CODE_EXEC:
                            safe_obs = _filter_code_output(tool_result.content)
                        else:
                            safe_obs = _filter_tool_injection(tool_result.content)
                        
                        # For web search, write results to file and start background summarization
                        if tool_intent == Intent.WEB_SEARCH:
                            # Let file_write tool handle directory automatically
                            filename = f"search_results_{int(time.time())}.md"
                            file_content = f"# Web Search Results: {tool_input}\n\n*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n\n{safe_obs}"
                            try:
                                # Use file_write tool for consistency
                                from tools import dispatch
                                file_result, file_failed, file_retry = await _dispatch_with_retry(
                                    Intent.FILE_WRITE, f"Write search results to {filename}:\n\n{file_content}"
                                )
                                
                                if not file_failed and file_result:
                                    logger.info(f"[agent] Web search results written to {filename} using file_write tool")
                                    
                                    # Start background summarization task
                                    import asyncio
                                    try:
                                        asyncio.create_task(_summarize_search_results_background(filename, tool_input))
                                        logger.info(f"[agent] Started background summarization for {filename}")
                                    except Exception as bg_error:
                                        logger.error(f"[agent] Failed to start background summarization: {bg_error}")
                                    
                                    # Immediate response with file link
                                    immediate_response = f"✅ **Search complete!** Full results saved to `{filename}`\n\n🔄 **Generating summary in background...**\n\n💡 *Quick preview:*\n{_truncate_web_search_results(safe_obs)}"
                                    trace.final_response = immediate_response
                                    trace.steps.append(AgentStep(
                                        iteration=iteration + 1, thought=thought,
                                        tool_name=tool_name, tool_input=tool_input, observation=f"Results written to {filename}, summary started in background",
                                    ))
                                    yield StreamChunk(text=immediate_response, done=False)
                                    yield StreamChunk(text="", done=True)
                                    return
                                else:
                                    logger.error(f"[agent] file_write tool failed: {file_result}")
                                    raise Exception("File write tool failed")
                            except Exception as e:
                                logger.error(f"[agent] Failed to write search results using file_write tool: {e}")
                                # Fall back to normal processing if file write fails
                        
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
            t_final_start = time.monotonic()
            chat_coroutine = active_adapter.chat(force_msg, temperature=0.4)
            async for chunk in chat_coroutine:
                filtered_chunk = StreamChunk(
                    text=_filter_system_leaks(chunk.text),
                    done=chunk.done,
                )
                yield filtered_chunk
            t_final_end = time.monotonic()
            final_time_ms = round((t_final_end - t_final_start) * 1000)
            logger.info(f"[agent] final LLM synthesis: {final_time_ms}ms")
        except Exception as e:
            logger.warning(f"[agent] Final LLM synthesis failed: {e}")
            yield StreamChunk(
                text="I apologize, but I'm unable to synthesize a response due to time constraints. Here are the key findings from my search:\n\n" + observation_log,
                done=True
            )
