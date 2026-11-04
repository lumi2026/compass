import { useEffect, useRef } from 'react'
import ForceGraph3D from '3d-force-graph'
import SpriteText from 'three-spritetext'
import * as THREE from 'three'
import { SKELETON_KINDS, centerOnNode, spherePoint, zoomCamera } from './graph'

// Globe radius in world units: 200 events sit ~55 apart on the shell.
const GLOBE_R = 220

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
}

// Vanilla 3d-force-graph (same pinned lib as the standalone viewer)
// mounted inside React: avoids the react wrapper's peer-version risk
// while keeping identical colors, labels, and controls.
export default function GraphCanvas({ nodes, links, onSelect, controlsRef }) {
  const mountRef = useRef(null)
  const graphRef = useRef(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const globeOnRef = useRef(false)
  const globeResetRef = useRef(null)

  useEffect(() => {
    const el = mountRef.current
    if (!el) return
    const Graph = ForceGraph3D({ controlType: 'orbit' })(el)
      .backgroundColor('#ffffff')
      .showNavInfo(false)
      .nodeId('id')
      .nodeVal('val')
      .nodeResolution(16)
      .nodeColor(() => '#6b7fa3')
      .nodeLabel((n) => {
        let html = `<b>${escapeHtml(n.label)}</b><br><span>${n.kind} · degree ${n.deg}</span>`
        if (n.kind === 'Event') {
          const bits = [n.props?.category, n.props?.sub_title]
            .filter(Boolean)
            .map(escapeHtml)
          if (n.marketInfo?.lead?.price != null) bits.push(`lead ${n.marketInfo.lead.price}%`)
          if (bits.length) html += `<br><span>${bits.join(' · ')}</span>`
        }
        return html
      })
      .nodeThreeObject((n) => {
        // Labels on skeleton kinds only (Events, Subjects, Hypotheses,
        // Happenings); Article/Market dots stay unlabeled for readability.
        if (!SKELETON_KINDS.includes(n.kind)) return null
        const s = new SpriteText(n.short, 6, '#111111')
        s.backgroundColor = 'rgba(255,255,255,0.85)'
        s.material.depthWrite = false
        return s
      })
      .nodeThreeObjectExtend(true)
      .linkSource('source')
      .linkTarget('target')
      .linkColor((l) => l.color)
      .linkWidth((l) => l.w)
      .linkResolution(6)
      .linkDirectionalArrowLength(4)
      .linkDirectionalArrowRelPos(1)
      .onNodeClick((n) => onSelectRef.current?.({ type: 'node', id: n.id }))
      .onLinkClick((l) => {
        const s = typeof l.source === 'object' ? l.source.id : l.source
        const t = typeof l.target === 'object' ? l.target.id : l.target
        onSelectRef.current?.({ type: 'link', source: s, target: t, rel: l.rel, hypothesis: l.hypothesis ?? null })
      })
      .onBackgroundClick(() => onSelectRef.current?.(null))
    const controls = Graph.controls()
    controls.enableDamping = true
    // Scroll zoom is multiplicative (each notch scales distance), so it
    // feels capped near the target at the default speed — double it.
    controls.zoomSpeed = 2
    // No drift, ever: the target stays locked (pan off, cursor-zoom off
    // so zooming never walks it away) and zoom-out stops just past the
    // globe shell. Both drag buttons rotate; auto-rotate below pauses
    // while dragging and resumes on release.
    controls.enablePan = false
    controls.zoomToCursor = false
    controls.minDistance = 0.5
    controls.maxDistance = GLOBE_R * 3.2
    controls.autoRotateSpeed = 0.7
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.ROTATE,
    }
    controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_ROTATE }
    graphRef.current = Graph
    const frameGlobe = () => {
      const target = Graph.controls().target
      const off = Graph.camera().position.clone().sub(target)
      if (!Number.isFinite(off.length()) || off.length() <= 0) off.set(0, 0, 1)
      off.setLength(GLOBE_R * 2.4)
      target.set(0, 0, 0)
      Graph.camera().position.copy(target).add(off)
    }
    // Reset state: pin every Event to a globe slot, release evidence to
    // float, lock the target on the center, frame, and start the spin.
    // Newcomers from later polls keep their slots via the data effect.
    const layoutGlobe = () => {
      const G = graphRef.current
      if (!G) return false
      const data = G.graphData()
      const evts = (data.nodes ?? [])
        .filter((n) => n.kind === 'Event')
        .sort((a, b) => (a.id < b.id ? -1 : 1))
      evts.forEach((node, i) => {
        const p = spherePoint(i, evts.length, GLOBE_R)
        Object.assign(node, { x: p.x, y: p.y, z: p.z, fx: p.x, fy: p.y, fz: p.z })
      })
      ;(data.nodes ?? []).forEach((n) => {
        if (n.kind !== 'Event') {
          delete n.fx
          delete n.fy
          delete n.fz
        }
      })
      G.graphData(data)
      if (typeof G.refresh === 'function') G.refresh()
      globeOnRef.current = true
      const c = G.controls()
      c.target.set(0, 0, 0)
      c.autoRotate = true
      frameGlobe()
      return true
    }
    globeResetRef.current = layoutGlobe
    const zoomBy = (factor) => {
      const G = graphRef.current
      if (!G) return
      zoomCamera(G.camera(), G.controls().target, factor)
    }
    if (controlsRef) {
      controlsRef.current = {
        zoomIn: () => zoomBy(0.75),
        zoomOut: () => zoomBy(1.33),
        reset: () => {
          layoutGlobe()
        },
        focus: (id) => {
          const G = graphRef.current
          if (!G) return
          const node = (G.graphData().nodes ?? []).find((n) => n.id === id)
          if (!node || node.x == null) return
          // Park the spin while inspecting one event; the globe stays.
          G.controls().autoRotate = false
          centerOnNode(G.camera(), G.controls().target, node)
        },
      }
    }
    const onResize = () => {
      Graph.width(el.clientWidth).height(el.clientHeight)
    }
    window.addEventListener('resize', onResize)
    onResize()
    return () => {
      window.removeEventListener('resize', onResize)
      graphRef.current = null
      if (controlsRef) controlsRef.current = null
      el.innerHTML = ''
    }
  }, [])

  // Keep node positions across polls so the layout does not restart.
  // In globe mode, (re)pin every Event to its deterministic slot so
  // newcomers from the live loop land on the shell too.
  useEffect(() => {
    const Graph = graphRef.current
    if (!Graph) return
    const prev = new Map((Graph.graphData().nodes ?? []).map((n) => [n.id, n]))
    let nextNodes = nodes.map((n) => {
      const p = prev.get(n.id)
      if (!p || p.x === undefined) return n
      const keep = { ...n, x: p.x, y: p.y, z: p.z }
      if (p.fx !== undefined) {
        keep.fx = p.fx
        keep.fy = p.fy
        keep.fz = p.fz
      }
      return keep
    })
    if (globeOnRef.current) {
      const evts = nextNodes
        .filter((n) => n.kind === 'Event')
        .sort((a, b) => (a.id < b.id ? -1 : 1))
      const byId = new Map(nextNodes.map((n) => [n.id, n]))
      evts.forEach((node, i) => {
        const p = spherePoint(i, evts.length, GLOBE_R)
        Object.assign(byId.get(node.id), { x: p.x, y: p.y, z: p.z, fx: p.x, fy: p.y, fz: p.z })
      })
      nextNodes = [...byId.values()]
    } else if (nextNodes.length > 0 && globeResetRef.current) {
      // First data arrival: the reset state IS the globe.
      Graph.nodeColor((n) => n.color).linkColor((l) => l.color)
      Graph.graphData({ nodes: nextNodes, links })
      Graph.nodeThreeObject(Graph.nodeThreeObject())
      globeResetRef.current()
      return
    }
    Graph.nodeColor((n) => n.color).linkColor((l) => l.color)
    Graph.graphData({ nodes: nextNodes, links })
    Graph.nodeThreeObject(Graph.nodeThreeObject())
  }, [nodes, links])

  return <div ref={mountRef} style={{ width: '100%', height: '100%' }} />
}
