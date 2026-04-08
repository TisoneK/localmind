import { useState, useEffect } from 'react'
import { fetchSessions } from '../lib/api'

/**
 * useSession - fetches and manages session data.
 * Returns { sessions, loading, error, getSessionTitle }
 */
export function useSession() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const loadSessions = () => {
    let cancelled = false

    setLoading(true)
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
  }

  useEffect(() => {
    return loadSessions()
  }, [])

  const getSessionTitle = (sessionId) => {
    const session = sessions.find(s => s.id === sessionId)
    return session?.title || null
  }

  return { sessions, loading, error, getSessionTitle, refreshSessions: loadSessions }
}
