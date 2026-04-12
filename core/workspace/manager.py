"""
Workspace Manager — v1.0

Each turn gets an isolated directory under ~/.localmind/workspace/<turn_id>/.
Tools read from and write to this directory. They never see each other's
raw tool outputs — only the slices the orchestrator chooses to forward.

Directory layout per turn
─────────────────────────
  <turn_id>/
    TODO.md          ← task queue; orchestrator writes, tools check-off
    CONTEXT.md       ← growing shared knowledge for this turn
    thinking.md      ← orchestrator scratch-pad (deleted after turn)
    inbox/
      <tool>.in.md   ← exactly what that tool needs, nothing more
    outbox/
      <tool>.out.md  ← what that tool produced
    done             ← sentinel file; written when all tasks complete

Key design decisions
────────────────────
- Each tool's inbox contains ONLY its task + the minimum context it needs.
  The orchestrator slices CONTEXT.md before forwarding — no tool ever sees
  another tool's raw output unless the orchestrator explicitly promotes it.
- TODO.md uses a simple checked-list format so a small LLM can parse it
  reliably without structured JSON.
- Files are UTF-8 text throughout. No binary, no pickle, no JSON blobs.
  Plain text survives model context injection without escaping.
- WorkspaceManager is NOT async — all ops are synchronous local disk I/O
  which completes in <1ms. No need for async overhead.
"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

# Root workspace dir — one subdir per turn
_WORKSPACE_ROOT: Optional[Path] = None
_ROOT_LOCK = threading.Lock()


def _root() -> Path:
    global _WORKSPACE_ROOT
    if _WORKSPACE_ROOT is None:
        with _ROOT_LOCK:
            if _WORKSPACE_ROOT is None:
                from core.config import settings
                _WORKSPACE_ROOT = Path(
                    getattr(settings, "localmind_workspace_path", None)
                    or Path.home() / ".localmind" / "workspace"
                )
                _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return _WORKSPACE_ROOT


class WorkspaceManager:
    """
    Manages one turn's isolated workspace directory.

    Usage:
        ws = WorkspaceManager.new()          # create fresh turn workspace
        ws.write_todo(tasks)                 # write the task queue
        ws.write_inbox("sysinfo", text)      # write tool-specific instruction
        ws.write_outbox("sysinfo", result)   # tool writes its output
        ws.write_context(section, text)      # promote result to shared context
        result = ws.read_outbox("sysinfo")   # read what a tool produced
        ws.cleanup()                         # delete turn dir after use
    """

    def __init__(self, turn_id: str):
        self.turn_id = turn_id
        self.root = _root() / turn_id
        (self.root / "inbox").mkdir(parents=True, exist_ok=True)
        (self.root / "outbox").mkdir(parents=True, exist_ok=True)

    @classmethod
    def new(cls) -> "WorkspaceManager":
        """Create a fresh workspace for a new turn."""
        turn_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        return cls(turn_id)

    # ── TODO.md ───────────────────────────────────────────────────────────

    def write_todo(self, tasks: list[dict]) -> None:
        """
        Write the task queue.

        tasks is a list of dicts:
          {
            "id": "t1",
            "tool": "sysinfo",
            "goal": "Get current RAM and CPU usage",
            "depends_on": [],   # list of task ids that must complete first
          }

        Format in file:
          ## Tasks
          - [ ] t1 [sysinfo] Get current RAM and CPU usage
          - [ ] t2 [web_search] Find DDR5 upgrade prices (needs: t1)
        """
        lines = ["## Tasks\n"]
        for t in tasks:
            deps = t.get("depends_on", [])
            dep_str = f" (needs: {', '.join(deps)})" if deps else ""
            lines.append(f"- [ ] {t['id']} [{t['tool']}] {t['goal']}{dep_str}\n")
        (self.root / "TODO.md").write_text("".join(lines), encoding="utf-8")

    def mark_done(self, task_id: str) -> None:
        """Check off a task in TODO.md."""
        todo = self.root / "TODO.md"
        if not todo.exists():
            return
        text = todo.read_text(encoding="utf-8")
        text = text.replace(f"- [ ] {task_id} ", f"- [x] {task_id} ", 1)
        todo.write_text(text, encoding="utf-8")

    def read_todo(self) -> str:
        todo = self.root / "TODO.md"
        return todo.read_text(encoding="utf-8") if todo.exists() else ""

    def pending_tasks(self) -> list[dict]:
        """Parse TODO.md and return unchecked tasks with their metadata."""
        text = self.read_todo()
        tasks = []
        for line in text.splitlines():
            if line.startswith("- [ ]"):
                # "- [ ] t1 [sysinfo] Get RAM (needs: t0)"
                rest = line[5:].strip()
                parts = rest.split(" ", 1)
                task_id = parts[0]
                remainder = parts[1] if len(parts) > 1 else ""
                tool = ""
                if remainder.startswith("["):
                    end = remainder.index("]")
                    tool = remainder[1:end]
                    remainder = remainder[end + 2:]
                goal = remainder
                depends_on = []
                if "(needs:" in goal:
                    goal, dep_part = goal.split("(needs:", 1)
                    depends_on = [d.strip().rstrip(")") for d in dep_part.split(",")]
                    goal = goal.strip()
                tasks.append({
                    "id": task_id,
                    "tool": tool,
                    "goal": goal.strip(),
                    "depends_on": depends_on,
                })
        return tasks

    def completed_task_ids(self) -> set[str]:
        """Return the set of checked-off task IDs."""
        text = self.read_todo()
        done = set()
        for line in text.splitlines():
            if line.startswith("- [x]"):
                rest = line[5:].strip()
                task_id = rest.split(" ", 1)[0]
                done.add(task_id)
        return done

    # ── CONTEXT.md ────────────────────────────────────────────────────────

    def write_context(self, section: str, content: str) -> None:
        """
        Append a named section to CONTEXT.md.
        Each section is separated so the orchestrator can extract slices.
        """
        ctx = self.root / "CONTEXT.md"
        existing = ctx.read_text(encoding="utf-8") if ctx.exists() else ""
        block = f"\n## {section}\n{content.strip()}\n"
        ctx.write_text(existing + block, encoding="utf-8")

    def read_context(self, sections: Optional[list[str]] = None) -> str:
        """
        Read CONTEXT.md. If sections is given, return only those sections.
        This is how the orchestrator limits what each tool sees.
        """
        ctx = self.root / "CONTEXT.md"
        if not ctx.exists():
            return ""
        text = ctx.read_text(encoding="utf-8")
        if sections is None:
            return text

        # Extract only the requested sections
        result = []
        current_section: Optional[str] = None
        current_lines: list[str] = []

        for line in text.splitlines(keepends=True):
            if line.startswith("## "):
                if current_section and current_section in sections:
                    result.append(f"## {current_section}\n{''.join(current_lines)}")
                current_section = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_section and current_section in sections:
            result.append(f"## {current_section}\n{''.join(current_lines)}")

        return "\n".join(result)

    # ── Inbox / Outbox ────────────────────────────────────────────────────

    def write_inbox(self, tool_name: str, instruction: str) -> Path:
        """
        Write a tool's inbox file — exactly what that tool needs to act.
        The orchestrator assembles this from the task goal + relevant context slices.
        Nothing else.
        """
        p = self.root / "inbox" / f"{tool_name}.in.md"
        p.write_text(instruction.strip(), encoding="utf-8")
        return p

    def read_inbox(self, tool_name: str) -> str:
        p = self.root / "inbox" / f"{tool_name}.in.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write_outbox(self, tool_name: str, result: str) -> Path:
        """Tool writes its output here. Orchestrator decides what gets promoted."""
        p = self.root / "outbox" / f"{tool_name}.out.md"
        p.write_text(result.strip(), encoding="utf-8")
        return p

    def read_outbox(self, tool_name: str) -> str:
        p = self.root / "outbox" / f"{tool_name}.out.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def outbox_exists(self, tool_name: str) -> bool:
        return (self.root / "outbox" / f"{tool_name}.out.md").exists()

    # ── Thinking scratchpad ───────────────────────────────────────────────

    def write_thinking(self, text: str) -> None:
        (self.root / "thinking.md").write_text(text.strip(), encoding="utf-8")

    def read_thinking(self) -> str:
        p = self.root / "thinking.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Delete the entire turn workspace. Call after streaming completes."""
        try:
            shutil.rmtree(self.root, ignore_errors=True)
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"WorkspaceManager(turn={self.turn_id}, root={self.root})"
