import { useEffect, useRef, useState } from 'react'
import { formatTime } from '../lib/utils'

const S = {
  list: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px 0',
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  empty: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#6b6b7a',
    gap: '12px',
  },
  emptyTitle: { fontSize: '20px', fontWeight: 600, color: '#9090a8' },
  emptyHint: { fontSize: '14px', color: '#55556a' },
  row: (role) => ({
    display: 'flex',
    justifyContent: role === 'user' ? 'flex-end' : 'flex-start',
    padding: '2px 16px',
  }),
  bubble: (role, error) => ({
    maxWidth: '72%',
    padding: '10px 14px',
    borderRadius: role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
    background: error ? '#2a1515' : role === 'user' ? '#3730a3' : '#1e1e2e',
    color: error ? '#f87171' : '#e2e2e8',
    fontSize: '14px',
    lineHeight: 1.65,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    border: error ? '1px solid #7f1d1d' : role === 'user' ? 'none' : '1px solid #2a2a3e',
  }),
  meta: {
    fontSize: '11px',
    color: '#55556a',
    marginTop: '4px',
    padding: '0 4px',
  },
  fileBadge: {
    display: 'inline-block',
    fontSize: '11px',
    color: '#a5b4fc',
    background: '#1e1e3a',
    border: '1px solid #3730a3',
    borderRadius: '6px',
    padding: '2px 8px',
    marginBottom: '6px',
  },
  cursor: {
    display: 'inline-block',
    width: '2px',
    height: '14px',
    background: '#6366f1',
    marginLeft: '2px',
    verticalAlign: 'middle',
    animation: 'blink 1s step-end infinite',
  },
  // Thinking block styles
  thinkingWrap: {
    maxWidth: '72%',
    marginBottom: '4px',
  },
  thinkingToggle: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '12px',
    color: '#6366f1',
    cursor: 'pointer',
    userSelect: 'none',
    padding: '4px 0',
    background: 'none',
    border: 'none',
    outline: 'none',
  },
  thinkingBody: {
    background: '#13131f',
    border: '1px solid #2a2a3e',
    borderRadius: '8px',
    padding: '8px 12px',
    fontSize: '12px',
    color: '#6b6b9a',
    fontStyle: 'italic',
    lineHeight: 1.6,
    marginTop: '4px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  // Tool steps
  toolStepsWrap: {
    maxWidth: '72%',
    marginBottom: '4px',
    display: 'flex',
    flexDirection: 'column',
    gap: '3px',
  },
  toolStep: (status) => ({
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    fontSize: '12px',
    color: status === 'running' ? '#a5b4fc' : status === 'failed' ? '#f87171' : '#6b9a6b',
    padding: '3px 8px',
    background: '#13131f',
    borderRadius: '6px',
    border: `1px solid ${status === 'running' ? '#3730a3' : status === 'failed' ? '#7f1d1d' : '#1a3a1a'}`,
  }),
  toolDot: (status) => ({
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    flexShrink: 0,
    background: status === 'running' ? '#6366f1' : status === 'failed' ? '#f87171' : '#4ade80',
    animation: status === 'running' ? 'pulse 1.2s ease-in-out infinite' : 'none',
  }),
}

function ThinkingBlock({ text, pending }) {
  const [open, setOpen] = useState(true)

  // Auto-collapse when streaming stops
  useEffect(() => {
    if (!pending && text) setOpen(false)
  }, [pending])

  if (!text) return null

  return (
    <div style={S.thinkingWrap}>
      <button style={S.thinkingToggle} onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: '10px' }}>{open ? '▾' : '▸'}</span>
        <span>{pending ? 'Thinking…' : 'Reasoning'}</span>
        {!open && <span style={{ color: '#44446a', fontStyle: 'italic', fontSize: '11px' }}>
          {text.slice(0, 60).trim()}{text.length > 60 ? '…' : ''}
        </span>}
      </button>
      {open && <div style={S.thinkingBody}>{text.trim()}</div>}
    </div>
  )
}

function ToolSteps({ steps }) {
  if (!steps || steps.length === 0) return null
  const icons = { running: '⟳', done: '✓', failed: '✗' }
  return (
    <div style={S.toolStepsWrap}>
      {steps.map((s, i) => (
        <div key={i} style={S.toolStep(s.status)}>
          <span style={S.toolDot(s.status)} />
          <span style={{ fontFamily: 'monospace' }}>{s.action}</span>
          {s.input && <span style={{ color: '#44446a' }}>— {s.input.slice(0, 60)}</span>}
          <span style={{ marginLeft: 'auto', opacity: 0.6 }}>{icons[s.status] || ''}</span>
        </div>
      ))}
    </div>
  )
}

export function MessageList({ messages }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div style={S.empty}>
        <div style={S.emptyTitle}>LocalMind</div>
        <div style={S.emptyHint}>Upload a file or ask anything. Runs fully local.</div>
      </div>
    )
  }

  return (
    <div style={S.list}>
      <style>{`
        @keyframes blink { 50% { opacity: 0 } }
        @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.3 } }
      `}</style>
      {messages.map((msg) => (
        <div key={msg.id} style={S.row(msg.role)}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
            {msg.file && (
              <span style={S.fileBadge}>{msg.file}</span>
            )}

            {/* Thinking block — assistant only, persists after turn */}
            {msg.role === 'assistant' && (msg.thinking || (msg.pending && !msg.content)) && (
              <ThinkingBlock text={msg.thinking} pending={msg.pending} />
            )}

            {/* Tool steps — shown while and after tool calls */}
            {msg.role === 'assistant' && msg.toolSteps?.length > 0 && (
              <ToolSteps steps={msg.toolSteps} />
            )}

            {/* Main response bubble — only show if there's content */}
            {(msg.content || msg.error || (msg.pending && !msg.thinking)) && (
              <div style={S.bubble(msg.role, msg.error)}>
                {msg.content || (msg.pending && !msg.thinking && !msg.toolSteps?.length ? '…' : '')}
                {msg.pending && !msg.error && <span style={S.cursor} />}
              </div>
            )}

            <div style={{ ...S.meta, textAlign: msg.role === 'user' ? 'right' : 'left' }}>
              {msg.role === 'assistant' ? 'LocalMind' : 'You'}
            </div>
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
