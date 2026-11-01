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

// Mirror workspace/viewer.py: drop isolated nodes, size by degree.
// Cap by degree (not insertion order) so cross-links survive.
export function transformElements(elements, cap = 200) {
  const raw = (elements ?? []).map((el) => el.data ?? {})
  const degree = {}
  const neighbours = {}
  for (const d of raw) {
    if ('source' in d) {
      degree[d.source] = (degree[d.source] ?? 0) + 1
      degree[d.target] = (degree[d.target] ?? 0) + 1
      ;(neighbours[d.source] ??= []).push(d.target)
      ;(neighbours[d.target] ??= []).push(d.source)
    }
  }
  let nodeRows = raw.filter((d) => !('source' in d) && (degree[d.id] ?? 0) > 0)
  const totalNodes = nodeRows.length
  nodeRows.sort((a, b) => (degree[b.id] ?? 0) - (degree[a.id] ?? 0))
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
  return { nodes, links, totalNodes, totalLinks, capped: totalNodes > nodes.length }
}
