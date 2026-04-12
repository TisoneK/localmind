import { useState, useRef, useCallback } from 'react'
import { FileUpload } from './FileUpload'

const C = {
  bg:       '#0c0c10',
  surface:  '#13131a',
  border:   '#232333',
  accent:   '#7c6af7',
  green:    '#3ecf8e',
  muted:    '#55558a',
  faint:    '#35354a',
  text:     '#e8e8f0',
}

const S = {
  outer: {
    borderTop: `1px solid ${C.border}`,
    background: C.bg,
    padding: '10px 16px 14px',
    flexShrink: 0,
  },
  fileBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '5px',
    fontSize: '11px',
    color: '#9090d0',
    background: '#16162a',
    border: '1px solid #2a2a50',
    borderRadius: '6px',
    padding: '3px 8px',
    marginBottom: '8px',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  inputRow: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: '8px',
    background: C.surface,
    border: `1px solid ${C.border}`,
    borderRadius: '12px',
    padding: '8px 8px 8px 12px',
    transition: 'border-color 0.15s',
  },
  textarea: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: C.text,
    fontSize: '14px',
    lineHeight: 1.6,
    resize: 'none',
    minHeight: '24px',
    maxHeight: '160px',
    overflowY: 'auto',
    fontFamily: 'inherit',
    padding: 0,
  },
  sendBtn: (active) => ({
    flexShrink: 0,
    width: '34px',
    height: '34px',
    borderRadius: '9px',
    border: 'none',
    background: active ? C.green : '#1a1a28',
    color: active ? '#0a1a12' : C.faint,
    cursor: active ? 'pointer' : 'default',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '15px',
    fontWeight: 700,
    transition: 'all 0.15s',
    boxShadow: active ? `0 0 10px ${C.green}40` : 'none',
  }),
  stopBtn: {
    flexShrink: 0,
    width: '34px',
    height: '34px',
    borderRadius: '9px',
    border: `1px solid ${C.border}`,
    background: '#16161f',
    color: C.muted,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '11px',
    fontWeight: 700,
    letterSpacing: '0.02em',
  },
  hint: {
    fontSize: '10.5px',
    color: C.faint,
    marginTop: '7px',
    textAlign: 'center',
    letterSpacing: '0.01em',
  },
}

export function ChatInput({ onSend, isStreaming, onStop, file, onFile }) {
  const [text, setText] = useState('')
  const textareaRef = useRef(null)

  const submit = useCallback(() => {
    const trimmed = text.trim()
    if (!trimmed || isStreaming) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }, [text, isStreaming, onSend])

  const handleKey = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }, [submit])

  const handleInput = (e) => {
    setText(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }

  const canSend = text.trim().length > 0 && !isStreaming

  return (
    <div style={S.outer}>
      {file && (
        <div style={S.fileBadge}>📎 {file.name}</div>
      )}
      <div style={S.inputRow}>
        <FileUpload file={file} onFile={onFile} />
        <textarea
          ref={textareaRef}
          style={S.textarea}
          value={text}
          onChange={handleInput}
          onKeyDown={handleKey}
          placeholder={isStreaming ? 'Responding…' : 'Ask anything, or attach a file…'}
          disabled={isStreaming}
          rows={1}
          autoFocus
        />
        {isStreaming ? (
          <button style={S.stopBtn} onClick={onStop} title="Stop generation">■</button>
        ) : (
          <button style={S.sendBtn(canSend)} onClick={submit} disabled={!canSend} title="Send (Enter)">
            ↑
          </button>
        )}
      </div>
      <div style={S.hint}>Enter to send · Shift+Enter for new line</div>
    </div>
  )
}
