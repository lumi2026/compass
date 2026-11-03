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
  'has-market': '#94a3b8',
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
export const HIDDEN_BY_DEFAULT = ['Market', 'Subject']

export function transformElements(elements, cap = 200, opts = {}) {
  const { showMarkets = false, showSubjects = false } = opts
  const raw = (elements ?? []).map((el) => el.data ?? {})
  const kindOf = {}
  for (const d of raw) {
    if (!('source' in d)) kindOf[d.id] = d.kind || 'Market'
  }
  const isHidden = (id) => {
    const k = kindOf[id] ?? ''
    return (!showMarkets && k === 'Market') || (!showSubjects && k === 'Subject')
  }
  const hiddenCount = (kind, shown) =>
    !shown ? Object.values(kindOf).filter((k) => k === kind).length : 0
  const marketsHidden = hiddenCount('Market', showMarkets)
  const subjectsHidden = hiddenCount('Subject', showSubjects)
  // Per-node market rollup from the FULL edge set, so the sidebar can
  // still say what a topic's markets are doing while they stay hidden.
  const rawById = {}
  for (const d of raw) {
    if (!('source' in d)) rawById[d.id] = d
  }
  const marketInfo = {}
  for (const d of raw) {
    if (!('source' in d)) continue
    for (const [a, b] of [[d.source, d.target], [d.target, d.source]]) {
      if ((kindOf[b] ?? '') !== 'Market' || (kindOf[a] ?? '') === 'Market') continue
      const m = rawById[b] ?? {}
      const info = (marketInfo[a] ??= { count: 0, lead: null })
      info.count += 1
      const price = m.props?.price_cents ?? null
      if (price != null && (info.lead == null || price > info.lead.price)) {
        info.lead = { label: String(m.label ?? b), price }
      }
    }
  }
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
  // Hypotheses render as edges, not nodes: a Hypothesis with exactly
  // two Event endpoints (via supports) becomes one Event→Event link
  // carrying its title, evidence articles, and any audit challenge.
  // Anything else keeps its node so no data is ever dropped.
  const supportsFrom = {}
  const challengesTo = {}
  for (const d of raw) {
    if (!('source' in d)) continue
    if (d.label === 'supports') (supportsFrom[d.source] ??= []).push(d.target)
    else if (d.label === 'challenges') (challengesTo[d.target] ??= []).push(d.source)
  }
  const collapsed = new Set()
  const hypothesisLinks = []
  for (const d of nodeRows) {
    if ((d.kind || 'Market') !== 'Hypothesis') continue
    const ends = [...new Set((supportsFrom[d.id] ?? []).filter(
      (t) => (kindOf[t] ?? '') === 'Event'))]
    if (ends.length !== 2) continue
    const evidence = [...new Set((supportsFrom[d.id] ?? []).filter(
      (t) => (kindOf[t] ?? '') === 'Article'))]
    const audits = challengesTo[d.id] ?? []
    const [a, b] = ends.sort()
    hypothesisLinks.push({
      source: a,
      target: b,
      rel: 'hypothesizes',
      color: '#7c3aed',
      w: 2,
      hypothesis: {
        title: String(d.label ?? d.id),
        evidence: evidence.map((id) => ({ id, label: String(rawById[id]?.label ?? id) })),
        challenged: audits.length > 0,
        challengeReason: audits.length ? String(rawById[audits[0]]?.label ?? '') : '',
      },
    })
    collapsed.add(d.id)
  }
  nodeRows = nodeRows.filter((d) => !collapsed.has(d.id))
  const totalNodes = nodeRows.length
  // Events pin first so the cap never drops the skeleton for evidence.
  nodeRows.sort((a, b) => {
    const ae = (a.kind || 'Market') === 'Event' ? 1 : 0
    const be = (b.kind || 'Market') === 'Event' ? 1 : 0
    return be - ae || (degree[b.id] ?? 0) - (degree[a.id] ?? 0)
  })
  if (cap != null) nodeRows = nodeRows.slice(0, cap)
  const kept = new Set(nodeRows.map((d) => d.id))
  // Drop non-Events left neighbourless by hiding/collapse (a spent
  // audit, an article whose only linker collapsed away): with no
  // visible edge they render as dangling dots.
  let pruned = true
  while (pruned) {
    pruned = false
    for (const d of nodeRows) {
      if ((d.kind || 'Market') === 'Event' || !kept.has(d.id)) continue
      const nb = (neighbours[d.id] ?? []).filter((m) => kept.has(m))
      if (nb.length === 0) {
        kept.delete(d.id)
        pruned = true
      }
    }
  }
  nodeRows = nodeRows.filter((d) => kept.has(d.id))
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
      neighbors: [...new Set(neighbours[d.id] ?? [])].filter((m) => kept.has(m)),
      props: d.props ?? {},
      marketInfo: marketInfo[d.id] ?? { count: 0, lead: null },
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
    .concat(hypothesisLinks.filter((l) => kept.has(l.source) && kept.has(l.target)))
  return { nodes, links, totalNodes, totalLinks, capped: totalNodes > nodes.length, marketsHidden, subjectsHidden }
}

// Zoom step for the zoom buttons: move the camera toward/away from
// the orbit target. Pure geometry over {position}-shaped objects so
// the math is unit-testable without a renderer.
export function zoomCamera(camera, target, factor, limits = [10, 3000]) {
  const off = camera.position.clone().sub(target).multiplyScalar(factor)
  const len = off.length()
  if (!Number.isFinite(len) || len <= 0) return camera.position
  const [lo, hi] = limits
  off.setLength(Math.min(hi, Math.max(lo, len)))
  return camera.position.copy(target).add(off)
}

// Sidebar/hover copy for one Event node: what the topic is about,
// assembled from its own meta plus its linked markets and articles.
export function eventSummary(node, byId, links = []) {
  const meta = node?.props ?? {}
  const articles = []
  const hypothesisTitles = []
  for (const id of node?.neighbors ?? []) {
    const n = byId?.get(id)
    if (!n) continue
    if (n.kind === 'Article') articles.push(n.label)
    else if (n.kind === 'Hypothesis') hypothesisTitles.push(n.label)
  }
  for (const l of links ?? []) {
    if (!l?.hypothesis) continue
    const s = typeof l.source === 'object' ? l.source.id : l.source
    const t = typeof l.target === 'object' ? l.target.id : l.target
    if (s === node?.id || t === node?.id) hypothesisTitles.push(l.hypothesis.title)
  }
  const mi = node?.marketInfo ?? { count: 0, lead: null }
  return {
    category: meta.category ?? '',
    series: meta.series_ticker ?? '',
    subTitle: meta.sub_title ?? '',
    marketCount: mi.count,
    lead: mi.lead,
    articleTitles: articles.slice(0, 3),
    articleCount: articles.length,
    hypotheses: hypothesisTitles.length,
    hypothesisTitles: hypothesisTitles.slice(0, 3),
  }
}
