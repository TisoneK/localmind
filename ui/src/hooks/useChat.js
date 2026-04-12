import { useState, useCallback, useRef, useEffect } from 'react'
import { streamChat, newSessionId, fetchHistory } from '../lib/api'

/**
 * useChat — manages all chat state and streaming.
 *
 * Message shape:
 *   { id, role, content, pending, error, file, filePath,
 *     thinking: string,        <- raw thinking text
 *     thinkMs:  number,        <- thinking duration in ms
 *     isAgent:  boolean,       <- true only for agent-loop intents
 *     toolSteps: [{action, input, status}],
 *   }
 *
 * Thinking chunk detection:
 *   The agent loop emits thinking as a single chunk: "*{text}*\n\n"
 *   We detect it by: chunk starts with "*" AND ends with "*\n" or "*\n\n"
 *   This is deliberately strict to avoid routing real markdown content
 *   (bold text, bullets, search result previews) into the thinking box.
 *
 * isAgent is set to true when the SSE intent event carries an agent intent.
 * ThinkingBlock only renders for agent turns, so chat turns never show a spinner.
 */

// Intents that run through the agent loop and emit thinking chunks
const AGENT_INTENTS = new Set(['chat', 'memory_op', 'code_exec', 'file_write', 'web_search'])

/** True only for agent thinking chunks emitted by loop.py as *text*\n\n */
function isThinkingChunk(chunk) {
  return (
    chunk.startsWith('*') &&
    /\*\n*$/.test(chunk.trimEnd()) &&
    !chunk.startsWith('**')   // exclude bold **text**
  )
}

/** Strip the wrapping * markers and trailing whitespace */
function extractThinking(chunk) {
  return chunk
    .replace(/^\*/, '')       // leading *
    .replace(/\*\n*$/, '')    // trailing *\n or *\n\n
    .trim()
}

export function useChat(initialSessionId) {
  const [messages, setMessages]           = useState([])
  const [sessionId, setSessionId]         = useState(initialSessionId || newSessionId())
  const [isStreaming, setIsStreaming]      = useState(false)
  const [error, setError]                 = useState(null)
  const [file, setFile]                   = useState(null)
  const [observabilityData, setObservabilityData] = useState({})

  useEffect(() => {
    if (!initialSessionId) { setMessages([]); return }
    let cancelled = false
    fetchHistory(initialSessionId)
      .then((history) => {
        if (cancelled) return
        setMessages(history.map((msg, index) => ({
          id: index, role: msg.role, content: msg.content, timestamp: msg.timestamp,
        })))
      })
      .catch((err) => {
        if (!cancelled) { console.error('Failed to load history:', err); setMessages([]) }
      })
    return () => { cancelled = true }
  }, [initialSessionId])

  const abortRef       = useRef(null)
  const streamingIdRef = useRef(null)
  const thinkStartRef  = useRef(null)

  const send = useCallback((text) => {
    if (!text.trim() || isStreaming) return

    setError(null)
    setObservabilityData({})

    const userMsg = {
      id: Date.now(), role: 'user', content: text,
      file: file?.name, filePath: file?.webkitRelativePath || file?.name,
    }
    const assistantId = Date.now() + 1
    const assistantMsg = {
      id: assistantId, role: 'assistant', content: '',
      pending: true, thinking: '', thinkMs: 0, isAgent: false, toolSteps: [],
    }

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    streamingIdRef.current = assistantId
    thinkStartRef.current  = Date.now()
    setIsStreaming(true)

    const currentFile = file
    setFile(null)

    const abort = streamChat({
      message: text,
      sessionId,
      file: currentFile,

      onToolStatus: ({ tool, label, status }) => {
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== streamingIdRef.current) return m
            const idx = m.toolSteps.findIndex(s => s.action === tool)
            if (idx >= 0) {
              const updated = m.toolSteps.map((s, i) => i === idx ? { ...s, status } : s)
              return { ...m, toolSteps: updated }
            }
            return { ...m, toolSteps: [...m.toolSteps, { action: tool, input: label, status }] }
          })
        )
      },

      onChunk: (chunk) => {
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== streamingIdRef.current) return m

            // ### Action: header → new tool step (agent loop marker)
            if (chunk.includes('### Action:')) {
              const actionName = chunk
                .replace(/[\s\S]*### Action:\s*`?/, '')
                .replace(/`?\n[\s\S]*/s, '')
                .trim()
              return {
                ...m,
                toolSteps: [...m.toolSteps, { action: actionName, input: '', status: 'running' }],
              }
            }

            // ### Reasoning header — skip, content follows in next chunks
            if (chunk.startsWith('\n### Reasoning') || chunk === '\n### Reasoning\n') {
              return m
            }

            // Thinking chunk: *{text}*\n\n — strict detection, not loose *-contains
            if (isThinkingChunk(chunk)) {
              const text = extractThinking(chunk)
              const nowMs = Date.now() - (thinkStartRef.current || Date.now())
              return { ...m, thinking: (m.thinking || '') + text, thinkMs: nowMs }
            }

            // Mark last running tool step as done when real content arrives
            const updatedSteps = m.toolSteps.map((s, i) =>
              i === m.toolSteps.length - 1 && s.status === 'running'
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
          confidence: intentData.confidence,
        }))
        // Mark the current message as agent/non-agent so ThinkingBlock
        // only renders for turns that actually emit thinking chunks.
        const isAgent = AGENT_INTENTS.has(intentData.intent)
        setMessages((prev) =>
          prev.map((m) =>
            m.id === streamingIdRef.current ? { ...m, isAgent } : m
          )
        )
      },

      onObsEvent: (event) => {
        setObservabilityData(prev => {
          const updated = { ...prev }
          switch (event.type) {
            case 'tool_dispatched':
              updated.toolCalls = [...(prev.toolCalls || []), {
                name: event.data.tool,
                success: event.data.success,
                latency: event.data.latency_ms,
              }]
              break
            case 'memory_retrieved':
              updated.memoryHits = event.data.facts || []
              break
            case 'turn_complete':
              updated.latency = event.data.total_latency_ms
              updated.tokens  = event.data.tokens_approx
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
        const elapsed = Date.now() - (thinkStartRef.current || Date.now())
        setMessages((prev) =>
          prev.map((m) =>
            m.id === streamingIdRef.current
              ? { ...m, pending: false, thinkMs: m.thinkMs || elapsed }
              : m
          )
        )
        setIsStreaming(false)
        streamingIdRef.current = null
        abortRef.current = null
      },
    })

    abortRef.current = abort
  }, [isStreaming, sessionId, file])

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

  return {
    messages, sessionId, isStreaming, error, file, setFile,
    send, reset, cancelStream, observabilityData,
  }
}
