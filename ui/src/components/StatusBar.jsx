import { useHealth } from '../hooks/useHealth'

const S = {
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '0 16px',
    height: '36px',
    background: '#0a0a10',
    borderBottom: '1px solid #1a1a26',
    fontSize: '12px',
    color: '#55556a',
    flexShrink: 0,
  },
  dot: (ok) => ({
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    background: ok ? '#22c55e' : '#ef4444',
    flexShrink: 0,
  }),
  label: { color: '#9090a8' },
  model: {
    color: '#6366f1',
    fontFamily: 'monospace',
    fontSize: '11px',
  },
  spacer: { flex: 1 },
  newBtn: {
    background: '#22c55e',
    border: '1px solid #22c55e',
    borderRadius: '6px',
    color: '#ffffff',
    fontSize: '11px',
    padding: '3px 8px',
    cursor: 'pointer',
    fontWeight: '500',
  },
}

export function StatusBar({ onNewChat, sessionId }) {
  const { health, loading } = useHealth()

  const ok = health?.ollama_reachable === true
  const model = health?.model || '—'
  const status = loading ? 'connecting…' : ok ? 'connected' : 'Ollama offline'

  return (
    <div style={S.bar}>
      <div style={S.dot(ok && !loading)} />
      <span style={S.label}>{status}</span>
      {ok && <span style={S.model}>{model}</span>}
      <div style={S.spacer} />
      {sessionId && (
        <span title={`Session: ${sessionId}`}>
          #{sessionId.slice(0, 8)}
        </span>
      )}
      <button style={S.newBtn} onClick={onNewChat} title="Start a new conversation">
        + New chat
      </button>
    </div>
  )
}
