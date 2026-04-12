/**
 * Workload View (Phase 3)
 * Horizontal bar chart of estimated_minutes grouped by assignee, segmented by status.
 */
const WorkloadView = {
  STATUS_COLORS: {
    todo: '#6b7280', in_progress: '#3b82f6', executing: '#f59e0b',
    done: '#10b981', failed: '#ef4444', skipped: '#9ca3af',
  },
  OVERLOAD_MINUTES: 480,

  render(tasks, container, options = {}) {
    container.innerHTML = '';
    const self = this;
    const onTaskClick = options.onTaskClick || null;

    const wrapper = document.createElement('div');
    wrapper.className = 'workload-view';

    // Group by assignee
    const groups = {};
    tasks.forEach(t => {
      const who = t.assigned_to || 'Unassigned';
      if (!groups[who]) groups[who] = [];
      groups[who].push(t);
    });

    const people = Object.keys(groups).sort((a, b) => a === 'Unassigned' ? 1 : b === 'Unassigned' ? -1 : a.localeCompare(b));
    const maxMinutes = Math.max(self.OVERLOAD_MINUTES, ...people.map(p => groups[p].reduce((s, t) => s + (t.estimated_minutes || 0), 0)));

    if (!people.length) {
      wrapper.innerHTML = '<div class="empty-state-large"><div class="empty-state-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><rect x="15" y="11" width="8" height="2.5" rx="1" fill="currentColor" opacity=".6"/><rect x="15" y="15" width="5" height="2.5" rx="1" fill="currentColor" opacity=".4"/><rect x="15" y="19" width="6.5" height="2.5" rx="1" fill="currentColor" opacity=".5"/></svg></div><div class="empty-state-title">Workload View</div><div class="empty-state-desc">Assign tasks to team members to see workload distribution.</div></div>';
      container.appendChild(wrapper);
      return;
    }

    // Header
    const header = document.createElement('div');
    header.className = 'workload-header';
    header.innerHTML = '<h3>Workload by Assignee</h3>';

    // Legend
    const legend = document.createElement('div');
    legend.className = 'workload-legend';
    Object.entries(self.STATUS_COLORS).forEach(([s, c]) => {
      legend.innerHTML += `<span class="workload-legend-item"><span class="workload-legend-dot" style="background:${c}"></span>${s.replace('_', ' ')}</span>`;
    });
    header.appendChild(legend);
    wrapper.appendChild(header);

    // Rows
    people.forEach(person => {
      const row = document.createElement('div');
      row.className = 'workload-row';

      const label = document.createElement('div');
      label.className = 'workload-label';
      label.textContent = person;

      const barWrap = document.createElement('div');
      barWrap.className = 'workload-bar-wrap';

      const totalMin = groups[person].reduce((s, t) => s + (t.estimated_minutes || 0), 0);

      // Status segments
      const statusTotals = {};
      groups[person].forEach(t => {
        const st = t.status || 'todo';
        statusTotals[st] = (statusTotals[st] || 0) + (t.estimated_minutes || 0);
      });

      Object.entries(statusTotals).forEach(([st, mins]) => {
        const seg = document.createElement('div');
        seg.className = 'workload-segment';
        seg.style.width = ((mins / maxMinutes) * 100) + '%';
        seg.style.background = self.STATUS_COLORS[st] || '#6b7280';
        seg.title = `${st.replace('_', ' ')}: ${mins}m`;
        barWrap.appendChild(seg);
      });

      const total = document.createElement('span');
      total.className = 'workload-total';
      total.textContent = totalMin + 'm';

      // Overload indicator
      if (totalMin > self.OVERLOAD_MINUTES) {
        const warn = document.createElement('span');
        warn.className = 'workload-overload';
        warn.textContent = '⚠️ Overloaded';
        warn.title = `${totalMin}m exceeds ${self.OVERLOAD_MINUTES}m (8h) daily capacity`;
        total.appendChild(warn);
      }

      // Task count
      const count = document.createElement('span');
      count.className = 'workload-count';
      count.textContent = `${groups[person].length} task${groups[person].length !== 1 ? 's' : ''}`;

      const meta = document.createElement('div');
      meta.className = 'workload-meta';
      meta.appendChild(total);
      meta.appendChild(count);

      row.appendChild(label);
      row.appendChild(barWrap);
      row.appendChild(meta);

      // 8h capacity line
      const capPx = (self.OVERLOAD_MINUTES / maxMinutes) * 100;
      if (capPx < 100) {
        const cap = document.createElement('div');
        cap.className = 'workload-capacity-line';
        cap.style.left = capPx + '%';
        cap.title = '8h capacity';
        barWrap.appendChild(cap);
      }

      wrapper.appendChild(row);
    });

    container.appendChild(wrapper);
  }
};

if (typeof module !== 'undefined') module.exports = WorkloadView;
