import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import taxonomy from '../../shared/hypothesis_taxonomy.json' with { type: 'json' }
import { EDGE_COLORS, HYPOTHESIS_COLORS, HYPOTHESIS_DESCRIPTIONS, HYPOTHESIS_TYPES, KIND_COLORS, LEGACY_MODE_TYPE, centerOnNode, eventSummary, faceNode, hypothesisType, spherePoint, transformElements, zoomCamera } from '../src/graph.js'

class V {
  constructor(x = 0, y = 0, z = 0) {
    this.x = x
    this.y = y
    this.z = z
  }
  clone() {
    return new V(this.x, this.y, this.z)
  }
  sub(v) {
    this.x -= v.x
    this.y -= v.y
    this.z -= v.z
    return this
  }
  multiplyScalar(s) {
    this.x *= s
    this.y *= s
    this.z *= s
    return this
  }
  length() {
    return Math.hypot(this.x, this.y, this.z)
  }
  setLength(l) {
    return this.multiplyScalar(l / this.length())
  }
  copy(v) {
    this.x = v.x
    this.y = v.y
    this.z = v.z
    return this
  }
  add(v) {
    this.x += v.x
    this.y += v.y
    this.z += v.z
    return this
  }
}

describe('zoomCamera', () => {
  it('moves toward and away from the target', () => {
    const cam = { position: new V(0, 0, 100) }
    zoomCamera(cam, new V(0, 0, 0), 0.75)
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [0, 0, 75])
    zoomCamera(cam, new V(0, 0, 0), 2)
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [0, 0, 150])
  })

  it('orbits an offset target and clamps range', () => {
    const cam = { position: new V(10, 0, 100) }
    zoomCamera(cam, new V(10, 0, 0), 0.5)
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [10, 0, 50])
    zoomCamera(cam, new V(10, 0, 0), 100)
    assert.equal(cam.position.z, 3000)
  })

  it('leaves a degenerate camera alone', () => {
    const cam = { position: new V(5, 5, 5) }
    zoomCamera(cam, new V(5, 5, 5), 0.5)
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [5, 5, 5])
  })
})

describe('spherePoint', () => {
  it('places every slot on the shell', () => {
    for (let i = 0; i < 200; i += 7) {
      const p = spherePoint(i, 200, 220)
      assert.ok(Math.abs(Math.hypot(p.x, p.y, p.z) - 220) < 1e-6)
    }
  })

  it('is deterministic and spreads neighbors apart', () => {
    const a = spherePoint(3, 200, 220)
    const b = spherePoint(3, 200, 220)
    assert.deepEqual(a, b)
    const c = spherePoint(4, 200, 220)
    assert.ok(Math.hypot(a.x - c.x, a.y - c.y, a.z - c.z) > 10)
  })

  it('handles a single node without crashing', () => {
    const p = spherePoint(0, 1, 220)
    assert.equal(Math.hypot(p.x, p.y, p.z), 220)
  })
})

describe('centerOnNode', () => {
  it('swings the orbit target onto the node at a fixed distance', () => {
    const cam = { position: new V(0, 0, 100) }
    centerOnNode(cam, new V(0, 0, 0), { x: 10, y: 20, z: 30 })
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [10, 20, 280])
  })

  it('recovers when the camera sits exactly on the target', () => {
    const cam = { position: new V(5, 5, 5) }
    centerOnNode(cam, new V(5, 5, 5), { x: 1, y: 2, z: 3 })
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [1, 2, 253])
  })
})

describe('faceNode', () => {
  it('puts the camera on the node radial at the same distance, target centered', () => {
    const cam = { position: new V(100, 200, 300) }
    const tgt = new V(10, 10, 10)
    faceNode(cam, tgt, { x: 220, y: 0, z: 0 }, 528)
    assert.deepEqual([tgt.x, tgt.y, tgt.z], [0, 0, 0])
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [528, 0, 0])
  })

  it('scales an off-axis node onto the viewing sphere', () => {
    const cam = { position: new V(0, 0, 528) }
    const tgt = new V(0, 0, 0)
    faceNode(cam, tgt, { x: 0, y: 110, z: 110 }, 440)
    const s = 440 / Math.hypot(0, 110, 110)
    assert.ok(Math.abs(cam.position.y - 110 * s) < 1e-9)
    assert.ok(Math.abs(cam.position.z - 110 * s) < 1e-9)
    assert.equal(cam.position.x, 0)
  })

  it('falls back to front-on when the node sits at the center', () => {
    const cam = { position: new V(1, 2, 3) }
    faceNode(cam, new V(9, 9, 9), { x: 0, y: 0, z: 0 }, 528)
    assert.deepEqual([cam.position.x, cam.position.y, cam.position.z], [0, 0, 528])
  })

  it('falls back to a sane distance on bad input', () => {
    const cam = { position: new V(1, 2, 3) }
    faceNode(cam, new V(0, 0, 0), { x: 220, y: 0, z: 0 }, -5)
    assert.ok(Math.abs(cam.position.x - 500) < 1e-9)
    assert.equal(cam.position.y, 0)
    assert.equal(cam.position.z, 0)
  })
})

