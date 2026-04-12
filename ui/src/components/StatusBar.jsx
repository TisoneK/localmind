const C = {
  bg:       '#08080d',
  border:   '#1a1a28',
  accent:   '#7c6af7',
  green:    '#3ecf8e',
  red:      '#f87171',
  muted:    '#55558a',
  faint:    '#35354a',
  codeFont: '"JetBrains Mono", "Fira Code", monospace',
}

const S = {
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '0 16px',
    height: '38px',
    background: C.bg,
    borderBottom: `1px solid ${C.border}`,
    fontSize: '12px',
    color: C.muted,
    flexShrink: 0,
    userSelect: 'none',
  },
  statusDot: (ok) => ({
    width: '7px',
    height: '7px',
    borderRadius: '50%',
    background: ok ? C.green : C.red,
    flexShrink: 0,
    boxShadow: ok ? `0 0 6px ${C.green}60` : `0 0 6px ${C.red}60`,
  }),
  model: {
    color: C.accent,
    fontFamily: C.codeFont,
    fontSize: '10.5px',
    background: '#12122a',
    padding: '2px 7px',
    borderRadius: '5px',
    border: `1px solid #2a2a50`,
  },
  sep: {
    width: '1px',
    height: '14px',
    background: C.border,
    flexShrink: 0,
  },
  session: {
    fontSize: '10.5px',
    color: C.faint,
    fontFamily: C.codeFont,
    maxWidth: '120px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  spacer: { flex: 1 },
  newBtn: {
    background: 'transparent',
    border: `1px solid #2a2a50`,
    borderRadius: '7px',
    color: C.accent,
    fontSize: '11px',
    padding: '4px 10px',
    cursor: 'pointer',
    fontFamily: 'inherit',
    letterSpacing: '0.01em',
    transition: 'all 0.15s',
  },
}

export function StatusBar({ onNewChat, sessionId, health, sessionTitle }) {
  const ok      = health?.ollama_reachable === true
  const model   = health?.model || '---'
  const status  = health == null ? 'connecting' : ok ? 'connected' : 'offline'
  const display = sessionTitle || (sessionId ? sessionId.slice(0, 8) : null)

  return (
    <div style={S.bar}>
      <div style={S.statusDot(ok)} />
      <span>{status}</span>
      {ok && <span style={S.model}>{model}</span>}
      <div style={S.spacer} />
      {display && (
        <>
          <div style={S.sep} />
          <span style={S.session} title={`Session: ${sessionId}`}>{display}</span>
        </>
      )}
      <button style={S.newBtn} onClick={onNewChat}>+ new chat</button>
    </div>
  )
}
