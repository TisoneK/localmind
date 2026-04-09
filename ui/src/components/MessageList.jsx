import { useEffect, useRef } from 'react'
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
    background: error
      ? '#2a1515'
      : role === 'user'
      ? '#3730a3'
      : '#1e1e2e',
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
      <style>{`@keyframes blink { 50% { opacity: 0 } }`}</style>
      {messages.map((msg) => (
        <div key={msg.id} style={S.row(msg.role)}>
          <div>
            {msg.file && (
              <div style={{ textAlign: msg.role === 'user' ? 'right' : 'left' }}>
                <span style={S.fileBadge}>{msg.file} - {msg.filePath}</span>
              </div>
            )}
            <div style={S.bubble(msg.role, msg.error)}>
              {msg.content}
              {msg.pending && !msg.error && <span style={S.cursor} />}
            </div>
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
