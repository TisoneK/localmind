import { useEffect, useRef, useState, useCallback } from 'react'
import { formatTime } from '../lib/utils'

/* ── Tokens ──────────────────────────────────────────────────────────────── */
const C = {
  bg:          '#0c0c10',
  surface:     '#13131a',
  surfaceUp:   '#1a1a24',
  border:      '#232333',
  borderFaint: '#1a1a28',
  accent:      '#7c6af7',
  accentDim:   '#3d3680',
  accentGlow:  'rgba(124,106,247,0.12)',
  green:       '#3ecf8e',
  greenDim:    '#1a3d2e',
  amber:       '#f59e0b',
  amberDim:    '#2d1f00',
  red:         '#f87171',
  redDim:      '#2a1010',
  textPrimary: '#e8e8f0',
  textMuted:   '#6868a0',
  textFaint:   '#3a3a5a',
  codeFont:    '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
}

/* ── Styles ──────────────────────────────────────────────────────────────── */
const S = {
  list: {
    flex: 1,
    overflowY: 'auto',
    padding: '24px 0 8px',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    scrollbarWidth: 'thin',
    scrollbarColor: `${C.border} transparent`,
  },
  empty: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '16px',
    padding: '40px',
  },
  emptyGlyph: {
    width: '48px',
    height: '48px',
    borderRadius: '14px',
    background: `linear-gradient(135deg, ${C.accentDim}, ${C.surface})`,
    border: `1px solid ${C.border}`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '22px',
  },
  emptyTitle: {
    fontSize: '17px',
    fontWeight: 600,
    color: C.textMuted,
    letterSpacing: '-0.01em',
  },
  emptyHint: {
    fontSize: '13px',
    color: C.textFaint,
    textAlign: 'center',
    maxWidth: '260px',
    lineHeight: 1.6,
  },
  row: (role) => ({
    display: 'flex',
    justifyContent: role === 'user' ? 'flex-end' : 'flex-start',
    padding: '3px 20px',
    alignItems: 'flex-start',
    gap: '10px',
  }),
  // User bubble
  userBubble: {
    maxWidth: '70%',
    padding: '10px 14px',
    borderRadius: '16px 16px 4px 16px',
    background: `linear-gradient(135deg, #4a3fc5, #3730a3)`,
    color: '#f0f0ff',
    fontSize: '14px',
    lineHeight: 1.65,
    wordBreak: 'break-word',
    boxShadow: '0 2px 8px rgba(55,48,163,0.3)',
  },
  // Assistant message container
  assistantWrap: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    maxWidth: '75%',
    minWidth: '100px',
  },
  // Main response bubble
  responseBubble: (error) => ({
    padding: '12px 15px',
    borderRadius: '4px 16px 16px 16px',
    background: error ? C.redDim : C.surface,
    color: error ? C.red : C.textPrimary,
    fontSize: '14px',
    lineHeight: 1.7,
    wordBreak: 'break-word',
    border: `1px solid ${error ? '#5a1515' : C.border}`,
    whiteSpace: 'pre-wrap',
  }),
  // Avatar dot
  avatar: (role) => ({
    width: '26px',
    height: '26px',
    borderRadius: '8px',
    flexShrink: 0,
    marginTop: '2px',
    background: role === 'user'
      ? 'linear-gradient(135deg, #4a3fc5, #3730a3)'
      : `linear-gradient(135deg, ${C.accentDim}, #1a1a30)`,
    border: `1px solid ${role === 'user' ? '#3730a3' : C.border}`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '11px',
    color: role === 'user' ? '#c4c0ff' : C.textMuted,
    fontWeight: 700,
    letterSpacing: '-0.02em',
  }),
  // Thinking toggle button
  thinkToggle: (open) => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '11.5px',
    color: open ? C.accent : C.textMuted,
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    padding: '3px 0',
    fontFamily: 'inherit',
    transition: 'color 0.15s',
  }),
  thinkArrow: (open) => ({
    fontSize: '9px',
    transform: open ? 'rotate(90deg)' : 'rotate(0deg)',
    transition: 'transform 0.2s',
    display: 'inline-block',
  }),
  thinkDuration: {
    color: C.textFaint,
    fontStyle: 'italic',
  },
  // Thinking body
  thinkBody: {
    background: C.bg,
    border: `1px solid ${C.borderFaint}`,
    borderLeft: `2px solid ${C.accentDim}`,
    borderRadius: '0 6px 6px 0',
    padding: '8px 12px',
    fontSize: '12px',
    color: C.textMuted,
    fontStyle: 'italic',
    lineHeight: 1.65,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    marginTop: '2px',
    maxHeight: '280px',
    overflowY: 'auto',
    scrollbarWidth: 'thin',
  },
  // Tool step chips
  toolChip: (status) => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '11px',
    color: status === 'failed' ? C.red : status === 'running' ? C.accent : C.green,
    background: status === 'failed' ? C.redDim : status === 'running' ? C.accentGlow : C.greenDim,
    border: `1px solid ${status === 'failed' ? '#5a1515' : status === 'running' ? C.accentDim : '#1d4a35'}`,
    borderRadius: '20px',
    padding: '3px 9px 3px 7px',
    fontFamily: C.codeFont,
    width: 'fit-content',
  }),
  toolDot: (status) => ({
    width: '5px',
    height: '5px',
    borderRadius: '50%',
    flexShrink: 0,
    background: status === 'failed' ? C.red : status === 'running' ? C.accent : C.green,
  }),
  // File badge
  fileBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '5px',
    fontSize: '11px',
    color: '#9090d0',
    background: '#16162a',
    border: `1px solid ${C.accentDim}`,
    borderRadius: '6px',
    padding: '3px 8px',
    marginBottom: '4px',
    alignSelf: 'flex-end',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  // Copy button
  copyBtn: (copied) => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '10.5px',
    color: copied ? C.green : C.textFaint,
    background: 'none',
    border: `1px solid ${copied ? '#1d4a35' : C.borderFaint}`,
    borderRadius: '5px',
    padding: '2px 7px',
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
    flexShrink: 0,
  }),
  // Code block
  codeBlock: {
    background: '#0a0a0f',
    border: `1px solid ${C.border}`,
    borderRadius: '8px',
    overflow: 'hidden',
    margin: '6px 0',
    fontSize: '12.5px',
    fontFamily: C.codeFont,
  },
  codeHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 12px',
    background: C.surface,
    borderBottom: `1px solid ${C.border}`,
  },
  codeLang: {
    fontSize: '10px',
    color: C.textFaint,
    fontFamily: C.codeFont,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
  },
  codePre: {
    margin: 0,
    padding: '12px',
    overflowX: 'auto',
    color: '#c8c8f8',
    lineHeight: 1.6,
    fontSize: '12.5px',
    fontFamily: C.codeFont,
    whiteSpace: 'pre',
  },
  // Streaming cursor
  cursor: {
    display: 'inline-block',
    width: '2px',
    height: '13px',
    background: C.accent,
    marginLeft: '2px',
    verticalAlign: 'middle',
    borderRadius: '1px',
  },
  // Meta line
  meta: (align) => ({
    fontSize: '10.5px',
    color: C.textFaint,
    padding: '0 2px',
    textAlign: align,
  }),
  // Message actions bar
  actionsBar: {
    display: 'flex',
    gap: '4px',
    opacity: 0,
    transition: 'opacity 0.15s',
  },
}

