import { useMemo, useState } from 'react'
import GraphCanvas from './GraphCanvas'
import { transformElements } from './graph'
import { useGraph } from './useGraph'

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
  const [at, setAt] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [sel, setSel] = useState(null)
  const { elements, revision, counts, loading, error, stale } = useGraph({ at })

  const view = useMemo(() => transformElements(elements, 200), [elements])
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
    detail = (
      <div>
        <h2>{sel.rel}</h2>
        <div className="kind">link</div>
        <div>
          {byId.get(sel.source)?.label ?? sel.source} →{' '}
          {byId.get(sel.target)?.label ?? sel.target}
        </div>
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
        </span>
        <span className="dim">blue=Event grey=Market green=Article amber=Subject purple=Hypothesis</span>
        <label>
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> all
          labels
        </label>
        <label title="Filter by world time (valid_at). Odds stay live until replay lands.">
          as-of{' '}
          <input
            value={at}
            onChange={(e) => setAt(e.target.value)}
            placeholder="20261028T120000Z"
            style={{ width: 170 }}
          />
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
            <GraphCanvas nodes={view.nodes} links={view.links} showAll={showAll} onSelect={setSel} />
          )}
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