const N = (id, kind, label = id) => ({ data: { id, kind, label } })
const E = (source, target, label = 'relates-to') => ({ data: { source, target, label } })

// One event with two markets and one linked article.
const elements = [
  N('kalshi:A', 'Event', 'A'),
  N('market:A-1', 'Market'),
  N('market:A-2', 'Market'),
  N('article:1', 'Article', 'story'),
  E('market:A-1', 'kalshi:A', 'about'),
  E('market:A-2', 'kalshi:A', 'about'),
  E('article:1', 'kalshi:A', 'about'),
]

describe('events-first transform', () => {
  it('always hides Market nodes', () => {
    const v = transformElements(elements, 200)
    assert.ok(!v.nodes.some((n) => n.kind === 'Market'))
  })

  it('sizes Events by visible edges only, not hidden markets', () => {
    const v = transformElements(elements, 200)
    const ev = v.nodes.find((n) => n.id === 'kalshi:A')
    assert.equal(ev.deg, 1) // the article edge; market edges are hidden
  })

  it('keeps unlinked Events (the isolated control is data)', () => {
    const v = transformElements([N('kalshi:B', 'Event', 'B')], 200)
    assert.ok(v.nodes.some((n) => n.id === 'kalshi:B'))
  })

  it('pins Events first under the cap', () => {
    const v = transformElements(elements, 1)
    assert.deepEqual(v.nodes.map((n) => n.id), ['kalshi:A'])
    assert.ok(v.capped)
  })

  it('always hides Subject nodes', () => {
    const els = [
      NM('kalshi:A', 'Event', 'A'),
      NM('subject:growth', 'Subject', 'growth 2034'),
      NM('article:1', 'Article', 'story'),
      E('article:1', 'subject:growth', 'mentions'),
      E('article:1', 'kalshi:A', 'about'),
    ]
    const v = transformElements(els, 200)
    assert.ok(!v.nodes.some((n) => n.kind === 'Subject'))
    // article and event stay linked through the visible about edge
    assert.ok(v.nodes.some((n) => n.id === 'article:1'))
  })
})

describe('article node identity', () => {
  it('wears the bright article color, distinct from edge greens', () => {
    const v = transformElements(elements, 200)
    const art = v.nodes.find((n) => n.id === 'article:1')
    assert.equal(art.color, KIND_COLORS.Article)
    assert.notEqual(KIND_COLORS.Article, EDGE_COLORS.mentions)
  })
})

const NM = (id, kind, label = id, props = {}) => ({ data: { id, kind, label, props } })

// Event with meta, two folded markets (one priced), one article, one hypothesis.
const rich = [
  NM('kalshi:A', 'Event', 'A', { category: 'Elections', series_ticker: 'KXA', sub_title: 'Soon', markets: { 'A-1': { title: 'Yes wins [A-1]', price_cents: 63 }, 'A-2': { title: 'No wins [A-2]' } } }),
  NM('market:A-1', 'Market', 'Yes wins [A-1]', { price_cents: 63 }),
  NM('market:A-2', 'Market', 'No wins [A-2]', {}),
  NM('article:1', 'Article', 'story one'),
  NM('hypothesis:x', 'Hypothesis', 'hunch'),
  E('market:A-1', 'kalshi:A', 'about'),
  E('market:A-2', 'kalshi:A', 'about'),
  E('article:1', 'kalshi:A', 'about'),
  E('hypothesis:x', 'kalshi:A', 'asserts'),
]

