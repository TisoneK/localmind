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

MAX_ITERATIONS = 6

AGENT_INTENTS = {
    Intent.WEB_SEARCH,
    Intent.CODE_EXEC,
    Intent.FILE_WRITE,
    Intent.MEMORY_OP,
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
        f"  - {t['intent']}: {t['description']} "
        f"(latency: {t.get('latency_ms', '?')}ms, cost: {t.get('cost', '?')})"
        for t in available_tools
    )
    return f"""You are LocalMind, a reasoning agent running on the user's local machine.
You reason step by step before acting. After observing a tool result, you reflect on its quality.

Available tools:
{tool_list}

RESPONSE FORMAT — use ONE of these per iteration:

Use a tool:
<action>
tool: <tool_name>
input: <what to send to the tool>
</action>

Reflect on a tool result (use after observing a result):
<reflect>
quality: good|partial|failed
issue: what was wrong (if partial/failed)
next: what you will do differently
</reflect>

Ask user to clarify (ONLY if intent is completely ambiguous):
<clarify>Your specific question here</clarify>

Deliver final answer (when ready):
<finish>
Your complete answer in markdown.
</finish>

Rules:
- One tag per iteration — never combine action + finish in same response
- After a tool FAILS: always output <reflect> then try a different approach
- After a PARTIAL result: reflect, then either retry with better input or finish with what you have
- After a GOOD result: proceed to <finish> or next <action>
- Maximum {MAX_ITERATIONS} iterations total
- Current primary intent: {intent.value}
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
    ) -> AsyncIterator[StreamChunk]:
        trace = AgentTrace()

        # Clarification gate: if confidence is very low, ask before running tools
        if confidence < CLARIFICATION_THRESHOLD and intent in AGENT_INTENTS:
            clarification = (
                f"I want to make sure I understand — it looks like you want me to "
                f"use the **{intent.value.replace('_', ' ')}** tool, but I'm not very confident. "
                f"Could you confirm, or rephrase what you'd like me to do?"
            )
            trace.clarification_issued = True
            for char in clarification:
                yield StreamChunk(text=char, done=False)
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
            async for chunk in self._adapter.chat(iteration_messages, temperature=0.3):
                thought_chunks.append(chunk.text)
            thought = "".join(thought_chunks)

            logger.debug(f"[agent] iter={iteration+1} thought={thought[:100]}...")

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
                for char in question:
                    yield StreamChunk(text=char, done=False)
                yield StreamChunk(text="", done=True)
                return

            # ── Finish ───────────────────────────────────────────────────────
            finish_match = _FINISH_PATTERN.search(thought)
            if finish_match:
                final_response = finish_match.group(1).strip()
                trace.final_response = final_response
                trace.steps.append(AgentStep(
                    iteration=iteration + 1, thought=thought,
                    tool_name=None, tool_input=None, observation=None,
                ))
                for char in final_response:
                    yield StreamChunk(text=char, done=False)
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
                continue

            # ── Action ───────────────────────────────────────────────────────
            action_match = _ACTION_PATTERN.search(thought)
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_input = action_match.group(2).strip()
                logger.info(f"[agent] iter={iteration+1} tool={tool_name} input={tool_input[:80]}")

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
                observation_log += (
                    f"\n[Tool: {tool_name} | Input: {tool_input[:60]} | "
                    f"Status: {'FAILED' if tool_failed else 'OK'}]\n"
                    f"{observation}\n"
                )
                continue

            # No structured tag found — treat as finish
            logger.warning(f"[agent] no tag at iteration {iteration+1}, treating as finish")
            for char in thought:
                yield StreamChunk(text=char, done=False)
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
        async for chunk in self._adapter.chat(force_msg, temperature=0.4):
            yield chunk
