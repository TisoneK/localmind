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

// localStorage utilities for session persistence
const STORAGE_KEY = 'localmind_current_session'
const saveCurrentSession = (sessionId) => {
  try {
    if (sessionId) {
      localStorage.setItem(STORAGE_KEY, sessionId)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch (err) {
    console.warn('Failed to save session to localStorage:', err)
  }
}

const loadCurrentSession = () => {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch (err) {
    console.warn('Failed to load session from localStorage:', err)
    return null
  }
}

const clearCurrentSession = () => {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch (err) {
    console.warn('Failed to clear session from localStorage:', err)
  }
}

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
  // Always start with new chat, don't load saved session on startup
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const { refreshSessions, sessions } = useSession()
  
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
  } = useChat(null) // Always start with fresh session

  // Set initial session when useChat creates one
  useEffect(() => {
    console.log('Initial session effect:', { sessionId, currentSessionId })
    if (sessionId && currentSessionId === null) {
      console.log('Setting initial session:', sessionId)
      setCurrentSessionId(sessionId)
      saveCurrentSession(sessionId)
    }
  }, [sessionId])

  // Sync session ID between sidebar and chat, save to localStorage
  useEffect(() => {
    console.log('Sync effect:', { sessionId, currentSessionId })
    if (sessionId !== currentSessionId && currentSessionId !== null) {
      console.log('Syncing session ID:', sessionId)
      setCurrentSessionId(sessionId)
      saveCurrentSession(sessionId)
    }
  }, [sessionId, currentSessionId])

  const handleSessionSelect = async (session) => {
    const newSessionId = session.id
    if (newSessionId === currentSessionId) return
    
    setCurrentSessionId(newSessionId)
    saveCurrentSession(newSessionId) // Save selected session
    // Load history for the selected session
    try {
      const history = await fetchHistory(newSessionId)
      // The useChat hook will handle loading the history
    } catch (err) {
      console.error('Failed to load session history:', err)
    }
  }

  const handleNewChat = () => {
    console.log('handleNewChat called')
    // Clear any saved session to ensure fresh start
    clearCurrentSession()
    const newSessionId = reset()
    console.log('New session created:', newSessionId)
    setCurrentSessionId(newSessionId)
    saveCurrentSession(newSessionId) // Save new session
    // Sidebar will auto-refresh when sessionId changes
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