describe('event description', () => {
  it('rolls markets up even while they stay hidden', () => {
    const v = transformElements(rich, 200)
    assert.ok(!v.nodes.some((n) => n.kind === 'Market'))
    const ev = v.nodes.find((n) => n.id === 'kalshi:A')
    assert.deepEqual(ev.marketInfo, {
      count: 2,
      lead: { label: 'Yes wins [A-1]', price: 63 },
    })
  })

  it('summarizes meta, lead, articles and hypotheses', () => {
    const v = transformElements(rich, 200)
    const byId = new Map(v.nodes.map((n) => [n.id, n]))
    assert.deepEqual(eventSummary(byId.get('kalshi:A'), byId), {
      category: 'Elections',
      series: 'KXA',
      subTitle: 'Soon',
      marketCount: 2,
      lead: { label: 'Yes wins [A-1]', price: 63 },
      articleTitles: ['story one'],
      articleCount: 1,
      hypotheses: 1,
      hypothesisTitles: ['hunch'],
    })
  })

  it('summarizes bare events without crashing', () => {
    const v = transformElements([N('kalshi:B', 'Event', 'B')], 200)
    const byId = new Map(v.nodes.map((n) => [n.id, n]))
    const s = eventSummary(byId.get('kalshi:B'), byId)
    assert.equal(s.category, '')
    assert.equal(s.marketCount, 0)
    assert.equal(s.lead, null)
  })
})

// Hypothesis with two Event endpoints collapses to one edge;
// a single-endpoint one keeps its node.
const hyp = [
  NM('kalshi:A', 'Event', 'A'),
  NM('kalshi:B', 'Event', 'B'),
  NM('hypothesis:h1', 'Hypothesis', 'A x B because news'),
  NM('article:1', 'Article', 'story one'),
  NM('article:2', 'Article', 'story two'),
  NM('audit:a1', 'Audit', 'flags h1: reason here'),
  NM('hypothesis:h2', 'Hypothesis', 'lonely'),
  E('hypothesis:h1', 'kalshi:A', 'asserts'),
  E('hypothesis:h1', 'kalshi:B', 'asserts'),
  E('hypothesis:h1', 'article:1', 'cites'),
  E('hypothesis:h1', 'article:2', 'cites'),
  E('audit:a1', 'hypothesis:h1', 'challenges'),
  E('hypothesis:h2', 'kalshi:A', 'asserts'),
  E('article:1', 'kalshi:A', 'about'),
  E('article:2', 'kalshi:B', 'about'),
]

describe('hypothesis as edge', () => {
  it('collapses two-endpoint hypotheses to evidence links', () => {
    const v = transformElements(hyp, 200)
    assert.ok(!v.nodes.some((n) => n.id === 'hypothesis:h1'))
    const [l] = v.links.filter((x) => x.rel === 'hypothesizes')
    assert.deepEqual([l.source, l.target], ['kalshi:A', 'kalshi:B'])
    assert.equal(l.color, '#7c3aed')
    assert.equal(l.hypothesis.title, 'A x B because news')
    assert.deepEqual(l.hypothesis.evidence.map((a) => a.id), ['article:1', 'article:2'])
    assert.equal(l.hypothesis.challenged, true)
    assert.equal(l.hypothesis.challengeReason, 'flags h1: reason here')
  })

  it('keeps single-endpoint hypotheses as nodes', () => {
    const v = transformElements(hyp, 200)
    assert.ok(v.nodes.some((n) => n.id === 'hypothesis:h2'))
  })

  it('drops the spent audit node but keeps its challenge on the edge', () => {
    const v = transformElements(hyp, 200)
    assert.ok(!v.nodes.some((n) => n.id === 'audit:a1'))
    const [l] = v.links.filter((x) => x.rel === 'hypothesizes')
    assert.equal(l.hypothesis.challenged, true)
  })

  it('counts collapsed links in the event summary', () => {
    const v = transformElements(hyp, 200)
    const byId = new Map(v.nodes.map((n) => [n.id, n]))
    // h2 node neighbor + h1 collapsed link
    assert.equal(eventSummary(byId.get('kalshi:A'), byId, v.links).hypotheses, 2)
    assert.equal(eventSummary(byId.get('kalshi:B'), byId, v.links).hypotheses, 1)
  })

  it('lists collapsed-hypothesis endpoints as neighbours', () => {
    const v = transformElements(hyp, 200)
    const byId = new Map(v.nodes.map((n) => [n.id, n]))
    // h1 renders as an A-B edge with no raw counterpart: both ends
    // must still list each other, mirroring the visible link
    assert.ok(byId.get('kalshi:A').neighbors.includes('kalshi:B'))
    assert.ok(byId.get('kalshi:B').neighbors.includes('kalshi:A'))
  })
})

