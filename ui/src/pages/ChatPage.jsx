import { MessageList } from '../components/MessageList'
import { ChatInput } from '../components/ChatInput'
import { ErrorBanner } from '../components/ErrorBanner'
import { useChat } from '../hooks/useChat'

const S = {
  page: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
  },
}

export function ChatPage() {
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
  } = useChat()

  return (
    <div style={S.page}>
      <ErrorBanner error={error} onDismiss={() => {}} />
      <MessageList messages={messages} />
      <ChatInput
        onSend={send}
        isStreaming={isStreaming}
        onStop={cancelStream}
        file={file}
        onFile={setFile}
      />
    </div>
  )
}

// Export sessionId and reset so App can pass to StatusBar
export { useChat }
