export const KIND_COLORS = {
  Event: '#2f6fed',
  Market: '#6b7fa3',
  Article: '#1f9d55',
  Subject: '#d97706',
  Hypothesis: '#7c3aed',
  Happening: '#0e7490',
  Audit: '#dc2626',
}

export const EDGE_COLORS = {
  'shares-subject-with': '#d92d20',
  about: '#2f6fed',
  mentions: '#1f9d55',
  supports: '#7c3aed',
}

export const SKELETON_KINDS = ['Event', 'Subject', 'Hypothesis', 'Happening']

const SHORT_LEN = 28

export function short(label) {
  const s = String(label ?? '').split(/\s+/).join(' ')
  return s.length <= SHORT_LEN ? s : s.slice(0, SHORT_LEN - 1) + '…'
}

// Events-first: Market nodes (odds bookkeeping) are hidden by default so
// the news story — Events linked through Articles/Subjects/Hypotheses —
// dominates. Events always stay, even unlinked ones (the isolated control
// is data too). Degree, size and cap are computed on the visible subgraph
// only, so hidden markets cannot inflate Events or eat the cap.
export const HIDDEN_BY_DEFAULT = ['Market']

export function transformElements(elements, cap = 200, opts = {}) {
  const { showMarkets = false } = opts
  const raw = (elements ?? []).map((el) => el.data ?? {})
  const kindOf = {}
  for (const d of raw) {
    if (!('source' in d)) kindOf[d.id] = d.kind || 'Market'
  }
  const isHidden = (id) =>
    !showMarkets && (kindOf[id] ?? '') === 'Market'
  const marketsHidden = Object.values(kindOf).filter((k) => !showMarkets && k === 'Market').length
  const degree = {}
  const neighbours = {}
  for (const d of raw) {
    if ('source' in d && !isHidden(d.source) && !isHidden(d.target)) {
      degree[d.source] = (degree[d.source] ?? 0) + 1
      degree[d.target] = (degree[d.target] ?? 0) + 1
      ;(neighbours[d.source] ??= []).push(d.target)
      ;(neighbours[d.target] ??= []).push(d.source)
    }
  }
  let nodeRows = raw.filter(
    (d) =>
      !('source' in d) &&
      !isHidden(d.id) &&
      ((d.kind || 'Market') === 'Event' || (degree[d.id] ?? 0) > 0),
  )
  const totalNodes = nodeRows.length
  // Events pin first so the cap never drops the skeleton for evidence.
  nodeRows.sort((a, b) => {
    const ae = (a.kind || 'Market') === 'Event' ? 1 : 0
    const be = (b.kind || 'Market') === 'Event' ? 1 : 0
    return be - ae || (degree[b.id] ?? 0) - (degree[a.id] ?? 0)
  })
  if (cap != null) nodeRows = nodeRows.slice(0, cap)
  const kept = new Set(nodeRows.map((d) => d.id))
  const nodes = nodeRows.map((d) => {
    const kind = d.kind || 'Market'
    const deg = degree[d.id] ?? 0
    return {
      id: d.id,
      label: String(d.label ?? d.id),
      short: short(String(d.label ?? d.id)),
      kind,
      color: KIND_COLORS[kind] ?? '#6b7fa3',
      val: Math.round((1 + Math.min(deg, 12) / 3) * 100) / 100,
      deg,
      neighbors: [...new Set(neighbours[d.id] ?? [])],
      props: d.props ?? {},
    }
  })
  const totalLinks = raw.filter((d) => 'source' in d).length
  const links = raw
    .filter((d) => 'source' in d && kept.has(d.source) && kept.has(d.target))
    .map((d) => ({
      source: d.source,
      target: d.target,
      rel: d.label ?? '',
      color: EDGE_COLORS[d.label] ?? '#999999',
      w: d.label === 'shares-subject-with' ? 3 : 1,
    }))
  return { nodes, links, totalNodes, totalLinks, capped: totalNodes > nodes.length, marketsHidden }
}
