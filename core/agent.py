"""
Agent Loop — v0.3: think → act → observe → reflect → adjust

Upgrades from v0.2:
- Reflection step after each observation: evaluates action quality
- Failure recovery: bad tool call triggers strategy rethink
- Clarification gate: low-confidence intents ask user before proceeding
- AgentTrace now captures reflection decisions
- Tool scoring integrated: uses ScoredTool metadata in prompts
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from core.models import Intent, StreamChunk, ToolResult
from tools import dispatch

logger = logging.getLogger(__name__)

# Import leak filter once at module level
from core.filters import _filter_system_leaks

MAX_ITERATIONS = 6  # was 12 — tighter loop, faster bail-out

AGENT_INTENTS = {
    Intent.WEB_SEARCH,
    Intent.CODE_EXEC,
    Intent.SHELL,
    Intent.FILE_WRITE,
    Intent.MEMORY_OP,
    # Intent.SYSINFO intentionally excluded — instant offline tool, no reasoning loop needed
}

# Confidence threshold below which we ask user to confirm intent
CLARIFICATION_THRESHOLD = 0.45

_ACTION_PATTERN = re.compile(
    r"<action>\s*tool:\s*(\w+)\s*input:\s*(.*?)\s*</action>",
    re.DOTALL | re.IGNORECASE,
)
_FINISH_PATTERN = re.compile(r"<finish>(.*?)</finish>", re.DOTALL | re.IGNORECASE)
_REFLECT_PATTERN = re.compile(r"<reflect>(.*?)</reflect>", re.DOTALL | re.IGNORECASE)
_CLARIFY_PATTERN = re.compile(r"<clarify>(.*?)</clarify>", re.DOTALL | re.IGNORECASE)


@dataclass
class AgentStep:
    iteration: int
    thought: str
    tool_name: Optional[str]
    tool_input: Optional[str]
    observation: Optional[str]
    reflection: Optional[str] = None
    tool_failed: bool = False


@dataclass
class AgentTrace:
    steps: list[AgentStep] = field(default_factory=list)
    final_response: str = ""
    iterations_used: int = 0
    hit_limit: bool = False
    clarification_issued: bool = False


def _build_agent_system_prompt(intent: Intent, available_tools: list[dict]) -> str:
    tool_list = "\n".join(
        f"  - {t['intent']}: {t['description']}"
        for t in available_tools
    )
    return f"""You are LocalMind's reasoning agent. You have tools available and MUST use them — never simulate or guess tool results.

