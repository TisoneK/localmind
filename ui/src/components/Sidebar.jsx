/**
 * Sidebar — v0.5
 *
 * Sessions list is now passed in as props (fetched by App via useSession).
 * Sidebar no longer owns a fetch — it's a pure display + interaction component.
 *
 * Props:
 *   sessionId        — currently active session UUID (or null for new-chat)
 *   sessions         — array from useSession
 *   onSessionSelect  — (session) => void
 *   onSessionDelete  — (deletedId) => void  ← NEW: App handles state transition
 *   onNewChat        — () => void
 *   onRefresh        — () => void  (called after delete to refresh list)
 */
import { useState } from 'react'
import { deleteSession } from '../lib/api'

const S = {
  sidebar: (minimized) => ({
    width: minimized ? '60px' : '280px',
    background: '#0a0a10',
    borderRight: '1px solid #1a1a26',
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    transition: 'width 0.2s ease-in-out',
  }),
  header: { padding: '16px', borderBottom: '1px solid #1a1a26' },
  title: { fontSize: '14px', fontWeight: '600', color: '#9090a8', marginBottom: '12px' },
  newChatBtn: {
    width: '100%',
    background: '#22c55e',
    border: '1px solid #22c55e',
    borderRadius: '6px',
    color: '#ffffff',
    fontSize: '12px',
    padding: '8px 12px',
    cursor: 'pointer',
    fontWeight: '500',
  },
  sessionList: { flex: 1, overflowY: 'auto', padding: '8px' },
  sessionItem: (active) => ({
    padding: '10px 12px',
    borderRadius: '6px',
    cursor: 'pointer',
    marginBottom: '4px',
    background: active ? '#1e1e2e' : 'transparent',
    border: active ? '1px solid #2a2a3e' : '1px solid transparent',
    transition: 'background 0.15s, border 0.15s',
  }),
  sessionItemHover: { background: '#1a1a26' },
  sessionTitle: {
    fontSize: '13px',
    color: '#e2e2e8',
    marginBottom: '4px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  sessionMeta: {
    fontSize: '11px',
    color: '#6b6b7a',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  deleteBtn: (hovered) => ({
    background: 'none',
    border: 'none',
    color: '#6b6b7a',
    cursor: 'pointer',
    fontSize: '10px',
    padding: '2px 6px',
    borderRadius: '3px',
    opacity: hovered ? 0.7 : 0,
    transition: 'opacity 0.15s',
  }),
  empty: { padding: '20px', textAlign: 'center', color: '#6b6b7a', fontSize: '12px' },
  collapsed: {
    padding: '12px 8px',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '8px',
  },
  toggleBtn: {
    background: 'none',
    border: 'none',
    color: '#6b6b7a',
    cursor: 'pointer',
    fontSize: '16px',
    padding: '4px',
    borderRadius: '4px',
    transition: 'color 0.15s',
    alignSelf: 'flex-end',
  },
  collapsedSession: (active) => ({
    width: '40px',
    height: '40px',
    borderRadius: '6px',
    background: active ? '#3730a3' : '#1e1e2e',
    border: active ? '1px solid #4f46e5' : '1px solid #2a2a3e',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    fontSize: '10px',
    color: active ? '#e2e2e8' : '#6b6b7a',
    transition: 'all 0.15s',
  }),
  newChatBtnCollapsed: {
    width: '40px',
    height: '40px',
    borderRadius: '6px',
    background: '#22c55e',
    border: '1px solid #22c55e',
    color: '#ffffff',
    fontSize: '16px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
}

function formatTime(timestamp) {
  if (!timestamp) return '—'
  const date = new Date(timestamp)
  if (isNaN(date.getTime())) return '—'
  const diffMins = Math.floor((Date.now() - date) / 60000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`
  return `${Math.floor(diffMins / 1440)}d ago`
}

export function Sidebar({
  sessionId,
  sessions = [],
  onSessionSelect,
  onSessionDelete,
  onNewChat,
  onRefresh,
}) {
  const [isMinimized, setIsMinimized] = useState(false)
  const [hoveredSession, setHoveredSession] = useState(null)

  const handleDelete = async (e, id) => {
    e.stopPropagation()
    try {
      await deleteSession(id)
    } catch (err) {
      console.error('Failed to delete session:', err)
    } finally {
      // Notify App of deletion (App decides what to do with session state)
      onSessionDelete?.(id)
      // Refresh the list
      onRefresh?.()
    }
  }

  if (isMinimized) {
    return (
      <div style={S.sidebar(true)}>
        <div style={S.collapsed}>
          <button style={S.toggleBtn} onClick={() => setIsMinimized(false)} title="Expand sidebar">
            »
          </button>
          <button style={S.newChatBtnCollapsed} onClick={onNewChat} title="New chat">
            +
          </button>
          {sessions.slice(0, 8).map((session) => (
            <div
              key={session.id}
              style={S.collapsedSession(session.id === sessionId)}
              onClick={() => onSessionSelect(session)}
              title={`${session.title || session.id?.slice(0, 8) || 'Untitled'} (${session.message_count || 0} msgs)`}
            >
              {(session.title || session.id || '?').charAt(0).toUpperCase()}
            </div>
          ))}
          {sessions.length > 8 && (
            <div style={{ fontSize: '10px', color: '#6b6b7a' }}>+{sessions.length - 8} more</div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div style={S.sidebar(false)}>
      <div style={S.header}>
        <div style={S.title}>Sessions</div>
        <button
          style={S.toggleBtn}
          onClick={() => setIsMinimized(true)}
          title="Minimize sidebar"
          onMouseEnter={(e) => (e.target.style.color = '#e2e2e8')}
          onMouseLeave={(e) => (e.target.style.color = '#6b6b7a')}
        >
          «
        </button>
        <button style={S.newChatBtn} onClick={onNewChat}>
          + New Chat
        </button>
      </div>

      <div style={S.sessionList}>
        {sessions.length === 0 ? (
          <div style={S.empty}>No sessions yet</div>
        ) : (
          sessions.map((session) => {
            const active = session.id === sessionId
            const hovered = hoveredSession === session.id
            return (
              <div
                key={session.id}
                style={{ ...S.sessionItem(active), ...(hovered && !active ? S.sessionItemHover : {}) }}
                onClick={() => onSessionSelect(session)}
                onMouseEnter={() => setHoveredSession(session.id)}
                onMouseLeave={() => setHoveredSession(null)}
                title={session.title}
              >
                <div style={S.sessionTitle}>
                  {session.title || session.id?.slice(0, 8) || 'Untitled'}
                </div>
                <div style={S.sessionMeta}>
                  <span>{session.message_count || 0} msgs</span>
                  <span>{formatTime(session.last_active)}</span>
                  <button
                    style={S.deleteBtn(hovered)}
                    onClick={(e) => handleDelete(e, session.id)}
                    title="Delete session"
                  >
                    ×
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
