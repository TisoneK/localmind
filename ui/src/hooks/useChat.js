import { useState, useCallback, useRef, useEffect } from 'react'
import { streamChat, newSessionId, fetchHistory } from '../lib/api'

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
 *   observabilityData - observability data
 */
export function useChat(initialSessionId) {
  const [messages, setMessages] = useState([])
  const [sessionId, setSessionId] = useState(initialSessionId || newSessionId())
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState(null)
  const [file, setFile] = useState(null)
  const [observabilityData, setObservabilityData] = useState({})

  // Load history when session ID changes
  useEffect(() => {
    const loadHistory = async () => {
      if (sessionId) {
        try {
          const history = await fetchHistory(sessionId)
          setMessages(history.map((msg, index) => ({
            id: index,
            role: msg.role,
            content: msg.content,
            timestamp: msg.timestamp
          })))
        } catch (err) {
          console.error('Failed to load history:', err)
          setMessages([])
        }
      } else {
        setMessages([])
      }
    }

    loadHistory()
  }, [sessionId])

  const abortRef = useRef(null)
  const streamingIdRef = useRef(null)

  const send = useCallback(
    (text) => {
      if (!text.trim() || isStreaming) return

      setError(null)
      setObservabilityData({}) // Reset observability for new message

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
        onChunk: (chunk) => {
          // Text chunk for message content
          setMessages((prev) =>
            prev.map((m) =>
              m.id === streamingIdRef.current
                ? { ...m, content: m.content + chunk, pending: true }
                : m
            )
          )
        },
        onIntent: (intentData) => {
          // Intent classification event
          setObservabilityData(prev => ({
            ...prev,
            intent: intentData.intent,
            confidence: intentData.confidence
          }))
        },
        onObsEvent: (event) => {
          // Other observability events
          setObservabilityData(prev => {
            const updated = { ...prev }
            
            switch (event.type) {
              case 'tool_dispatched':
                updated.toolCalls = [...(prev.toolCalls || []), {
                  name: event.data.tool,
                  success: event.data.success,
                  latency: event.data.latency_ms
                }]
                break
              case 'memory_retrieved':
                updated.memoryHits = event.data.facts || []
                break
              case 'turn_complete':
                updated.latency = event.data.total_latency_ms
                updated.tokens = event.data.tokens_approx
                break
            }
            
            return updated
          })
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
          abortRef.current = null
        },
      })

      abortRef.current = abort
    },
    [isStreaming, sessionId, file]
  )

  const reset = useCallback(() => {
    if (abortRef.current) abortRef.current()
    setMessages([])
    const newId = newSessionId()
    setSessionId(newId)
    setIsStreaming(false)
    setError(null)
    setFile(null)
    setObservabilityData({})
    return newId
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

  return { messages, sessionId, isStreaming, error, file, setFile, send, reset, cancelStream, observabilityData }
}
