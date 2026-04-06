import { useState, useCallback, useRef } from 'react'
import { streamChat, newSessionId } from '../lib/api'

/**
 * useChat — manages all chat state and streaming.
 *
 * Returns:
 *   messages    - array of {id, role, content, pending}
 *   sessionId   - current session UUID
 *   isStreaming  - true while a response is in-flight
 *   error        - last error string or null
 *   file         - currently attached File or null
 *   setFile      - setter for file
 *   send         - function(messageText) → starts a stream
 *   reset        - clears messages and starts a new session
 */
export function useChat(initialSessionId) {
  const [messages, setMessages] = useState([])
  const [sessionId, setSessionId] = useState(initialSessionId || newSessionId)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState(null)
  const [file, setFile] = useState(null)

  const abortRef = useRef(null)
  const streamingIdRef = useRef(null)

  const send = useCallback(
    (text) => {
      if (!text.trim() || isStreaming) return

      setError(null)

      // Add user message immediately
      const userMsg = { id: Date.now(), role: 'user', content: text, file: file?.name }
      const assistantId = Date.now() + 1
      const assistantMsg = { id: assistantId, role: 'assistant', content: '', pending: true }

      setMessages((prev) => [...prev, userMsg, assistantMsg])
      streamingIdRef.current = assistantId
      setIsStreaming(true)

      const currentFile = file
      setFile(null) // clear after send

      const abort = streamChat({
        message: text,
        sessionId,
        file: currentFile,
        onChunk: (token) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === streamingIdRef.current
                ? { ...m, content: m.content + token, pending: true }
                : m
            )
          )
        },
        onError: (err) => {
          setError(err)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === streamingIdRef.current
                ? { ...m, content: `Error: ${err}`, pending: false, error: true }
                : m
            )
          )
        },
        onDone: () => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === streamingIdRef.current ? { ...m, pending: false } : m
            )
          )
          setIsStreaming(false)
          streamingIdRef.current = null
        },
      })

      abortRef.current = abort
    },
    [isStreaming, sessionId, file]
  )

  const reset = useCallback(() => {
    if (abortRef.current) abortRef.current()
    setMessages([])
    setSessionId(newSessionId())
    setIsStreaming(false)
    setError(null)
    setFile(null)
  }, [])

  const cancelStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current()
      setIsStreaming(false)
      setMessages((prev) =>
        prev.map((m) =>
          m.id === streamingIdRef.current ? { ...m, pending: false } : m
        )
      )
    }
  }, [])

  return { messages, sessionId, isStreaming, error, file, setFile, send, reset, cancelStream }
}
