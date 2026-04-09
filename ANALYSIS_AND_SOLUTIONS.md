# LocalMind — Comprehensive Analysis & Solutions

*Generated: 2026-04-09 | Based on full codebase review of localmind-20260409-142057*

---

## 1. Agent Module Refactor (`core/agent/`)

### What was done

The monolithic `core/agent.py` (≈ 450 lines) has been split into a proper Python package at `core/agent/`:

| File | Responsibility |
|------|---------------|
| `__init__.py` | Public API — re-exports `AgentLoop`, `AgentTrace`, `AgentStep`, `AGENT_INTENTS` |
| `constants.py` | All tunable values in one place (`MAX_ITERATIONS`, thresholds, retry config, etc.) |
| `models.py` | `AgentStep` and `AgentTrace` dataclasses — importable without pulling in the loop |
| `prompts.py` | `build_agent_system_prompt()` — editable independently of loop mechanics |
| `agent_filters.py` | Thought sanitization, re-exports `core.filters` so loop has one import point |
| `search.py` | `truncate_web_search_results()` + `create_extractive_summary()` |
| `tool_dispatch.py` | `dispatch_with_retry()` with exponential backoff |
| `loop.py` | `AgentLoop` class — the think→act→observe→reflect cycle |

**Engine compatibility:** `engine.py` already imports `from core.agent import AgentLoop, AGENT_INTENTS` — this matches the new package's public API exactly. No changes needed to `engine.py`.

### Bugs fixed during refactor

1. **Duplicate `from tools import dispatch` import** inside the web-search branch of the action handler — removed (module-level import used instead).
2. **Double `except` block on file read** in `_summarize_search_results_background` — was a copy-paste error, both blocks identical.
3. **Dead background task** — `_summarize_search_results_background` was defined and scheduled but never awaited, meaning it silently did nothing. The inline extractive summary (added in v0.4) is the correct path. The background task has been removed.
4. **`observation_log` mutation inside generator** — the old loop mutated a local string inside what was effectively a generator, which could cause stale state across iterations. Extracted to `_handle_action()` with explicit return-by-instance-attribute pattern.

### Old file

`core/agent.py` has been renamed to `core/agent.py.bak` for safety. Delete it once you've confirmed the new package works.

---

## 2. UI Session Auto-Selection Bug (RESOLVED)

### Root cause

Two competing `useEffect` hooks in `App.jsx` fought over `currentSessionId`:

```js
// Effect 1 — fired when useChat created a new sessionId
useEffect(() => {
  if (sessionId && currentSessionId === null) {
    setCurrentSessionId(sessionId)   // ← triggered on mount
  }
}, [sessionId])

// Effect 2 — "sync" effect that overrode explicit selections
useEffect(() => {
  if (sessionId !== currentSessionId && currentSessionId !== null) {
    setCurrentSessionId(sessionId)   // ← overrode sidebar selection
  }
}, [sessionId, currentSessionId])
```

**Effect 1** auto-promoted useChat's internal sessionId on mount → user always landed in the last session.

**Effect 2** then kept overwriting explicit sidebar selections with useChat's stale id.

Additionally, `Sidebar.jsx` re-fetched sessions on every `sessionId` change and had no way to tell App about deletions cleanly — it called `onNewChat()` directly, which could collide with Effect 1.

### Fix

**`App.jsx`** — both effects removed. Session state is now a simple state machine:

```
null  (startup, New Chat, delete-active)
  ↓  user clicks session in sidebar
uuid  (active session)
  ↓  user sends first message in new-chat mode
uuid  (promoted from useChat internal id — only path where useChat influences App state)
```

**`Sidebar.jsx`** — sessions list is now passed in as props from `useSession()` (owned by App). Sidebar no longer fetches. Deletion calls `onSessionDelete(id)` so App decides what happens to state.

**`useChat.js`** — history loading now depends only on `initialSessionId` prop changes (not `sessionId` internal state). `null` → clear messages, `uuid` → fetch history.

**`useSession.js`** — rewritten as a proper hook with cancellation and a ref-guard preventing double-fetch on mount.

### Files changed

