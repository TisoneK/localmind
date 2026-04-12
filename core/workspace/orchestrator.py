"""
Workspace Orchestrator — v1.0

The orchestrator is the only component that sees everything.
Every tool sees only its own inbox.

Flow
────
  1. Plan   — LLM reads the user message, produces a TODO.md task list.
              Each task names one tool and states its goal in one sentence.
              Dependencies are declared explicitly.

  2. Dispatch — For each task whose dependencies are satisfied:
                a. Build its inbox: goal + only the context sections it needs.
                b. Call the tool (existing dispatch() function — unchanged).
                c. Write result to outbox.
                d. Promote result to CONTEXT.md under the tool's section name.
                e. Mark task done in TODO.md.
                f. Check if any newly-unblocked tasks can now run.
                Parallelizable tasks with satisfied deps run concurrently.

  3. Synthesise — LLM reads CONTEXT.md (all sections) + original message,
                  produces the final answer. It never saw intermediate tool
                  inputs — only the promoted outputs.

Context discipline (the core rule)
───────────────────────────────────
  Each tool's inbox = task_goal + context_slices[tool_name]
  context_slices defines which CONTEXT.md sections each tool may see.
  Default: a tool sees nothing from previous tools unless the orchestrator
  explicitly declares it needs that context via TOOL_CONTEXT_NEEDS.

  This keeps each tool's instruction under ~300 tokens regardless of how
  many tools have already run.

Planner prompt budget
─────────────────────
  The planner sees: user message + tool descriptions (names + one-line desc).
  It does NOT see history, memory facts, or prior tool results — those land
  in CONTEXT.md and are visible only during synthesis.
  Estimated tokens to planner: ~200 (message) + ~100 (tool list) = ~300 total.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import AsyncIterator, Optional

from core.models import Intent, StreamChunk, ToolResult
from core.workspace.manager import WorkspaceManager
from tools import dispatch, available_tools

logger = logging.getLogger(__name__)

# ── Intent → tool name mapping ────────────────────────────────────────────────
# Maps the tool name strings used in TODO.md back to Intent enum values.
_TOOL_NAME_TO_INTENT: dict[str, Intent] = {
    "sysinfo":    Intent.SYSINFO,
    "web_search": Intent.WEB_SEARCH,
    "file_task":  Intent.FILE_TASK,
    "file_write": Intent.FILE_WRITE,
    "code_exec":  Intent.CODE_EXEC,
    "shell":      Intent.SHELL,
    "memory_op":  Intent.MEMORY_OP,
}

# ── Context needs — which CONTEXT.md sections each tool may see ───────────────
# A tool listed here will receive the named sections pre-pended to its inbox.
# Tools not listed see nothing from previous tools (default: isolated).
# Declare only what's genuinely needed — every section adds tokens.
TOOL_CONTEXT_NEEDS: dict[str, list[str]] = {
    "web_search": [],                          # searches blind — no prior context needed
    "file_write": ["sysinfo", "web_search", "code_exec", "shell", "memory_op", "file_task"],
    "code_exec":  ["sysinfo", "file_task"],    # may need specs or file content
    "shell":      ["sysinfo"],                 # may need OS/path info
    "memory_op":  [],                          # reads its own store — no injected context
    "sysinfo":    [],                          # offline — never needs context
    "file_task":  [],                          # reads the file itself
}

# ── Planner prompt ────────────────────────────────────────────────────────────
_PLANNER_SYSTEM = """\
You are a task planner for a local AI assistant. Given a user request, output a JSON task list.

Available tools:
{tool_list}

Rules:
- Use only the tools listed above.
- Each task has exactly one tool.
- State the goal in one sentence — the tool will receive only this goal as its instruction.
- Declare depends_on only when the tool genuinely needs a prior tool's output.
- If the request needs only one tool, output one task.
- If the request can be answered from knowledge alone, output an empty task list.

