/**
 * Table / Spreadsheet View (Phase 3)
 * Sortable, filterable table with inline editing and batch selection.
 */
const TableView = {
  _sortCol: 'order_index',
  _sortAsc: true,
  _filter: '',
  _statusFilter: '',
  _selected: new Set(),

  STATUSES: ['todo', 'in_progress', 'executing', 'done', 'failed', 'skipped'],

  COLUMNS: [
    { key: 'select', label: '', sortable: false },
    { key: 'title', label: 'Title', sortable: true },
    { key: 'status', label: 'Status', sortable: true },
    { key: 'priority', label: 'Priority', sortable: true },
    { key: 'due_date', label: 'Due Date', sortable: true },
    { key: 'estimated_minutes', label: 'Est. Time', sortable: true },
    { key: 'tags', label: 'Tags', sortable: false },
    { key: 'assigned_to', label: 'Assignee', sortable: false },
  ],

  render(tasks, container, options = {}) {
    container.innerHTML = '';
    const self = this;
    const onStatusChange = options.onStatusChange || null;
    const onTaskClick = options.onTaskClick || null;
    const onBatchSelect = options.onBatchSelect || null;

    const wrapper = document.createElement('div');
    wrapper.className = 'table-view';

    // --- Filter bar ---
    const filterBar = document.createElement('div');
    filterBar.className = 'table-filter-bar';
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search tasks…';
    searchInput.className = 'table-search';
    searchInput.value = self._filter;
    searchInput.addEventListener('input', () => { self._filter = searchInput.value; self.render(tasks, container, options); });

    const statusSelect = document.createElement('select');
    statusSelect.className = 'table-status-filter';
    statusSelect.innerHTML = '<option value="">All statuses</option>' +
      self.STATUSES.map(s => `<option value="${s}"${self._statusFilter === s ? ' selected' : ''}>${s.replace('_', ' ')}</option>`).join('');
    statusSelect.addEventListener('change', () => { self._statusFilter = statusSelect.value; self.render(tasks, container, options); });

    filterBar.appendChild(searchInput);
    filterBar.appendChild(statusSelect);
    wrapper.appendChild(filterBar);

    // --- Filter & sort tasks ---
    let filtered = [...tasks];
    if (self._filter) {
      const q = self._filter.toLowerCase();
      filtered = filtered.filter(t => (t.title || '').toLowerCase().includes(q) ||
        (t.tags || []).some(tag => tag.toLowerCase().includes(q)) ||
        (t.assigned_to || '').toLowerCase().includes(q));
    }
    if (self._statusFilter) {
      filtered = filtered.filter(t => t.status === self._statusFilter);
    }
    filtered.sort((a, b) => {
      let va = a[self._sortCol] ?? '', vb = b[self._sortCol] ?? '';
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
      if (va < vb) return self._sortAsc ? -1 : 1;
      if (va > vb) return self._sortAsc ? 1 : -1;
      return 0;
    });

    // --- Table ---
    const table = document.createElement('table');
    table.className = 'table-grid';

    // Header
    const thead = document.createElement('thead');
    const hrow = document.createElement('tr');
    self.COLUMNS.forEach(col => {
      const th = document.createElement('th');
      if (col.key === 'select') {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = filtered.length > 0 && filtered.every(t => self._selected.has(t.id));
        cb.addEventListener('change', () => {
          filtered.forEach(t => cb.checked ? self._selected.add(t.id) : self._selected.delete(t.id));
          self.render(tasks, container, options);
          if (onBatchSelect) onBatchSelect([...self._selected]);
        });
        th.appendChild(cb);
      } else {
        th.textContent = col.label;
        if (col.sortable) {
          th.style.cursor = 'pointer';
          if (self._sortCol === col.key) th.textContent += self._sortAsc ? ' ▲' : ' ▼';
          th.addEventListener('click', () => {
            if (self._sortCol === col.key) self._sortAsc = !self._sortAsc;
            else { self._sortCol = col.key; self._sortAsc = true; }
            self.render(tasks, container, options);
          });
        }
      }
      hrow.appendChild(th);
    });
    thead.appendChild(hrow);
    table.appendChild(thead);

    // Body
    const tbody = document.createElement('tbody');
    filtered.forEach(task => {
      const tr = document.createElement('tr');
      tr.dataset.taskId = task.id;

      self.COLUMNS.forEach(col => {
        const td = document.createElement('td');

        if (col.key === 'select') {
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.checked = self._selected.has(task.id);
          cb.addEventListener('change', () => {
            cb.checked ? self._selected.add(task.id) : self._selected.delete(task.id);
            if (onBatchSelect) onBatchSelect([...self._selected]);
          });
          td.appendChild(cb);
        } else if (col.key === 'title') {
          td.textContent = task.title || '';
          td.className = 'table-cell-title';
          td.style.cursor = 'pointer';
          td.addEventListener('click', () => { if (onTaskClick) onTaskClick(task); });
        } else if (col.key === 'status') {
          const badge = document.createElement('span');
          badge.className = 'table-status-badge status-' + (task.status || 'todo');
          badge.textContent = (task.status || 'todo').replace('_', ' ');
          td.appendChild(badge);
          if (onStatusChange) {
            td.style.cursor = 'pointer';
            td.addEventListener('click', () => {
              const idx = self.STATUSES.indexOf(task.status || 'todo');
              const next = self.STATUSES[(idx + 1) % self.STATUSES.length];
              onStatusChange(task.id, next);
            });
          }
        } else if (col.key === 'priority') {
          td.textContent = task.priority != null ? task.priority : '—';
          td.className = 'table-cell-editable';
          self._makeEditable(td, task, 'priority', tasks, container, options);
        } else if (col.key === 'due_date') {
          td.textContent = task.due_date ? task.due_date.substring(0, 10) : '—';
        } else if (col.key === 'estimated_minutes') {
          td.textContent = task.estimated_minutes != null ? task.estimated_minutes + 'm' : '—';
        } else if (col.key === 'tags') {
          td.innerHTML = (task.tags || []).map(t => `<span class="tag">${escHtml(t)}</span>`).join(' ');
        } else if (col.key === 'assigned_to') {
          td.textContent = task.assigned_to || '—';
        }

        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrapper.appendChild(table);

    // Batch count
    if (self._selected.size > 0) {
      const info = document.createElement('div');
      info.className = 'table-batch-info';
      info.textContent = `${self._selected.size} task(s) selected`;
      wrapper.appendChild(info);
    }

    container.appendChild(wrapper);
  },

  _makeEditable(td, task, field, tasks, container, options) {
    td.addEventListener('dblclick', () => {
      const current = task[field] != null ? String(task[field]) : '';
      const input = document.createElement('input');
      input.type = 'text';
      input.value = current;
      input.className = 'table-inline-edit';
      td.textContent = '';
      td.appendChild(input);
      input.focus();
      const finish = () => {
        const v = input.value.trim();
        task[field] = v === '' ? null : (isNaN(v) ? v : Number(v));
        this.render(tasks, container, options);
      };
      input.addEventListener('blur', finish);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(); if (e.key === 'Escape') this.render(tasks, container, options); });
    });
  }
};

function escHtml(s) {
  if (typeof window !== 'undefined' && window.escHtml) return window.escHtml(s);
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

if (typeof module !== 'undefined') module.exports = TableView;
