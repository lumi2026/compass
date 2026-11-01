import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchGraph } from './api'

export function useGraph({ at = '', pollMs = 5000 } = {}) {
  const [elements, setElements] = useState([])
  const [revision, setRevision] = useState(0)
  const [counts, setCounts] = useState({ nodes: 0, edges: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [stale, setStale] = useState(false)
  const etagRef = useRef('')
  const atRef = useRef(at)
  atRef.current = at

  const tick = useCallback(async () => {
    try {
      const res = await fetchGraph({ at: atRef.current, etag: etagRef.current })
      if (res.notModified) {
        setStale(false)
        setError('')
        return
      }
      if (res.etag) etagRef.current = res.etag
      setElements(res.data.elements ?? [])
      setRevision(res.data.revision ?? 0)
      setCounts({
        nodes: res.data.nodes_count ?? 0,
        edges: res.data.edges_count ?? 0,
      })
      setStale(false)
      setError('')
    } catch (e) {
      setError(String(e.message ?? e))
      setStale(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    etagRef.current = ''
    tick()
    const id = setInterval(tick, pollMs)
    return () => clearInterval(id)
  }, [at, pollMs, tick])

  return { elements, revision, counts, loading, error, stale, refresh: tick }
}
