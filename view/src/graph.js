import taxonomy from '../../shared/hypothesis_taxonomy.json' with { type: 'json' }

export const KIND_COLORS = {
  Event: '#ea580c',
  Market: '#6b7fa3',
  Article: '#22d3ee',
  Subject: '#d97706',
  Hypothesis: '#7c3aed',
  Happening: '#0e7490',
  Audit: '#dc2626',
}

export const EDGE_COLORS = {
  'shares-subject-with': '#d92d20',
  about: '#ea580c',
  mentions: '#1f9d55',
  supports: '#7c3aed',
  'has-market': '#94a3b8',
}

// Closed hypothesis relation taxonomy, shared with the Python squad via
// shared/hypothesis_taxonomy.json (the single source of truth). Each
// link type gets its own edge color so the globe reads by relation,
// not just by link. Legacy nodes carry only props.mode and fall back
// through the shared mode map.
export const HYPOTHESIS_TYPES = taxonomy.types

export const HYPOTHESIS_COLORS = {
  co_mention: '#7c3aed',
  co_location: '#14b8a6',
  shared_context: '#d97706',
  co_outcome: '#c026d3',
}

export const HYPOTHESIS_DESCRIPTIONS = {
  co_mention: 'mentioned together',
  co_location: 'shared place',
  shared_context: 'shared theme',
  co_outcome: 'correlated odds',
}

export const LEGACY_MODE_TYPE = taxonomy.mode_to_type

export function hypothesisType(props = {}) {
  if (props.type && HYPOTHESIS_COLORS[props.type]) return props.type
  const legacy = LEGACY_MODE_TYPE[props.mode]
  if (legacy) return legacy
  return 'co_mention'
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
  const all = (elements ?? []).map((el) => el.data ?? {})
  // Layer-2 transitive metas (secondary hypotheses with no direct
  // article evidence) are omitted from the render: as nodes they
  // read as purple-on-purple hops far from the cited events. The
  // data stays in the graph store; the view just skips those nodes
  // and their edges, letting the cited parents collapse into plain
  // Event→Event links.
  const transitiveMeta = new Set(
    all
      .filter((d) => !('source' in d) && (d.kind || '') === 'Hypothesis' &&
        (d.props?.mode === 'transitive' || d.props?.layer === 2))
      .map((d) => d.id),
  )
  const raw = all.filter((d) => ('source' in d
    ? !transitiveMeta.has(d.source) && !transitiveMeta.has(d.target)
    : !transitiveMeta.has(d.id)))
  const transitiveHidden = transitiveMeta.size
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
    // The Event-to-Event subject mesh is always filtered from links, so it
    // stays out of degree/neighbours too — otherwise the Details tab lists
    // mesh neighbours with no visible link to explain them.
    if (d.label === 'shares-subject-with') continue
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
  // Layer-2 meta links (hypothesis supports hypothesis) pin both ends
  // as nodes: collapsing either would orphan the meta edge.
  const hypLinked = new Set()
  for (const d of raw) {
    if (!('source' in d) || d.label !== 'supports') continue
    if ((kindOf[d.source] ?? '') === 'Hypothesis' && (kindOf[d.target] ?? '') === 'Hypothesis') {
      hypLinked.add(d.source)
      hypLinked.add(d.target)
    }
  }
  const hypothesisLinks = []
  for (const d of nodeRows) {
    if ((d.kind || 'Market') !== 'Hypothesis') continue
    const ends = [...new Set((supportsFrom[d.id] ?? []).filter(
      (t) => (kindOf[t] ?? '') === 'Event'))]
    if (ends.length !== 2 || hypLinked.has(d.id)) continue
    const evidence = [...new Set((supportsFrom[d.id] ?? []).filter(
      (t) => (kindOf[t] ?? '') === 'Article'))]
    const audits = challengesTo[d.id] ?? []
    const [a, b] = ends.sort()
    const htype = hypothesisType(d.props ?? {})
    hypothesisLinks.push({
      source: a,
      target: b,
      rel: 'hypothesizes',
      color: HYPOTHESIS_COLORS[htype],
      w: 2,
      hypothesis: {
        title: String(d.label ?? d.id),
        type: htype,
        evidence: evidence.map((id) => ({ id, label: String(rawById[id]?.label ?? id) })),
        challenged: audits.length > 0,
        challengeReason: audits.length ? String(rawById[audits[0]]?.label ?? '') : '',
      },
    })
    collapsed.add(d.id)
  }
  nodeRows = nodeRows.filter((d) => !collapsed.has(d.id))
  const totalNodes = nodeRows.length
  // Split budget: Events pin first up to the cap, but evidence gets its
  // own share so it is never starved out once Events fill the cap (with
  // 200 Events a single shared budget rendered toggles dead). Opted-in
  // markets bypass the evidence budget: the toggle is an explicit
  // request for all of them.
  const isEvent = (d) => (d.kind || 'Market') === 'Event'
  nodeRows.sort((a, b) => {
    const ae = isEvent(a) ? 1 : 0
    const be = isEvent(b) ? 1 : 0
    return be - ae || (degree[b.id] ?? 0) - (degree[a.id] ?? 0)
  })
  if (cap != null) {
    const evts = nodeRows.filter(isEvent).slice(0, cap)
    let rest = nodeRows.filter((d) => !isEvent(d))
    let extra = []
    if (showMarkets) {
      extra = rest.filter((d) => (d.kind || 'Market') === 'Market')
      rest = rest.filter((d) => (d.kind || 'Market') !== 'Market')
    }
    rest = rest.slice(0, Math.floor(cap / 2))
    nodeRows = evts.concat(rest, extra)
  }
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
    // Uncollapsed Hypothesis nodes (single-endpoint layer-1, layer-2
    // metas) wear their relation color instead of the generic purple.
    const color = kind === 'Hypothesis'
      ? HYPOTHESIS_COLORS[hypothesisType(d.props ?? {})]
      : KIND_COLORS[kind] ?? '#6b7fa3'
    return {
      id: d.id,
      label: String(d.label ?? d.id),
      short: short(String(d.label ?? d.id)),
      kind,
      color,
      val: Math.round((1 + Math.min(deg, 12) / 3) * 100) / 100,
      deg,
      neighbors: [...new Set(neighbours[d.id] ?? [])].filter((m) => kept.has(m)),
      props: d.props ?? {},
      marketInfo: marketInfo[d.id] ?? { count: 0, lead: null },
    }
  })
  const totalLinks = raw.filter((d) => 'source' in d).length
  // The Event-to-Event subject mesh (one edge per pair per shared
  // subject) is always filtered: it is a full mesh that buries the
  // hypothesis links. The count is reported for transparency.
  const subjectLinksHidden = raw.filter(
    (d) => 'source' in d && (d.label ?? '') === 'shares-subject-with').length
  const links = raw
    .filter((d) => 'source' in d && kept.has(d.source) && kept.has(d.target))
    .filter((d) => (d.label ?? '') !== 'shares-subject-with')
    .map((d) => {
      // Layer-2 meta links (hypothesis supports hypothesis) wear the
      // citing hypothesis's relation color instead of generic purple.
      let color = EDGE_COLORS[d.label] ?? '#999999'
      if (d.label === 'supports' && (kindOf[d.source] ?? '') === 'Hypothesis') {
        color = HYPOTHESIS_COLORS[hypothesisType(rawById[d.source]?.props ?? {})]
      }
      return {
        source: d.source,
        target: d.target,
        rel: d.label ?? '',
        color,
        w: d.label === 'shares-subject-with' ? 3 : 1,
      }
    })
    .concat(hypothesisLinks.filter((l) => kept.has(l.source) && kept.has(l.target)))
  // Neighbours mirror the rendered links exactly, so the Details tab
  // lists what the eye sees. Collapsed hypotheses render as
  // Event->Event edges with no raw counterpart, so the raw pair map
  // above misses those endpoints (the subject mesh stays excluded:
  // it never reaches the rendered links).
  const renderedNbrs = {}
  for (const l of links) {
    ;(renderedNbrs[l.source] ??= []).push(l.target)
    ;(renderedNbrs[l.target] ??= []).push(l.source)
  }
  for (const n of nodes) n.neighbors = [...new Set(renderedNbrs[n.id] ?? [])]
  return { nodes, links, totalNodes, totalLinks, capped: totalNodes > nodes.length, marketsHidden, subjectsHidden, subjectLinksHidden, transitiveHidden }
}

