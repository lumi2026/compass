// Same-origin by default (Vite dev proxy forwards to :8090).
// VITE_GRAPH_URL overrides for direct backend access.
// VITE_GRAPH_TOKEN (optional) pre-wires the future Bearer gate.
const BASE = (import.meta.env.VITE_GRAPH_URL ?? '').replace(/\/$/, '')
const TOKEN = import.meta.env.VITE_GRAPH_TOKEN ?? ''

function headers(extra = {}) {
  const h = { ...extra }
  if (TOKEN) h.Authorization = `Bearer ${TOKEN}`
  return h
}

export async function fetchHealth() {
  const r = await fetch(`${BASE}/health`, { headers: headers() })
  if (!r.ok) throw new Error(`health ${r.status}`)
  return r.json()
}

// When `at` is set we skip If-None-Match: ETag tracks the global
// revision, not the filtered slice, so a 304 would return the wrong view.
export async function fetchGraph({ at = '', etag = '' } = {}) {
  const q = at ? `?at=${encodeURIComponent(at)}` : ''
  const h = {}
  if (etag && !at) h['If-None-Match'] = etag
  const r = await fetch(`${BASE}/graph${q}`, { headers: headers(h) })
  if (r.status === 304) return { notModified: true }
  if (!r.ok) throw new Error(`graph ${r.status}`)
  const data = await r.json()
  return { data, etag: r.headers.get('etag') ?? '' }
}
