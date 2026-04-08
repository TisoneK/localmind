import { useState, useEffect } from 'react'
import { fetchSessions, deleteSession } from '../lib/api'

const S = {
  sidebar: (isMinimized) => ({
    width: isMinimized ? '60px' : '280px',
    background: '#0a0a10',
    borderRight: '1px solid #1a1a26',
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    transition: 'width 0.2s ease-in-out',
  }),
  header: {
    padding: '16px',
    borderBottom: '1px solid #1a1a26',
  },
  title: {
    fontSize: '14px',
    fontWeight: '600',
    color: '#9090a8',
    marginBottom: '12px',
  },
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
  sessionList: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px',
  },
  sessionItem: (isActive) => ({
    padding: '10px 12px',
    borderRadius: '6px',
    cursor: 'pointer',
    marginBottom: '4px',
    background: isActive ? '#1e1e2e' : 'transparent',
    border: isActive ? '1px solid #2a2a3e' : '1px solid transparent',
    transition: 'background 0.15s, border 0.15s',
  }),
  sessionItemHover: {
    background: '#1a1a26',
  },
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
  deleteBtn: {
    background: 'none',
    border: 'none',
    color: '#6b6b7a',
    cursor: 'pointer',
    fontSize: '10px',
    padding: '2px 6px',
    borderRadius: '3px',
    opacity: 0.4,
    transition: 'opacity 0.15s',
  },
  sessionItemHovering: {
    opacity: 1,
  },
  empty: {
    padding: '20px',
    textAlign: 'center',
    color: '#6b6b7a',
    fontSize: '12px',
  },
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
  toggleBtnHover: {
    color: '#e2e2e8',
  },
  collapsedSession: {
    width: '40px',
    height: '40px',
    borderRadius: '6px',
    background: '#1e1e2e',
    border: '1px solid #2a2a3e',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    fontSize: '10px',
    color: '#6b6b7a',
    transition: 'all 0.15s',
    position: 'relative',
  },
  collapsedSessionActive: {
    background: '#3730a3',
    borderColor: '#4f46e5',
    color: '#e2e2e8',
  },
  collapsedSessionHover: {
    background: '#2a2a3e',
    borderColor: '#3a3a4e',
    color: '#9090a8',
  },
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
    transition: 'all 0.15s',
  },
  newChatBtnCollapsedHover: {
    background: '#16a34a',
    borderColor: '#16a34a',
  },
}

export function Sidebar({ sessionId, onSessionSelect, onNewChat }) {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [hoveredSession, setHoveredSession] = useState(null)
  const [isMinimized, setIsMinimized] = useState(false)
  const [hoveredCollapsed, setHoveredCollapsed] = useState(null)

  const loadSessions = async () => {
    try {
      const data = await fetchSessions()
      setSessions(data || [])
    } catch (err) {
      console.error('Failed to load sessions:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSessions()
  }, [sessionId]) // re-fetch when active session changes

  const handleDeleteSession = async (e, sessionIdToDelete) => {
    e.stopPropagation()
    
    // Optimistically remove from local state
    setSessions(prev => prev.filter(s => s.id !== sessionIdToDelete))
    
    try {
      await deleteSession(sessionIdToDelete)
      
      // If we deleted the current session, create a new one
      if (sessionIdToDelete === sessionId) {
        onNewChat()
      }
    } catch (err) {
      console.error('Failed to delete session:', err)
      // Revert on failure
      loadSessions()
    }
  }

  const handleSessionClick = (session) => {
    onSessionSelect(session)
  }

  const formatTime = (timestamp) => {
    if (!timestamp) return '—'
    const date = new Date(timestamp)
    if (isNaN(date.getTime())) return '—'
    
    const now = new Date()
    const diffMs = now - date
    const diffMins = Math.floor(diffMs / 60000)
    
    if (diffMins < 1) return 'just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`
    return `${Math.floor(diffMins / 1440)}d ago`
  }

  if (loading) {
    return (
      <div style={S.sidebar(isMinimized)}>
        {isMinimized ? (
          <div style={S.collapsed}>
            <button
              style={S.toggleBtn}
              onClick={() => setIsMinimized(false)}
              title="Expand sidebar"
            >
              »
            </button>
            <div style={{ fontSize: '10px', color: '#6b6b7a' }}>...</div>
          </div>
        ) : (
          <div style={S.header}>
            <div style={S.title}>Sessions</div>
            <button
              style={S.toggleBtn}
              onClick={() => setIsMinimized(true)}
              title="Minimize sidebar"
            >
              «
            </button>
          </div>
        )}
      </div>
    )
  }

  if (isMinimized) {
    return (
      <div style={S.sidebar(true)}>
        <div style={S.collapsed}>
          <button
            style={S.toggleBtn}
            onClick={() => setIsMinimized(false)}
            title="Expand sidebar"
          >
            »
          </button>
          
          <button
            style={S.newChatBtnCollapsed}
            onClick={onNewChat}
            title="New chat"
            onMouseEnter={(e) => {
              e.target.style.background = '#16a34a'
              e.target.style.borderColor = '#16a34a'
            }}
            onMouseLeave={(e) => {
              e.target.style.background = '#22c55e'
              e.target.style.borderColor = '#22c55e'
            }}
          >
            +
          </button>
          
          {sessions.slice(0, 8).map((session) => {
            const isActive = session.id === sessionId
            const isHovered = hoveredCollapsed === session.id
            
            return (
              <div
                key={session.id}
                style={{
                  ...S.collapsedSession,
                  ...(isActive ? S.collapsedSessionActive : {}),
                  ...(isHovered && !isActive ? S.collapsedSessionHover : {}),
                }}
                onClick={() => handleSessionClick(session)}
                onMouseEnter={() => setHoveredCollapsed(session.id)}
                onMouseLeave={() => setHoveredCollapsed(null)}
                title={`${session.title || session.id?.slice(0, 8) || 'Untitled'} (${session.message_count || 0} msgs)`}
              >
                {session.title ? session.title.charAt(0).toUpperCase() : session.id?.slice(0, 1).toUpperCase() || '?'}
              </div>
            )
          })}
          
          {sessions.length > 8 && (
            <div style={{ fontSize: '10px', color: '#6b6b7a', textAlign: 'center' }}>
              +{sessions.length - 8} more
            </div>
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
          onMouseEnter={(e) => e.target.style.color = '#e2e2e8'}
          onMouseLeave={(e) => e.target.style.color = '#6b6b7a'}
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
            const isActive = session.id === sessionId
            const isHovered = hoveredSession === session.id
            
            return (
              <div
                key={session.id}
                style={{
                  ...S.sessionItem(isActive),
                  ...(isHovered && !isActive ? S.sessionItemHover : {}),
                }}
                onClick={() => handleSessionClick(session)}
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
                    style={{
                      ...S.deleteBtn,
                      ...(isHovered ? S.sessionItemHovering : {}),
                    }}
                    onClick={(e) => handleDeleteSession(e, session.id)}
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
