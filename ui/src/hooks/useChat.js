import { useState, useCallback, useRef, useEffect } from 'react'
import { streamChat, newSessionId, fetchHistory } from '../lib/api'

/**
 * useChat — manages all chat state and streaming.
 *
 * Message shape:
 *   { id, role, content, pending, error, file, filePath,
 *     thinking: string,        <- raw thinking text, persists after tool runs
 *     toolSteps: [{action, input, status}],  <- tool calls made during the turn
 *   }
 */
export function useChat(initialSessionId) {
  const [messages, setMessages] = useState([])
  const [sessionId, setSessionId] = useState(initialSessionId || newSessionId())
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState(null)
  const [file, setFile] = useState(null)
  const [observabilityData, setObservabilityData] = useState({})

  useEffect(() => {
    if (!initialSessionId) {
      setMessages([])
      return
    }
    let cancelled = false
    fetchHistory(initialSessionId)
      .then((history) => {
        if (cancelled) return
        setMessages(history.map((msg, index) => ({
          id: index,
          role: msg.role,
          content: msg.content,
          timestamp: msg.timestamp,
        })))
      })
      .catch((err) => {
        if (cancelled) return
        console.error('Failed to load history:', err)
        setMessages([])
      })
    return () => { cancelled = true }
  }, [initialSessionId])

  const abortRef = useRef(null)
  const streamingIdRef = useRef(null)

  const send = useCallback(
    (text) => {
      if (!text.trim() || isStreaming) return

      setError(null)
      setObservabilityData({})

      const userMsg = { id: Date.now(), role: 'user', content: text, file: file?.name, filePath: file?.webkitRelativePath || file?.name }
      const assistantId = Date.now() + 1
      const assistantMsg = {
        id: assistantId,
        role: 'assistant',
        content: '',
        pending: true,
        thinking: '',       // accumulates *...* reasoning text
        toolSteps: [],      // [{action, input, status: 'running'|'done'|'failed'}]
      }

      setMessages((prev) => [...prev, userMsg, assistantMsg])
      streamingIdRef.current = assistantId
      setIsStreaming(true)

      const currentFile = file
      setFile(null)

      // Track whether the current chunk is in a thinking/action section
      // so we can route it to the right field.
      let inThinking = false

      const abort = streamChat({
        message: text,
        sessionId,
        file: currentFile,
        onChunk: (chunk) => {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== streamingIdRef.current) return m

              // Detect ### Action: lines → start a new tool step
              if (chunk.startsWith('\n### Action:') || chunk.includes('### Action:')) {
                const actionName = chunk.replace(/.*### Action:\s*`?/, '').replace(/`?\n.*/, '').trim()
                return {
                  ...m,
                  toolSteps: [...m.toolSteps, { action: actionName, input: '', status: 'running' }],
                  thinking: m.thinking, // keep existing thinking
                }
              }

              // Detect ### Reasoning lines → treat as thinking
              if (chunk.startsWith('\n### Reasoning') || chunk.includes('### Reasoning')) {
                inThinking = true
                return m
              }

              // Italic thinking text (agent emits *...*)
              if (chunk.startsWith('*') || (m.thinking && !chunk.includes('###'))) {
                const cleaned = chunk.replace(/^\*|\*$/g, '')
                // If there are active tool steps, this is post-tool thinking
                if (m.toolSteps.length > 0 || chunk.startsWith('*')) {
                  return { ...m, thinking: (m.thinking || '') + cleaned }
                }
              }

              // Mark last tool step as done when we get content after an action
              const steps = m.toolSteps
              const updatedSteps = steps.map((s, i) =>
                i === steps.length - 1 && s.status === 'running'
                  ? { ...s, status: 'done' }
                  : s
              )

              return { ...m, content: m.content + chunk, pending: true, toolSteps: updatedSteps }
            })
          )
        },
        onIntent: (intentData) => {
          setObservabilityData(prev => ({
            ...prev,
            intent: intentData.intent,
            confidence: intentData.confidence
          }))
        },
        onObsEvent: (event) => {
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