// Fibonacci-lattice point on a sphere: even, deterministic globe slots
// for N events. Pure geometry like the camera helpers, tested below.
export function spherePoint(i, n, radius) {
  if (n <= 1) return { x: 0, y: radius, z: 0 }
  const golden = Math.PI * (3 - Math.sqrt(5))
  const y = 1 - (i / (n - 1)) * 2
  const r = Math.sqrt(Math.max(0, 1 - y * y))
  const theta = golden * i
  return { x: Math.cos(theta) * r * radius, y: y * radius, z: Math.sin(theta) * r * radius }
}

// Face helper for event selection: spin the globe so the node sits
// front-center. The orbit target stays locked on the globe center and
// the camera moves onto the node's radial line at the current viewing
// distance — the picked event faces the viewer with its neighbours
// around it. Pure geometry like zoomCamera so it stays testable.
export function faceNode(camera, target, pos, distance) {
  const d = Number.isFinite(distance) && distance > 0 ? distance : 500
  const px = pos.x ?? 0
  const py = pos.y ?? 0
  const pz = pos.z ?? 0
  const len = Math.hypot(px, py, pz)
  target.copy({ x: 0, y: 0, z: 0 })
  if (len <= 0) {
    camera.position.copy({ x: 0, y: 0, z: d })
    return camera.position
  }
  const s = d / len
  camera.position.copy({ x: px * s, y: py * s, z: pz * s })
  return camera.position
}

// Focus helper for the event picker: swing the orbit target onto the
// node's position while keeping the current view direction, parked at
// a fixed distance. Pure geometry like zoomCamera so it stays testable.
export function centerOnNode(camera, target, pos, distance = 250, limits = [2, 3000]) {
  const [lo, hi] = limits
  const d = Math.min(hi, Math.max(lo, distance))
  const off = camera.position.clone().sub(target)
  const len = off.length()
  if (!Number.isFinite(len) || len <= 0) {
    off.x = 0
    off.y = 0
    off.z = 1
  }
  off.setLength(d)
  target.copy({ x: pos.x ?? 0, y: pos.y ?? 0, z: pos.z ?? 0 })
  camera.position.copy(target).add(off)
  return camera.position
}

// Zoom step for the zoom buttons: move the camera toward/away from
// the orbit target. Pure geometry over {position}-shaped objects so
// the math is unit-testable without a renderer.
export function zoomCamera(camera, target, factor, limits = [2, 3000]) {
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
