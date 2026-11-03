import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { transformElements } from '../src/graph.js'

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
})
