import { useMemo, useRef, useState } from 'react'
import GraphCanvas from './GraphCanvas'
import { HIDDEN_KINDS, HYPOTHESIS_COLORS, HYPOTHESIS_DESCRIPTIONS, KIND_COLORS, eventSummary, transformElements } from './graph'
import { useGraph } from './useGraph'

function EventAbout({ node, byId, links }) {
  const s = eventSummary(node, byId, links)
  const meta = [s.category, s.series, s.subTitle].filter(Boolean).join(' · ')
  return (
    <div>
      {meta && <div className="kind">{meta}</div>}
      {s.lead && (
        <div>
          Lead: {s.lead.label} — {s.lead.price}%
        </div>
      )}
      <div>
        {s.marketCount} market{s.marketCount === 1 ? '' : 's'} · {s.articleCount} article
        {s.articleCount === 1 ? '' : 's'} · {s.hypotheses} hypoth
        {s.hypotheses === 1 ? 'esis' : 'eses'}
      </div>
      {s.articleTitles.length > 0 && (
        <ul>
          {s.articleTitles.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
      {s.hypothesisTitles.length > 0 && (
        <div>
          <div style={{ marginTop: 8, fontWeight: 600 }}>Hypotheses</div>
          <ul>
            {s.hypothesisTitles.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function PropsTable({ props }) {
  const entries = Object.entries(props ?? {})
  if (entries.length === 0) return null
  return (
    <div>
      <div style={{ marginTop: 8, fontWeight: 600 }}>Live attributes</div>
      <ul>
        {entries.map(([k, v]) => (
          <li key={k}>
            {k}: {String(v)}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function App() {
  const [sel, setSel] = useState(null)
  const controlsRef = useRef(null)
  const { elements, revision, counts, loading, error, stale } = useGraph()

  const view = useMemo(() => transformElements(elements, 200), [elements])

  const hiddenBits = []
  if (view.transitiveHidden > 0) hiddenBits.push(`${view.transitiveHidden} transitive ${view.transitiveHidden === 1 ? 'hypothesis' : 'hypotheses'}`)
  const hiddenText = hiddenBits.length ? ` — ${hiddenBits.join(', ')} hidden` : ''
  const byId = useMemo(() => new Map(view.nodes.map((n) => [n.id, n])), [view.nodes])
  const events = useMemo(
    () => view.nodes.filter((n) => n.kind === 'Event').sort((a, b) => a.label.localeCompare(b.label)),
    [view.nodes],
  )
  const selEventId = sel?.type === 'node' && byId.get(sel.id)?.kind === 'Event' ? sel.id : ''
  const centerOnEvent = (id) => {
    setSel(id ? { type: 'node', id } : null)
    if (id) controlsRef.current?.focus(id)
    else controlsRef.current?.reset()
  }

  let detail = <p>Click a node or link.</p>
  if (sel?.type === 'node' && byId.get(sel.id)) {
    const n = byId.get(sel.id)
    detail = (
      <div>
        <h2>{n.label}</h2>
        <div className="kind">
          {n.kind} · degree {n.deg}
        </div>
        {n.kind === 'Event' && <EventAbout node={n} byId={byId} links={view.links} />}
        <div>Neighbours:</div>
        <ul>
          {n.neighbors.map((m) => (
            <li key={m}>{byId.get(m)?.label ?? m}</li>
          ))}
        </ul>
        <PropsTable props={n.props} />
        <div className="nid">{n.id}</div>
      </div>
    )
  } else if (sel?.type === 'link') {
    const h = sel.hypothesis
    detail = (
      <div>
        <h2>{h?.title ?? sel.rel}</h2>
        <div className="kind">
          {h ? `hypothesis link · ${HYPOTHESIS_DESCRIPTIONS[h.type] ?? h.type ?? 'mentioned together'}` : 'link'}
        </div>
        <div>
          {byId.get(sel.source)?.label ?? sel.source} →{' '}
          {byId.get(sel.target)?.label ?? sel.target}
        </div>
        {h && (
          <div>
            {h.challenged && (
              <div className="warn">challenged: {h.challengeReason}</div>
            )}
            <div>Evidence ({h.evidence.length}):</div>
            <ul>
              {h.evidence.map((a) => (
                <li key={a.id}>{a.label}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div id="bar">
        <h1>Compass graph</h1>
        <span>
          {view.nodes.length} nodes, {view.links.length} links (rev {revision})
          {view.capped ? ` — showing ${view.nodes.length} of ${view.totalNodes}` : ''}
          {hiddenText}
        </span>
        <span className="dim">drag rotates · scroll zooms · reset for the globe</span>
        {stale && <span className="warn">stale — retrying</span>}
        <div id="controls">
          <label>
            Event{' '}
            <select aria-label="Center on event" value={selEventId} onChange={(e) => centerOnEvent(e.target.value)}>
              <option value="">all — reset view</option>
              {events.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <div id="wrap">
        <div id="view3d">
          {loading ? (
            <p className="pad">Loading graph…</p>
          ) : error && view.nodes.length === 0 ? (
            <p className="pad">
              Graph API unreachable ({error}). Start it with{' '}
              <code>uv run python -m services.graph_api --port 8090</code> then reload.
            </p>
          ) : view.nodes.length === 0 ? (
            <p className="pad">No linked nodes yet — run the news loop, then this view fills in.</p>
          ) : (
            <GraphCanvas
              nodes={view.nodes}
              links={view.links}
              onSelect={setSel}
              controlsRef={controlsRef}
            />
          )}
          <div id="zoom">
            <button type="button" aria-label="Zoom in" onClick={() => controlsRef.current?.zoomIn()}>
              +
            </button>
            <button type="button" aria-label="Zoom out" onClick={() => controlsRef.current?.zoomOut()}>
              −
            </button>
            <button
              type="button"
              className="wide"
              aria-label="Reset view"
              onClick={() => {
                controlsRef.current?.reset()
                setSel(null)
              }}
            >
              Reset
            </button>
          </div>
        </div>
        <aside id="info">
          <h2>Details</h2>
          {detail}
          <div className="meta">Hypothesis links</div>
          <ul className="legend">
            {Object.keys(HYPOTHESIS_COLORS).map((t) => (
              <li key={t}>
                <span className="swatch" style={{ background: HYPOTHESIS_COLORS[t] }} />
                {HYPOTHESIS_DESCRIPTIONS[t]}
              </li>
            ))}
          </ul>
          <div className="meta">Nodes</div>
          <ul className="legend">
            {Object.keys(KIND_COLORS).filter((k) => !HIDDEN_KINDS.includes(k)).map((k) => (
              <li key={k}>
                <span className="swatch" style={{ background: KIND_COLORS[k] }} />
                {k}
                {k === 'Event' ? ' (gem)' : ''}
                {k === 'Article' ? ' (diamond)' : ''}
              </li>
            ))}
          </ul>
          <div className="meta">
            rendered {view.nodes.length} nodes · {view.links.length} links (live{' '}
            {counts.nodes} nodes · {counts.edges} edges · rev {revision})
          </div>
        </aside>
      </div>
    </div>
  )
}
