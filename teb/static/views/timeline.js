/**
 * Timeline View (WP-03)
 * Renders tasks as horizontal bars proportional to estimated_minutes.
 */
const TimelineView = {
  render(tasks, container) {
    container.innerHTML = '';
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
