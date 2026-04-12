/**
 * Timeline View (WP-03)
 * Renders tasks as horizontal bars proportional to estimated_minutes.
 */
const TimelineView = {
  render(tasks, container) {
    container.innerHTML = '';

    if (!tasks || tasks.length === 0) {
      container.innerHTML = '<div class="empty-state-large"><div class="empty-state-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="22"/><circle cx="12" cy="6" r="2.5" fill="currentColor"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/><circle cx="12" cy="18" r="2.5" fill="currentColor"/><line x1="14.5" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="9.5" y2="12"/><line x1="14.5" y1="18" x2="20" y2="18"/></svg></div><div class="empty-state-title">Timeline</div><div class="empty-state-desc">Tasks will appear here as they are created, ordered by estimated effort.</div></div>';
      return;
    }

    const timeline = document.createElement('div');
    timeline.className = 'timeline-view';

    const maxMinutes = Math.max(...tasks.map(t => t.estimated_minutes || 30), 60);

    const sorted = [...tasks].sort((a, b) => a.order_index - b.order_index);
    sorted.forEach(task => {
      const row = document.createElement('div');
      row.className = 'timeline-row';

      const widthPct = Math.max(((task.estimated_minutes || 30) / maxMinutes) * 100, 5);
      const statusColors = {
        todo: '#6b7280', in_progress: '#3b82f6', executing: '#f59e0b',
        done: '#10b981', failed: '#ef4444', skipped: '#9ca3af',
      };
      const color = statusColors[task.status] || '#6b7280';

      row.innerHTML = `
        <div class="timeline-label" title="${task.description || ''}">
          <span class="timeline-task-title">${task.title}</span>
          <span class="timeline-task-status status-${task.status}">${task.status}</span>
        </div>
        <div class="timeline-bar-container">
          <div class="timeline-bar" style="width: ${widthPct}%; background: ${color}">
            <span class="timeline-bar-text">${task.estimated_minutes}m</span>
          </div>
        </div>
      `;
      timeline.appendChild(row);
    });

    container.appendChild(timeline);
  }
};

if (typeof module !== 'undefined') module.exports = TimelineView;
