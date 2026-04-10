/**
 * Kanban Board View (WP-03)
 * Renders tasks as cards in status columns.
 */
const KanbanView = {
  COLUMNS: [
    { key: 'todo', label: 'To Do', color: '#6b7280' },
    { key: 'in_progress', label: 'In Progress', color: '#3b82f6' },
    { key: 'executing', label: 'Executing', color: '#f59e0b' },
    { key: 'done', label: 'Done', color: '#10b981' },
    { key: 'failed', label: 'Failed', color: '#ef4444' },
    { key: 'skipped', label: 'Skipped', color: '#9ca3af' },
  ],

  render(tasks, container) {
    container.innerHTML = '';
    const board = document.createElement('div');
    board.className = 'kanban-board';

    this.COLUMNS.forEach(col => {
      const column = document.createElement('div');
      column.className = 'kanban-column';
      column.innerHTML = `
        <div class="kanban-column-header" style="border-top: 3px solid ${col.color}">
          <span class="kanban-column-title">${col.label}</span>
          <span class="kanban-column-count">${tasks.filter(t => t.status === col.key).length}</span>
        </div>
        <div class="kanban-cards" data-status="${col.key}"></div>
      `;

      const cardsContainer = column.querySelector('.kanban-cards');
      tasks
        .filter(t => t.status === col.key)
        .sort((a, b) => a.order_index - b.order_index)
        .forEach(task => {
          const card = document.createElement('div');
          card.className = 'kanban-card';
          card.dataset.taskId = task.id;
          const tags = (task.tags || []).map(t => `<span class="tag">${t}</span>`).join('');
          const dueDate = task.due_date ? `<span class="due-date">\u{1F4C5} ${task.due_date}</span>` : '';
          card.innerHTML = `
            <div class="kanban-card-title">${task.title}</div>
            <div class="kanban-card-meta">
              <span class="est-time">\u{23F1} ${task.estimated_minutes}m</span>
              ${dueDate}
            </div>
            ${tags ? `<div class="kanban-card-tags">${tags}</div>` : ''}
          `;
          cardsContainer.appendChild(card);
        });

      board.appendChild(column);
    });

    container.appendChild(board);
  }
};

if (typeof module !== 'undefined') module.exports = KanbanView;
