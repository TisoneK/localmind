"""
Agent Loop — multi-step reasoning over tools.

Transforms LocalMind from single-pass (classify → tool → respond) into
an iterative loop: think → act → observe → think → … → respond.

The loop runs when:
- The engine is in AGENT mode (intent requires reasoning)
- The initial tool result is insufficient or spawns a follow-up
- The model signals it needs another tool via a structured action tag

Loop contract:
    Each iteration yields one of:
        AgentAction(tool=..., input=...)  — run a tool, continue loop
        AgentFinish(response=...)         — stream final answer, stop

Safety:
    - Hard cap of MAX_ITERATIONS to prevent infinite loops
    - Each iteration's tool result is appended to the observation log
    - The full trace is available for debugging via AgentTrace

This is intentionally minimal — no LangChain, no framework dependency.
The loop is just a while loop with a structured prompt.
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

# Intents that benefit from multi-step reasoning
AGENT_INTENTS = {
    Intent.WEB_SEARCH,
    Intent.CODE_EXEC,
    Intent.FILE_WRITE,
    Intent.MEMORY_OP,
}

# Structured action format the model must use to request a tool
_ACTION_PATTERN = re.compile(
    r"<action>\s*tool:\s*(\w+)\s*input:\s*(.*?)\s*</action>",
    re.DOTALL | re.IGNORECASE,
)
_FINISH_PATTERN = re.compile(r"<finish>(.*?)</finish>", re.DOTALL | re.IGNORECASE)


@dataclass
class AgentStep:
    iteration: int
    thought: str
    tool_name: Optional[str]
    tool_input: Optional[str]
    observation: Optional[str]


@dataclass
class AgentTrace:
    """Full trace of an agent run — useful for logging and debugging."""
    steps: list[AgentStep] = field(default_factory=list)
    final_response: str = ""
    iterations_used: int = 0
    hit_limit: bool = False


def _build_agent_system_prompt(intent: Intent, available_tools: list[dict]) -> str:
    tool_list = "\n".join(
        f"  - {t['intent']}: {t['description']}" for t in available_tools
    )
    return f"""You are LocalMind, a reasoning agent running on the user's local machine.
You have access to tools and must reason step by step before answering.

Available tools:
{tool_list}

To use a tool, output EXACTLY this format and nothing else:
<action>
tool: <tool_name>
input: <what to search/execute/write>
</action>

When you have enough information to answer the user, output:
<finish>
Your final answer here, in markdown if helpful.
</finish>

Rules:
- Think before acting. One tool per iteration.
- Only use tools when truly needed — prefer your own knowledge for simple facts.
- If a tool returns an error, try a different approach or admit the limitation.
- Maximum {MAX_ITERATIONS} iterations before you must finish.
- Current intent: {intent.value}
"""


class AgentLoop:
    """
    Drives the think → act → observe loop for complex intents.

    Usage:
        loop = AgentLoop(adapter=adapter)
        async for chunk in loop.run(messages, intent, initial_tool_result):
            yield chunk
    """

    def __init__(self, adapter):
        self._adapter = adapter

    async def run(
        self,
        messages: list[dict],
        intent: Intent,
        initial_tool_result: Optional[ToolResult],
        available_tools: list[dict],
    ) -> AsyncIterator[StreamChunk]:
        """
        Run the agent loop and stream the final response.

        Args:
            messages: Prompt messages from context_builder (system + history + user)
            intent: Classified intent for this turn
            initial_tool_result: Result from the pre-loop tool dispatch (may be None)
            available_tools: Tool metadata list from the registry

        Yields:
            StreamChunk objects from the final model call.
        """
        trace = AgentTrace()

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

        # Seed the observation log with the initial tool result
        observation_log = ""
        if initial_tool_result:
            observation_log = (
                f"[Initial tool result from {initial_tool_result.source}]\n"
                f"{initial_tool_result.content}\n"
            )

        for iteration in range(MAX_ITERATIONS):
            trace.iterations_used = iteration + 1

            # Build the current loop context
            context_msg = (
                f"{observation_log}\n"
                f"Iteration {iteration + 1}/{MAX_ITERATIONS}. "
                f"Think and then either use a tool or provide your final answer."
            )
            iteration_messages = loop_messages + [
                {"role": "user", "content": context_msg}
            ]

            # Get model's thought
            thought_chunks = []
            async for chunk in self._adapter.chat(iteration_messages, temperature=0.3):
                thought_chunks.append(chunk.text)

            thought = "".join(thought_chunks)
            logger.debug(f"[agent loop] iteration={iteration+1} thought={thought[:120]}...")

            # Check if model wants to finish
            finish_match = _FINISH_PATTERN.search(thought)
            if finish_match:
                final_response = finish_match.group(1).strip()
                trace.final_response = final_response
                trace.steps.append(AgentStep(
                    iteration=iteration + 1,
                    thought=thought,
                    tool_name=None,
                    tool_input=None,
                    observation=None,
                ))
                # Stream the final response token by token
                for char in final_response:
                    yield StreamChunk(text=char, done=False)
                yield StreamChunk(text="", done=True)
                logger.info(f"[agent loop] finished at iteration {iteration+1}")
                return

            # Check if model wants to use a tool
            action_match = _ACTION_PATTERN.search(thought)
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_input = action_match.group(2).strip()
                logger.info(f"[agent loop] iteration={iteration+1} tool={tool_name} input={tool_input[:80]}")

                # Try to map tool_name to an Intent
                try:
                    tool_intent = Intent(tool_name)
                    tool_result = await dispatch(tool_intent, tool_input)
                    observation = tool_result.content if tool_result else f"[Tool '{tool_name}' returned no result]"
                except (ValueError, Exception) as e:
                    observation = f"[Tool '{tool_name}' failed: {e}]"
                    tool_result = None

                trace.steps.append(AgentStep(
                    iteration=iteration + 1,
                    thought=thought,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    observation=observation[:500],
                ))
                observation_log += f"\n[Tool: {tool_name} | Input: {tool_input[:60]}]\n{observation}\n"
                continue

            # Model didn't use a structured format — treat the response as final
            logger.warning(f"[agent loop] no action/finish tag at iteration {iteration+1}, treating as finish")
            trace.hit_limit = True
            for char in thought:
                yield StreamChunk(text=char, done=False)
            yield StreamChunk(text="", done=True)
            return

        # Hit MAX_ITERATIONS — force a finish
        trace.hit_limit = True
        logger.warning(f"[agent loop] hit MAX_ITERATIONS={MAX_ITERATIONS}, forcing finish")
        force_msg = loop_messages + [
            {
                "role": "user",
                "content": (
                    f"{observation_log}\n"
                    "You have reached the maximum number of iterations. "
                    "Provide your best answer now based on everything gathered."
                )
            }
        ]
        async for chunk in self._adapter.chat(force_msg, temperature=0.4):
            yield chunk
