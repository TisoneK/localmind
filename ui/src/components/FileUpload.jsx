import { useRef } from 'react'
import { ACCEPTED_EXTENSIONS, formatBytes } from '../lib/utils'

const S = {
  wrap: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  btn: (hasFile) => ({
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '7px 10px',
    background: hasFile ? '#1e1e3a' : 'transparent',
    border: `1px solid ${hasFile ? '#3730a3' : '#2a2a3e'}`,
    borderRadius: '8px',
    color: hasFile ? '#a5b4fc' : '#6b6b7a',
    fontSize: '13px',
    cursor: 'pointer',
    flexShrink: 0,
    transition: 'all 0.15s',
    whiteSpace: 'nowrap',
    maxWidth: '200px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  }),
  clear: {
    background: 'none',
    border: 'none',
    color: '#6b6b7a',
    cursor: 'pointer',
    fontSize: '16px',
    lineHeight: 1,
    padding: '4px',
    borderRadius: '4px',
    flexShrink: 0,
  },
}

export function FileUpload({ file, onFile }) {
  const inputRef = useRef(null)

  const handleChange = (e) => {
    const f = e.target.files?.[0]
    if (f) onFile(f)
    e.target.value = '' // allow re-selecting same file
  }

  const label = file
    ? `${file.name} (${formatBytes(file.size)})`
    : 'Attach file'

  return (
    <div style={S.wrap}>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_EXTENSIONS}
        style={{ display: 'none' }}
        onChange={handleChange}
      />
      <button
        style={S.btn(!!file)}
        onClick={() => inputRef.current?.click()}
        title={file ? file.name : 'Attach a file (PDF, DOCX, TXT, CSV, code)'}
      >
        <span>📎</span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
      </button>
      {file && (
        <button style={S.clear} onClick={() => onFile(null)} title="Remove file">
          ✕
        </button>
      )}
    </div>
  )
}
