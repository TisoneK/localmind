import { useState, useEffect, useRef } from 'react'
import { fetchHealth } from '../lib/api'

const POLL_INTERVAL_MS = 1500
const POLL_TIMEOUT_MS  = 120000

/**
 * useHealth — polls /api/health until engine_ready=true, then stops.
 *
 * Returns:
 *   engineReady  {bool}   — true once startup() has fully completed
 *   health       {object} — last successful health payload
 *   error        {string} — set if polling times out
 */
export function useHealth() {
  const [engineReady, setEngineReady] = useState(false)
  const [health, setHealth]           = useState(null)
  const [error, setError]             = useState(null)

  const timerRef     = useRef(null)
  const startedAt    = useRef(Date.now())
  const cancelledRef = useRef(false)

  useEffect(() => {
    cancelledRef.current = false

    async function poll() {
      if (cancelledRef.current) return
      try {
        const data = await fetchHealth()
        if (cancelledRef.current) return
        setHealth(data)
        if (data.engine_ready) {
          setEngineReady(true)
          return
        }
      } catch (_) {
        // Server still booting — swallow network errors and keep polling
      }

      if (Date.now() - startedAt.current > POLL_TIMEOUT_MS) {
        if (!cancelledRef.current) {
          setError('Server did not become ready within 2 minutes. Check Ollama is running.')
        }
        return
      }

      timerRef.current = setTimeout(poll, POLL_INTERVAL_MS)
    }

    poll()
    return () => {
      cancelledRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  return { engineReady, health, error }
}
