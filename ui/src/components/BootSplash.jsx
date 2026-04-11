/**
 * BootSplash — full-screen overlay shown while the engine warms up.
 *
 * Blocks all interaction until engineReady=true, then fades out.
 * Polls /api/health every 1.5s via the useHealth hook.
 *
 * States:
 *   booting  — server starting, Ollama not yet reachable
 *   warming  — server up, model loading into VRAM
 *   ready    — engine_ready=true, fade-out begins
 *   error    — timeout exceeded
 */
import { useState, useEffect } from 'react'

const FADE_DURATION_MS = 600

const S = {
  overlay: (visible, fading) => ({
    position:       'fixed',
    inset:          0,
    zIndex:         9999,
    display:        'flex',
    flexDirection:  'column',
    alignItems:     'center',
    justifyContent: 'center',
    background:     '#0f0f12',
    transition:     `opacity ${FADE_DURATION_MS}ms ease`,
    opacity:        fading ? 0 : 1,
    pointerEvents:  visible ? 'all' : 'none',
  }),
  logo: {
    width:        56,
    height:       56,
    borderRadius: 14,
    background:   'linear-gradient(135deg, #22c55e 0%, #16a34a 100%)',
    display:      'flex',
    alignItems:   'center',
    justifyContent: 'center',
    fontSize:     22,
    fontWeight:   700,
    color:        '#fff',
    marginBottom: 28,
    letterSpacing: -0.5,
  },
  title: {
    fontSize:     22,
    fontWeight:   600,
    color:        '#f4f4f5',
    marginBottom: 8,
    letterSpacing: -0.3,
  },
  status: {
    fontSize:   14,
    color:      '#71717a',
    marginBottom: 32,
    minHeight:  20,
  },
  barTrack: {
    width:        220,
    height:       3,
    borderRadius: 2,
    background:   '#27272a',
    overflow:     'hidden',
    marginBottom: 20,
  },
  barFill: (pct) => ({
    height:       '100%',
    width:        `${pct}%`,
    borderRadius: 2,
    background:   '#22c55e',
    transition:   'width 0.6s ease',
  }),
  errorBox: {
    maxWidth:     340,
    padding:      '14px 18px',
    borderRadius: 10,
    background:   '#1c1c1f',
    border:       '1px solid #3f3f46',
    color:        '#f87171',
    fontSize:     13,
    lineHeight:   1.6,
    textAlign:    'center',
  },
  hint: {
    fontSize: 12,
    color:    '#3f3f46',
    marginTop: 16,
  },
}

function statusText(health, error) {
  if (error)                         return null
  if (!health)                       return 'Connecting to server…'
  if (!health.ollama_reachable)      return 'Waiting for Ollama…'
  if (!health.engine_ready)          return `Loading ${health.model}…`
  return 'Ready'
}

function progressPct(health, error) {
  if (error)                    return 100
  if (!health)                  return 5
  if (!health.ollama_reachable) return 25
  if (!health.engine_ready)     return 65
  return 100
}

export function BootSplash({ engineReady, health, error }) {
  const [visible, setVisible] = useState(true)
  const [fading,  setFading]  = useState(false)

  useEffect(() => {
    if (!engineReady) return
    // Start fade-out, then unmount
    setFading(true)
    const t = setTimeout(() => setVisible(false), FADE_DURATION_MS)
    return () => clearTimeout(t)
  }, [engineReady])

  if (!visible) return null

  const pct = progressPct(health, error)

  return (
    <div style={S.overlay(visible, fading)}>
      <div style={S.logo}>lm</div>
      <div style={S.title}>LocalMind</div>

      {error ? (
        <div style={S.errorBox}>
          {error}
          <div style={{ marginTop: 10, color: '#71717a' }}>
            Make sure Ollama is running, then refresh the page.
          </div>
        </div>
      ) : (
        <>
          <div style={S.status}>{statusText(health, error)}</div>
          <div style={S.barTrack}>
            <div style={S.barFill(pct)} />
          </div>
          <div style={S.hint}>
            {!health
              ? 'Starting server…'
              : !health.ollama_reachable
              ? 'Ollama unreachable — check it is running on port 11434'
              : `Pre-loading ${health.model} into memory`}
          </div>
        </>
      )}
    </div>
  )
}