describe('transitive meta omission', () => {
  const els = [
    NM('kalshi:A', 'Event', 'A'),
    NM('kalshi:B', 'Event', 'B'),
    NM('kalshi:C', 'Event', 'C'),
    NM('hypothesis:h1', 'Hypothesis', 'A x B'),
    NM('hypothesis:h2', 'Hypothesis', 'B x C'),
    NM('hypothesis:m', 'Hypothesis', 'transitive co_mention: A x C via B',
      { mode: 'transitive', type: 'co_mention', layer: 2 }),
    NM('article:9', 'Article', 'second-hand story'),
    E('hypothesis:h1', 'kalshi:A', 'asserts'),
    E('hypothesis:h1', 'kalshi:B', 'asserts'),
    E('hypothesis:h2', 'kalshi:B', 'asserts'),
    E('hypothesis:h2', 'kalshi:C', 'asserts'),
    E('hypothesis:m', 'hypothesis:h1', 'supports'),
    E('hypothesis:m', 'hypothesis:h2', 'supports'),
    E('hypothesis:m', 'article:9', 'cites'),
  ]

  it('omits the transitive meta node and its edges', () => {
    const v = transformElements(els, 200)
    assert.ok(!v.nodes.some((n) => n.id === 'hypothesis:m'))
    assert.ok(!v.links.some((l) => l.source === 'hypothesis:m' || l.target === 'hypothesis:m'))
    assert.equal(v.transitiveHidden, 1)
  })

  it('lets the cited parents collapse into plain event links', () => {
    const v = transformElements(els, 200)
    const rels = v.links.filter((l) => l.rel === 'hypothesizes')
      .map((l) => [l.source, l.target].sort().join('x')).sort()
    assert.deepEqual(rels, ['kalshi:Axkalshi:B', 'kalshi:Bxkalshi:C'])
  })
})

describe('evidence budget', () => {
  const els = [
    NM('kalshi:A', 'Event', 'A'),
    NM('kalshi:B', 'Event', 'B'),
    NM('kalshi:C', 'Event', 'C'),
    NM('article:1', 'Article', 'story one'),
    NM('article:2', 'Article', 'story two'),
    NM('market:A-1', 'Market', 'm1'),
    E('article:1', 'kalshi:A', 'about'),
    E('article:1', 'kalshi:B', 'about'),
    E('article:2', 'kalshi:C', 'about'),
    E('market:A-1', 'kalshi:A', 'about'),
  ]

  it('reserves evidence room past a full event cap', () => {
    const v = transformElements(els, 2)
    assert.equal(v.nodes.filter((n) => n.kind === 'Event').length, 2)
    // floor(2/2) = 1 slot goes to the highest-degree article
    assert.ok(v.nodes.some((n) => n.id === 'article:1'))
    assert.ok(!v.nodes.some((n) => n.id === 'article:2'))
  })

  it('keeps markets out of the evidence budget', () => {
    const v = transformElements(els, 4)
    assert.ok(!v.nodes.some((n) => n.kind === 'Market'))
    assert.ok(v.nodes.some((n) => n.id === 'article:1'))
  })
})

