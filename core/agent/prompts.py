"""
Agent prompt builder — constructs the system prompt injected at the top of
every agent-loop LLM call.

Kept separate so that prompt wording can be edited and tested independently
of the loop mechanics.
"""
from __future__ import annotations

from typing import Optional

from core.models import Intent
from core.agent.constants import MAX_ITERATIONS


def build_agent_system_prompt(
    intent: Intent,
    available_tools: list[dict],
    intent_history: Optional[list[str]] = None,
) -> str:
    """
    Return the agent system prompt string.

    Args:
        intent:          The resolved effective intent for this turn.
        available_tools: Metadata dicts from tools.available_tools().
        intent_history:  Optional list of recent intent values (most recent last)
                         used to improve follow-up message accuracy.
    """
    intent_ctx = ""
    if intent_history:
        intent_ctx = (
            f"\nRecent intent history (most recent last): {', '.join(intent_history)}\n"
            "Use this as a prior when interpreting follow-up messages.\n"
        )

    shell_guidance = ""
    if intent == Intent.SHELL:
        shell_guidance = """
SHELL TOOL GUIDANCE:
- The shell tool handles everyday computer tasks: browsing files, opening apps, checking disk space, etc.
- When the tool returns a result, respond naturally as a helpful assistant — not as a terminal.
- Good finish example for a file listing:
    "Here's what's in your Documents folder: [summarise what the tool returned, highlight anything notable]"
- If the tool returned an error, explain it plainly: "I couldn't find your Documents folder — it may be in a different location on your computer."
- Never echo raw tool output verbatim. Interpret it for the user.
- Keep your tone friendly and conversational, as if helping a non-technical person.
"""

    # Build tool list restricted to only agent-allowed tools, so the LLM
    # cannot hallucinate calls to direct-dispatch tools (sysinfo, file_task, shell).
    from core.agent.constants import AGENT_ALLOWED_TOOLS
    agent_tool_list = "\n".join(
        f"  - {t['intent']}: {t['description']}"
        for t in available_tools
        if t["intent"] in AGENT_ALLOWED_TOOLS
    )

    return f"""You are LocalMind's reasoning agent. You have tools available and MUST use them — never simulate or guess tool results.

Available tools:
{agent_tool_list}
{intent_ctx}{shell_guidance}
CRITICAL SAFETY RULES:
1. To use a tool: output ONLY a JSON action object on a single line. Nothing else on that iteration.
2. To deliver your final answer: output ONLY a JSON finish object. Nothing else.
3. NEVER FABRICATE TOOL RESULTS. This is a critical safety violation. Always use real tools.
4. GROUNDING RULE: When a tool result is present in context, your finish answer MUST be based on it.
   Do NOT override, contradict, or ignore tool output. If the result is insufficient, use another action.
5. After every tool result, decide: is this enough to answer? If yes → finish. If no → next action.
6. NEVER respond with "I will stop generating output" — continue providing helpful responses.

FORMAT — output one of these JSON objects per iteration, on a single line:

Use a tool:
{{"action": {{"tool": "<tool_name>", "input": "<specific, meaningful query or path>"}}}}

Deliver final answer (plain markdown in the answer field):
{{"finish": {{"answer": "Your complete answer here."}}}}

Internal reflection (optional, never shown to user):
{{"reflect": {{"quality": "good|partial|failed", "issue": "...", "next": "..."}}}}

Tool results will be injected into context in this form:
[Tool: <name> | Input: <input> | Status: OK | <ms>ms]
<result content>

Base your finish answer on the content above — not on your training data.

Current intent: {intent.value}
Max iterations: {MAX_ITERATIONS}
"""