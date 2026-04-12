/**
 * Chart Widgets — pure SVG chart rendering.
 * Exports: renderBarChart, renderLineChart, renderPieChart
 */
const Charts = {
  _defaultColors: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#6366f1', '#14b8a6'],

  _svgNS: 'http://www.w3.org/2000/svg',

  _createSvg(container, width, height) {
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return null;
    container.innerHTML = '';
    const svg = document.createElementNS(this._svgNS, 'svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.style.overflow = 'visible';
    container.appendChild(svg);
    return svg;
  },

  renderBarChart(container, data, options = {}) {
    const w = options.width || 500;
    const h = options.height || 300;
    const svg = this._createSvg(container, w, h);
    if (!svg || !data || !data.length) return;

    const title = options.title || '';
    const colors = options.colors || this._defaultColors;
    const pad = { top: title ? 40 : 20, right: 20, bottom: 50, left: 60 };
    const chartW = w - pad.left - pad.right;
    const chartH = h - pad.top - pad.bottom;

    // Title
    if (title) {
      const t = document.createElementNS(this._svgNS, 'text');
      t.setAttribute('x', w / 2);
      t.setAttribute('y', 20);
      t.setAttribute('text-anchor', 'middle');
      t.setAttribute('fill', 'var(--text, #333)');
      t.setAttribute('font-size', '14');
      t.setAttribute('font-weight', 'bold');
      t.textContent = title;
      svg.appendChild(t);
    }

    const maxVal = Math.max(...data.map(d => d.value), 1);
    const barWidth = Math.max(chartW / data.length - 8, 4);

    // Y-axis
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + chartH - (i / 4) * chartH;
      const line = document.createElementNS(this._svgNS, 'line');
      line.setAttribute('x1', pad.left);
      line.setAttribute('y1', y);
      line.setAttribute('x2', pad.left + chartW);
      line.setAttribute('y2', y);
      line.setAttribute('stroke', 'var(--border, #e5e7eb)');
      line.setAttribute('stroke-width', '0.5');
      svg.appendChild(line);

      const label = document.createElementNS(this._svgNS, 'text');
      label.setAttribute('x', pad.left - 8);
      label.setAttribute('y', y + 4);
      label.setAttribute('text-anchor', 'end');
      label.setAttribute('fill', 'var(--text-secondary, #666)');
      label.setAttribute('font-size', '10');
      label.textContent = Math.round(maxVal * i / 4);
      svg.appendChild(label);
    }

    // Bars
    data.forEach((d, i) => {
      const barH = (d.value / maxVal) * chartH;
      const x = pad.left + (i * (chartW / data.length)) + (chartW / data.length - barWidth) / 2;
      const y = pad.top + chartH - barH;

      const rect = document.createElementNS(this._svgNS, 'rect');
      rect.setAttribute('x', x);
      rect.setAttribute('y', y);
      rect.setAttribute('width', barWidth);
      rect.setAttribute('height', barH);
      rect.setAttribute('fill', colors[i % colors.length]);
      rect.setAttribute('rx', '3');

      const titleEl = document.createElementNS(this._svgNS, 'title');
      titleEl.textContent = `${d.label}: ${d.value}`;
      rect.appendChild(titleEl);
      svg.appendChild(rect);

      // X-axis label
      const label = document.createElementNS(this._svgNS, 'text');
      label.setAttribute('x', x + barWidth / 2);
      label.setAttribute('y', pad.top + chartH + 16);
      label.setAttribute('text-anchor', 'middle');
      label.setAttribute('fill', 'var(--text-secondary, #666)');
      label.setAttribute('font-size', '9');
      label.textContent = (d.label || '').substring(0, 10);
      svg.appendChild(label);
    });
  },

  renderLineChart(container, data, options = {}) {
    const w = options.width || 500;
    const h = options.height || 300;
    const svg = this._createSvg(container, w, h);
    if (!svg || !data || !data.length) return;

    const title = options.title || '';
    const colors = options.colors || this._defaultColors;
    const color = colors[0];
    const pad = { top: title ? 40 : 20, right: 20, bottom: 50, left: 60 };
    const chartW = w - pad.left - pad.right;
    const chartH = h - pad.top - pad.bottom;

    if (title) {
      const t = document.createElementNS(this._svgNS, 'text');
      t.setAttribute('x', w / 2);
      t.setAttribute('y', 20);
      t.setAttribute('text-anchor', 'middle');
      t.setAttribute('fill', 'var(--text, #333)');
      t.setAttribute('font-size', '14');
      t.setAttribute('font-weight', 'bold');
      t.textContent = title;
      svg.appendChild(t);
    }

    const maxVal = Math.max(...data.map(d => d.value), 1);

    // Y-axis grid
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + chartH - (i / 4) * chartH;
      const line = document.createElementNS(this._svgNS, 'line');
      line.setAttribute('x1', pad.left);
      line.setAttribute('y1', y);
      line.setAttribute('x2', pad.left + chartW);
      line.setAttribute('y2', y);
      line.setAttribute('stroke', 'var(--border, #e5e7eb)');
      line.setAttribute('stroke-width', '0.5');
      svg.appendChild(line);

      const label = document.createElementNS(this._svgNS, 'text');
      label.setAttribute('x', pad.left - 8);
      label.setAttribute('y', y + 4);
      label.setAttribute('text-anchor', 'end');
      label.setAttribute('fill', 'var(--text-secondary, #666)');
      label.setAttribute('font-size', '10');
      label.textContent = Math.round(maxVal * i / 4);
      svg.appendChild(label);
    }

    // Points and line
    const points = data.map((d, i) => {
      const x = pad.left + (i / Math.max(data.length - 1, 1)) * chartW;
      const y = pad.top + chartH - (d.value / maxVal) * chartH;
      return { x, y, d };
    });

    // Area fill
    const areaPath = document.createElementNS(this._svgNS, 'polygon');
    const areaPoints = [
      `${points[0].x},${pad.top + chartH}`,
      ...points.map(p => `${p.x},${p.y}`),
      `${points[points.length - 1].x},${pad.top + chartH}`
    ].join(' ');
    areaPath.setAttribute('points', areaPoints);
    areaPath.setAttribute('fill', color);
    areaPath.setAttribute('fill-opacity', '0.1');
    svg.appendChild(areaPath);

    // Line
    const polyline = document.createElementNS(this._svgNS, 'polyline');
    polyline.setAttribute('points', points.map(p => `${p.x},${p.y}`).join(' '));
    polyline.setAttribute('fill', 'none');
    polyline.setAttribute('stroke', color);
    polyline.setAttribute('stroke-width', '2');
    polyline.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(polyline);

    // Dots and labels
    points.forEach((p, i) => {
      const circle = document.createElementNS(this._svgNS, 'circle');
      circle.setAttribute('cx', p.x);
      circle.setAttribute('cy', p.y);
      circle.setAttribute('r', '4');
      circle.setAttribute('fill', color);
      circle.setAttribute('stroke', '#fff');
      circle.setAttribute('stroke-width', '2');

      const titleEl = document.createElementNS(this._svgNS, 'title');
      titleEl.textContent = `${p.d.label}: ${p.d.value}`;
      circle.appendChild(titleEl);
      svg.appendChild(circle);

      // X-axis labels (skip some if too many)
      if (data.length <= 15 || i % Math.ceil(data.length / 10) === 0) {
        const label = document.createElementNS(this._svgNS, 'text');
        label.setAttribute('x', p.x);
        label.setAttribute('y', pad.top + chartH + 16);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('fill', 'var(--text-secondary, #666)');
        label.setAttribute('font-size', '9');
        label.textContent = (p.d.label || '').substring(0, 10);
        svg.appendChild(label);
      }
    });
  },

  renderPieChart(container, data, options = {}) {
    const w = options.width || 300;
    const h = options.height || 300;
    const svg = this._createSvg(container, w, h);
    if (!svg || !data || !data.length) return;

    const title = options.title || '';
    const colors = options.colors || this._defaultColors;
    const cx = w / 2;
    const cy = (title ? h / 2 + 12 : h / 2);
    const r = Math.min(cx, cy) - 40;

    if (title) {
      const t = document.createElementNS(this._svgNS, 'text');
      t.setAttribute('x', w / 2);
      t.setAttribute('y', 18);
      t.setAttribute('text-anchor', 'middle');
      t.setAttribute('fill', 'var(--text, #333)');
      t.setAttribute('font-size', '14');
      t.setAttribute('font-weight', 'bold');
      t.textContent = title;
      svg.appendChild(t);
    }

    const total = data.reduce((s, d) => s + d.value, 0);
    if (total === 0) return;

    let startAngle = -Math.PI / 2;
    data.forEach((d, i) => {
      const sliceAngle = (d.value / total) * Math.PI * 2;
      const endAngle = startAngle + sliceAngle;

      const x1 = cx + r * Math.cos(startAngle);
      const y1 = cy + r * Math.sin(startAngle);
      const x2 = cx + r * Math.cos(endAngle);
      const y2 = cy + r * Math.sin(endAngle);
      const largeArc = sliceAngle > Math.PI ? 1 : 0;

      const path = document.createElementNS(this._svgNS, 'path');
      path.setAttribute('d', `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`);
      path.setAttribute('fill', colors[i % colors.length]);
      path.setAttribute('stroke', '#fff');
      path.setAttribute('stroke-width', '2');

      const titleEl = document.createElementNS(this._svgNS, 'title');
      titleEl.textContent = `${d.label}: ${d.value} (${Math.round(d.value / total * 100)}%)`;
      path.appendChild(titleEl);
      svg.appendChild(path);

      // Label
      const midAngle = startAngle + sliceAngle / 2;
      const labelR = r * 0.65;
      const lx = cx + labelR * Math.cos(midAngle);
      const ly = cy + labelR * Math.sin(midAngle);

      if (sliceAngle > 0.3) {
        const label = document.createElementNS(this._svgNS, 'text');
        label.setAttribute('x', lx);
        label.setAttribute('y', ly + 4);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('fill', '#fff');
        label.setAttribute('font-size', '10');
        label.setAttribute('font-weight', 'bold');
        label.textContent = Math.round(d.value / total * 100) + '%';
        svg.appendChild(label);
      }

      startAngle = endAngle;
    });

    // Legend
    const legendY = cy + r + 20;
    data.forEach((d, i) => {
      const lx = 10 + (i % 3) * (w / 3);
      const ly = legendY + Math.floor(i / 3) * 16;
      const rect = document.createElementNS(this._svgNS, 'rect');
      rect.setAttribute('x', lx);
      rect.setAttribute('y', ly - 8);
      rect.setAttribute('width', 10);
      rect.setAttribute('height', 10);
      rect.setAttribute('rx', 2);
      rect.setAttribute('fill', colors[i % colors.length]);
      svg.appendChild(rect);

      const label = document.createElementNS(this._svgNS, 'text');
      label.setAttribute('x', lx + 14);
      label.setAttribute('y', ly);
      label.setAttribute('fill', 'var(--text-secondary, #666)');
      label.setAttribute('font-size', '9');
      label.textContent = (d.label || '').substring(0, 15);
      svg.appendChild(label);
    });
  }
};

if (typeof module !== 'undefined') module.exports = Charts;
