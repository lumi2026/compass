import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { eventSummary, transformElements, zoomCamera } from '../src/graph.js'

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
  it('hides Market nodes by default and reports them', () => {
    const v = transformElements(elements, 200)
    assert.ok(!v.nodes.some((n) => n.kind === 'Market'))
    assert.equal(v.marketsHidden, 2)
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

  it('restores markets with showMarkets', () => {
    const v = transformElements(elements, 200, { showMarkets: true })
    assert.equal(v.nodes.filter((n) => n.kind === 'Market').length, 2)
    assert.equal(v.marketsHidden, 0)
    assert.equal(v.nodes.find((n) => n.id === 'kalshi:A').deg, 3)
  })

  it('pins Events first under the cap', () => {
    const v = transformElements(elements, 1)
    assert.deepEqual(v.nodes.map((n) => n.id), ['kalshi:A'])
    assert.ok(v.capped)
  })

  it('hides Subject nodes by default and restores them on request', () => {
    const els = [
      NM('kalshi:A', 'Event', 'A'),
      NM('subject:growth', 'Subject', 'growth 2034'),
      NM('article:1', 'Article', 'story'),
      E('article:1', 'subject:growth', 'mentions'),
      E('article:1', 'kalshi:A', 'about'),
    ]
    const v = transformElements(els, 200)
    assert.ok(!v.nodes.some((n) => n.kind === 'Subject'))
    assert.equal(v.subjectsHidden, 1)
    // article and event stay linked through the visible about edge
    assert.ok(v.nodes.some((n) => n.id === 'article:1'))
    const open = transformElements(els, 200, { showSubjects: true })
    assert.ok(open.nodes.some((n) => n.id === 'subject:growth'))
    assert.equal(open.subjectsHidden, 0)
  })
})

const NM = (id, kind, label = id, props = {}) => ({ data: { id, kind, label, props } })

// Event with meta, two markets (one priced), one article, one hypothesis.
const rich = [
  NM('kalshi:A', 'Event', 'A', { category: 'Elections', series_ticker: 'KXA', sub_title: 'Soon' }),
  NM('market:A-1', 'Market', 'Yes wins [A-1]', { price_cents: 63 }),
  NM('market:A-2', 'Market', 'No wins [A-2]', {}),
  NM('article:1', 'Article', 'story one'),
  NM('hypothesis:x', 'Hypothesis', 'hunch'),
  E('market:A-1', 'kalshi:A', 'about'),
  E('market:A-2', 'kalshi:A', 'about'),
  E('article:1', 'kalshi:A', 'about'),
  E('hypothesis:x', 'kalshi:A', 'supports'),
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
  E('hypothesis:h1', 'kalshi:A', 'supports'),
  E('hypothesis:h1', 'kalshi:B', 'supports'),
  E('hypothesis:h1', 'article:1', 'supports'),
  E('hypothesis:h1', 'article:2', 'supports'),
  E('audit:a1', 'hypothesis:h1', 'challenges'),
  E('hypothesis:h2', 'kalshi:A', 'supports'),
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
})
