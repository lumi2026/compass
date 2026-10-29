"""Render a graph payload as a standalone interactive 3D page.

Pure view layer: takes the `to_cytoscape` elements dict and emits a
self-contained page (pinned CDN modules: three + 3d-force-graph +
three-spritetext). Drag rotates the point of view, scroll zooms,
right-drag pans; hover shows the full title, click pins details plus
neighbours in the side panel. Labels stay sparse on the canvas (skeleton
nodes only, toggle for all). No server needed — open the file in a browser.
"""

import html
import json

KIND_COLORS = {
    "Event": "#2f6fed",
    "Market": "#6b7fa3",
    "Article": "#1f9d55",
    "Subject": "#d97706",
    "Hypothesis": "#7c3aed",
}

EDGE_COLORS = {
    "shares-subject-with": "#d92d20",
    "about": "#2f6fed",
    "mentions": "#1f9d55",
    "supports": "#7c3aed",
}

# Pinned exact versions. three comes from unpkg; the two graph libs come
# from esm.sh with ?external=three so the whole page shares the one pinned
# three module (their .mjs dists carry bare transitive imports that plain
# import maps cannot close: three-forcegraph, three-render-objects, d3-*,
# kapsule, tween, ...). All URLs below verified 200.
THREE_VERSION = "0.186.1"
SPRITETEXT_VERSION = "1.10.0"
FORCE3D_VERSION = "1.80.1"

# Node kinds that always get a text sprite; the rest label on demand.
SKELETON_KINDS = ("Event", "Subject", "Hypothesis")

SHORT_LEN = 28


def _short(label: str) -> str:
    label = " ".join(label.split())
    return label if len(label) <= SHORT_LEN else label[:SHORT_LEN - 1] + "…"


def render_page(payload: dict, title: str = "Compass graph") -> str:
    """Build a standalone interactive 3D page for a `to_cytoscape` payload.

    Nodes with no link are dropped: an event graph shows relationships,
    and isolated nodes add only clutter. Node size scales with degree.
    """
    raw = [dict(el.get("data", {})) for el in payload.get("elements", [])]
    degree: dict[str, int] = {}
    neighbours: dict[str, list[str]] = {}
    for d in raw:
        if "source" in d:
            degree[d["source"]] = degree.get(d["source"], 0) + 1
            degree[d["target"]] = degree.get(d["target"], 0) + 1
            neighbours.setdefault(d["source"], []).append(d["target"])
            neighbours.setdefault(d["target"], []).append(d["source"])
    nodes, links = [], []
    for d in raw:
        if "source" not in d:
            if d.get("id") not in degree:
                continue
            kind = d.get("kind") or "Market"
            deg = degree[d["id"]]
            nodes.append({
                "id": d["id"],
                "label": str(d.get("label") or d["id"]),
                "short": _short(str(d.get("label") or d["id"])),
                "kind": kind,
                "color": KIND_COLORS.get(kind, "#6b7fa3"),
                "val": round(1 + min(deg, 12) / 3, 2),
                "deg": deg,
                "neighbors": sorted(set(neighbours.get(d["id"], []))),
            })
        else:
            rel = d.get("label", "")
            links.append({
                "source": d["source"],
                "target": d["target"],
                "rel": rel,
                "color": EDGE_COLORS.get(rel, "#999999"),
                "w": 3 if rel == "shares-subject-with" else 1,
            })
    graph_json = json.dumps({"nodes": nodes, "links": links},
                            ensure_ascii=False)
    safe_title = html.escape(title)
    skel_json = json.dumps(list(SKELETON_KINDS))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
  body {{ margin: 0; font-family: system-ui, -apple-system, sans-serif; }}
  #bar {{ padding: 10px 14px; border-bottom: 1px solid #e2e2e2;
         display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }}
  #bar h1 {{ font-size: 15px; margin: 0; font-weight: 600; }}
  #bar span {{ font-size: 12px; color: #555; }}
  #bar label {{ font-size: 12px; color: #333; }}
  #wrap {{ display: flex; height: calc(100vh - 42px); }}
  #view3d {{ flex: 1; display: block; min-width: 0; }}
  #info {{ width: 300px; border-left: 1px solid #e2e2e2; padding: 12px 14px;
           font-size: 13px; overflow-y: auto; }}
  #info h2 {{ font-size: 13px; margin: 0 0 6px; overflow-wrap: break-word; }}
  #info .kind {{ font-size: 11px; color: #555; margin-bottom: 8px; }}
  #info .nid {{ font-size: 11px; color: #777; word-break: break-all;
               margin-top: 8px; }}
  #info ul {{ margin: 6px 0; padding-left: 18px; }}
  #info li {{ margin: 2px 0; }}
  #info a {{ color: #2f6fed; text-decoration: none; cursor: pointer; }}
  #zoom {{ position: absolute; left: 12px; top: 54px; display: flex;
           flex-direction: column; gap: 4px; z-index: 5; }}
  #zoom button {{ width: 30px; height: 30px; font-size: 16px; line-height: 1;
                  border: 1px solid #ccc; background: #fff; cursor: pointer;
                  border-radius: 4px; }}
  #zoom button:hover {{ background: #f0f0f0; }}
  #legend {{ font-size: 12px; color: #555; }}
  .scene-tooltip {{ font-size: 12px; max-width: 320px; }}
  #err {{ display: none; margin: 20px; padding: 12px 16px; font-size: 14px;
          border: 1px solid #d92d20; border-radius: 6px; }}
