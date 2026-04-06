import { useState, useEffect } from 'react'
import { fetchHealth } from '../lib/api'

/**
 * useHealth — polls the /api/health endpoint on mount.
 * Returns { health, loading, error }
 */
export function useHealth() {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    fetchHealth()
      .then((data) => {
        if (!cancelled) {
          setHealth(data)
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

  return { health, loading, error }
}
