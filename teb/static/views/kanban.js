/**
 * Kanban Board View (WP-03)
 * Renders tasks as cards in status columns with drag-and-drop.
 * Enhancements: swimlanes, WIP limits, card aging.
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

  _getCardAgingClass(task) {
    if (!task.updated_at && !task.created_at) return '';
    const ref = task.updated_at || task.created_at;
    const date = typeof ref === 'string' ? new Date(ref) : ref;
    const days = (Date.now() - date.getTime()) / (1000 * 60 * 60 * 24);
    if (days > 14) return 'kanban-card-aging-orange';
    if (days > 7) return 'kanban-card-aging-yellow';
    return '';
  },

  render(tasks, container, options = {}) {
    this._onStatusChange = options.onStatusChange || null;
    this._onCardClick = options.onCardClick || null;
    const swimlaneField = options.swimlaneField || null;
    const wipLimits = options.wipLimits || {};
    container.innerHTML = '';

    if (!tasks || tasks.length === 0) {
      container.innerHTML = '<div class="empty-state-large"><div class="empty-state-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Kanban board"><rect x="2" y="3" width="5" height="18" rx="1"/><rect x="9.5" y="3" width="5" height="12" rx="1"/><rect x="17" y="3" width="5" height="15" rx="1"/></svg></div><div class="empty-state-title">Kanban Board</div><div class="empty-state-desc">Create your first task to see it on the board. Tasks are organized by status columns.</div></div>';
      return;
    }

    // Swimlane grouping
    let lanes = [{ key: '__all__', label: '', tasks }];
    if (swimlaneField) {
      const groups = {};
      tasks.forEach(t => {
        let val = '';
        if (swimlaneField === 'assigned_to') {
          val = t.assigned_to ? String(t.assigned_to) : 'Unassigned';
        } else if (swimlaneField === 'tags') {
          const tags = Array.isArray(t.tags) ? t.tags : [];
          val = tags.length ? tags[0] : 'No Tag';
        } else {
          val = t[swimlaneField] || 'None';
        }
        (groups[val] = groups[val] || []).push(t);
      });
      lanes = Object.entries(groups).map(([key, tasks]) => ({
        key, label: key, tasks,
      }));
    }

    // Toolbar for swimlane and WIP config
    const toolbar = document.createElement('div');
    toolbar.className = 'kanban-toolbar';
    toolbar.innerHTML = `
      <label class="kanban-toolbar-label">Swimlanes:
        <select class="kanban-swimlane-select">
          <option value="">None</option>
          <option value="assigned_to" ${swimlaneField === 'assigned_to' ? 'selected' : ''}>Assignee</option>
          <option value="tags" ${swimlaneField === 'tags' ? 'selected' : ''}>Tags</option>
        </select>
      </label>
    `;
    container.appendChild(toolbar);

    const selectEl = toolbar.querySelector('.kanban-swimlane-select');
    selectEl.addEventListener('change', () => {
      this.render(tasks, container, {
        ...options,
        swimlaneField: selectEl.value || null,
      });
    });

    lanes.forEach(lane => {
      if (swimlaneField) {
        const laneHeader = document.createElement('div');
        laneHeader.className = 'kanban-swimlane-header';
        laneHeader.textContent = lane.label;
        container.appendChild(laneHeader);
      }

      const board = document.createElement('div');
      board.className = 'kanban-board';

      this.COLUMNS.forEach(col => {
        const colTasks = lane.tasks.filter(t => t.status === col.key);
        const wipLimit = wipLimits[col.key] || 0;
        const overWip = wipLimit > 0 && colTasks.length > wipLimit;

        const column = document.createElement('div');
        column.className = 'kanban-column' + (overWip ? ' kanban-wip-exceeded' : '');
        column.innerHTML = `
          <div class="kanban-column-header" style="border-top: 3px solid ${col.color}">
            <span class="kanban-column-title">${col.label}</span>
            <span class="kanban-column-count">${colTasks.length}${wipLimit ? '/' + wipLimit : ''}</span>
          </div>
          <div class="kanban-cards" data-status="${col.key}"></div>
        `;

        const cardsContainer = column.querySelector('.kanban-cards');

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

        colTasks
          .sort((a, b) => a.order_index - b.order_index)
          .forEach(task => {
            const agingClass = this._getCardAgingClass(task);
            const card = document.createElement('div');
            card.className = 'kanban-card' + (agingClass ? ' ' + agingClass : '');
            card.dataset.taskId = task.id;
            card.draggable = true;

            card.addEventListener('dragstart', (e) => {
              e.dataTransfer.setData('text/plain', task.id);
              e.dataTransfer.effectAllowed = 'move';
              card.classList.add('dragging');
            });
            card.addEventListener('dragend', () => {
              card.classList.remove('dragging');
              document.querySelectorAll('.kanban-cards.drag-over').forEach(el => el.classList.remove('drag-over'));
            });

            card.addEventListener('click', () => {
              if (this._onCardClick) this._onCardClick(task);
            });

            const tags = (task.tags || []).map(t => `<span class="tag">${escHtml(t)}</span>`).join('');
            const dueDate = task.due_date ? `<span class="due-date">\uD83D\uDCC5 ${escHtml(task.due_date)}</span>` : '';
            const priority = task.priority || 'normal';
            const dotHtml = `<span class="priority-dot priority-dot--${escHtml(priority)}" title="Priority: ${escHtml(priority)}"></span>`;
            card.innerHTML = `
              <div class="kanban-card-title">${dotHtml} ${escHtml(task.title)}</div>
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
    });
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
