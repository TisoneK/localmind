const S = {
  banner: {
    margin: '8px 16px',
    padding: '10px 14px',
    background: '#2a1515',
    border: '1px solid #7f1d1d',
    borderRadius: '8px',
    color: '#f87171',
    fontSize: '13px',
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  dismiss: {
    marginLeft: 'auto',
    background: 'none',
    border: 'none',
    color: '#f87171',
    cursor: 'pointer',
    fontSize: '16px',
    lineHeight: 1,
    padding: '2px 4px',
  },
}

export function ErrorBanner({ error, onDismiss }) {
  if (!error) return null
  return (
    <div style={S.banner}>
      <span>⚠</span>
      <span>{error}</span>
      <button style={S.dismiss} onClick={onDismiss}>✕</button>
    </div>
  )
}
