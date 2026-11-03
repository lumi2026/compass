import { useMemo, useRef, useState } from 'react'
import GraphCanvas from './GraphCanvas'
import { eventSummary, transformElements } from './graph'
import { useGraph } from './useGraph'

function EventAbout({ node, byId, links }) {
  const s = eventSummary(node, byId, links)
  const meta = [s.category, s.series, s.subTitle].filter(Boolean).join(' · ')
  return (
    <div>
      {meta && <div className="kind">{meta}</div>}
      {s.lead && (
        <div>
          Lead: {s.lead.label} — {s.lead.price}¢
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
  const [showAll, setShowAll] = useState(false)
  const [showMarkets, setShowMarkets] = useState(false)
  const [showSubjects, setShowSubjects] = useState(false)
  const [sel, setSel] = useState(null)
  const controlsRef = useRef(null)
  const { elements, revision, counts, loading, error, stale } = useGraph()

  const view = useMemo(
    () => transformElements(elements, 200, { showMarkets, showSubjects }),
    [elements, showMarkets, showSubjects],
  )

  const hiddenBits = []
  if (!showMarkets && view.marketsHidden > 0) hiddenBits.push(`${view.marketsHidden} markets`)
  if (!showSubjects && view.subjectsHidden > 0) hiddenBits.push(`${view.subjectsHidden} subjects`)
  const hiddenText = hiddenBits.length ? ` — ${hiddenBits.join(', ')} hidden` : ''
  const byId = useMemo(() => new Map(view.nodes.map((n) => [n.id, n])), [view.nodes])

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
        <div className="kind">{h ? 'hypothesis link' : 'link'}</div>
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
          {view.capped ? ` — showing 200 of ${view.totalNodes}` : ''}
          {hiddenText}
        </span>
        <span className="dim">drag moves · right-drag rotates · scroll zooms</span>
        <label>
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> all
          labels
        </label>
        <label title="Show the grey Market odds nodes (hidden by default so the news links dominate).">
          <input type="checkbox" checked={showMarkets} onChange={(e) => setShowMarkets(e.target.checked)} />{' '}
          markets
        </label>
        <label title="Show the amber Subject nodes (internal matching logic, hidden by default).">
          <input type="checkbox" checked={showSubjects} onChange={(e) => setShowSubjects(e.target.checked)} />{' '}
          subjects
        </label>
        {stale && <span className="warn">stale — retrying</span>}
      </div>
      <div id="wrap">
        <div id="view3d">
          {loading ? (
            <p className="pad">Loading graph…</p>
          ) : error && view.nodes.length === 0 ? (
            <p className="pad">
              Graph API unreachable ({error}). Start it with{' '}
              <code>uv run python -m services.graph_server --port 8090</code> then reload.
            </p>
          ) : view.nodes.length === 0 ? (
            <p className="pad">No linked nodes yet — run the news loop, then this view fills in.</p>
          ) : (
            <GraphCanvas
              nodes={view.nodes}
              links={view.links}
              showAll={showAll}
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
          <div className="meta">
            nodes {counts.nodes} · edges {counts.edges} · rev {revision}
          </div>
        </aside>
      </div>
    </div>
  )
}