</style>
<script type="importmap">
{{"imports": {{
  "three": "https://unpkg.com/three@{THREE_VERSION}/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@{THREE_VERSION}/examples/jsm/",
  "three/examples/jsm/": "https://unpkg.com/three@{THREE_VERSION}/examples/jsm/",
  "three/webgpu": "https://unpkg.com/three@{THREE_VERSION}/build/three.webgpu.js",
  "three/tsl": "https://unpkg.com/three@{THREE_VERSION}/build/three.tsl.js",
  "three-spritetext": "https://esm.sh/three-spritetext@{SPRITETEXT_VERSION}?external=three",
  "3d-force-graph": "https://esm.sh/3d-force-graph@{FORCE3D_VERSION}?external=three"
}}}}
</script>
</head>
<body>
<div id="bar"><h1>{safe_title}</h1>
<span id="counts"></span><span id="legend">blue=Event grey=Market
green=Article amber=Subject purple=Hypothesis red=shares-subject —
drag rotates, scroll zooms, hover for title, click for details</span>
<label><input type="checkbox" id="allLabels"> all labels</label></div>
<div id="wrap"><div id="view3d"></div>
<aside id="info"><h2>Details</h2><p>Click a node or link.</p></aside></div>
<div id="zoom"><button id="zin" title="zoom in">+</button><button id="zout" title="zoom out">&minus;</button><button id="zreset" title="reset view">&#10226;</button></div>
<div id="err"></div>
<script type="module">
import ForceGraph3D from '3d-force-graph';
import SpriteText from 'three-spritetext';

const errBox = document.getElementById('err');
function fail(msg) {{
  errBox.style.display = 'block';
  errBox.textContent = msg;
}}
window.addEventListener('error', e => fail('Page error: ' + (e.message || e.type)));
window.addEventListener('unhandledrejection', e => fail('Load error: ' + (e.reason && e.reason.message || e.reason)));

const graphData = {graph_json};
const SKELETON = new Set({skel_json});
const info = document.getElementById('info');
let booted = false;
setTimeout(() => {{ if (!booted) fail('3D libraries did not load — check network access to unpkg.com.'); }}, 12000);

let hoverNode = null, hoverLink = null, selected = null, fitted = false;
let showAll = false;
const reduceMotion = window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const Graph = ForceGraph3D({{ controlType: 'orbit' }})
  (document.getElementById('view3d'))
  .graphData(graphData)
  .backgroundColor('#ffffff')
  .showNavInfo(false)
  .nodeId('id')
  .nodeVal('val')
  .nodeResolution(16)
  .nodeColor(n => n === selected ? '#111111'
    : n === hoverNode ? '#000000' : n.color)
  .nodeLabel(n => `<b>${{escapeHtml(n.label)}}</b><br><span>${{n.kind}} · degree ${{n.deg}}</span>`)
  .nodeThreeObject(n => {{
    if (!showAll && !SKELETON.has(n.kind)) return null;
    const s = new SpriteText(n.short, 4, '#111111');
    s.backgroundColor = 'rgba(255,255,255,0.85)';
    s.material.depthWrite = false;
    return s;
  }})
  .nodeThreeObjectExtend(true)
  .linkSource('source')
  .linkTarget('target')
  .linkColor(l => l === hoverLink ? '#111111' : l.color)
  .linkWidth(l => l === hoverLink ? 3 : l.w)
  .linkResolution(6)
  .linkDirectionalArrowLength(4)
  .linkDirectionalArrowRelPos(1)
  .linkLabel(l => {{
    const s = nodeById(l.source), t = nodeById(l.target);
    return `<b>${{escapeHtml(l.rel)}}</b><br><span>${{escapeHtml(shortOf(s))}} &rarr; ${{escapeHtml(shortOf(t))}}</span>`;
  }})
  .onNodeHover(n => {{
    hoverNode = n || null;
    document.body.style.cursor = n ? 'pointer' : null;
    Graph.nodeColor(Graph.nodeColor());
  }})
  .onLinkHover(l => {{
    hoverLink = l || null;
    document.body.style.cursor = l ? 'pointer' : null;
    Graph.linkWidth(Graph.linkWidth());
    Graph.linkColor(Graph.linkColor());
  }})
  .onNodeClick(showNode)
  .onLinkClick(showLink)
  .onBackgroundClick(() => {{
    selected = null;
    Graph.nodeColor(Graph.nodeColor());
    info.innerHTML = '<h2>Details</h2><p>Click a node or link.</p>';
  }})
  .onEngineStop(() => {{
    if (!fitted) {{
      fitted = true;
      Graph.zoomToFit(reduceMotion ? 0 : 900, 70);
    }}
  }});