describe('hypothesis types', () => {
  const typed = [
    NM('kalshi:A', 'Event', 'A'),
    NM('kalshi:B', 'Event', 'B'),
    NM('hypothesis:h1', 'Hypothesis', 'A x B in Gaza',
      { type: 'co_location', mode: 'co-location', evidence: 1 }),
    NM('article:1', 'Article', 'Gaza summit'),
    E('hypothesis:h1', 'kalshi:A', 'asserts'),
    E('hypothesis:h1', 'kalshi:B', 'asserts'),
    E('hypothesis:h1', 'article:1', 'cites'),
  ]

  it('maps explicit types, legacy modes, and unknown props', () => {
    assert.equal(hypothesisType({ type: 'co_location' }), 'co_location')
    assert.equal(hypothesisType({ mode: 'shared-subject' }), 'shared_context')
    assert.equal(hypothesisType({ mode: 'odds-correlation' }), 'co_outcome')
    assert.equal(hypothesisType({ mode: 'co-location' }), 'co_location')
    assert.equal(hypothesisType({ mode: 'transitive' }), 'shared_context')
    assert.equal(hypothesisType({}), 'co_mention')
    assert.equal(hypothesisType({ type: 'bogus' }), 'co_mention')
  })

  it('describes every relation type without identifiers', () => {
    for (const t of Object.keys(HYPOTHESIS_COLORS)) {
      assert.ok(HYPOTHESIS_DESCRIPTIONS[t])
      assert.ok(!HYPOTHESIS_DESCRIPTIONS[t].includes('co_'))
    }
  })

  it('reads the taxonomy from the file shared with the Python squad', () => {
    assert.deepEqual(HYPOTHESIS_TYPES, taxonomy.types)
    assert.deepEqual(LEGACY_MODE_TYPE, taxonomy.mode_to_type)
    assert.deepEqual(Object.keys(HYPOTHESIS_COLORS).sort(),
      [...taxonomy.types].sort())
    assert.deepEqual(Object.keys(HYPOTHESIS_DESCRIPTIONS).sort(),
      [...taxonomy.types].sort())
    for (const mode of taxonomy.detection_modes) {
      assert.ok(taxonomy.mode_to_type[mode], `${mode} needs a type`)
    }
  })

  it('colors collapsed links by relation type and carries it', () => {
    const v = transformElements(typed, 200)
    const [l] = v.links.filter((x) => x.rel === 'hypothesizes')
    assert.equal(l.color, HYPOTHESIS_COLORS.co_location)
    assert.equal(l.hypothesis.type, 'co_location')
  })

  it('colors uncollapsed hypothesis nodes by type', () => {
    const els = [
      NM('kalshi:A', 'Event', 'A'),
      NM('hypothesis:h2', 'Hypothesis', 'lonely odds',
        { type: 'co_outcome', mode: 'odds-correlation' }),
      E('hypothesis:h2', 'kalshi:A', 'asserts'),
    ]
    const v = transformElements(els, 200)
    const h2 = v.nodes.find((n) => n.id === 'hypothesis:h2')
    assert.equal(h2.color, HYPOTHESIS_COLORS.co_outcome)
  })

  it('colors hyp-to-hyp supports edges by the citing type', () => {
    const els = typed.concat([
      NM('hypothesis:h2', 'Hypothesis', 'meta', { type: 'shared_context' }),
      E('hypothesis:h2', 'hypothesis:h1', 'supports'),
    ])
    const v = transformElements(els, 200)
    // h1 stays a node (it is meta-linked, not collapsed) ...
    assert.ok(v.nodes.some((n) => n.id === 'hypothesis:h1'))
    assert.ok(v.nodes.some((n) => n.id === 'hypothesis:h2'))
    const [l] = v.links.filter((x) => x.source === 'hypothesis:h2')
    assert.equal(l.color, HYPOTHESIS_COLORS.shared_context)
  })
})

describe('subject link filter', () => {
  // derive emits the mesh in both directions, like the live graph
  const mesh = [
    NM('kalshi:A', 'Event', 'A'),
    NM('kalshi:B', 'Event', 'B'),
    E('kalshi:A', 'kalshi:B', 'shares-subject-with'),
    E('kalshi:B', 'kalshi:A', 'shares-subject-with'),
    E('kalshi:A', 'kalshi:B', 'hypothesizes'),
  ]

  it('always filters the subject mesh but keeps hypothesis links', () => {
    const v = transformElements(mesh, 200)
    assert.ok(!v.links.some((l) => l.rel === 'shares-subject-with'))
    assert.ok(v.links.some((l) => l.rel === 'hypothesizes'))
    assert.equal(v.subjectLinksHidden, 2)
  })

  it('excludes the mesh from neighbours and degree', () => {
    const meshOnly = mesh.filter(
      (el) => !('source' in el.data) || el.data.label !== 'hypothesizes',
    )
    const v = transformElements(meshOnly, 200)
    const a = v.nodes.find((n) => n.id === 'kalshi:A')
    assert.ok(!a.neighbors.includes('kalshi:B'))
    assert.equal(a.deg, 0)
  })
})