Available tools:
{tool_list}

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
        adapter=None,          # specialist adapter for this intent; falls back to self._adapter
    ) -> AsyncIterator[StreamChunk]:
        _adapter = adapter or self._adapter
        trace = AgentTrace()

        # Clarification gate: if confidence is very low, ask before running tools
        if confidence < CLARIFICATION_THRESHOLD and intent in AGENT_INTENTS:
            clarification = (
                f"I want to make sure I understand — it looks like you want me to "
                f"use the **{intent.value.replace('_', ' ')}** tool, but I'm not very confident. "
                f"Could you confirm, or rephrase what you'd like me to do?"
            )
            trace.clarification_issued = True
            yield StreamChunk(text=clarification, done=False)  # B6: yield as single chunk
            yield StreamChunk(text="", done=True)
            return

        # Inject agent system prompt
        agent_system = _build_agent_system_prompt(intent, available_tools)
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
            observation_log = (
                f"[Initial tool result from {initial_tool_result.source}]\n"
                f"{initial_tool_result.content}\n"
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

            thought_chunks = []
            async for chunk in _adapter.chat(iteration_messages, temperature=0.3):
                thought_chunks.append(chunk.text)
            thought = "".join(thought_chunks)

            logger.debug(f"[agent] iter={iteration+1} thought={thought[:100]}...")
            
            # Stream sanitized thinking summary to UI before processing tags
            thinking_display = re.sub(r"<(action|finish|reflect|clarify)>.*?</\1>", "", thought, flags=re.DOTALL | re.IGNORECASE).strip()
            if thinking_display:
                yield StreamChunk(text=f"*{thinking_display}*\n", done=False)

            # ── Clarify ──────────────────────────────────────────────────────
            clarify_match = _CLARIFY_PATTERN.search(thought)
            if clarify_match:
                question = clarify_match.group(1).strip()
                trace.clarification_issued = True
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                    reflection="clarification issued",
                ))
                yield StreamChunk(text=question, done=False)  # B6: single chunk
                yield StreamChunk(text="", done=True)
                return

            # ── Finish ───────────────────────────────────────────────────────
            finish_match = _FINISH_PATTERN.search(thought)
            if finish_match:
                final_response = finish_match.group(1).strip()
                # Filter out any system leaks that might have gotten through
                final_response = _filter_system_leaks(final_response)
                trace.final_response = final_response
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                ))
                yield StreamChunk(text=final_response, done=False)  # B6: single chunk
                yield StreamChunk(text="", done=True)
                logger.info(f"[agent] finished at iteration {iteration+1}")
                return

            # ── Reflect ──────────────────────────────────────────────────────
            reflect_match = _REFLECT_PATTERN.search(thought)
            if reflect_match:
                reflection_text = reflect_match.group(1).strip()
                observation_log += f"\n[Reflection]\n{reflection_text}\n"
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                    reflection=reflection_text,
                ))
                last_tool_failed = "failed" in reflection_text.lower()
                # Stream descriptive thinking output so users understand what's happening
                yield StreamChunk(text=f"\n# Reasoning\n", done=False)
                
                # Parse reflection content for meaningful details (robust parsing)
                lines = reflection_text.splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Case-insensitive parsing with fallback
                    lower_line = line.lower()
                    
                    if lower_line.startswith("quality:"):
                        quality = line.split(":", 1)[1].strip().lower()
                        if quality == "good":
                            yield StreamChunk(text="Task completed successfully.\n", done=False)
                        elif quality == "partial":
                            yield StreamChunk(text="Partial progress made, continuing...\n", done=False)
                        elif quality == "failed":
                            yield StreamChunk(text="Previous approach failed, trying alternative.\n", done=False)
                        else:
                            yield StreamChunk(text=f"Status: {quality}\n", done=False)
                    elif lower_line.startswith("issue:"):
                        issue = line.split(":", 1)[1].strip()
                        yield StreamChunk(text=f"Problem: {issue}\n", done=False)
                    elif lower_line.startswith("next:"):
                        next_step = line.split(":", 1)[1].strip()
                        yield StreamChunk(text=f"Next step: {next_step}\n", done=False)
                continue

            # ── Action ───────────────────────────────────────────────────────
            action_match = _ACTION_PATTERN.search(thought)
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_input = action_match.group(2).strip()
                logger.info(f"[agent] iter={iteration+1} tool={tool_name} input={tool_input[:80]}")
                # Stream descriptive tool action so users see what's happening
                yield StreamChunk(text=f"\n## Action: {tool_name}\n", done=False)
                # Add context based on tool type
                if tool_name == "file_write":
                    yield StreamChunk(text=f"Creating file: {tool_input}\n", done=False)
                elif tool_name == "code_exec":
                    yield StreamChunk(text=f"Executing code...\n", done=False)
                elif tool_name == "web_search":
                    yield StreamChunk(text=f"Searching for: {tool_input}\n", done=False)
                elif tool_name == "read_file":
                    yield StreamChunk(text=f"Reading file: {tool_input}\n", done=False)
                else:
                    yield StreamChunk(text=f"Input: {tool_input}\n", done=False)

                tool_failed = False
                try:
                    tool_intent = Intent(tool_name)
                    tool_result = await dispatch(tool_intent, tool_input)
                    if tool_result:
                        observation = tool_result.content
                        last_tool_failed = False
                        consecutive_failures = 0
                    else:
                        observation = f"[Tool '{tool_name}' returned no result]"
                        tool_failed = True
                except (ValueError, Exception) as e:
                    observation = f"[Tool '{tool_name}' failed: {e}]"
                    tool_failed = True

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
                ))
                # Truncate observation for context to prevent bloat (same limit as trace)
                truncated_obs = observation[:500] + ("..." if len(observation) > 500 else "")
                observation_log += (
                    f"\n[Tool: {tool_name} | Input: {tool_input[:60]} | "
                    f"Status: {'FAILED' if tool_failed else 'OK'}]\n"
                    f"{truncated_obs}\n"
                )
                continue

            # No structured tag found — treat as finish
            logger.warning(f"[agent] no tag at iteration {iteration+1}, treating as finish")
            filtered_thought = _filter_system_leaks(thought)
            yield StreamChunk(text=filtered_thought, done=False)  # B6: single chunk
            yield StreamChunk(text="", done=True)
            return

        # Hit MAX_ITERATIONS
        trace.hit_limit = True
        logger.warning(f"[agent] hit MAX_ITERATIONS={MAX_ITERATIONS}, forcing finish")
        force_msg = loop_messages + [{
            "role": "user",
            "content": (
                f"{observation_log}\n"
                "Maximum iterations reached. Provide your best answer now "
                "based on everything gathered so far."
            )
        }]
        async for chunk in _adapter.chat(force_msg, temperature=0.4):
            # Filter any leaks from the fallback response
            filtered_chunk = StreamChunk(text=_filter_system_leaks(chunk.text), done=chunk.done)
            yield filtered_chunk
