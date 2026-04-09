# Chat Behavior Investigation Report

## Issue Summary
The LocalMind UI has persistent issues with automatic session selection:
1. **Startup Issue**: App opens most recent chat instead of new chat
2. **Delete Issue**: After deleting current chat, opens second most recent instead of new chat

## Investigation Process

### Initial Analysis
- Examined App.jsx session initialization logic
- Analyzed Sidebar.jsx session loading and deletion handling  
- Reviewed useChat.js hook behavior
- Checked backend session ordering (most recent first)

### Key Findings

#### 1. Root Cause: Session Synchronization Logic
The primary issue is in App.jsx where multiple useEffect hooks compete to set `currentSessionId`:

```javascript
// Initial session setting
useEffect(() => {
  if (sessionId && currentSessionId === null) {
    setCurrentSessionId(sessionId)
    saveCurrentSession(sessionId)
  }
}, [sessionId])

// Sync effect that can override
useEffect(() => {
  if (sessionId !== currentSessionId && currentSessionId !== null) {
    setCurrentSessionId(sessionId)
    saveCurrentSession(sessionId)
  }
}, [sessionId, currentSessionId])
```

#### 2. Session List Loading Interference
- Sidebar loads sessions ordered by `last_active DESC` (most recent first)
- When sessions list loads, something triggers auto-selection of first session
- Timing issues between session creation and list loading

#### 3. localStorage Persistence Conflicts
- localStorage persistence added to maintain sessions across refreshes
- This conflicts with "always start new" requirement
- Clearing localStorage doesn't prevent auto-selection

### Attempts Made

#### 1. Fixed Startup Behavior
- Changed `useChat(savedSessionId)` to `useChat(null)` 
- Added validation to clear invalid saved sessions
- **Result**: Partial success, but auto-selection still occurs

#### 2. Improved Delete Handling
- Enhanced `handleDeleteSession` with immediate localStorage clearing
- Added setTimeout for proper sequencing (later removed)
- **Result**: Still auto-selects second most recent session

#### 3. Session Sync Logic Fixes
- Modified sync effects to prevent overriding new sessions
- Added initialization flags to prevent interference
- **Result**: Sessions list stopped updating

#### 4. Added Debugging
- Added console.log statements throughout the flow
- **Result**: Identified that auto-selection happens after sessions load

### Current State
- Sessions list updates properly
- New chat creation works
- Delete operation creates new session
- **BUT**: Auto-selection of most recent session still occurs

## Technical Details

### Backend Session Ordering
```sql
ORDER BY last_active DESC
```
This means sessions are always returned with most recent first.

### Critical Code Paths
1. **App.jsx**: Session initialization and sync logic
2. **Sidebar.jsx**: Session loading and deletion
3. **useChat.js**: Session creation and history loading

### Timing Issues Identified
1. `useChat(null)` creates new session immediately
2. Sidebar loads sessions asynchronously
3. Something triggers selection of first session in list
4. Sync effects may override intended session

## Recommendations

### Short-term Fixes
1. **Add explicit new session flag**: Prevent any auto-selection when creating new chat
2. **Delay session list loading**: Ensure new session is fully established before loading list
3. **Remove sync effect complexity**: Simplify session management logic

### Long-term Solutions
1. **Refactor session state management**: Centralize session logic in custom hook
2. **Implement proper state machine**: Define clear states for session lifecycle
3. **Add comprehensive tests**: Prevent regression of session behavior

## Files Modified
- `ui/src/App.jsx`: Session initialization and sync logic
- `ui/src/components/Sidebar.jsx`: Session loading and deletion
- `ui/src/hooks/useChat.js`: History loading prevention

## Debugging Added
- Console logging throughout session lifecycle
- Session change tracking
- List loading monitoring

## Next Steps
1. Identify exact point where auto-selection occurs (requires deeper debugging)
2. Implement session state machine to prevent race conditions
3. Consider moving session management to dedicated context/hook
4. Add unit tests for session behavior scenarios

## Status
**UNRESOLVED**: Auto-selection issue persists despite multiple fix attempts.
**REQUIRES**: Deeper investigation into session selection trigger points.
