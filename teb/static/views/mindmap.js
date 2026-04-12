/**
 * Mind Map View — renders goals/sub-goals as a radial/tree mind map using SVG.
 * Export: renderMindMap(containerId, goals)
 */
function renderMindMap(containerId, goals) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';

  const width = container.clientWidth || 800;
  const height = container.clientHeight || 600;

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.style.cursor = 'grab';
  svg.style.userSelect = 'none';

  const mainGroup = document.createElementNS(svgNS, 'g');
  svg.appendChild(mainGroup);
  container.appendChild(svg);

  // Build tree: find roots and children
  const byId = {};
  const children = {};
  goals.forEach(g => {
    byId[g.id] = g;
    children[g.id] = [];
  });
  const roots = [];
  goals.forEach(g => {
    if (g.parent_goal_id && byId[g.parent_goal_id]) {
      children[g.parent_goal_id].push(g);
    } else {
      roots.push(g);
    }
  });

  if (!roots.length && goals.length) {
    // Fallback: treat all as roots
    roots.push(...goals);
  }

  const statusColors = {
    drafting: '#6b7280',
    clarifying: '#3b82f6',
    decomposed: '#8b5cf6',
    in_progress: '#f59e0b',
    done: '#10b981',
  };

  // Layout: radial tree
  const nodes = [];
  const cx = width / 2;
  const cy = height / 2;

  function layoutTree(items, ox, oy, startAngle, endAngle, radius) {
    if (!items.length) return;
    const step = (endAngle - startAngle) / items.length;
    items.forEach((item, i) => {
      const angle = startAngle + step * (i + 0.5);
      const x = ox + radius * Math.cos(angle);
      const y = oy + radius * Math.sin(angle);
      nodes.push({ item, x, y, parentX: ox, parentY: oy });
      const kids = children[item.id] || [];
      if (kids.length) {
        const spread = Math.min(step, Math.PI * 0.8);
        layoutTree(kids, x, y, angle - spread / 2, angle + spread / 2, radius * 0.6);
      }
    });
  }

  if (roots.length === 1) {
    nodes.push({ item: roots[0], x: cx, y: cy, parentX: null, parentY: null });
    const kids = children[roots[0].id] || [];
    layoutTree(kids, cx, cy, 0, Math.PI * 2, Math.min(width, height) * 0.35);
  } else {
    layoutTree(roots, cx, cy, 0, Math.PI * 2, Math.min(width, height) * 0.3);
  }

  // Draw connections
  nodes.forEach(n => {
    if (n.parentX !== null) {
      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', n.parentX);
      line.setAttribute('y1', n.parentY);
      line.setAttribute('x2', n.x);
      line.setAttribute('y2', n.y);
      line.setAttribute('stroke', 'var(--border, #ccc)');
      line.setAttribute('stroke-width', '2');
      line.setAttribute('stroke-opacity', '0.6');
      mainGroup.appendChild(line);
    }
  });

  // Draw nodes
  nodes.forEach(n => {
    const g = document.createElementNS(svgNS, 'g');
    g.setAttribute('transform', `translate(${n.x}, ${n.y})`);
    g.style.cursor = 'pointer';

    const circle = document.createElementNS(svgNS, 'circle');
    const r = n.parentX === null && roots.length === 1 ? 40 : 28;
    circle.setAttribute('r', r);
    circle.setAttribute('fill', statusColors[n.item.status] || '#6b7280');
    circle.setAttribute('stroke', '#fff');
    circle.setAttribute('stroke-width', '2');
    circle.setAttribute('opacity', '0.9');
    g.appendChild(circle);

    const text = document.createElementNS(svgNS, 'text');
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dy', '0.35em');
    text.setAttribute('fill', '#fff');
    text.setAttribute('font-size', r > 30 ? '11' : '9');
    text.setAttribute('font-family', 'sans-serif');
    const title = (n.item.title || '').substring(0, 18);
    text.textContent = title.length < (n.item.title || '').length ? title + '…' : title;
    g.appendChild(text);

    // Tooltip
    const titleEl = document.createElementNS(svgNS, 'title');
    titleEl.textContent = `${n.item.title} [${n.item.status}]`;
    g.appendChild(titleEl);

    mainGroup.appendChild(g);
  });

  // Zoom and pan
  let scale = 1;
  let panX = 0, panY = 0;
  let isDragging = false, lastX = 0, lastY = 0;

  function applyTransform() {
    mainGroup.setAttribute('transform', `translate(${panX},${panY}) scale(${scale})`);
  }

  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.max(0.2, Math.min(5, scale * delta));
    // Zoom toward mouse position
    const rect = svg.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    panX = mx - (mx - panX) * (newScale / scale);
    panY = my - (my - panY) * (newScale / scale);
    scale = newScale;
    applyTransform();
  }, { passive: false });

  svg.addEventListener('mousedown', (e) => {
    isDragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    svg.style.cursor = 'grabbing';
  });

  window.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    panX += e.clientX - lastX;
    panY += e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    applyTransform();
  });

  window.addEventListener('mouseup', () => {
    isDragging = false;
    svg.style.cursor = 'grab';
  });
}

if (typeof module !== 'undefined') module.exports = { renderMindMap };