/* ── Copy hook ───────────────────────────────────────────────────────────── */
function useCopy(text) {
  const [copied, setCopied] = useState(false)
  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [text])
  return [copied, copy]
}

/* ── Code block renderer ─────────────────────────────────────────────────── */
function CodeBlock({ lang, code }) {
  const [copied, copy] = useCopy(code)
  return (
    <div style={S.codeBlock}>
      <div style={S.codeHeader}>
        <span style={S.codeLang}>{lang || 'code'}</span>
        <button style={S.copyBtn(copied)} onClick={copy}>
          {copied ? '✓ copied' : '⧉ copy'}
        </button>
      </div>
      <pre style={S.codePre}>{code}</pre>
    </div>
  )
}

/* ── Inline markdown renderer ────────────────────────────────────────────── */
// Minimal: handles fenced code blocks, bold, inline code. Keeps it dependency-free.
/**
 * renderContent — full markdown renderer (no external deps).
 *
 * Handles (in processing order):
 *   ``` fenced code blocks  → <CodeBlock>
 *   ## / ### / #### headers → <h2/h3/h4>
 *   - / * / + bullet lists  → <ul><li>
 *   blank lines             → paragraph breaks
 *   **bold** *italic* `code`→ inline spans
 */
function renderContent(text) {
  if (!text) return null

  // ── 1. Extract fenced code blocks first (protect from line processing) ──
  const CODE_FENCE = /```(\w*)\n?([\s\S]*?)```/g
  const segments = []   // {type: 'text'|'code', content, lang?}
  let last = 0, m
  while ((m = CODE_FENCE.exec(text)) !== null) {
    if (m.index > last) segments.push({ type: 'text', content: text.slice(last, m.index) })
    segments.push({ type: 'code', lang: m[1], content: m[2].trimEnd() })
    last = m.index + m[0].length
  }
  if (last < text.length) segments.push({ type: 'text', content: text.slice(last) })

  // ── 2. Render each segment ───────────────────────────────────────────────
  const out = []
  let key = 0

  for (const seg of segments) {
    if (seg.type === 'code') {
      out.push(<CodeBlock key={key++} lang={seg.lang} code={seg.content} />)
      continue
    }

    // Text segment: split into lines and parse markdown structure
    const lines = seg.content.split('\n')
    let i = 0
    let listItems = []

    const flushList = () => {
      if (listItems.length === 0) return
      out.push(
        <ul key={key++} style={{ margin: '6px 0 6px 18px', padding: 0, listStyle: 'disc' }}>
          {listItems.map((li, j) => (
            <li key={j} style={{ marginBottom: '3px', lineHeight: 1.65 }}>
              <InlineSpans text={li} />
            </li>
          ))}
        </ul>
      )
      listItems = []
    }

    while (i < lines.length) {
      const line = lines[i]
      const trimmed = line.trim()

      // Blank line → flush list, paragraph gap
      if (trimmed === '') {
        flushList()
        out.push(<div key={key++} style={{ height: '8px' }} />)
        i++
        continue
      }

      // ATX headers: ## / ### / ####
      const hMatch = trimmed.match(/^(#{1,4})\s+(.+)$/)
      if (hMatch) {
        flushList()
        const level = hMatch[1].length
        const sizes = { 1: '17px', 2: '15px', 3: '14px', 4: '13px' }
        const weights = { 1: 700, 2: 650, 3: 600, 4: 600 }
        out.push(
          <div key={key++} style={{
            fontSize: sizes[level] || '14px',
            fontWeight: weights[level] || 600,
            color: C.textPrimary,
            margin: '10px 0 4px',
            letterSpacing: '-0.01em',
          }}>
            <InlineSpans text={hMatch[2]} />
          </div>
        )
        i++
        continue
      }

      // Bullet list items: -, *, + at line start
      const liMatch = trimmed.match(/^[-*+]\s+(.+)$/)
      if (liMatch) {
        listItems.push(liMatch[1])
        i++
        continue
      }

      // Numbered list: 1. 2. etc
      const numMatch = trimmed.match(/^\d+\.\s+(.+)$/)
      if (numMatch) {
        flushList()
        // Collect consecutive numbered items
        const numItems = []
        while (i < lines.length) {
          const nl = lines[i].trim()
          const nm = nl.match(/^\d+\.\s+(.+)$/)
          if (!nm) break
          numItems.push(nm[1])
          i++
        }
        out.push(
          <ol key={key++} style={{ margin: '6px 0 6px 20px', padding: 0 }}>
            {numItems.map((ni, j) => (
              <li key={j} style={{ marginBottom: '3px', lineHeight: 1.65 }}>
                <InlineSpans text={ni} />
              </li>
            ))}
          </ol>
        )
        continue
      }

      // Horizontal rule
      if (/^[-*_]{3,}$/.test(trimmed)) {
        flushList()
        out.push(<hr key={key++} style={{ border: 'none', borderTop: `1px solid ${C.border}`, margin: '8px 0' }} />)
        i++
        continue
      }

      // Regular paragraph line — accumulate until blank or structure
      flushList()
      const paraLines = []
      while (i < lines.length) {
        const pl = lines[i].trim()
        if (pl === '') break
        if (/^#{1,4}\s/.test(pl)) break
        if (/^[-*+]\s/.test(pl)) break
        if (/^\d+\.\s/.test(pl)) break
        if (/^[-*_]{3,}$/.test(pl)) break
        paraLines.push(lines[i])
        i++
      }
      if (paraLines.length > 0) {
        out.push(
          <p key={key++} style={{ margin: '3px 0', lineHeight: 1.7 }}>
            <InlineSpans text={paraLines.join('\n')} />
          </p>
        )
      }
    }

    flushList()
  }

  return out.length === 1 ? out[0] : <>{out}</>
}

/* ── Inline markdown spans ───────────────────────────────────────────────── */
function InlineSpans({ text }) {
  // Handles: **bold**, *italic*, `code`, \n as <br>
  const tokens = []
  const re = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g
  let last = 0, m, i = 0

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      const before = text.slice(last, m.index)
      tokens.push(...renderLineBreaks(before, i))
      i += 10
    }
    const raw = m[0]
    if (raw.startsWith('**')) {
      tokens.push(<strong key={i++} style={{ color: C.textPrimary, fontWeight: 650 }}>{raw.slice(2, -2)}</strong>)
    } else if (raw.startsWith('*')) {
      tokens.push(<em key={i++} style={{ color: C.textMuted, fontStyle: 'italic' }}>{raw.slice(1, -1)}</em>)
    } else {
      tokens.push(
        <code key={i++} style={{
          fontFamily: C.codeFont, fontSize: '12px',
          background: '#0d0d1a', padding: '1px 6px',
          borderRadius: '4px', color: '#9d8fff',
          border: `1px solid ${C.borderFaint}`,
        }}>{raw.slice(1, -1)}</code>
      )
    }
    last = m.index + raw.length
    i++
  }
  if (last < text.length) {
    tokens.push(...renderLineBreaks(text.slice(last), i))
  }
  return <>{tokens}</>
}

function renderLineBreaks(text, baseKey) {
  const parts = text.split('\n')
  return parts.flatMap((p, j) =>
    j < parts.length - 1
      ? [<span key={baseKey + j}>{p}</span>, <br key={baseKey + j + 1000} />]
      : [<span key={baseKey + j}>{p}</span>]
  )
}

// Keep InlineText as alias for backwards compat (ThinkingBlock uses it indirectly)
function InlineText({ text }) { return <InlineSpans text={text} /> }

/* ── ThinkingBlock ───────────────────────────────────────────────────────── */
function ThinkingBlock({ text, pending, durationMs }) {
  const [open, setOpen] = useState(true)
  const [elapsed, setElapsed] = useState(0)
  const timerRef = useRef(null)

  // Live elapsed timer while pending
  useEffect(() => {
    if (pending) {
      const start = Date.now()
      timerRef.current = setInterval(() => setElapsed(Date.now() - start), 250)
    } else {
      clearInterval(timerRef.current)
    }
    return () => clearInterval(timerRef.current)
  }, [pending])

  // Auto-collapse when done
  useEffect(() => {
    if (!pending && (text || durationMs > 0)) {
      const t = setTimeout(() => setOpen(false), 800)
      return () => clearTimeout(t)
    }
  }, [pending, text, durationMs])

  // Always render during pending; hide only if done with no text
  if (!pending && !text) return null

  const msToDisplay = pending ? elapsed : durationMs
  const timeStr = msToDisplay >= 60000
    ? `${Math.round(msToDisplay / 60000)}m`
    : msToDisplay >= 1000
      ? `${(msToDisplay / 1000).toFixed(1)}s`
      : `${Math.round(msToDisplay / 100) * 100}ms`

  const label = pending ? `Thinking… ${timeStr}` : `Thought for ${timeStr}`

  return (
    <div>
      <button style={S.thinkToggle(open)} onClick={() => setOpen(o => !o)}>
        {pending
          ? <span style={{ fontSize: '10px', animation: 'lm-pulse 1.2s ease-in-out infinite', display: 'inline-block' }}>◉</span>
          : <span style={S.thinkArrow(open)}>▶</span>
        }
        <span>{label}</span>
        {!open && !pending && text && (
          <span style={S.thinkDuration}>
            — {text.trim().slice(0, 55)}{text.trim().length > 55 ? '…' : ''}
          </span>
        )}
      </button>
      {open && (
        <div style={S.thinkBody}>
          {text
            ? text.trim()
            : <span style={{ color: C.textFaint, fontStyle: 'italic' }}>Waiting for response…</span>
          }
        </div>
      )}
    </div>
  )
}

/* ── ToolSteps ───────────────────────────────────────────────────────────── */
function ToolSteps({ steps }) {
  if (!steps?.length) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      {steps.map((s, i) => (
        <div key={i} style={S.toolChip(s.status)}>
          <span style={S.toolDot(s.status)} />
          <span>{s.action}</span>
          {s.input && <span style={{ color: C.textFaint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '180px' }}>· {s.input}</span>}
        </div>
      ))}
    </div>
  )
}

