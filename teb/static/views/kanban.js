/**
 * Kanban Board View (WP-03)
 * Renders tasks as cards in status columns with drag-and-drop.
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

  _onStatusChange: null,
  _onCardClick: null,

  render(tasks, container, options = {}) {
    this._onStatusChange = options.onStatusChange || null;
    this._onCardClick = options.onCardClick || null;
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

      // Drag-and-drop: allow drops on column
      cardsContainer.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        cardsContainer.classList.add('drag-over');
      });
      cardsContainer.addEventListener('dragleave', () => {
        cardsContainer.classList.remove('drag-over');
      });
      cardsContainer.addEventListener('drop', (e) => {
        e.preventDefault();
        cardsContainer.classList.remove('drag-over');
        const taskId = e.dataTransfer.getData('text/plain');
        if (taskId && this._onStatusChange) {
          this._onStatusChange(taskId, col.key);
        }
      });

      tasks
        .filter(t => t.status === col.key)
        .sort((a, b) => a.order_index - b.order_index)
        .forEach(task => {
          const card = document.createElement('div');
          card.className = 'kanban-card';
          card.dataset.taskId = task.id;
          card.draggable = true;

          // Drag-and-drop: make card draggable
          card.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', task.id);
            e.dataTransfer.effectAllowed = 'move';
            card.classList.add('dragging');
          });
          card.addEventListener('dragend', () => {
            card.classList.remove('dragging');
            document.querySelectorAll('.kanban-cards.drag-over').forEach(el => el.classList.remove('drag-over'));
          });

          // Click to open task detail
          card.addEventListener('click', () => {
            if (this._onCardClick) this._onCardClick(task);
          });

          const tags = (task.tags || []).map(t => `<span class="tag">${escHtml(t)}</span>`).join('');
          const dueDate = task.due_date ? `<span class="due-date">\uD83D\uDCC5 ${escHtml(task.due_date)}</span>` : '';
          card.innerHTML = `
            <div class="kanban-card-title">${escHtml(task.title)}</div>
            <div class="kanban-card-meta">
              <span class="est-time">\u{23F1} ${task.estimated_minutes || 0}m</span>
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

// Safe HTML escaping helper (available globally from app.js, fallback here)
function escHtml(s) {
  if (typeof window !== 'undefined' && window.escHtml) return window.escHtml(s);
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

if (typeof module !== 'undefined') module.exports = KanbanView;
