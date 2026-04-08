import { useState } from 'react'
import { StatusBar } from './components/StatusBar'
import { MessageList } from './components/MessageList'
import { ChatInput } from './components/ChatInput'
import { ErrorBanner } from './components/ErrorBanner'
import { ObservabilityPanel } from './components/ObservabilityPanel'
import { useChat } from './hooks/useChat'

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
  } = useChat()

  return (
    <div style={S.root}>
      <StatusBar onNewChat={reset} sessionId={sessionId} />
      <ErrorBanner error={error} onDismiss={() => {}} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
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
