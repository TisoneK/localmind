/**
 * App.jsx — v0.6
 *
 * Session state owned here. State machine:
 *   null   → new chat (startup default, no history)
 *   string → active session uuid
 */
import { useState, useCallback } from 'react'
import { StatusBar }          from './components/StatusBar'
import { Sidebar }            from './components/Sidebar'
import { MessageList }        from './components/MessageList'
import { ChatInput }          from './components/ChatInput'
import { ErrorBanner }        from './components/ErrorBanner'
import { ObservabilityPanel } from './components/ObservabilityPanel'
import { useChat }            from './hooks/useChat'
import { useSession }         from './hooks/useSession'
import { useHealth }          from './hooks/useHealth'
import { BootSplash }         from './components/BootSplash'

const S = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    overflow: 'hidden',
    background: '#0c0c10',
    fontFamily: '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
  },
  body: { display: 'flex', flex: 1, overflow: 'hidden' },
  main: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
}

export default function App() {
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const { sessions, refreshSessions }           = useSession()
  const { engineReady, health, error: healthError } = useHealth()

  const {
    messages, sessionId, isStreaming, error, file, setFile,
    send, reset, cancelStream, observabilityData,
  } = useChat(currentSessionId)

  const handleNewChat = useCallback(() => {
    setCurrentSessionId(null); reset(); refreshSessions()
  }, [reset, refreshSessions])

  const handleSessionSelect = useCallback((session) => {
    if (session.id === currentSessionId) return
    setCurrentSessionId(session.id)
  }, [currentSessionId])

  const handleSessionDelete = useCallback((deletedId) => {
    if (deletedId === currentSessionId) { setCurrentSessionId(null); reset() }
    refreshSessions()
  }, [currentSessionId, reset, refreshSessions])

  const handleSend = useCallback((text) => {
    send(text)
    if (currentSessionId === null) { setCurrentSessionId(sessionId); refreshSessions() }
  }, [send, currentSessionId, sessionId, refreshSessions])

  return (
    <>
      <BootSplash engineReady={engineReady} health={health} error={healthError} />
      <div style={S.root}>
        <StatusBar
          onNewChat={handleNewChat}
          sessionId={currentSessionId || sessionId}
          health={health}
        />
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
