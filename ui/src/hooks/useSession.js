import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchSessions } from '../lib/api'

/**
 * useSession — centralized session state machine.
 *
 * States:
 *   "new"      — user is in a fresh, unsaved chat (no session selected)
 *   "active"   — user is chatting in a known session
 *   "loading"  — history is being fetched for a selected session
 *
 * Rules:
 *   - App always opens in "new" state (no auto-selection of last session)
 *   - Deleting the active session always transitions back to "new"
 *   - Clicking a session transitions to "active" for that session
 *   - New Chat button always transitions to "new"
 *   - Sessions list refresh NEVER changes the active session
 */
export function useSession() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Track whether we've done the initial fetch so we don't double-load
  const initialFetchDone = useRef(false)

  const loadSessions = useCallback(() => {
    setLoading(true)
    let cancelled = false

    fetchSessions()
      .then((data) => {
        if (!cancelled) {
          setSessions(data || [])
          setLoading(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message)
          setLoading(false)
        }
      })

    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!initialFetchDone.current) {
      initialFetchDone.current = true
      loadSessions()
    }
  }, [loadSessions])

  const getSessionTitle = useCallback((sessionId) => {
    const session = sessions.find(s => s.id === sessionId)
    return session?.title || null
  }, [sessions])

  return {
    sessions,
    loading,
    error,
    getSessionTitle,
    refreshSessions: loadSessions,
  }
}
