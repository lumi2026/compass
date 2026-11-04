import { useEffect, useRef } from 'react'
import ForceGraph3D from '3d-force-graph'
import SpriteText from 'three-spritetext'
import * as THREE from 'three'
import { SKELETON_KINDS, centerOnNode, zoomCamera } from './graph'

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
          if (n.marketInfo?.lead?.price != null) bits.push(`lead ${n.marketInfo.lead.price}¢`)
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
    // Left-drag pans the graph in any direction; right-drag rotates.
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.ROTATE,
    }
    controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_ROTATE }
    graphRef.current = Graph
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
          const G = graphRef.current
          if (G && typeof G.zoomToFit === 'function') G.zoomToFit(500)
        },
        focus: (id) => {
          const G = graphRef.current
          if (!G) return
          const node = (G.graphData().nodes ?? []).find((n) => n.id === id)
          if (!node || node.x == null) return
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
  useEffect(() => {
    const Graph = graphRef.current
    if (!Graph) return
    const prev = new Map((Graph.graphData().nodes ?? []).map((n) => [n.id, n]))
    const nextNodes = nodes.map((n) => {
      const p = prev.get(n.id)
      return p && p.x !== undefined ? { ...n, x: p.x, y: p.y, z: p.z } : n
    })
    Graph.nodeColor((n) => n.color).linkColor((l) => l.color)
    Graph.graphData({ nodes: nextNodes, links })
    Graph.nodeThreeObject(Graph.nodeThreeObject())
  }, [nodes, links])

  return <div ref={mountRef} style={{ width: '100%', height: '100%' }} />
}