| File | Change |
|------|--------|
| `ui/src/App.jsx` | Removed both sync useEffects; added `handleSend`/`handleSessionDelete`; passes `sessions` prop to Sidebar |
| `ui/src/components/Sidebar.jsx` | Now accepts `sessions` as prop; pure display component; calls `onSessionDelete` |
| `ui/src/hooks/useChat.js` | History effect simplified to depend only on `initialSessionId` |
| `ui/src/hooks/useSession.js` | Rewritten with cancellation and mount-guard |

---

## 3. Other Issues Found (Not Blocking, Recommended Fixes)

### 3.1 `App.jsx` had broken imports

```js
import { useHealth } from '../hooks/useHealth'   // ← wrong relative path
import { useSession } from '../hooks/useSession'  // ← wrong relative path
```

Both pointed to `../hooks/` (one level up from `src/`) but the hooks live at `src/hooks/`. Fixed in the new App.jsx (`./hooks/useSession`). `useHealth` was imported but never used — removed.

### 3.2 `fetchHistory` called in `handleSessionSelect` but result discarded

Old App.jsx:
```js
const history = await fetchHistory(newSessionId)
// The useChat hook will handle loading the history ← comment lie
```
The result was thrown away. `useChat` never received it. Fixed by letting `useChat`'s `useEffect` on `initialSessionId` handle history loading.

### 3.3 `MAX_ITERATIONS = 2` is very low for local LLMs

The agent loop gets at most 2 think→act cycles. With slow local models (Ollama), a single tool call + reflection can exhaust the budget, forcing a low-quality forced synthesis. Consider raising to 3–4 once the local model is tuned.

### 3.4 `OBS_LOG_MAX_CHARS = 100` causes context truncation artifacts

At 100 characters per observation entry the agent often has less than one sentence of context to reason from. This causes the "I will stop generating output" pathology noted in the chat. For models with context windows > 4k tokens, raise to 500–1000.

### 3.5 `WEB_SEARCH_MAX_RESULTS = 1` means one result

Useful for minimizing context, but the single result is also truncated to 100 chars — effectively giving the agent ~a title and nothing else. If web search quality is poor, this is why. Consider 2 results × 300 chars each.

### 3.6 Agent system prompt safety rule #9 creates a loop

> 9. NEVER respond with "I will stop generating output" - continue providing helpful responses.

This rule was added to fix the symptom, not the cause. The cause is rules 1–2 conflicting: the model outputs a `<finish>` block, the agent strips it, the model sees its finish was ignored, panics, and outputs "I will stop". Fix: ensure `_FINISH_PATTERN` regex is robust and always matches. See §3.4 above — context truncation is likely causing malformed `<finish>` tags.

### 3.7 `engine.py` stores user message twice on agent path

In `_event_stream` (chat route), the user message is stored before streaming begins:
```python
store.append(session_id, user_message)
```
Then at the end of `engine.process()`:
```python
self._store.append(session_id, Message(role=Role.USER, content=message))
```
This results in every user message being stored twice in the database. Fix: remove the duplicate storage in `engine.process()` for sessions where the chat route already stored it, or move all storage to the engine layer and remove it from the route.

### 3.8 `useChat` `sessionId` initialized to `newSessionId()` on every render path

```js
const [sessionId, setSessionId] = useState(initialSessionId || newSessionId())
```
If `initialSessionId` is null, a random UUID is generated. This UUID is used as the `session_id` in the first API call. If the user types in new-chat mode, messages go to this ephemeral UUID — which is fine — but the session is never explicitly "created" in the database until the first message is stored by the engine. This is correct behavior but worth documenting for future developers.

---

## 4. Recommended Next Steps

1. **Delete `core/agent.py.bak`** after confirming the agent package works end-to-end.
2. **Tune constants** in `core/agent/constants.py`: raise `MAX_ITERATIONS` to 3, `OBS_LOG_MAX_CHARS` to 500, `WEB_SEARCH_MAX_RESULTS` to 2.
3. **Fix double message storage** (§3.7) — one-line fix in `engine.py`.
4. **Add agent package tests** — `core/agent/tool_dispatch.py` and `core/agent/search.py` are now independently testable without a running Ollama instance.
5. **Session state machine test** — the new App.jsx session flow should be covered by Playwright or Vitest + React Testing Library tests for the three scenarios: startup, new-chat, delete-active.

---

*End of analysis. Questions? Check the inline docstrings in each new `core/agent/*.py` file.*
