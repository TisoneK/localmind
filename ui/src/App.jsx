import { useState, useEffect } from 'react'
import { StatusBar } from './components/StatusBar'
import { Sidebar } from './components/Sidebar'
import { MessageList } from './components/MessageList'
import { ChatInput } from './components/ChatInput'
import { ErrorBanner } from './components/ErrorBanner'
import { ObservabilityPanel } from './components/ObservabilityPanel'
import { useChat } from './hooks/useChat'
import { fetchHistory } from './lib/api'
import { useHealth } from '../hooks/useHealth'
import { useSession } from '../hooks/useSession'

const S = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    overflow: 'hidden',
    background: '#0f0f12',
  },
}

export default function App() {
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const { refreshSessions } = useSession()
  
  const {
    messages,
    sessionId,
    isStreaming,
    error,
    file,
    setFile,
    send,
    reset,
    cancelStream,
    observabilityData,
  } = useChat(currentSessionId)

  // Sync session ID between sidebar and chat
  useEffect(() => {
    if (sessionId !== currentSessionId) {
      setCurrentSessionId(sessionId)
      refreshSessions() // Refresh to get updated session titles
    }
  }, [sessionId, refreshSessions])

  const handleSessionSelect = async (session) => {
    const newSessionId = session.id
    if (newSessionId === currentSessionId) return
    
    setCurrentSessionId(newSessionId)
    // Load history for the selected session
    try {
      const history = await fetchHistory(newSessionId)
      // The useChat hook will handle loading the history
    } catch (err) {
      console.error('Failed to load session history:', err)
    }
  }

  const handleNewChat = () => {
    const newSessionId = reset()
    setCurrentSessionId(newSessionId)
    refreshSessions() // Refresh session list to get the new session title
  }

  return (
    <div style={S.root}>
      <StatusBar onNewChat={handleNewChat} sessionId={currentSessionId} />
      <ErrorBanner error={error} onDismiss={() => {}} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar 
          sessionId={currentSessionId}
          onSessionSelect={handleSessionSelect}
          onNewChat={handleNewChat}
        />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <MessageList messages={messages} />
          <ChatInput
            onSend={send}
            isStreaming={isStreaming}
            onStop={cancelStream}
            file={file}
            onFile={setFile}
          />
        </div>
        <ObservabilityPanel observabilityData={observabilityData} />
      </div>
    </div>
  )
}
