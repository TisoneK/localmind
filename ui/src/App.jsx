/**
 * App.jsx — v0.5
 *
 * Session state is owned here and follows a simple state machine:
 *
 *   null          → "new chat" mode (no session selected, no history loaded)
 *   string (uuid) → active session, history loaded / loading
 *
 * Rules:
 *   - On startup: always null (new chat mode, never auto-select last session)
 *   - New Chat button: set to null
 *   - Session selected from sidebar: set to that UUID
 *   - Session deleted: if it was active, set to null
 *   - sessionId from useChat: NEVER auto-overwrites currentSessionId
 *     (that was the root cause of the auto-selection bug — removed entirely)
 */
import { useState, useCallback } from 'react'
import { StatusBar }           from './components/StatusBar'
import { Sidebar }             from './components/Sidebar'
import { MessageList }         from './components/MessageList'
import { ChatInput }           from './components/ChatInput'
import { ErrorBanner }         from './components/ErrorBanner'
import { ObservabilityPanel }  from './components/ObservabilityPanel'
import { useChat }             from './hooks/useChat'
import { useSession }          from './hooks/useSession'
import { useHealth }           from './hooks/useHealth'
import { BootSplash }          from './components/BootSplash'

const S = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    overflow: 'hidden',
    background: '#0f0f12',
  },
  body: { display: 'flex', flex: 1, overflow: 'hidden' },
  main: { flex: 1, display: 'flex', flexDirection: 'column' },
}

export default function App() {
  /**
   * currentSessionId: single source of truth.
   * null  = new-chat mode (startup default)
   * uuid  = explicit user selection or first-send promotion
   */
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const { sessions, refreshSessions } = useSession()
  const { engineReady, health, error: healthError } = useHealth()

  const {
    messages,
    sessionId,      // internal uuid for the active stream
    isStreaming,
    error,
    file,
    setFile,
    send,
    reset,
    cancelStream,
    observabilityData,
  } = useChat(currentSessionId)

  // ── Session actions ──────────────────────────────────────────────────────

  const handleNewChat = useCallback(() => {
    setCurrentSessionId(null)
    reset()
    refreshSessions()
  }, [reset, refreshSessions])

  const handleSessionSelect = useCallback((session) => {
    if (session.id === currentSessionId) return
    setCurrentSessionId(session.id)
  }, [currentSessionId])

  const handleSessionDelete = useCallback((deletedId) => {
    if (deletedId === currentSessionId) {
      setCurrentSessionId(null)
      reset()
    }
    refreshSessions()
  }, [currentSessionId, reset, refreshSessions])

  /**
   * Intercept first send in new-chat mode:
   * promote useChat's internal sessionId to currentSessionId so the
   * sidebar highlights the newly created session.
   * This is the ONLY path where useChat's sessionId flows into App state.
   */
  const handleSend = useCallback((text) => {
    send(text)
    if (currentSessionId === null) {
      setCurrentSessionId(sessionId)
      refreshSessions()
    }
  }, [send, currentSessionId, sessionId, refreshSessions])

  return (
    <>
      <BootSplash engineReady={engineReady} health={health} error={healthError} />
    <div style={S.root}>
      <StatusBar onNewChat={handleNewChat} sessionId={currentSessionId || sessionId} />
      <ErrorBanner error={error} onDismiss={() => {}} />
      <div style={S.body}>
        <Sidebar
          sessionId={currentSessionId}
          sessions={sessions}
          onSessionSelect={handleSessionSelect}
          onSessionDelete={handleSessionDelete}
          onNewChat={handleNewChat}
          onRefresh={refreshSessions}
        />
        <div style={S.main}>
          <MessageList messages={messages} />
          <ChatInput
            onSend={handleSend}
            isStreaming={isStreaming}
            onStop={cancelStream}
            file={file}
            onFile={setFile}
          />
        </div>
        <ObservabilityPanel observabilityData={observabilityData} />
      </div>
    </div>
    </>
  )
}