Respond with ONLY a JSON array, no markdown, no explanation:
[
  {{"id": "t1", "tool": "sysinfo", "goal": "Get current RAM usage and total memory", "depends_on": []}},
  {{"id": "t2", "tool": "web_search", "goal": "Find DDR5 32GB upgrade options and prices", "depends_on": []}},
  {{"id": "t3", "tool": "file_write", "goal": "Write a comparison report of current vs upgrade options", "depends_on": ["t1", "t2"]}}
]"""

_PLANNER_USER = "User request: {message}"

# ── Synthesis prompt ──────────────────────────────────────────────────────────
_SYNTHESIS_SYSTEM = """\
You are LocalMind. Using the tool results below, answer the user's request directly.

Rules:
- Report tool results accurately — do not substitute your own estimates.
- Be concise. Use markdown only for code blocks and tables.
- If a tool failed or returned no data, say so plainly."""

_MAX_CONTEXT_TOKENS = 2000   # synthesis context cap — trim oldest sections first
_MAX_SECTION_CHARS  = 800    # per-section cap before truncation


# ── Orchestrator ──────────────────────────────────────────────────────────────

class WorkspaceOrchestrator:
    """
    Parallel-track orchestrator. Runs independently of the existing AgentLoop.
    Called by the engine for complex multi-step requests.

    Usage:
        orch = WorkspaceOrchestrator(adapter)
        async for chunk in orch.run(message, session_id, memory_facts, obs):
            yield chunk
    """

    def __init__(self, adapter):
        self._adapter = adapter

    async def run(
        self,
        message: str,
        session_id: str,
        memory_facts: list[str],
        obs,
    ) -> AsyncIterator[StreamChunk]:
        ws = WorkspaceManager.new()
        logger.info("[orch] turn=%s session=%s", ws.turn_id, session_id)

        try:
            # Seed CONTEXT.md with memory facts so synthesis can use them,
            # but NO tool sees them unless explicitly declared in TOOL_CONTEXT_NEEDS.
            if memory_facts:
                ws.write_context("memory", "\n".join(f"- {f}" for f in memory_facts))

            # ── 1. Plan ───────────────────────────────────────────────────
            obs.emit("workspace_planning", turn=ws.turn_id)
            tasks = await self._plan(message, ws)

            if not tasks:
                # Pure knowledge answer — skip tools entirely
                logger.info("[orch] planner: no tools needed, direct answer")
                async for chunk in self._synthesise(message, ws, obs, skip_context=True):
                    yield chunk
                return

            ws.write_todo(tasks)
            logger.info("[orch] planned %d tasks: %s",
                        len(tasks), [t["tool"] for t in tasks])
            obs.emit("workspace_tasks_planned", count=len(tasks),
                     tools=[t["tool"] for t in tasks])

            # ── 2. Dispatch loop ───────────────────────────────────────────
            await self._dispatch_loop(tasks, ws, obs)

            # ── 3. Synthesise ─────────────────────────────────────────────
            async for chunk in self._synthesise(message, ws, obs):
                yield chunk

        except Exception as exc:
            logger.error("[orch] fatal error: %s", exc, exc_info=True)
            yield StreamChunk(text=f"Orchestrator error: {exc}", done=False)
            yield StreamChunk(text="", done=True)
        finally:
            ws.cleanup()

    # ── Planning ──────────────────────────────────────────────────────────

    async def _plan(self, message: str, ws: WorkspaceManager) -> list[dict]:
        """Ask the LLM to produce a task list. Returns [] for pure-knowledge requests."""
        tools = available_tools()
        tool_list = "\n".join(
            f"- {t['intent']}: {t['description']}" for t in tools
        )
        messages = [
            {"role": "system", "content": _PLANNER_SYSTEM.format(tool_list=tool_list)},
            {"role": "user",   "content": _PLANNER_USER.format(message=message)},
        ]

        chunks = []
        try:
            async for chunk in self._adapter.chat(messages, temperature=0.0):
                chunks.append(chunk.text)
        except Exception as exc:
            logger.warning("[orch] planner LLM failed: %s", exc)
            return []

        raw = "".join(chunks).strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                return []
            tasks = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                tool = item.get("tool", "").strip().lower()
                if tool not in _TOOL_NAME_TO_INTENT:
                    logger.warning("[orch] planner returned unknown tool: %s", tool)
                    continue
                tasks.append({
                    "id":         item.get("id", f"t{len(tasks)+1}"),
                    "tool":       tool,
                    "goal":       item.get("goal", ""),
                    "depends_on": item.get("depends_on", []),
                })
            ws.write_thinking(f"Plan:\n{json.dumps(tasks, indent=2)}")
            return tasks
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[orch] planner JSON parse failed: %s — raw: %s", exc, raw[:200])
            return []

    # ── Dispatch loop ─────────────────────────────────────────────────────

    async def _dispatch_loop(
        self,
        tasks: list[dict],
        ws: WorkspaceManager,
        obs,
    ) -> None:
        """
        Execute tasks respecting dependencies.
        Tasks with satisfied deps run concurrently.
        Waits for all tasks to complete before returning.
        """
        remaining = {t["id"]: t for t in tasks}
        in_flight: set[str] = set()

        while remaining or in_flight:
            completed = ws.completed_task_ids()
            runnable = [
                t for tid, t in remaining.items()
                if tid not in in_flight
                and all(dep in completed for dep in t["depends_on"])
            ]

            if not runnable and not in_flight:
                # Dependency deadlock — run whatever is left
                logger.warning("[orch] dependency deadlock — forcing remaining tasks")
                runnable = list(remaining.values())

            if runnable:
                # Build inboxes and launch all runnable tasks concurrently
                coros = []
                for task in runnable:
                    inbox = self._build_inbox(task, ws)
                    ws.write_inbox(task["tool"], inbox)
                    in_flight.add(task["id"])
                    del remaining[task["id"]]
                    obs.emit("workspace_tool_start",
                             task_id=task["id"], tool=task["tool"])
                    coros.append(self._run_one_task(task, ws, obs))

                results = await asyncio.gather(*coros, return_exceptions=True)
                for task, result in zip(runnable, results):
                    in_flight.discard(task["id"])
                    if isinstance(result, BaseException):
                        logger.error("[orch] task %s failed: %s", task["id"], result)
                        ws.write_outbox(task["tool"], f"[Error: {result}]")
                        ws.write_context(task["tool"], f"Tool failed: {result}")
                    ws.mark_done(task["id"])
            else:
                # Nothing runnable right now — wait briefly for in-flight to finish
                await asyncio.sleep(0.05)

    def _build_inbox(self, task: dict, ws: WorkspaceManager) -> str:
        """
        Assemble the tool's inbox: task goal + only the context it needs.
        Hard cap: ~300 tokens. Nothing from other tools unless declared.
        """
        lines = [f"Task: {task['goal']}"]

        needed_sections = TOOL_CONTEXT_NEEDS.get(task["tool"], [])
        if needed_sections:
            ctx = ws.read_context(sections=needed_sections)
            if ctx.strip():
                # Trim each section to avoid bloat
                trimmed = _trim_context(ctx, max_chars=600)
                lines.append(f"\nRelevant context:\n{trimmed}")

        return "\n".join(lines)

    async def _run_one_task(
        self,
        task: dict,
        ws: WorkspaceManager,
        obs,
    ) -> None:
        """Execute one tool, write its result to outbox, promote to CONTEXT.md."""
        tool_name = task["tool"]
        intent = _TOOL_NAME_TO_INTENT[tool_name]
        inbox = ws.read_inbox(tool_name)

        t0 = time.monotonic()
        try:
            result: Optional[ToolResult] = await dispatch(intent, inbox)
            latency = round((time.monotonic() - t0) * 1000)

            # A tool that needs user confirmation (e.g. file_writer permission
            # gate) cannot be completed inside the orchestrator — the user's
            # reply would never reach _do_write.  Surface the confirmation
            # prompt as the final answer and bail out of this task.
            if result and getattr(result, "requires_confirmation", False):
                ws.write_outbox(tool_name, result.content)
                ws.write_context(tool_name, result.content[:_MAX_SECTION_CHARS])
                obs.emit("workspace_tool_confirmation_required",
                         task_id=task["id"], tool=tool_name)
                logger.info("[orch] task %s [%s] requires confirmation — surfacing to user",
                            task["id"], tool_name)
                return

            content = result.content if result else "[no result]"
            ws.write_outbox(tool_name, content)
            # Promote to CONTEXT.md — trimmed to _MAX_SECTION_CHARS
            ws.write_context(tool_name, content[:_MAX_SECTION_CHARS])

            obs.emit("workspace_tool_done",
                     task_id=task["id"], tool=tool_name,
                     latency_ms=latency, chars=len(content))
            logger.info("[orch] task %s [%s] done in %dms (%d chars)",
                        task["id"], tool_name, latency, len(content))

        except Exception as exc:
            ws.write_outbox(tool_name, f"[Error: {exc}]")
            ws.write_context(tool_name, f"Tool error: {exc}")
            obs.emit("workspace_tool_error",
                     task_id=task["id"], tool=tool_name, error=str(exc)[:80])
            raise

    # ── Synthesis ─────────────────────────────────────────────────────────

    async def _synthesise(
        self,
        message: str,
        ws: WorkspaceManager,
        obs,
        skip_context: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        """Stream the final answer to the user."""
        obs.emit("workspace_synthesising")

        context_block = ""
        if not skip_context:
            ctx = ws.read_context()
            if ctx.strip():
                # Trim total context to _MAX_CONTEXT_TOKENS * ~4 chars
                trimmed = _trim_context(ctx, max_chars=_MAX_CONTEXT_TOKENS * 4)
                context_block = f"\n\nTool results:\n{trimmed}"

        messages = [
            {"role": "system", "content": _SYNTHESIS_SYSTEM + context_block},
            {"role": "user",   "content": message},
        ]

        async for chunk in self._adapter.chat(messages):
            yield StreamChunk(text=chunk.text, done=chunk.done)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trim_context(text: str, max_chars: int) -> str:
    """
    Trim context text to max_chars. Trims from the oldest sections first
    (top of file) to preserve the most recent tool outputs.
    """
    if len(text) <= max_chars:
        return text
    # Drop content from the top until it fits
    lines = text.splitlines(keepends=True)
    while len("".join(lines)) > max_chars and lines:
        lines.pop(0)
    trimmed = "".join(lines)
    return f"[... earlier context trimmed ...]\n{trimmed}"


def _should_use_orchestrator(message: str, intent: Intent) -> bool:
    """
    Heuristic: use the workspace orchestrator for requests that likely need
    multiple tools or file-based coordination. Single-tool requests stay on
    the existing fast path.

    Returns True to engage the orchestrator, False to use existing engine path.
    """
    # Multi-tool trigger phrases
    MULTI_TOOL_SIGNALS = [
        "and save", "and write", "compare", "then save", "create a report",
        "save the result", "write a file", "generate a", "and store",
        "summarise and", "analyse and", "search and", "find and write",
        "read and", "check and", "get and",
    ]
    msg_lower = message.lower()
    if any(sig in msg_lower for sig in MULTI_TOOL_SIGNALS):
        return True

    # Only escalate FILE_WRITE / CODE_EXEC to the orchestrator when the
    # request is genuinely multi-step (already caught by MULTI_TOOL_SIGNALS
    # above).  A bare "write hello.py" or "run this code" is a single-tool
    # request — route it through the existing AgentLoop fast path instead.
    # Returning False here is safe: the engine's AGENT_INTENTS branch handles
    # FILE_WRITE and CODE_EXEC perfectly well on its own.
    return False
