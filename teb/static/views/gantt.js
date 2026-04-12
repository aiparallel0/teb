/**
 * Gantt Chart View (Phase 3)
 * Renders tasks as horizontal bars on a timeline with dependency arrows.
 */
const GanttView = {
  STATUS_COLORS: {
    todo: '#6b7280', in_progress: '#3b82f6', executing: '#f59e0b',
    done: '#10b981', failed: '#ef4444', skipped: '#9ca3af',
  },

  _zoom: 'week',

  render(tasks, container, options = {}) {
    container.innerHTML = '';
    const self = this;
    const onTaskClick = options.onTaskClick || null;
    const onStatusChange = options.onStatusChange || null;
    const sorted = [...tasks].sort((a, b) => (a.order_index || 0) - (b.order_index || 0));

    const wrapper = document.createElement('div');
    wrapper.className = 'gantt-view';

    // --- Zoom controls ---
    const header = document.createElement('div');
    header.className = 'gantt-header';
    const zoomWrap = document.createElement('div');
    zoomWrap.className = 'gantt-zoom-controls';
    ['day', 'week', 'month'].forEach(z => {
      const btn = document.createElement('button');
      btn.textContent = z.charAt(0).toUpperCase() + z.slice(1);
      btn.className = 'btn-secondary btn-sm' + (self._zoom === z ? ' active' : '');
      btn.addEventListener('click', () => { self._zoom = z; self.render(tasks, container, options); });
      zoomWrap.appendChild(btn);
    });
    header.appendChild(zoomWrap);
    wrapper.appendChild(header);

    if (!sorted.length) {
      wrapper.innerHTML += '<div class="empty-state-large"><div class="empty-state-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="14" height="3" rx="1.5" fill="currentColor" opacity=".7"/><rect x="6" y="10" width="10" height="3" rx="1.5" fill="currentColor" opacity=".5"/><rect x="4" y="16" width="16" height="3" rx="1.5" fill="currentColor" opacity=".3"/></svg></div><div class="empty-state-title">Gantt Chart</div><div class="empty-state-desc">Add due dates and dependencies for Gantt visualization.</div></div>';
      container.appendChild(wrapper);
      return;
    }

    // --- Compute date range ---
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const pxPerUnit = { day: 40, week: 120, month: 200 }[self._zoom];
    const unitMs = { day: 864e5, week: 6048e5, month: 2592e6 }[self._zoom];

    let minDate = new Date(today); let maxDate = new Date(today);
    sorted.forEach(t => {
      if (t.due_date) {
        const d = new Date(t.due_date); d.setHours(0, 0, 0, 0);
        if (d < minDate) minDate = new Date(d);
        if (d > maxDate) maxDate = new Date(d);
      }
    });
    // Pad range
    minDate.setDate(minDate.getDate() - 7);
    maxDate.setDate(maxDate.getDate() + 14);
    const totalDays = Math.max(30, Math.ceil((maxDate - minDate) / 864e5));
    const chartWidth = Math.ceil(totalDays * (pxPerUnit / ({ day: 1, week: 7, month: 30 }[self._zoom])));

    // --- Chart container ---
    const chart = document.createElement('div');
    chart.className = 'gantt-chart';

    const labels = document.createElement('div');
    labels.className = 'gantt-labels';
    const barsArea = document.createElement('div');
    barsArea.className = 'gantt-bars';
    barsArea.style.width = chartWidth + 'px';

    // --- Date headers ---
    const dateRow = document.createElement('div');
    dateRow.className = 'gantt-date-row';
    dateRow.style.width = chartWidth + 'px';
    const step = { day: 1, week: 7, month: 30 }[self._zoom];
    for (let d = new Date(minDate); d <= maxDate; d.setDate(d.getDate() + step)) {
      const marker = document.createElement('span');
      marker.className = 'gantt-date-marker';
      marker.style.width = pxPerUnit + 'px';
      marker.textContent = d.toLocaleDateString(undefined, self._zoom === 'day' ? { day: 'numeric', month: 'short' } : { month: 'short', day: 'numeric' });
      dateRow.appendChild(marker);
    }
    barsArea.appendChild(dateRow);

    // --- SVG overlay for dependencies ---
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('gantt-svg-overlay');
    svg.style.width = chartWidth + 'px';

    const ROW_HEIGHT = 36;
    const barPositions = {};

    // --- Rows ---
    sorted.forEach((task, idx) => {
      // Label
      const label = document.createElement('div');
      label.className = 'gantt-label';
      label.textContent = escHtml(task.title).length > 28 ? task.title.slice(0, 28) + '…' : task.title;
      label.title = task.title;
      labels.appendChild(label);

      // Bar
      const row = document.createElement('div');
      row.className = 'gantt-bar-row';
      row.style.height = ROW_HEIGHT + 'px';

      let startPx, widthPx;
      if (task.due_date) {
        const due = new Date(task.due_date); due.setHours(0, 0, 0, 0);
        const durDays = Math.max(1, (task.estimated_minutes || 60) / 480);
        const barStart = new Date(due); barStart.setDate(barStart.getDate() - durDays);
        startPx = Math.max(0, ((barStart - minDate) / 864e5) * (pxPerUnit / step));
        widthPx = Math.max(20, durDays * (pxPerUnit / step));
      } else {
        startPx = idx * 50;
        widthPx = Math.max(20, ((task.estimated_minutes || 60) / 480) * (pxPerUnit / step));
      }

      const bar = document.createElement('div');
      bar.className = 'gantt-bar';
      bar.style.left = startPx + 'px';
      bar.style.width = widthPx + 'px';
      bar.style.background = self.STATUS_COLORS[task.status] || '#6b7280';
      bar.title = `${task.title} — ${task.status}`;

      if (onTaskClick) {
        bar.style.cursor = 'pointer';
        bar.addEventListener('click', () => onTaskClick(task));
      }

      // Status dropdown on right-click
      if (onStatusChange) {
        bar.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          self._showStatusMenu(e, task, onStatusChange);
        });
      }

      row.appendChild(bar);
      barsArea.appendChild(row);

      barPositions[task.id] = { x: startPx + widthPx, y: (idx + 1) * ROW_HEIGHT + ROW_HEIGHT / 2 + ROW_HEIGHT };
    });

    // --- Today marker ---
    const todayPx = ((today - minDate) / 864e5) * (pxPerUnit / step);
    if (todayPx >= 0 && todayPx <= chartWidth) {
      const todayLine = document.createElement('div');
      todayLine.className = 'gantt-today';
      todayLine.style.left = todayPx + 'px';
      todayLine.style.height = ((sorted.length + 1) * ROW_HEIGHT + ROW_HEIGHT) + 'px';
      barsArea.appendChild(todayLine);
    }

    // --- Dependency arrows ---
    svg.style.height = ((sorted.length + 1) * ROW_HEIGHT + ROW_HEIGHT) + 'px';
    sorted.forEach(task => {
      (task.depends_on || []).forEach(depId => {
        const from = barPositions[depId];
        const to = barPositions[task.id];
        if (from && to) {
          const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          const mx = from.x + 10;
          path.setAttribute('d', `M${from.x},${from.y} H${mx} V${to.y} H${to.x - 10}`);
          path.setAttribute('class', 'gantt-dependency');
          path.setAttribute('marker-end', 'url(#arrowhead)');
          svg.appendChild(path);
        }
      });
    });

    // Arrowhead marker definition
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', 'arrowhead');
    marker.setAttribute('markerWidth', '8');
    marker.setAttribute('markerHeight', '6');
    marker.setAttribute('refX', '8');
    marker.setAttribute('refY', '3');
    marker.setAttribute('orient', 'auto');
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    poly.setAttribute('points', '0 0, 8 3, 0 6');
    poly.setAttribute('fill', 'var(--muted, #78716c)');
    marker.appendChild(poly);
    defs.appendChild(marker);
    svg.insertBefore(defs, svg.firstChild);
    barsArea.appendChild(svg);

    // Label header placeholder
    const labelHeader = document.createElement('div');
    labelHeader.className = 'gantt-label gantt-label-header';
    labelHeader.textContent = 'Task';
    labels.insertBefore(labelHeader, labels.firstChild);

    chart.appendChild(labels);
    chart.appendChild(barsArea);
    wrapper.appendChild(chart);
    container.appendChild(wrapper);
  },

  _showStatusMenu(e, task, onStatusChange) {
    document.querySelectorAll('.gantt-status-menu').forEach(m => m.remove());
    const menu = document.createElement('div');
    menu.className = 'gantt-status-menu';
    menu.style.position = 'fixed';
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';
    Object.entries(this.STATUS_COLORS).forEach(([status, color]) => {
      const item = document.createElement('button');
      item.className = 'gantt-status-menu-item';
      item.innerHTML = `<span style="background:${color};width:10px;height:10px;border-radius:50%;display:inline-block"></span> ${status.replace('_', ' ')}`;
      item.addEventListener('click', () => { menu.remove(); onStatusChange(task.id, status); });
      menu.appendChild(item);
    });
    document.body.appendChild(menu);
    const dismiss = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', dismiss); } };
    setTimeout(() => document.addEventListener('click', dismiss), 0);
  }
};

function escHtml(s) {
  if (typeof window !== 'undefined' && window.escHtml) return window.escHtml(s);
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

if (typeof module !== 'undefined') module.exports = GanttView;