if (Graph.linkHoverPrecision) Graph.linkHoverPrecision(5);
Graph.controls().enableDamping = true;
booted = true;

const byId = {{}};
graphData.nodes.forEach(n => {{ byId[n.id] = n; }});
function nodeById(ref) {{
  return (ref && typeof ref === 'object') ? ref : byId[ref];
}}
function shortOf(ref) {{
  const n = nodeById(ref);
  return n ? (n.short || n.label || n.id) : String(ref);
}}
function escapeHtml(s) {{
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
}}
document.getElementById('counts').textContent =
  graphData.nodes.length + ' nodes, ' + graphData.links.length + ' links';

// Zoom controls.
function zoomBy(f) {{
  const cam = Graph.cameraPosition();
  Graph.cameraPosition(
    {{ x: cam.x * f, y: cam.y * f, z: cam.z * f }}, undefined,
    reduceMotion ? 0 : 300);
}}
document.getElementById('zin').onclick = () => zoomBy(1 / 1.3);
document.getElementById('zout').onclick = () => zoomBy(1.3);
document.getElementById('zreset').onclick = () =>
  Graph.zoomToFit(reduceMotion ? 0 : 600, 70);
document.getElementById('allLabels').onchange = e => {{
  showAll = e.target.checked;
  Graph.nodeThreeObject(Graph.nodeThreeObject());
}};

function focusNode(n) {{
  const dist = 70, cam = Graph.cameraPosition();
  const dx = cam.x - n.x, dy = cam.y - n.y, dz = cam.z - n.z;
  const len = Math.hypot(dx, dy, dz) || 1;
  Graph.cameraPosition(
    {{ x: n.x + dx / len * dist, y: n.y + dy / len * dist,
      z: n.z + dz / len * dist }},
    n, reduceMotion ? 0 : 600);
}}
function wireLinks() {{
  info.querySelectorAll('a[data-id]').forEach(a => {{
    a.onclick = () => {{
      const n = byId[a.getAttribute('data-id')];
      if (n && n.x !== undefined) focusNode(n);
    }};
  }});
}}
function showNode(n) {{
  selected = n;
  Graph.nodeColor(Graph.nodeColor());
  const nbs = (n.neighbors || []).map(m => {{
    const nd = nodeById(m);
    const title = nd ? (nd.label || nd.id) : String(m);
    const id = nd ? nd.id : m;
    const kind = nd ? nd.kind : '';
    return `<li><a data-id="${{escapeHtml(id)}}">${{escapeHtml(title)}}</a> ` +
      `<span style="color:#777">[${{kind}}]</span></li>`;
  }});
  info.innerHTML = `<h2>${{escapeHtml(n.label)}}</h2>` +
    `<div class="kind">${{n.kind}} · degree ${{n.deg}}</div>` +
    (nbs.length ? '<div>Neighbours:</div><ul>' + nbs.join('') + '</ul>'
               : '<p>No links.</p>') +
    `<div class="nid">${{escapeHtml(n.id)}}</div>`;
  wireLinks();
}}
function showLink(l) {{
  const s = nodeById(l.source), t = nodeById(l.target);
  const sTitle = s ? (s.label || s.id) : String(l.source);
  const tTitle = t ? (t.label || t.id) : String(l.target);
  const sId = s ? s.id : l.source, tId = t ? t.id : l.target;
  info.innerHTML = `<h2>${{escapeHtml(l.rel)}}</h2>` +
    '<div class="kind">link</div>' +
    `<div><a data-id="${{escapeHtml(sId)}}">${{escapeHtml(sTitle)}}</a> &rarr; ` +
    `<a data-id="${{escapeHtml(tId)}}">${{escapeHtml(tTitle)}}</a></div>` +
    `<div class="nid">${{escapeHtml(String(sId))}} &rarr; ${{escapeHtml(String(tId))}}</div>`;
  wireLinks();
}}
</script>
</body>
</html>
"""
