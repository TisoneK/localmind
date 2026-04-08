import { useState, useRef, useCallback } from 'react'
import { FileUpload } from './FileUpload'

const S = {
  outer: {
    borderTop: '1px solid #1e1e2e',
    background: '#0f0f12',
    padding: '12px 16px 16px',
  },
  row: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: '8px',
    background: '#1a1a26',
    border: '1px solid #2a2a3e',
    borderRadius: '12px',
    padding: '8px 8px 8px 12px',
  },
  textarea: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#e2e2e8',
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
    borderRadius: '8px',
    border: 'none',
    background: active ? '#22c55e' : '#2a2a3e',
    color: active ? '#fff' : '#55556a',
    cursor: active ? 'pointer' : 'default',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '16px',
    transition: 'background 0.15s',
  }),
  stopBtn: {
    flexShrink: 0,
    width: '34px',
    height: '34px',
    borderRadius: '8px',
    border: '1px solid #3730a3',
    background: '#1e1e3a',
    color: '#a5b4fc',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '12px',
  },
  toolbar: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    marginBottom: '8px',
  },
  hint: {
    fontSize: '11px',
    color: '#35354a',
    marginTop: '8px',
    textAlign: 'center',
  },
}

export function ChatInput({ onSend, isStreaming, onStop, file, onFile }) {
  const [text, setText] = useState('')
  const textareaRef = useRef(null)

  const handleKey = useCallback(
    (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        submit()
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text, isStreaming]
  )

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || isStreaming) return
    onSend(trimmed)
    setText('')
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleInput = (e) => {
    setText(e.target.value)
    // Auto-expand textarea
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }

  const canSend = text.trim().length > 0 && !isStreaming

  return (
    <div style={S.outer}>
      {file && (
        <div style={S.toolbar}>
          <FileUpload file={file} onFile={onFile} />
        </div>
      )}
      <div style={S.row}>
        {!file && <FileUpload file={file} onFile={onFile} />}
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
          <button style={S.stopBtn} onClick={onStop} title="Stop">
            ■
          </button>
        ) : (
          <button
            style={S.sendBtn(canSend)}
            onClick={submit}
            disabled={!canSend}
            title="Send (Enter)"
          >
            ↑
          </button>
        )}
      </div>
      <div style={S.hint}>Enter to send · Shift+Enter for new line</div>
    </div>
  )
}
