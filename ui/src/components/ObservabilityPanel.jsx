import { useState, useEffect } from 'react'

const S = {
  panel: {
    width: '300px',
    background: '#0a0a10',
    borderLeft: '1px solid #1a1a26',
    padding: '16px',
    overflowY: 'auto',
    fontSize: '12px',
    fontFamily: 'monospace',
  },
  header: {
    fontSize: '14px',
    fontWeight: '600',
    color: '#9090a8',
    marginBottom: '16px',
    paddingBottom: '8px',
    borderBottom: '1px solid #1a1a26',
  },
  section: {
    marginBottom: '16px',
  },
  sectionTitle: {
    fontSize: '11px',
    color: '#6b6b7a',
    marginBottom: '4px',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
  },
  value: {
    color: '#e2e2e8',
    marginBottom: '8px',
  },
  metric: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: '4px',
  },
  metricLabel: {
    color: '#6b6b7a',
  },
  metricValue: {
    color: '#6366f1',
  },
  tool: {
    background: '#1e1e2e',
    padding: '6px 8px',
    borderRadius: '4px',
    marginBottom: '4px',
    border: '1px solid #2a2a3e',
  },
  toolName: {
    color: '#a5b4fc',
    fontWeight: '500',
  },
  toolStatus: {
    fontSize: '10px',
    marginLeft: '8px',
  },
  success: {
    color: '#22c55e',
  },
  error: {
    color: '#ef4444',
  },
  memoryFact: {
    background: '#1a1a26',
    padding: '4px 6px',
    borderRadius: '3px',
    marginBottom: '2px',
    fontSize: '10px',
    color: '#9090a8',
    borderLeft: '2px solid #6366f1',
  },
}

export function ObservabilityPanel({ observabilityData }) {
  const [isOpen, setIsOpen] = useState(true)

  if (!isOpen) {
    return (
      <div style={{ ...S.panel, width: '60px', cursor: 'pointer' }} onClick={() => setIsOpen(true)}>
        <div style={{ color: '#6b6b7a', writingMode: 'vertical-rl', textAlign: 'center' }}>
          OBS
        </div>
      </div>
    )
  }

  const { intent, confidence, toolCalls, memoryHits, latency, tokens } = observabilityData || {}

  return (
    <div style={S.panel}>
      <div style={{ ...S.header, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>Observability</span>
        <button 
          onClick={() => setIsOpen(false)}
          style={{ 
            background: 'none', 
            border: 'none', 
            color: '#6b6b7a', 
            cursor: 'pointer',
            fontSize: '16px'
          }}
        >
          ×
        </button>
      </div>

      {/* Intent Classification */}
      <div style={S.section}>
        <div style={S.sectionTitle}>Intent</div>
        <div style={S.metric}>
          <span style={S.metricLabel}>Primary:</span>
          <span style={S.metricValue}>{intent || '—'}</span>
        </div>
        <div style={S.metric}>
          <span style={S.metricLabel}>Confidence:</span>
          <span style={S.metricValue}>{confidence ? `${(confidence * 100).toFixed(0)}%` : '—'}</span>
        </div>
      </div>

      {/* Tool Calls */}
      <div style={S.section}>
        <div style={S.sectionTitle}>Tool Calls</div>
        {toolCalls && toolCalls.length > 0 ? (
          toolCalls.map((tool, index) => (
            <div key={index} style={S.tool}>
              <span style={S.toolName}>{tool.name}</span>
              <span style={{ ...S.toolStatus, ...(tool.success ? S.success : S.error) }}>
                {tool.success ? '✓' : '✗'} {tool.latency}ms
              </span>
            </div>
          ))
        ) : (
          <div style={S.value}>No tools used</div>
        )}
      </div>

      {/* Memory Hits */}
      <div style={S.section}>
        <div style={S.sectionTitle}>Memory ({memoryHits?.length || 0})</div>
        {memoryHits && memoryHits.length > 0 ? (
          memoryHits.slice(0, 3).map((fact, index) => (
            <div key={index} style={S.memoryFact}>
              {fact.length > 60 ? fact.substring(0, 60) + '...' : fact}
            </div>
          ))
        ) : (
          <div style={S.value}>No memory retrieved</div>
        )}
      </div>

      {/* Performance */}
      <div style={S.section}>
        <div style={S.sectionTitle}>Performance</div>
        <div style={S.metric}>
          <span style={S.metricLabel}>Total Latency:</span>
          <span style={S.metricValue}>{latency || '—'}ms</span>
        </div>
        <div style={S.metric}>
          <span style={S.metricLabel}>Tokens:</span>
          <span style={S.metricValue}>{tokens || '—'}</span>
        </div>
      </div>
    </div>
  )
}