/* ── MessageRow ──────────────────────────────────────────────────────────── */
function MessageRow({ msg }) {
  const [hovered, setHovered] = useState(false)
  const [copied, copy] = useCopy(msg.content || '')
  const isUser = msg.role === 'user'

  return (
    <div
      style={S.row(msg.role)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Avatar — assistant left side */}
      {!isUser && (
        <div style={S.avatar('assistant')}>LM</div>
      )}

      {/* Content column */}
      {isUser ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px', maxWidth: '72%' }}>
          {msg.file && (
            <span style={S.fileBadge}>📎 {msg.file}</span>
          )}
          <div style={S.userBubble}>{msg.content}</div>
          <div style={S.meta('right')}>You</div>
        </div>
      ) : (
        <div style={S.assistantWrap}>
          {/* Thinking block — only assistant */}
          {msg.isAgent && (msg.thinking || (msg.pending && !msg.content)) && (
            <ThinkingBlock
              text={msg.thinking}
              pending={msg.pending}
              durationMs={msg.thinkMs || 0}
            />
          )}

          {/* Tool steps */}
          <ToolSteps steps={msg.toolSteps} />

          {/* Response bubble */}
          {(msg.content || msg.error) && (
            <div style={S.responseBubble(msg.error)}>
              {msg.content
                ? renderContent(msg.content)
                : (msg.pending ? '…' : '')}
              {msg.pending && !msg.error && <span style={S.cursor} />}
            </div>
          )}

          {/* Actions bar */}
          {!msg.pending && msg.content && (
            <div style={{ ...S.actionsBar, opacity: hovered ? 1 : 0 }}>
              <button style={S.copyBtn(copied)} onClick={copy}>
                {copied ? '✓ copied' : '⧉ copy'}
              </button>
            </div>
          )}

          <div style={S.meta('left')}>LocalMind</div>
        </div>
      )}

      {/* Avatar — user right side */}
      {isUser && (
        <div style={S.avatar('user')}>U</div>
      )}
    </div>
  )
}

/* ── MessageList ─────────────────────────────────────────────────────────── */
export function MessageList({ messages }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div style={S.empty}>
        <style>{`
          @keyframes lm-cursor { 50% { opacity: 0 } }
          @keyframes lm-pulse  { 0%,100% { opacity:1 } 50% { opacity:0.3 } }
        `}</style>
        <div style={S.emptyGlyph}>⚡</div>
        <div style={S.emptyTitle}>LocalMind</div>
        <div style={S.emptyHint}>Ask anything. Upload a file. Runs fully local — no cloud, no leaks.</div>
      </div>
    )
  }

  return (
    <div style={S.list}>
      <style>{`
        @keyframes lm-cursor { 50% { opacity: 0 } }
        @keyframes lm-pulse  { 0%,100% { opacity:1 } 50% { opacity:0.3 } }
        [data-lm-cursor] { animation: lm-cursor 1s step-end infinite; }
        [data-lm-pulse]  { animation: lm-pulse 1.2s ease-in-out infinite; }
      `}</style>
      {messages.map((msg) => (
        <MessageRow key={msg.id} msg={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
