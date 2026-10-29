"""Render a graph payload as a standalone Cytoscape.js HTML page.

Pure view layer: takes the `to_cytoscape` elements dict and emits a
self-contained page (CDN cytoscape.js, cose layout, color by node kind,
edge labels). No server needed — open the file in a browser.
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


def render_page(payload: dict, title: str = "Compass graph") -> str:
    """Build a standalone HTML page for a `to_cytoscape` payload."""
    elements = payload.get("elements", [])
    data = json.dumps(elements)
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
  body {{ margin: 0; font-family: system-ui, -apple-system, sans-serif; }}
  #bar {{ padding: 10px 14px; border-bottom: 1px solid #e2e2e2;
         display: flex; gap: 16px; align-items: baseline; }}
  #bar h1 {{ font-size: 15px; margin: 0; font-weight: 600; }}
  #bar span {{ font-size: 12px; color: #555; }}
  #cy {{ width: 100vw; height: calc(100vh - 41px); display: block; }}
  #legend {{ font-size: 12px; color: #555; }}
</style>
<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
</head>
<body>
<div id="bar"><h1>{safe_title}</h1>
<span id="counts"></span><span id="legend">blue=Event grey=Market
green=Article amber=Subject purple=Hypothesis red=shares-subject</span></div>
<div id="cy"></div>
<script>
const elements = {data};
const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements: elements.map(el => {{
    const d = Object.assign({{}}, el.data);
    if (el.data.source === undefined) {{
      const kind = d.kind || 'Market';
      d.color = {json.dumps(KIND_COLORS)}.hasOwnProperty(kind)
        ? {json.dumps(KIND_COLORS)}[kind] : '#6b7fa3';
      d.shape = kind === 'Event' ? 'hexagon'
        : kind === 'Subject' ? 'diamond'
        : kind === 'Hypothesis' ? 'triangle' : 'ellipse';
    }} else {{
      const rel = d.label || '';
      d.color = {json.dumps(EDGE_COLORS)}.hasOwnProperty(rel)
        ? {json.dumps(EDGE_COLORS)}[rel] : '#999';
      d.width = rel === 'shares-subject-with' ? 3 : 1;
    }}
    return {{ data: d }};
  }}),
  style: [
    {{ selector: 'node', style: {{
      'background-color': 'data(color)', 'label': 'data(label)',
      'font-size': 9, 'text-wrap': 'wrap', 'text-max-width': 120,
      'width': 28, 'height': 28, 'shape': 'data(shape)',
      'color': '#111', 'text-valign': 'bottom', 'text-halign': 'center' }} }},
    {{ selector: 'edge', style: {{
      'line-color': 'data(color)', 'target-arrow-color': 'data(color)',
      'target-arrow-shape': 'triangle', 'label': 'data(label)',
      'font-size': 8, 'color': '#555', 'width': 'data(width)',
      'curve-style': 'bezier' }} }}
  ],
  layout: {{ name: 'cose', animate: false }}
}});
document.getElementById('counts').textContent =
  elements.filter(e => e.data.source === undefined).length + ' nodes, ' +
  elements.filter(e => e.data.source !== undefined).length + ' edges';
</script>
</body>
</html>
"""
