/* app.js — teb frontend */

// ─── Utility: HTML escaping (must be defined first — used by toast and others) ─
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ─── Utility: Safe event binding helper ──────────────────────────────────────
function on(id, event, fn) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(event, fn);
}

// ─── Base path (injected by server; falls back to "" for standalone) ──────────
const BASE_PATH = (window.__BASE_PATH__ || '').replace(/\/$/, '');

// ─── Auth-aware API wrapper ──────────────────────────────────────────────────

function authHeaders() {
  const token = localStorage.getItem('teb_token');
  const h = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

const api = {
  async post(url, body) {
    const r = await fetch(BASE_PATH + url, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async get(url) {
    const r = await fetch(BASE_PATH + url, { headers: authHeaders() });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async patch(url, body) {
    const r = await fetch(BASE_PATH + url, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async del(url) {
    const r = await fetch(BASE_PATH + url, { method: 'DELETE', headers: authHeaders() });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
};

// ─── State ────────────────────────────────────────────────────────────────────

let currentGoalId = null;
let currentGoalTitle = '';
let currentTasks = [];
let dripMode = true; // default to drip mode
let authMode = 'login'; // 'login' or 'register'
let autopilotEnabled = false;
let _pendingOutcomeSuggestions = null;
let _adminUsersCache = [];
let _currentViewType = localStorage.getItem('teb_view_type') || 'list';

// ─── Toast notification system ────────────────────────────────────────────────

const toast = {
  _container: null,
  _getContainer() {
    if (!this._container) this._container = document.getElementById('toast-container');
    return this._container;
  },
  show(type, title, message, duration = 4000) {
    const container = this._getContainer();
    if (!container) return;

    const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.innerHTML = `
      <span class="toast-icon">${icons[type] || 'ℹ'}</span>
      <div class="toast-body">
        <div class="toast-title">${escHtml(title)}</div>
        ${message ? `<div class="toast-message">${escHtml(message)}</div>` : ''}
      </div>
      <button class="toast-close" aria-label="Dismiss">&times;</button>
    `;

    el.querySelector('.toast-close').addEventListener('click', () => this._dismiss(el));
    container.appendChild(el);

    if (duration > 0) {
      setTimeout(() => this._dismiss(el), duration);
    }
  },
  _dismiss(el) {
    if (!el.parentNode) return;
    el.classList.add('toast-out');
    el.addEventListener('animationend', () => el.remove());
  },
  success(title, msg) { this.show('success', title, msg); },
  error(title, msg) { this.show('error', title, msg, 6000); },
  info(title, msg) { this.show('info', title, msg); },
  warning(title, msg) { this.show('warning', title, msg, 5000); },
};

// ─── Dark mode ────────────────────────────────────────────────────────────────

function initTheme() {
  const saved = localStorage.getItem('teb_theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
  } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
  updateThemeIcon();

  // Listen for system preference changes
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      if (!localStorage.getItem('teb_theme')) {
        document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
        updateThemeIcon();
      }
    });
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('teb_theme', next);
  updateThemeIcon();
}

function updateThemeIcon() {
  const btn = document.getElementById('btn-theme-toggle');
  if (!btn) return;
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  btn.textContent = isDark ? '☀️' : '🌙';
  btn.title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
}

// ─── Loading overlay ──────────────────────────────────────────────────────────

function showLoading(msg) {
  const overlay = document.getElementById('loading-overlay');
  const msgEl = document.getElementById('loading-message');
  if (msgEl) msgEl.textContent = msg || 'Loading…';
  if (overlay) overlay.style.display = 'flex';
}

function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) overlay.style.display = 'none';
}

// ─── Screen management ────────────────────────────────────────────────────────

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const el = document.getElementById(id);
  if (el) el.classList.add('active');
}

// ─── Hash-based URL Router ────────────────────────────────────────────────────

const Router = {
  _current: '',
  routes: {
    '/home': () => {
      const token = localStorage.getItem('teb_token');
      if (!token) { showScreen('screen-auth'); updateBreadcrumbs([{text:'Sign in'}]); }
      else { showScreen('screen-landing'); updateBreadcrumbs([{text:'Home'}]); loadGoalList(); }
    },
    '/auth': () => { showScreen('screen-auth'); updateBreadcrumbs([{text:'Sign in'}]); },
    '/goal/:id': (params) => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal'}]);
      if (params.id && params.id !== currentGoalId) {
        // Load goal by ID
        loadGoalById(params.id);
      }
    },
    '/kanban': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Kanban'}]);
    },
    '/calendar': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Calendar'}]);
    },
    '/timeline': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Timeline'}]);
    },
    '/gantt': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Gantt'}]);
    },
    '/table': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Table'}]);
    },
    '/workload': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Workload'}]);
    },
    '/mindmap': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Mind Map'}]);
    },
    '/dashboard': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text:'Dashboard'}]);
      // Initialize dashboard builder in the all-tasks-section area
      const section = document.getElementById('all-tasks-section');
      if (section) {
        section.style.display = 'block';
        document.getElementById('drip-section') && (document.getElementById('drip-section').style.display = 'none');
        DashboardBuilder.init('all-tasks-section');
      }
    },
    '/settings': () => {
      showSettingsModal();
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text:'Settings'}]);
    },
    '/admin': () => {
      showAdminModal();
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text:'Admin'}]);
    },
  },

  navigate(hash) {
    if (!hash || hash === '#' || hash === '#/') hash = '#/home';
    if (hash !== location.hash) location.hash = hash;
    else this._handleRoute(hash);
  },

  _handleRoute(hash) {
    const path = hash.replace('#', '') || '/home';
    this._current = path;

    // Match parameterized routes
    for (const [pattern, handler] of Object.entries(this.routes)) {
      const paramNames = [];
      const regexStr = pattern.replace(/:(\w+)/g, (_, name) => {
        paramNames.push(name);
        return '([^/]+)';
      });
      const match = path.match(new RegExp('^' + regexStr + '$'));
      if (match) {
        const params = {};
        paramNames.forEach((name, i) => { params[name] = match[i + 1]; });
        handler(params);
        updateSidebarActive(pattern.split('/')[1]);
        return;
      }
    }
    // Fallback
    this.routes['/home']();
    updateSidebarActive('home');
  },

  init() {
    window.addEventListener('hashchange', () => this._handleRoute(location.hash));
    // Handle initial route
    const hash = location.hash || '#/home';
    this._handleRoute(hash);
  }
};

function updateBreadcrumbs(items) {
  const el = document.getElementById('breadcrumbs');
  if (!el) return;
  el.innerHTML = items.map((item, i) => {
    if (i < items.length - 1 && item.href) {
      return `<a href="${item.href}" class="breadcrumb-link">${escHtml(item.text)}</a><span class="breadcrumb-sep">›</span>`;
    }
    return `<span class="breadcrumb-current">${escHtml(item.text)}</span>`;
  }).join('');
}

function updateSidebarActive(route) {
  document.querySelectorAll('.sidebar-link').forEach(el => {
    el.classList.toggle('active', el.dataset.route === route);
  });
  document.querySelectorAll('.mobile-nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.route === route);
  });
}

async function loadGoalById(goalId) {
  try {
    const goal = await api.get(`/api/goals/${goalId}`);
    await showTasksScreen(goal, false);
  } catch (e) {
    toast.error('Error', 'Could not load goal');
    Router.navigate('#/home');
  }
}

// ─── Sidebar Management ───────────────────────────────────────────────────────

function initSidebar() {
  const collapsed = localStorage.getItem('teb_sidebar_collapsed') === 'true';
  if (collapsed) document.body.classList.add('sidebar-collapsed');

  document.getElementById('btn-sidebar-collapse')?.addEventListener('click', () => {
    document.body.classList.toggle('sidebar-collapsed');
    localStorage.setItem('teb_sidebar_collapsed', document.body.classList.contains('sidebar-collapsed'));
  });

  document.getElementById('btn-mobile-menu')?.addEventListener('click', () => {
    document.getElementById('sidebar')?.classList.toggle('mobile-open');
  });

  // Close sidebar on mobile when clicking a link
  document.querySelectorAll('.sidebar-link, .sidebar-goal-link').forEach(el => {
    el.addEventListener('click', () => {
      document.getElementById('sidebar')?.classList.remove('mobile-open');
    });
  });

  // Admin button in sidebar
  document.getElementById('btn-sidebar-admin')?.addEventListener('click', () => {
    Router.navigate('#/admin');
    document.getElementById('sidebar')?.classList.remove('mobile-open');
  });
}

function updateSidebarGoals(goals) {
  const container = document.getElementById('sidebar-goals-list');
  if (!container) return;
  container.innerHTML = (goals || []).slice(0, 10).map(g =>
    `<a href="#/goal/${g.id}" class="sidebar-goal-link ${g.id === currentGoalId ? 'active' : ''}" data-goal-id="${g.id}">
      <span class="sidebar-icon" style="font-size:.8rem">📌</span>
      <span class="sidebar-text">${escHtml(g.title)}</span>
    </a>`
  ).join('');
}

// ─── Command Palette (Cmd/Ctrl+K) ────────────────────────────────────────────

const CommandPalette = {
  _visible: false,
  _selectedIndex: 0,
  _results: [],
  _goals: [],

  show() {
    this._visible = true;
    const overlay = document.getElementById('command-palette');
    if (overlay) overlay.style.display = 'flex';
    const input = document.getElementById('command-palette-input');
    if (input) { input.value = ''; input.focus(); }
    this._renderResults('');
  },

  hide() {
    this._visible = false;
    const overlay = document.getElementById('command-palette');
    if (overlay) overlay.style.display = 'none';
  },

  toggle() { this._visible ? this.hide() : this.show(); },

  _getCommands(query) {
    const q = (query || '').toLowerCase();
    const commands = [
      { icon: '🏠', text: 'Go to Home', hint: 'G H', action: () => Router.navigate('#/home') },
      { icon: '📊', text: 'Go to Dashboard', hint: 'G D', action: () => Router.navigate('#/dashboard') },
      { icon: '📋', text: 'Open Kanban Board', hint: '', action: () => Router.navigate('#/kanban') },
      { icon: '📅', text: 'Open Calendar View', hint: '', action: () => Router.navigate('#/calendar') },
      { icon: '📈', text: 'Open Timeline View', hint: '', action: () => Router.navigate('#/timeline') },
      { icon: '⚙️', text: 'Open Settings', hint: '', action: () => Router.navigate('#/settings') },
      { icon: '🌙', text: 'Toggle Dark Mode', hint: '', action: () => toggleTheme() },
      { icon: '➕', text: 'Create New Goal', hint: '', action: () => { Router.navigate('#/home'); setTimeout(() => document.getElementById('goal-title')?.focus(), 100); } },
    ];

    // Add goals as searchable items
    const goalItems = (this._goals || []).map(g => ({
      icon: '🎯', text: g.title, hint: 'goal', action: () => Router.navigate(`#/goal/${g.id}`)
    }));

    const all = [...commands, ...goalItems];
    if (!q) return all;
    return all.filter(c => c.text.toLowerCase().includes(q));
  },

  _renderResults(query) {
    const results = this._getCommands(query);
    this._results = results;
    this._selectedIndex = 0;
    const container = document.getElementById('command-palette-results');
    if (!container) return;

    if (results.length === 0) {
      container.innerHTML = '<div class="cmd-result"><span class="cmd-result-text" style="color:var(--muted)">No results</span></div>';
      return;
    }

    container.innerHTML = results.map((r, i) => `
      <div class="cmd-result ${i === 0 ? 'selected' : ''}" data-index="${i}">
        <span class="cmd-result-icon">${r.icon}</span>
        <span class="cmd-result-text">${escHtml(r.text)}</span>
        ${r.hint ? `<span class="cmd-result-hint">${escHtml(r.hint)}</span>` : ''}
      </div>
    `).join('');

    container.querySelectorAll('.cmd-result').forEach(el => {
      el.addEventListener('click', () => {
        const idx = parseInt(el.dataset.index, 10);
        if (results[idx]) { results[idx].action(); this.hide(); }
      });
    });
  },

  _moveSelection(delta) {
    if (!this._results.length) return;
    this._selectedIndex = (this._selectedIndex + delta + this._results.length) % this._results.length;
    document.querySelectorAll('.cmd-result').forEach((el, i) => {
      el.classList.toggle('selected', i === this._selectedIndex);
    });
    document.querySelectorAll('.cmd-result')[this._selectedIndex]?.scrollIntoView({ block: 'nearest' });
  },

  _executeSelected() {
    if (this._results[this._selectedIndex]) {
      this._results[this._selectedIndex].action();
      this.hide();
    }
  },

  init() {
    const input = document.getElementById('command-palette-input');
    if (input) {
      input.addEventListener('input', () => this._renderResults(input.value));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); this._moveSelection(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); this._moveSelection(-1); }
        else if (e.key === 'Enter') { e.preventDefault(); this._executeSelected(); }
        else if (e.key === 'Escape') { this.hide(); }
      });
    }

    // Close on overlay click
    document.getElementById('command-palette')?.addEventListener('click', (e) => {
      if (e.target.id === 'command-palette') this.hide();
    });

    // Open button
    document.getElementById('btn-cmd-palette')?.addEventListener('click', () => this.toggle());
  }
};

// ─── Task Detail Panel ────────────────────────────────────────────────────────

const TaskDetailPanel = {
  _currentTask: null,

  open(task) {
    this._currentTask = task;
    const panel = document.getElementById('task-detail-panel');
    if (!panel) return;
    panel.style.display = 'block';
    requestAnimationFrame(() => panel.classList.add('open'));

    const titleEl = document.getElementById('task-detail-title');
    const statusEl = document.getElementById('task-detail-status');
    const descEl = document.getElementById('task-detail-desc');
    const dueEl = document.getElementById('task-detail-due');
    const estEl = document.getElementById('task-detail-est');
    const tagsEl = document.getElementById('task-detail-tags');
    if (titleEl) titleEl.textContent = task.title || '';
    if (statusEl) statusEl.value = task.status || 'todo';
    if (descEl) descEl.value = task.description || '';
    if (dueEl) dueEl.value = task.due_date || '';
    if (estEl) estEl.value = task.estimated_minutes || '';
    if (tagsEl) tagsEl.value = (task.tags || []).join(', ');

    // Subtask progress
    const subtasksEl = document.getElementById('task-detail-subtasks');
    if (subtasksEl) {
      const deps = task.depends_on || [];
      if (deps.length > 0) {
        const depTasks = currentTasks.filter(t => deps.includes(t.id));
        const done = depTasks.filter(t => t.status === 'done').length;
        subtasksEl.innerHTML = `<div class="subtask-progress">
          <div class="subtask-progress-bar"><div class="subtask-progress-fill" style="width:${depTasks.length ? (done/depTasks.length*100) : 0}%"></div></div>
          <span>${done}/${depTasks.length} dependencies done</span>
        </div>`;
      } else {
        subtasksEl.innerHTML = '<span style="color:var(--muted);font-size:var(--text-xs)">No dependencies</span>';
      }
    }
  },

  close() {
    const panel = document.getElementById('task-detail-panel');
    if (panel) { panel.classList.remove('open'); setTimeout(() => { panel.style.display = 'none'; }, 300); }
    this._currentTask = null;
  },

  async save() {
    if (!this._currentTask || !currentGoalId) return;
    const taskId = this._currentTask.id;
    try {
      const tagsEl = document.getElementById('task-detail-tags');
      const tagsStr = tagsEl ? tagsEl.value : '';
      const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];
      const statusEl = document.getElementById('task-detail-status');
      const descEl = document.getElementById('task-detail-desc');
      const dueEl = document.getElementById('task-detail-due');
      const estEl = document.getElementById('task-detail-est');
      await api.patch(`/api/goals/${currentGoalId}/tasks/${taskId}`, {
        status: statusEl ? statusEl.value : 'todo',
        description: descEl ? descEl.value : '',
        due_date: (dueEl ? dueEl.value : '') || null,
        estimated_minutes: parseInt((estEl ? estEl.value : ''), 10) || null,
        tags: tags,
      });
      toast.success('Saved', 'Task updated');
      this.close();
      await refreshGoalView();
    } catch (e) {
      toast.error('Error', e.message);
    }
  },

  async deleteTask() {
    if (!this._currentTask || !currentGoalId) return;
    if (!confirm('Delete this task?')) return;
    try {
      await api.del(`/api/goals/${currentGoalId}/tasks/${this._currentTask.id}`);
      toast.info('Deleted', 'Task removed');
      this.close();
      await refreshGoalView();
    } catch (e) {
      toast.error('Error', e.message);
    }
  },

  init() {
    document.getElementById('btn-close-task-detail')?.addEventListener('click', () => this.close());
    document.getElementById('btn-task-detail-save')?.addEventListener('click', () => this.save());
    document.getElementById('btn-task-detail-delete')?.addEventListener('click', () => this.deleteTask());
  }
};

// ─── Batch Operations ─────────────────────────────────────────────────────────

const BatchOps = {
  _selected: new Set(),

  toggle(taskId) {
    if (this._selected.has(taskId)) this._selected.delete(taskId);
    else this._selected.add(taskId);
    this._updateUI();
  },

  clear() {
    this._selected.clear();
    this._updateUI();
    document.querySelectorAll('.task-select-checkbox').forEach(cb => { cb.checked = false; });
  },

  _updateUI() {
    const bar = document.getElementById('batch-bar');
    const count = document.getElementById('batch-count');
    if (this._selected.size > 0) {
      if (bar) bar.style.display = 'flex';
      if (count) count.textContent = `${this._selected.size} selected`;
    } else {
      if (bar) bar.style.display = 'none';
    }
  },

  async bulkStatus(status) {
    if (!currentGoalId || this._selected.size === 0) return;
    const ids = Array.from(this._selected);
    for (const id of ids) {
      try { await api.patch(`/api/goals/${currentGoalId}/tasks/${id}`, { status }); }
      catch (e) { console.warn('Batch update failed for', id, e); }
    }
    toast.success('Updated', `${ids.length} task(s) set to ${status}`);
    this.clear();
    await refreshGoalView();
  },

  async bulkDelete() {
    if (!currentGoalId || this._selected.size === 0) return;
    if (!confirm(`Delete ${this._selected.size} task(s)?`)) return;
    const ids = Array.from(this._selected);
    for (const id of ids) {
      try { await api.del(`/api/goals/${currentGoalId}/tasks/${id}`); }
      catch (e) { console.warn('Batch delete failed for', id, e); }
    }
    toast.info('Deleted', `${ids.length} task(s) removed`);
    this.clear();
    await refreshGoalView();
  },

  init() {
    document.getElementById('btn-batch-done')?.addEventListener('click', () => this.bulkStatus('done'));
    document.getElementById('btn-batch-progress')?.addEventListener('click', () => this.bulkStatus('in_progress'));
    document.getElementById('btn-batch-delete')?.addEventListener('click', () => this.bulkDelete());
    document.getElementById('btn-batch-clear')?.addEventListener('click', () => this.clear());
  }
};

// ─── Keyboard Shortcuts ───────────────────────────────────────────────────────

function initKeyboardShortcuts() {
  let _taskFocusIndex = -1;

  document.addEventListener('keydown', (e) => {
    // Don't interfere with inputs
    const tag = document.activeElement?.tagName?.toLowerCase();
    const isInput = (tag === 'input' || tag === 'textarea' || tag === 'select' || document.activeElement?.isContentEditable);

    // Cmd/Ctrl+K: Command palette (always works)
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      CommandPalette.toggle();
      return;
    }

    // Escape: close panels
    if (e.key === 'Escape') {
      if (CommandPalette._visible) { CommandPalette.hide(); return; }
      if (TaskDetailPanel._currentTask) { TaskDetailPanel.close(); return; }
      return;
    }

    if (isInput) return; // Below shortcuts only work when not in an input

    const tasks = document.querySelectorAll('#task-list .task-item, #task-list .task-card');

    // j/k: navigate tasks
    if (e.key === 'j') {
      e.preventDefault();
      _taskFocusIndex = Math.min(_taskFocusIndex + 1, tasks.length - 1);
      tasks[_taskFocusIndex]?.scrollIntoView({ block: 'nearest' });
      tasks.forEach((t, i) => t.classList.toggle('keyboard-focus', i === _taskFocusIndex));
    }
    if (e.key === 'k') {
      e.preventDefault();
      _taskFocusIndex = Math.max(_taskFocusIndex - 1, 0);
      tasks[_taskFocusIndex]?.scrollIntoView({ block: 'nearest' });
      tasks.forEach((t, i) => t.classList.toggle('keyboard-focus', i === _taskFocusIndex));
    }

    // x: toggle complete on focused task
    if (e.key === 'x' && _taskFocusIndex >= 0 && tasks[_taskFocusIndex]) {
      e.preventDefault();
      const taskId = tasks[_taskFocusIndex].dataset.taskId;
      if (taskId) {
        const task = currentTasks.find(t => t.id === taskId);
        if (task) {
          const newStatus = task.status === 'done' ? 'todo' : 'done';
          api.patch(`/api/goals/${currentGoalId}/tasks/${taskId}`, { status: newStatus })
            .then(() => refreshGoalView())
            .catch(err => toast.error('Error', err.message));
        }
      }
    }

    // e: edit focused task
    if (e.key === 'e' && _taskFocusIndex >= 0 && tasks[_taskFocusIndex]) {
      e.preventDefault();
      const taskId = tasks[_taskFocusIndex].dataset.taskId;
      const task = currentTasks.find(t => t.id === taskId);
      if (task) TaskDetailPanel.open(task);
    }

    // n: new task (focus quick-add bar)
    if (e.key === 'n') {
      e.preventDefault();
      document.getElementById('quick-add-input')?.focus();
    }

    // Ctrl+Enter or Cmd+Enter to submit forms
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      const activeScreen = document.querySelector('.screen.active');
      if (!activeScreen) return;
      if (activeScreen.id === 'screen-landing') { document.getElementById('btn-create-goal').click(); e.preventDefault(); }
      else if (activeScreen.id === 'screen-clarify') { document.getElementById('btn-clarify-next').click(); e.preventDefault(); }
    }
  });
}

// ─── Quick-add Task Bar ───────────────────────────────────────────────────────

function initQuickAdd() {
  const input = document.getElementById('quick-add-input');
  const btn = document.getElementById('btn-quick-add');
  if (!input || !btn) return;

  async function addQuickTask() {
    const title = input.value.trim();
    if (!title || !currentGoalId) return;
    try {
      await api.post(`/api/goals/${currentGoalId}/tasks`, { title, description: '' });
      input.value = '';
      toast.success('Added', `Task "${title}" created`);
      await refreshGoalView();
    } catch (e) {
      toast.error('Error', e.message);
    }
  }

  btn.addEventListener('click', addQuickTask);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addQuickTask(); }
  });
}

// ─── Touch Gesture Support (swipe to complete) ───────────────────────────────

function initTouchGestures() {
  let _touchStartX = 0;
  let _touchEl = null;

  document.addEventListener('touchstart', (e) => {
    const taskEl = e.target.closest('.task-item, .kanban-card, .drip-card');
    if (!taskEl) return;
    _touchStartX = e.touches[0].clientX;
    _touchEl = taskEl;
  }, { passive: true });

  document.addEventListener('touchmove', (e) => {
    if (!_touchEl) return;
    const dx = e.touches[0].clientX - _touchStartX;
    if (dx < -50) {
      _touchEl.classList.add('swiped');
    } else {
      _touchEl.classList.remove('swiped');
    }
  }, { passive: true });

  document.addEventListener('touchend', () => {
    if (_touchEl && _touchEl.classList.contains('swiped')) {
      const taskId = _touchEl.dataset.taskId;
      if (taskId && currentGoalId) {
        api.patch(`/api/goals/${currentGoalId}/tasks/${taskId}`, { status: 'done' })
          .then(() => { toast.success('Done', 'Task completed!'); refreshGoalView(); })
          .catch(() => {});
      }
    }
    if (_touchEl) _touchEl.classList.remove('swiped');
    _touchEl = null;
  }, { passive: true });
}

// ─── Celebration animation ────────────────────────────────────────────────────

function showCelebration(emoji = '🎉') {
  const el = document.createElement('div');
  el.className = 'celebration-burst';
  el.textContent = emoji;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 700);
}

// ─── Header user display ──────────────────────────────────────────────────────

function updateHeaderUser() {
  const email = localStorage.getItem('teb_email');
  const emailEl = document.getElementById('header-user-email');
  const authBtn = document.getElementById('btn-header-auth');
  if (email) {
    if (emailEl) emailEl.textContent = email;
    if (authBtn) authBtn.style.display = 'none';
  } else {
    if (emailEl) emailEl.textContent = '';
    if (authBtn) authBtn.style.display = 'inline-flex';
  }
}

function showError(elId, msg) {
  const el = document.getElementById(elId);
  if (el) el.textContent = msg || '';
}

// ─── Relative time formatting ─────────────────────────────────────────────────

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const now = new Date();
  const past = new Date(dateStr);
  const diffMs = now - past;
  const diffMin = Math.floor(diffMs / 60000);
  const diffHr = Math.floor(diffMs / 3600000);
  const diffDay = Math.floor(diffMs / 86400000);

  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay < 7) return `${diffDay}d ago`;
  if (diffDay < 30) return `${Math.floor(diffDay / 7)}w ago`;
  return past.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ─── Skeleton loaders ─────────────────────────────────────────────────────────

function showSkeleton(container, count, type) {
  const html = [];
  for (let i = 0; i < count; i++) {
    if (type === 'card') {
      html.push('<div class="skeleton skeleton-card"></div>');
    } else if (type === 'stat') {
      html.push('<div class="skeleton skeleton-stat"></div>');
    } else {
      html.push('<div class="skeleton skeleton-text"></div>');
      html.push('<div class="skeleton skeleton-text"></div>');
      html.push('<div class="skeleton skeleton-text" style="width:65%"></div>');
    }
  }
  container.innerHTML = html.join('');
}

// ─── Celebration effect ───────────────────────────────────────────────────────

function triggerCelebration() {
  const overlay = document.createElement('div');
  overlay.className = 'celebration-overlay';
  document.body.appendChild(overlay);

  const colors = ['#3b82f6', '#8b5cf6', '#22c55e', '#f59e0b', '#ef4444', '#ec4899'];
  for (let i = 0; i < 40; i++) {
    const particle = document.createElement('div');
    particle.className = 'confetti-particle';
    particle.style.left = Math.random() * 100 + '%';
    particle.style.top = '-10px';
    particle.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
    particle.style.setProperty('--duration', (1.5 + Math.random() * 1.5) + 's');
    particle.style.animationDelay = Math.random() * 0.5 + 's';
    particle.style.width = (6 + Math.random() * 6) + 'px';
    particle.style.height = (6 + Math.random() * 6) + 'px';
    overlay.appendChild(particle);
  }
  setTimeout(() => overlay.remove(), 3500);
}

// ─── Animated counter ─────────────────────────────────────────────────────────

function animateCounter(el, targetValue) {
  const start = parseInt(el.textContent, 10) || 0;
  const diff = targetValue - start;
  if (diff === 0) { el.textContent = targetValue; return; }
  const duration = 600;
  const startTime = performance.now();

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    // ease-out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(start + diff * eased);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ─── Debounce ─────────────────────────────────────────────────────────────────

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// ─── Character counter ───────────────────────────────────────────────────────

function setupCharCounter(textareaId, counterId) {
  const textarea = document.getElementById(textareaId);
  const counter = document.getElementById(counterId);
  if (!textarea || !counter) return;
  const max = parseInt(textarea.getAttribute('maxlength'), 10);
  if (!max) return;

  function update() {
    const len = textarea.value.length;
    counter.textContent = `${len} / ${max}`;
    counter.classList.remove('near-limit', 'at-limit');
    if (len >= max) counter.classList.add('at-limit');
    else if (len >= max * 0.9) counter.classList.add('near-limit');
  }
  textarea.addEventListener('input', update);
  update();
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

function updateUserBar() {
  const token = localStorage.getItem('teb_token');
  const email = localStorage.getItem('teb_email');
  const bar = document.getElementById('user-bar');
  if (!bar) return;
  if (token && email) {
    const emailEl = document.getElementById('user-email');
    if (emailEl) emailEl.textContent = email;
    bar.style.display = 'flex';
  } else {
    bar.style.display = 'none';
  }
}

on('btn-auth-submit', 'click', async () => {
  const emailEl = document.getElementById('auth-email');
  const passEl = document.getElementById('auth-password');
  const email = emailEl ? emailEl.value.trim() : '';
  const password = passEl ? passEl.value : '';
  showError('error-auth', '');
  if (!email || !password) { showError('error-auth', 'Please enter email and password.'); return; }

  const btn = document.getElementById('btn-auth-submit');
  if (btn) btn.disabled = true;
  try {
    const endpoint = authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const res = await api.post(endpoint, { email, password });
    localStorage.setItem('teb_token', res.token);
    localStorage.setItem('teb_email', res.user.email);
    updateUserBar();
    updateHeaderUser();
    Router.navigate('#/home');
    toast.success('Welcome!', authMode === 'register' ? 'Account created successfully.' : 'Signed in.');
  } catch (e) {
    showError('error-auth', e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
});

on('auth-toggle-link', 'click', (e) => {
  e.preventDefault();
  authMode = authMode === 'login' ? 'register' : 'login';
  const authTitle = document.getElementById('auth-title');
  const authSubmit = document.getElementById('btn-auth-submit');
  const authToggleText = document.getElementById('auth-toggle-text');
  const authToggleLink = document.getElementById('auth-toggle-link');
  if (authTitle) authTitle.textContent = authMode === 'register' ? 'Create account' : 'Sign in';
  if (authSubmit) authSubmit.textContent = authMode === 'register' ? 'Register' : 'Sign in';
  if (authToggleText) authToggleText.textContent =
    authMode === 'register' ? 'Already have an account?' : "Don't have an account?";
  if (authToggleLink) authToggleLink.textContent =
    authMode === 'register' ? 'Sign in' : 'Register';
  showError('error-auth', '');
});

on('auth-skip-link', 'click', (e) => {
  e.preventDefault();
  localStorage.removeItem('teb_token');
  localStorage.removeItem('teb_email');
  updateUserBar();
  updateHeaderUser();
  Router.navigate('#/home');
});

on('btn-logout', 'click', () => {
  localStorage.removeItem('teb_token');
  localStorage.removeItem('teb_email');
  updateUserBar();
  updateHeaderUser();
  Router.navigate('#/auth');
  toast.info('Signed out', 'You have been logged out.');
});

on('auth-password', 'keydown', e => {
  if (e.key === 'Enter') {
    const btn = document.getElementById('btn-auth-submit');
    if (btn) btn.click();
  }
});

// ─── Landing screen ───────────────────────────────────────────────────────────

async function loadGoalList() {
  const ul = document.getElementById('goal-list');
  showSkeleton(ul, 3, 'card');
  try {
    const goals = await api.get('/api/goals');
    ul.innerHTML = '';
    // Update sidebar and command palette with goal list
    updateSidebarGoals(goals);
    CommandPalette._goals = goals;

    if (!goals.length) {
      ul.innerHTML = `
        <li class="empty-state-large">
          <div class="empty-state-icon">📌</div>
          <div class="empty-state-title">No goals yet</div>
          <div class="empty-state-desc">Define your first objective above to get started.</div>
        </li>`;
      return;
    }
    goals.forEach(g => {
      const li = document.createElement('li');
      li.className = 'goal-item';
      li.innerHTML = `
        <div>
          <span class="goal-item-title">${escHtml(g.title)}</span>
          <div style="font-size:var(--text-xs);color:var(--muted);margin-top:.15rem">${timeAgo(g.created_at)}</div>
        </div>
        <span class="goal-item-status status-${g.status}">${g.status.replace('_', ' ')}</span>
      `;
      li.addEventListener('click', () => openGoal(g.id));
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = '';
    console.warn('Could not load goal list', e);
  }
}

async function openGoal(goalId) {
  currentGoalId = goalId;
  try {
    const goal = await api.get(`/api/goals/${goalId}`);
    if (goal.status === 'decomposed' || goal.status === 'in_progress' || goal.status === 'done') {
      Router.navigate(`#/goal/${goalId}`);
      showTasksScreen(goal);
    } else {
      await startClarifyFlow(goal);
    }
  } catch (e) {
    showError('error-landing', e.message);
  }
}

on('btn-create-goal', 'click', async () => {
  const titleEl = document.getElementById('goal-title');
  const descEl = document.getElementById('goal-desc');
  const title = titleEl ? titleEl.value.trim() : '';
  const desc = descEl ? descEl.value.trim() : '';
  showError('error-landing', '');
  if (!title) { showError('error-landing', 'Please enter a goal.'); return; }

  const btn = document.getElementById('btn-create-goal');
  if (btn) { btn.disabled = true; btn.innerHTML = 'Working… <span class="spinner"></span>'; }

  try {
    const goal = await api.post('/api/goals', { title, description: desc });
    currentGoalId = goal.id;
    await startClarifyFlow(goal);
  } catch (e) {
    showError('error-landing', e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Decompose →'; }
  }
});

// Enter key on goal title input
on('goal-title', 'keydown', e => {
  if (e.key === 'Enter') {
    const btn = document.getElementById('btn-create-goal');
    if (btn) btn.click();
  }
});

// ─── Clarify screen ───────────────────────────────────────────────────────────

async function startClarifyFlow(goal) {
  const titleEl = document.getElementById('clarify-goal-title');
  if (titleEl) titleEl.textContent = goal.title;
  const q = await api.get(`/api/goals/${goal.id}/next_question`);
  if (q.done) {
    await triggerDecompose(goal.id);
    return;
  }
  showQuestion(q.question);
  showScreen('screen-clarify');
}

function showQuestion(q) {
  const qText = document.getElementById('clarify-question-text');
  const answerEl = document.getElementById('clarify-answer');
  if (qText) qText.textContent = q.text;
  if (answerEl) {
    answerEl.placeholder = q.hint || '';
    answerEl.value = '';
    answerEl.dataset.key = q.key;
    answerEl.focus();
  }
}

on('btn-clarify-next', 'click', submitClarifyAnswer);
on('clarify-answer', 'keydown', e => {
  if (e.key === 'Enter') submitClarifyAnswer();
});

async function submitClarifyAnswer() {
  const input = document.getElementById('clarify-answer');
  const answer = input ? input.value.trim() : '';
  const key = input ? input.dataset.key : '';
  showError('error-clarify', '');
  if (!answer) { showError('error-clarify', 'Please enter an answer (or click "Skip").'); return; }

  const btn = document.getElementById('btn-clarify-next');
  if (btn) btn.disabled = true;
  try {
    const res = await api.post(`/api/goals/${currentGoalId}/clarify`, { key, answer });
    if (res.done) {
      await triggerDecompose(currentGoalId);
    } else {
      showQuestion(res.next_question);
    }
  } catch (e) {
    showError('error-clarify', e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

on('btn-skip-clarify', 'click', async () => {
  await triggerDecompose(currentGoalId);
});

on('back-from-clarify', 'click', () => {
  Router.navigate('#/home');
});

// ─── Decompose ────────────────────────────────────────────────────────────────

async function triggerDecompose(goalId) {
  showLoading('Decomposing…');
  try {
    await api.post(`/api/goals/${goalId}/decompose`, {});
    const goal = await api.get(`/api/goals/${goalId}`);
    hideLoading();
    await showTasksScreen(goal, /* freshDecompose */ true);
  } catch (e) {
    hideLoading();
    showError('error-clarify', e.message);
  }
}

// ─── Tasks screen ─────────────────────────────────────────────────────────────

async function showTasksScreen(goal, freshDecompose) {
  currentGoalId = goal.id;
  currentGoalTitle = goal.title;
  const goalTitleEl = document.getElementById('tasks-goal-title');
  if (goalTitleEl) goalTitleEl.textContent = goal.title;
  currentTasks = goal.tasks || [];
  renderTasks(currentTasks);
  updateProgress(currentTasks);
  loadDrip();
  loadFocusTask();
  loadProgressDetail();
  loadCheckinHistory();
  loadOutcomeMetrics();
  loadNudge();
  loadAutopilotStatus();
  loadRoiDashboard();
  loadPlatformInsights();
  loadAgentActivity();
  showScreen('screen-tasks');
  updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: goal.title}]);

  // Smart default: show all tasks view when there are many tasks (e.g. from AI Orchestrate),
  // use drip mode only for fresh decompositions with fewer tasks
  const topLevel = currentTasks.filter(t => t.parent_id === null);
  const useDrip = freshDecompose || topLevel.length <= 8;
  setDripMode(useDrip);

  // After a fresh decompose, auto-fetch outcome suggestions
  if (freshDecompose) {
    await autoSuggestOutcomes(goal.id);
  }

  // Load proactive suggestions and service discovery in background
  loadProactiveSuggestions();

  // Initialize view switcher toolbar
  ViewSwitcher.init();
  if (_currentViewType !== 'list') {
    ViewSwitcher.loadView(_currentViewType);
  }
}

// ─── Drip Mode ────────────────────────────────────────────────────────────────

function setDripMode(on) {
  dripMode = on;
  const dripSection = document.getElementById('drip-section');
  const allTasksSection = document.getElementById('all-tasks-section');
  const toggleBtn = document.getElementById('btn-toggle-view');
  if (dripSection) dripSection.style.display = on ? 'block' : 'none';
  if (allTasksSection) allTasksSection.style.display = on ? 'none' : 'block';
  if (toggleBtn) toggleBtn.textContent = on ? 'Show all tasks' : 'Switch to drip mode';
  if (on) loadDrip();
}

on('btn-toggle-view', 'click', () => {
  setDripMode(!dripMode);
});

async function loadDrip() {
  if (!currentGoalId) return;
  try {
    const res = await api.get(`/api/goals/${currentGoalId}/drip`);
    const card = document.getElementById('drip-card');
    const doneMsg = document.getElementById('drip-done-msg');
    const msg = document.getElementById('drip-message');

    if (!res.task) {
      if (card) card.style.display = 'none';
      if (doneMsg) doneMsg.style.display = 'block';
      // BUG-05: Only show "All tasks completed" when all tasks are actually done
      const allDone = currentTasks.length > 0 &&
        currentTasks.filter(t => t.status === 'done').length === currentTasks.length;
      const doneTitle = document.getElementById('drip-done-title');
      const doneDesc = document.getElementById('drip-done-desc');
      if (allDone || (res.message && res.message.includes('well done'))) {
        if (doneTitle) doneTitle.textContent = 'All tasks completed!';
        if (doneDesc) doneDesc.textContent = 'Great job — you\'ve finished everything on your list.';
      } else {
        if (doneTitle) doneTitle.textContent = 'No tasks yet';
        if (doneDesc) doneDesc.textContent = res.message || 'Click "AI Orchestrate" or decompose your goal to get started.';
      }
      if (msg) msg.textContent = '';
      return;
    }

    if (doneMsg) doneMsg.style.display = 'none';
    if (card) {
      card.style.display = 'block';
      card.dataset.taskId = res.task.id;
    }
    const dripTitle = document.getElementById('drip-title');
    const dripDesc = document.getElementById('drip-desc');
    const dripMeta = document.getElementById('drip-meta');
    if (dripTitle) dripTitle.textContent = res.task.title;
    if (dripDesc) dripDesc.textContent = res.task.description;
    if (dripMeta) dripMeta.textContent = `~${res.task.estimated_minutes} min`;
    if (msg) msg.textContent = res.message || '';

    // Skip suggestion (P2.2)
    const skipSug = document.getElementById('drip-skip-suggestion');
    if (skipSug) {
      if (res.skip_suggestion) {
        skipSug.textContent = res.skip_suggestion;
        skipSug.style.display = 'block';
      } else {
        skipSug.style.display = 'none';
      }
    }

    // Stall detection (P2.3)
    const stallMsg = document.getElementById('drip-stall-msg');
    if (stallMsg) {
      if (res.stall_detected) {
        stallMsg.textContent = res.message;
        if (res.sub_task_suggestion) {
          stallMsg.textContent += ` Suggested mini-task: "${res.sub_task_suggestion.title}"`;
        }
        stallMsg.style.display = 'block';
      } else {
        stallMsg.style.display = 'none';
      }
    }

    // Adaptive question
    const aqSection = document.getElementById('drip-adaptive-question');
    if (aqSection) {
      if (res.adaptive_question) {
        const qText = document.getElementById('drip-q-text');
        const qAnswer = document.getElementById('drip-q-answer');
        if (qText) qText.textContent = res.adaptive_question.text;
        if (qAnswer) {
          qAnswer.placeholder = res.adaptive_question.hint || '';
          qAnswer.value = '';
          qAnswer.dataset.key = res.adaptive_question.key;
        }
        aqSection.style.display = 'block';
      } else {
        aqSection.style.display = 'none';
      }
    }
  } catch (e) {
    const dripMsg = document.getElementById('drip-message');
    if (dripMsg) dripMsg.textContent = 'Could not load next task.';
  }
}

// BUG-06: Don't parseInt task IDs — they may be UUIDs/strings
on('btn-drip-done', 'click', async () => {
  const card = document.getElementById('drip-card');
  const tid = card ? card.dataset.taskId : null;
  if (!tid) return;
  try {
    await api.patch(`/api/tasks/${tid}`, { status: 'done' });
    toast.success('Task completed!', 'Great job — keep it up.');
    await refreshGoalView();
    loadDrip();
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

on('btn-drip-skip', 'click', async () => {
  const card = document.getElementById('drip-card');
  const tid = card ? card.dataset.taskId : null;
  if (!tid) return;
  try {
    await api.patch(`/api/tasks/${tid}`, { status: 'skipped' });
    await refreshGoalView();
    loadDrip();
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

on('btn-drip-q-submit', 'click', async () => {
  const input = document.getElementById('drip-q-answer');
  const answer = input ? input.value.trim() : '';
  const key = input ? input.dataset.key : '';
  if (!answer || !key) return;
  try {
    await api.post(`/api/goals/${currentGoalId}/drip/clarify`, { key, answer });
    const aq = document.getElementById('drip-adaptive-question');
    if (aq) aq.style.display = 'none';
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

// ─── Full task list view ──────────────────────────────────────────────────────

const MAX_DECOMPOSE_DEPTH = 3;

function renderTasks(tasks) {
  const topLevel = tasks.filter(t => t.parent_id === null);
  const byParent = {};
  tasks.forEach(t => {
    if (t.parent_id !== null) {
      (byParent[t.parent_id] = byParent[t.parent_id] || []).push(t);
    }
  });

  const container = document.getElementById('task-list');
  container.innerHTML = '';

  if (!topLevel.length) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">📋</div>
        <div class="empty-state-title">No tasks yet</div>
        <div class="empty-state-desc">Tasks will appear here once your goal is decomposed.</div>
      </div>`;
    return;
  }

  topLevel.forEach(task => {
    const subtasks = byParent[task.id] || [];
    container.appendChild(buildTaskCard(task, subtasks, byParent, 0));
  });
}

function buildTaskCard(task, subtasks, byParent, depth) {
  const card = document.createElement('div');
  card.className = `task-card task-item${task.status === 'done' ? ' done-card' : ''}`;
  card.dataset.id = task.id;
  card.dataset.taskId = task.id;

  const cbClass = task.status === 'done' ? 'checked' : '';
  const hasSubtasks = subtasks.length > 0;
  const canDecompose = !hasSubtasks && task.status !== 'done' && depth < MAX_DECOMPOSE_DEPTH;
  const subtaskDone = hasSubtasks ? subtasks.filter(s => s.status === 'done').length : 0;
  const subtaskPct = hasSubtasks ? Math.round((subtaskDone / subtasks.length) * 100) : 0;

  card.innerHTML = `
    <div class="task-header">
      <input type="checkbox" class="task-select-checkbox" data-id="${task.id}" title="Select for batch" aria-label="Select task" />
      <div class="task-checkbox ${cbClass}" data-id="${task.id}" title="Mark done"></div>
      <div class="task-info">
        <div class="task-title task-title-editable" contenteditable="true" data-task-id="${task.id}" spellcheck="false">${escHtml(task.title)}</div>
        <div class="task-meta">~${task.estimated_minutes} min${hasSubtasks ? ` · ${subtasks.length} sub-tasks` : ''}</div>
        ${hasSubtasks ? `<div class="subtask-progress">
          <div class="subtask-progress-bar"><div class="subtask-progress-fill" style="width:${subtaskPct}%"></div></div>
          <span>${subtaskDone}/${subtasks.length}</span>
        </div>` : ''}
      </div>
      <button class="task-expand-btn" aria-label="expand">▾</button>
    </div>
    <div class="task-body" style="display:none">
      <p class="task-desc">${escHtml(task.description)}</p>
      <div class="task-actions">
        <select class="task-status-select" data-id="${task.id}">
          <option value="todo"${task.status === 'todo' ? ' selected' : ''}>To do</option>
          <option value="in_progress"${task.status === 'in_progress' ? ' selected' : ''}>In progress</option>
          <option value="done"${task.status === 'done' ? ' selected' : ''}>Done</option>
          <option value="skipped"${task.status === 'skipped' ? ' selected' : ''}>Skip</option>
        </select>
        ${canDecompose ? `<button class="btn-break-down" data-id="${task.id}">🔍 Break down further</button>` : ''}
        <button class="btn-secondary btn-sm" data-detail-id="${task.id}">📝 Details</button>
        <button class="btn-delete-task" data-id="${task.id}" title="Delete task">🗑</button>
      </div>
      ${hasSubtasks ? buildSubtaskList(subtasks, byParent, depth + 1) : ''}
    </div>
  `;

  // Toggle expand
  card.querySelector('.task-expand-btn').addEventListener('click', () => {
    const body = card.querySelector('.task-body');
    const expanded = body.style.display !== 'none';
    body.style.display = expanded ? 'none' : 'block';
    card.querySelector('.task-expand-btn').textContent = expanded ? '▾' : '▴';
  });

  // Checkbox click → toggle done (celebrate when completing, not undoing)
  card.querySelector('.task-checkbox').addEventListener('click', (e) => {
    const wasNotDone = task.status !== 'done';
    toggleTaskDone(task);
    if (wasNotDone) showCelebration('✅');
  });

  // Batch select checkbox
  card.querySelector('.task-select-checkbox').addEventListener('change', (e) => {
    BatchOps.toggle(task.id);
  });

  // Inline title editing
  const titleEl = card.querySelector('.task-title-editable');
  titleEl.addEventListener('blur', async () => {
    const newTitle = titleEl.textContent.trim();
    if (newTitle && newTitle !== task.title) {
      try {
        await api.patch(`/api/goals/${currentGoalId}/tasks/${task.id}`, { title: newTitle });
        task.title = newTitle;
      } catch (e) { titleEl.textContent = task.title; toast.error('Error', e.message); }
    }
  });
  titleEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
    if (e.key === 'Escape') { titleEl.textContent = task.title; titleEl.blur(); }
  });

  // Detail panel button
  const detailBtn = card.querySelector('[data-detail-id]');
  if (detailBtn) {
    detailBtn.addEventListener('click', () => TaskDetailPanel.open(task));
  }

  // Status select
  card.querySelector('.task-status-select').addEventListener('change', async (e) => {
    await patchTaskStatus(task.id, e.target.value);
  });

  // Break down button
  const breakBtn = card.querySelector('.btn-break-down');
  if (breakBtn) {
    breakBtn.addEventListener('click', () => decomposeTask(task.id));
  }

  // Delete button
  card.querySelector('.btn-delete-task').addEventListener('click', () => deleteTask(task.id));

  // Sub-task checkboxes
  card.querySelectorAll('.subtask-cb').forEach(cb => {
    cb.addEventListener('click', () => {
      const tid = parseInt(cb.dataset.id, 10);
      const sub = currentTasks.find(t => t.id === tid);
      if (sub) toggleTaskDone(sub);
    });
  });

  // Sub-task break-down buttons
  card.querySelectorAll('.btn-break-down-sub').forEach(btn => {
    btn.addEventListener('click', () => decomposeTask(parseInt(btn.dataset.id, 10)));
  });

  // Sub-task delete buttons
  card.querySelectorAll('.btn-delete-sub').forEach(btn => {
    btn.addEventListener('click', () => deleteTask(parseInt(btn.dataset.id, 10)));
  });

  return card;
}

function buildSubtaskList(subtasks, byParent, depth) {
  const items = subtasks.map(s => {
    const grandkids = (byParent && byParent[s.id]) || [];
    const hasGrandkids = grandkids.length > 0;
    const canDecompose = !hasGrandkids && s.status !== 'done' && depth < MAX_DECOMPOSE_DEPTH;
    return `
    <div class="subtask-item">
      <div class="subtask-cb ${s.status === 'done' ? 'checked' : ''}" data-id="${s.id}"></div>
      <div>
        <div class="subtask-title">${escHtml(s.title)}</div>
        <div class="subtask-meta">~${s.estimated_minutes} min${hasGrandkids ? ` · ${grandkids.length} sub-tasks` : ''}</div>
        <div class="subtask-actions">
          ${canDecompose ? `<button class="btn-break-down-sub" data-id="${s.id}">🔍 Break down</button>` : ''}
          <button class="btn-delete-sub" data-id="${s.id}" title="Delete">🗑</button>
        </div>
        ${hasGrandkids ? buildSubtaskList(grandkids, byParent, depth + 1) : ''}
      </div>
    </div>
  `}).join('');
  return `<div class="subtask-list">${items}</div>`;
}

async function toggleTaskDone(task) {
  const newStatus = task.status === 'done' ? 'todo' : 'done';
  await patchTaskStatus(task.id, newStatus);
  if (newStatus === 'done') {
    // Check if all tasks are done → celebration
    const goal = await api.get(`/api/goals/${currentGoalId}`).catch(() => null);
    if (goal) {
      const topLevel = (goal.tasks || []).filter(t => t.parent_id === null);
      const allDone = topLevel.length > 0 && topLevel.every(t => t.status === 'done' || t.status === 'skipped');
      if (allDone) triggerCelebration();
    }
  }
}

async function patchTaskStatus(taskId, status) {
  showError('error-tasks', '');
  try {
    await api.patch(`/api/tasks/${taskId}`, { status });
    await refreshGoalView();
  } catch (e) {
    showError('error-tasks', e.message);
  }
}

async function decomposeTask(taskId) {
  showError('error-tasks', '');
  try {
    await api.post(`/api/tasks/${taskId}/decompose`, {});
    await refreshGoalView();
    toast.info('Broken down', 'Task has been decomposed into sub-tasks.');
  } catch (e) {
    showError('error-tasks', e.message);
  }
}

async function deleteTask(taskId) {
  if (!confirm('Delete this task and all its sub-tasks? This cannot be undone.')) return;
  showError('error-tasks', '');
  try {
    await api.del(`/api/tasks/${taskId}`);
    await refreshGoalView();
    toast.info('Deleted', 'Task removed.');
  } catch (e) {
    showError('error-tasks', e.message);
  }
}

async function refreshGoalView() {
  const goal = await api.get(`/api/goals/${currentGoalId}`);
  currentTasks = goal.tasks || [];
  renderTasks(currentTasks);
  updateProgress(currentTasks);
  loadFocusTask();
  loadProgressDetail();
}

async function loadFocusTask() {
  const banner = document.getElementById('focus-banner');
  if (!banner) return;
  if (!currentGoalId) { banner.style.display = 'none'; return; }
  try {
    const res = await api.get(`/api/goals/${currentGoalId}/focus`);
    if (!res.focus_task) {
      banner.style.display = 'none';
      return;
    }
    const t = res.focus_task;
    const focusTitle = document.getElementById('focus-title');
    const focusDesc = document.getElementById('focus-desc');
    const focusMeta = document.getElementById('focus-meta');
    if (focusTitle) focusTitle.textContent = t.title;
    if (focusDesc) focusDesc.textContent = t.description;
    if (focusMeta) focusMeta.textContent = `~${t.estimated_minutes} min`;
    banner.style.display = 'block';
    banner.dataset.taskId = t.id;
  } catch (e) {
    banner.style.display = 'none';
  }
}

async function loadProgressDetail() {
  const el = document.getElementById('progress-detail');
  if (!el) return;
  if (!currentGoalId) { el.textContent = ''; return; }
  try {
    const p = await api.get(`/api/goals/${currentGoalId}/progress`);
    const parts = [];
    if (p.done) parts.push(`${p.done} done`);
    if (p.in_progress) parts.push(`${p.in_progress} in progress`);
    if (p.todo) parts.push(`${p.todo} remaining`);
    if (p.estimated_remaining_minutes > 0) {
      const hrs = Math.floor(p.estimated_remaining_minutes / 60);
      const mins = p.estimated_remaining_minutes % 60;
      const timeStr = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
      parts.push(`~${timeStr} left`);
    }
    el.textContent = parts.join(' · ');

    // 7.1: Surface stall detection in progress view
    const stallBanner = document.getElementById('stall-banner');
    if (stallBanner && p.stall_detected) {
      stallBanner.textContent = `⚠️ ${p.stall_message || 'A task appears stalled.'}`;
      if (p.sub_task_suggestion) {
        stallBanner.textContent += ` Try: "${p.sub_task_suggestion.title}"`;
      }
      stallBanner.style.display = 'block';
    } else if (stallBanner) {
      stallBanner.style.display = 'none';
    }
  } catch (e) {
    el.textContent = '';
  }
}

on('btn-focus-done', 'click', async () => {
  const banner = document.getElementById('focus-banner');
  const tid = banner ? banner.dataset.taskId : null;
  if (tid) {
    await patchTaskStatus(tid, 'done');
    toast.success('Done!', 'Task marked as completed.');
  }
});

on('btn-focus-start', 'click', async () => {
  const banner = document.getElementById('focus-banner');
  const tid = banner ? banner.dataset.taskId : null;
  if (tid) await patchTaskStatus(tid, 'in_progress');
});

function updateProgress(tasks) {
  const topLevel = tasks.filter(t => t.parent_id === null);
  if (!topLevel.length) { setProgress(0); renderStatusChart([]); return; }
  const done = topLevel.filter(t => t.status === 'done' || t.status === 'skipped').length;
  setProgress(Math.round((done / topLevel.length) * 100));
  renderStatusChart(topLevel);
}

function setProgress(pct) {
  const fill = document.getElementById('progress-fill');
  const label = document.getElementById('progress-label');
  if (fill) fill.style.width = pct + '%';
  if (label) label.textContent = pct + '% complete';
}

function renderStatusChart(tasks) {
  const container = document.getElementById('status-chart');
  if (!container) return;
  if (!tasks.length) { container.innerHTML = ''; return; }

  const counts = { done: 0, in_progress: 0, todo: 0, failed: 0, executing: 0, skipped: 0 };
  tasks.forEach(t => { counts[t.status] = (counts[t.status] || 0) + 1; });
  const total = tasks.length;
  const colors = {
    done: 'var(--color-success, #22c55e)', in_progress: 'var(--color-warning, #f59e0b)',
    todo: 'var(--text-muted, #94a3b8)', failed: 'var(--color-error, #ef4444)',
    executing: 'var(--color-info, #3b82f6)', skipped: '#64748b'
  };
  const labels = { done: 'Done', in_progress: 'In progress', todo: 'To do', failed: 'Failed', executing: 'Executing', skipped: 'Skipped' };

  // Build SVG donut chart
  const size = 100, cx = 50, cy = 50, r = 38, stroke = 10;
  const circ = 2 * Math.PI * r;
  let offset = 0;
  let segments = '';
  let legend = '';

  for (const [status, count] of Object.entries(counts)) {
    if (count === 0) continue;
    const pct = count / total;
    const len = pct * circ;
    segments += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[status]}" `
      + `stroke-width="${stroke}" stroke-dasharray="${len} ${circ - len}" `
      + `stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})" />`;
    offset += len;
    legend += `<span class="chart-legend-item"><span class="chart-legend-dot" style="background:${colors[status]}"></span>${labels[status]} ${count}</span>`;
  }

  container.innerHTML = `
    <div class="status-chart-wrap">
      <svg viewBox="0 0 ${size} ${size}" class="donut-chart">
        ${segments}
        <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central"
              class="donut-center-text">${counts.done}/${total}</text>
      </svg>
      <div class="chart-legend">${legend}</div>
    </div>`;
}

on('back-from-tasks', 'click', () => {
  Router.navigate('#/home');
});

on('btn-redecompose', 'click', async () => {
  if (!currentGoalId) return;
  showError('error-tasks', '');
  const btn = document.getElementById('btn-redecompose');
  if (btn) btn.disabled = true;
  showLoading('Re-generating tasks…');
  try {
    await api.post(`/api/goals/${currentGoalId}/decompose`, {});
    const goal = await api.get(`/api/goals/${currentGoalId}`);
    hideLoading();
    showTasksScreen(goal);
  } catch (e) {
    hideLoading();
    showError('error-tasks', e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
});

on('btn-add-task', 'click', async () => {
  if (!currentGoalId) return;
  const title = prompt('Task title:');
  if (!title || !title.trim()) return;
  showError('error-tasks', '');
  try {
    await api.post('/api/tasks', {
      goal_id: currentGoalId,
      title: title.trim(),
      description: '',
      estimated_minutes: 30,
    });
    await refreshGoalView();
    toast.success('Added', 'New task created.');
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

// ─── Autopilot toggle ─────────────────────────────────────────────────────────

async function loadAutopilotStatus() {
  if (!currentGoalId) return;
  try {
    const goal = await api.get(`/api/goals/${currentGoalId}`);
    autopilotEnabled = !!goal.auto_execute;
    const toggle = document.getElementById('autopilot-toggle');
    const status = document.getElementById('autopilot-status');
    if (toggle) toggle.checked = autopilotEnabled;
    if (status) status.textContent = autopilotEnabled ? 'On' : 'Off';
  } catch (e) {
    // Silent fail
  }
}

on('autopilot-toggle', 'change', async (e) => {
  const enabled = e.target.checked;
  const status = document.getElementById('autopilot-status');
  try {
    if (enabled) {
      await api.post(`/api/goals/${currentGoalId}/auto-execute`, {});
      autopilotEnabled = true;
      if (status) status.textContent = 'On';
      toast.info('Autopilot enabled', 'Tasks will be executed automatically.');
      // Check if a budget exists; if not, show the budget prompt
      await checkBudgetPrompt();
    } else {
      await api.del(`/api/goals/${currentGoalId}/auto-execute`);
      autopilotEnabled = false;
      if (status) status.textContent = 'Off';
      const bp = document.getElementById('budget-prompt');
      if (bp) bp.style.display = 'none';
    }
  } catch (e) {
    e.target.checked = !enabled;
    showError('error-tasks', e.message);
  }
});

async function checkBudgetPrompt() {
  const bp = document.getElementById('budget-prompt');
  if (!bp) return;
  try {
    const budgets = await api.get(`/api/goals/${currentGoalId}/budgets`);
    if (!budgets || !budgets.length) {
      bp.style.display = 'block';
    } else {
      bp.style.display = 'none';
    }
  } catch (e) {
    // Show prompt if we can't determine
    bp.style.display = 'block';
  }
}

on('btn-set-budget', 'click', async () => {
  const dailyEl = document.getElementById('budget-daily');
  const totalEl = document.getElementById('budget-total');
  const daily = parseFloat(dailyEl ? dailyEl.value : '') || 50;
  const total = parseFloat(totalEl ? totalEl.value : '') || 500;
  showError('error-budget', '');
  try {
    await api.post('/api/budgets', {
      goal_id: currentGoalId,
      daily_limit: daily,
      total_limit: total,
      category: 'general',
      require_approval: true,
      autopilot_enabled: true,
      autopilot_threshold: daily,
    });
    const bp = document.getElementById('budget-prompt');
    if (bp) bp.style.display = 'none';
    toast.success('Budget set', `Daily: $${daily}, Total: $${total}`);
  } catch (e) {
    showError('error-budget', e.message);
  }
});

// ─── AI Orchestrate ───────────────────────────────────────────────────────────

on('btn-orchestrate', 'click', async () => {
  const btn = document.getElementById('btn-orchestrate');
  const panel = document.getElementById('agent-activity-panel');
  const content = document.getElementById('agent-activity-content');

  btn.disabled = true;
  btn.textContent = 'Orchestrating…';
  panel.style.display = 'block';
  content.innerHTML = '<div class="agent-loading"><div class="loading-spinner-sm"></div><span>Dispatching agents…</span></div>';

  try {
    const result = await api.post(`/api/goals/${currentGoalId}/orchestrate`, {});
    const handoffs = result.handoffs || [];
    const messages = result.messages || [];

    let html = '';

    // Build agent activity timeline
    if (handoffs.length || messages.length) {
      html += '<div class="agent-timeline">';

      // Strategy summary
      if (result.strategy) {
        html += `<div class="agent-timeline-item agent-strategy">
          <div class="agent-timeline-icon">◆</div>
          <div class="agent-timeline-body">
            <div class="agent-timeline-title">Strategy</div>
            <div class="agent-timeline-text">${escHtml(result.strategy)}</div>
          </div>
        </div>`;
      }

      // Handoff chain as timeline
      handoffs.forEach(h => {
        const statusIcon = h.status === 'completed' ? '✅' : h.status === 'failed' ? '❌' : '⏳';
        html += `<div class="agent-timeline-item">
          <div class="agent-timeline-icon">${statusIcon}</div>
          <div class="agent-timeline-body">
            <div class="agent-timeline-title">
              <span class="agent-badge agent-from">${escHtml(h.from_agent || '')}</span>
              <span class="agent-arrow">→</span>
              <span class="agent-badge agent-to">${escHtml(h.to_agent || '')}</span>
            </div>
            ${h.input_summary ? `<div class="agent-timeline-input">${escHtml(h.input_summary)}</div>` : ''}
            ${h.output_summary ? `<div class="agent-timeline-output">${escHtml(h.output_summary)}</div>` : ''}
          </div>
        </div>`;
      });

      // Agent messages
      if (messages.length) {
        html += '<div class="agent-messages-section">';
        html += '<div class="agent-messages-title">Agent Communication</div>';
        messages.slice(0, 15).forEach(m => {
          const typeIcon = m.message_type === 'request' ? '?' : m.message_type === 'response' ? '→' : m.message_type === 'context' ? '•' : 'i';
          html += `<div class="agent-msg-card">
            <span class="agent-msg-icon">${typeIcon}</span>
            <span class="agent-badge agent-from">${escHtml(m.from_agent || '')}</span>
            <span class="agent-arrow">→</span>
            <span class="agent-badge agent-to">${escHtml(m.to_agent || '')}</span>
            <div class="agent-msg-content">${escHtml(m.content || '')}</div>
          </div>`;
        });
        html += '</div>';
      }

      html += '</div>';
    }

    if (!html) html = '<p class="agent-empty">Orchestration complete. Tasks have been created.</p>';
    content.innerHTML = html;
    panel.open = true;
    // Refresh tasks after orchestration
    await refreshGoalView();
    toast.success('Orchestration complete', `${handoffs.length} agent handoffs, tasks updated.`);
  } catch (e) {
    content.innerHTML = `<p class="error">${escHtml(e.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Orchestrate';
  }
});

// ─── Auto outcome suggestions ─────────────────────────────────────────────────

async function autoSuggestOutcomes(goalId) {
  try {
    const suggestions = await api.get(`/api/goals/${goalId}/outcome_suggestions`);
    if (!suggestions || !suggestions.length) return;

    _pendingOutcomeSuggestions = suggestions;
    const banner = document.getElementById('outcome-suggestions-banner');
    const text = document.getElementById('outcome-suggestions-text');
    const labels = suggestions.map(s => escHtml(s.label)).join(', ');
    text.innerHTML = `We suggest tracking these metrics: <strong>${labels}</strong>. Add them?`;
    banner.style.display = 'flex';
  } catch (e) {
    // Silent fail — suggestions are optional
  }
}

// ─── Agent activity (load on page view, not just after orchestrate) ───────────

async function loadAgentActivity() {
  if (!currentGoalId) return;
  const panel = document.getElementById('agent-activity-panel');
  const content = document.getElementById('agent-activity-content');
  try {
    const data = await api.get(`/api/goals/${currentGoalId}/agent-activity`);
    const handoffs = data.handoffs || [];
    const messages = data.messages || [];
    if (!handoffs.length && !messages.length) {
      panel.style.display = 'none';
      return;
    }
    panel.style.display = 'block';

    let html = '<div class="agent-timeline">';

    // Agent summary
    const agents = data.agents_involved || [];
    const tasksByAgent = data.tasks_by_agent || {};
    if (agents.length) {
      html += `<div class="agent-timeline-item agent-strategy">
        <div class="agent-timeline-icon">●</div>
        <div class="agent-timeline-body">
          <div class="agent-timeline-title">Agents: ${agents.map(a => `<span class="agent-badge agent-from">${escHtml(a)}</span>`).join(' ')}</div>
          <div class="agent-timeline-text">${data.total_tasks_created || 0} tasks created${Object.keys(tasksByAgent).length ? ' — ' + Object.entries(tasksByAgent).map(([a, c]) => `${a}: ${c}`).join(', ') : ''}</div>
        </div>
      </div>`;
    }

    // Handoffs
    handoffs.forEach(h => {
      const statusIcon = h.status === 'completed' ? '✅' : h.status === 'failed' ? '❌' : '⏳';
      html += `<div class="agent-timeline-item">
        <div class="agent-timeline-icon">${statusIcon}</div>
        <div class="agent-timeline-body">
          <div class="agent-timeline-title">
            <span class="agent-badge agent-from">${escHtml(h.from_agent || '')}</span>
            <span class="agent-arrow">→</span>
            <span class="agent-badge agent-to">${escHtml(h.to_agent || '')}</span>
          </div>
          ${h.input_summary ? `<div class="agent-timeline-input">${escHtml(h.input_summary)}</div>` : ''}
          ${h.output_summary ? `<div class="agent-timeline-output">${escHtml(h.output_summary)}</div>` : ''}
        </div>
      </div>`;
    });

    // Messages
    if (messages.length) {
      html += '<div class="agent-messages-section">';
      html += '<div class="agent-messages-title">Agent Communication</div>';
      messages.slice(0, 10).forEach(m => {
        const typeIcon = m.message_type === 'request' ? '?' : m.message_type === 'response' ? '→' : m.message_type === 'context' ? '•' : 'i';
        html += `<div class="agent-msg-card">
          <span class="agent-msg-icon">${typeIcon}</span>
          <span class="agent-badge agent-from">${escHtml(m.from_agent || '')}</span>
          <span class="agent-arrow">→</span>
          <span class="agent-badge agent-to">${escHtml(m.to_agent || '')}</span>
          <div class="agent-msg-content">${escHtml(m.content || '')}</div>
        </div>`;
      });
      html += '</div>';
    }

    html += '</div>';
    content.innerHTML = html;
  } catch (e) {
    panel.style.display = 'none';
  }
}

on('btn-add-all-outcomes', 'click', async () => {
  const banner = document.getElementById('outcome-suggestions-banner');
  if (!_pendingOutcomeSuggestions) return;
  try {
    for (const s of _pendingOutcomeSuggestions) {
      await api.post(`/api/goals/${currentGoalId}/outcomes`, {
        label: s.label,
        target_value: s.target_value,
        unit: s.unit || '',
      });
    }
    _pendingOutcomeSuggestions = null;
    if (banner) banner.style.display = 'none';
    loadOutcomeMetrics();
    toast.success('Metrics added', 'Outcome metrics are now being tracked.');
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

on('btn-skip-outcomes', 'click', () => {
  _pendingOutcomeSuggestions = null;
  const banner = document.getElementById('outcome-suggestions-banner');
  if (banner) banner.style.display = 'none';
});

// ─── Proactive suggestions ────────────────────────────────────────────────────

async function loadProactiveSuggestions() {
  if (!currentGoalId) return;
  try {
    const suggestions = await api.get(`/api/goals/${currentGoalId}/suggestions`);
    const container = document.getElementById('suggestions-list');
    const countEl = document.getElementById('suggestions-count');

    const active = (suggestions || []).filter(s => s.status === 'pending');
    if (countEl) countEl.textContent = active.length ? `(${active.length})` : '';

    if (!active.length) {
      container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-md)">
          <div class="empty-state-desc">No suggestions right now.</div>
        </div>`;
      return;
    }

    container.innerHTML = active.map(s => `
      <div class="suggestion-item" data-id="${s.id}">
        <div class="suggestion-text">${escHtml(s.suggestion || s.content || s.message || '')}</div>
        <div class="suggestion-rationale">${s.rationale ? '<span class="suggestion-category badge">' + escHtml(s.category || '') + '</span> ' + escHtml(s.rationale) : ''}</div>
        <div class="suggestion-actions">
          <button class="btn-accept-suggestion btn-primary btn-sm" data-id="${s.id}">Accept</button>
          <button class="btn-dismiss-suggestion btn-secondary btn-sm" data-id="${s.id}">Dismiss</button>
        </div>
      </div>
    `).join('');

    container.querySelectorAll('.btn-accept-suggestion').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await api.post(`/api/suggestions/${btn.dataset.id}`, { status: 'accepted' });
          loadProactiveSuggestions();
        } catch (e) { /* silent */ }
      });
    });
    container.querySelectorAll('.btn-dismiss-suggestion').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await api.post(`/api/suggestions/${btn.dataset.id}`, { status: 'dismissed' });
          loadProactiveSuggestions();
        } catch (e) { /* silent */ }
      });
    });
  } catch (e) {
    // Silent fail — suggestions are optional
  }
}

// ─── Service discovery ────────────────────────────────────────────────────────

on('btn-discover', 'click', async () => {
  const btn = document.getElementById('btn-discover');
  const container = document.getElementById('discovery-list');
  if (btn) { btn.disabled = true; btn.textContent = 'Searching…'; }
  try {
    const params = currentGoalTitle ? `?goal_title=${encodeURIComponent(currentGoalTitle)}` : '';
    const res = await api.get(`/api/discover/services${params}`);
    const services = res.services || res || [];
    if (!services.length) {
      if (container) container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-md)">
          <div class="empty-state-icon">🔍</div>
          <div class="empty-state-desc">No matching services found.</div>
        </div>`;
    } else {
      if (container) container.innerHTML = services.slice(0, 10).map(s => {
        const category = s.category ? `<span class="badge">${escHtml(s.category)}</span>` : '';
        const skillLevel = s.skill_level ? `<span class="badge">${escHtml(s.skill_level)}</span>` : '';
        return `
        <div class="discovery-item">
          <div>
            <div class="discovery-name">${escHtml(s.name || s.service_name || '')} ${category} ${skillLevel}</div>
            <div class="discovery-desc">${escHtml(s.description || '')}</div>
            ${s.website || s.url ? `<a href="${escHtml(s.website || s.url)}" target="_blank" rel="noopener" class="discovery-link">Visit →</a>` : ''}
          </div>
        </div>`;
      }).join('');
    }
  } catch (e) {
    if (container) container.innerHTML = `<p class="error">${escHtml(e.message)}</p>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Find matching services'; }
  }
});

// ─── Settings Modal ───────────────────────────────────────────────────────────

on('btn-settings', 'click', () => {
  showSettingsModal();
});

on('btn-close-settings', 'click', () => {
  const modal = document.getElementById('settings-modal');
  if (modal) modal.style.display = 'none';
});

// Settings tabs
document.querySelectorAll('.settings-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.settings-tab').forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.settings-pane').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
    const pane = document.getElementById('pane-' + tab.dataset.tab);
    if (pane) pane.classList.add('active');
  });
});

on('btn-tg-save', 'click', async () => {
  const tokenEl = document.getElementById('tg-bot-token');
  const chatIdEl = document.getElementById('tg-chat-id');
  const token = tokenEl ? tokenEl.value.trim() : '';
  const chatId = chatIdEl ? chatIdEl.value.trim() : '';
  showError('error-tg', '');
  if (!token || !chatId) { showError('error-tg', 'Both bot token and chat ID are required.'); return; }
  try {
    await api.post('/api/messaging/config', {
      channel: 'telegram',
      config: { bot_token: token, chat_id: chatId },
      notify_nudges: document.getElementById('notif-nudges')?.checked ?? false,
      notify_tasks: document.getElementById('notif-tasks')?.checked ?? false,
      notify_spending: document.getElementById('notif-spending')?.checked ?? false,
      notify_checkins: document.getElementById('notif-checkins')?.checked ?? false,
    });
    showError('error-tg', '');
    loadExistingConfigs();
    toast.success('Saved', 'Telegram config updated.');
  } catch (e) {
    showError('error-tg', e.message);
  }
});

on('btn-tg-test', 'click', async () => {
  showError('error-tg', '');
  const tokenEl = document.getElementById('tg-bot-token');
  const chatIdEl = document.getElementById('tg-chat-id');
  const token = tokenEl ? tokenEl.value.trim() : '';
  const chatId = chatIdEl ? chatIdEl.value.trim() : '';
  if (!token || !chatId) { showError('error-tg', 'Save a config first.'); return; }
  try {
    const cfg = await api.post('/api/messaging/config', {
      channel: 'telegram',
      config: { bot_token: token, chat_id: chatId },
      notify_nudges: true, notify_tasks: true, notify_spending: true, notify_checkins: false,
    });
    await api.post(`/api/messaging/test/${cfg.id}`);
    toast.success('Test sent', 'Telegram test message dispatched.');
  } catch (e) {
    showError('error-tg', e.message);
  }
});

on('btn-wh-save', 'click', async () => {
  const urlEl = document.getElementById('wh-url');
  const url = urlEl ? urlEl.value.trim() : '';
  showError('error-wh', '');
  if (!url) { showError('error-wh', 'URL is required.'); return; }
  try {
    await api.post('/api/messaging/config', {
      channel: 'webhook',
      config: { url },
      notify_nudges: document.getElementById('notif-nudges')?.checked ?? false,
      notify_tasks: document.getElementById('notif-tasks')?.checked ?? false,
      notify_spending: document.getElementById('notif-spending')?.checked ?? false,
      notify_checkins: document.getElementById('notif-checkins')?.checked ?? false,
    });
    showError('error-wh', '');
    loadExistingConfigs();
    toast.success('Saved', 'Webhook config updated.');
  } catch (e) {
    showError('error-wh', e.message);
  }
});

on('btn-wh-test', 'click', async () => {
  showError('error-wh', '');
  const urlEl = document.getElementById('wh-url');
  const url = urlEl ? urlEl.value.trim() : '';
  if (!url) { showError('error-wh', 'Save a config first.'); return; }
  try {
    const cfg = await api.post('/api/messaging/config', {
      channel: 'webhook',
      config: { url },
      notify_nudges: true, notify_tasks: true, notify_spending: true, notify_checkins: false,
    });
    await api.post(`/api/messaging/test/${cfg.id}`);
    toast.success('Test sent', 'Webhook test message dispatched.');
  } catch (e) {
    showError('error-wh', e.message);
  }
});

async function loadExistingConfigs() {
  try {
    const configs = await api.get('/api/messaging/configs');
    const container = document.getElementById('existing-configs');
    if (!configs.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:var(--text-xs)">No messaging configs yet.</p>';
      return;
    }
    container.innerHTML = '<h3 class="mt">Active Configs</h3>' + configs.map(c => `
      <div class="config-item">
        <span class="config-channel">${escHtml(c.channel)}</span>
        <span class="config-status">${c.enabled ? '✅' : '❌'}</span>
        <button class="btn-delete-config btn-secondary btn-sm" data-id="${c.id}">Delete</button>
      </div>
    `).join('');
    container.querySelectorAll('.btn-delete-config').forEach(btn => {
      btn.addEventListener('click', async () => {
        await api.del(`/api/messaging/config/${btn.dataset.id}`);
        loadExistingConfigs();
      });
    });
  } catch (e) {
    // Silent fail
  }
}

// ─── Credential vault ─────────────────────────────────────────────────────────

async function loadCredentials() {
  try {
    const creds = await api.get('/api/credentials');
    const container = document.getElementById('credentials-list');
    if (!creds || !creds.length) {
      container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-md)">
          <div class="empty-state-icon">🔑</div>
          <div class="empty-state-desc">No credentials stored yet.</div>
        </div>`;
      return;
    }
    container.innerHTML = creds.map(c => `
      <div class="credential-item">
        <div class="credential-info">
          <span class="credential-name">${escHtml(c.name)}</span>
          <span class="credential-url">${escHtml(c.base_url)}</span>
        </div>
        <button class="btn-delete-credential btn-secondary btn-sm" data-id="${c.id}">Delete</button>
      </div>
    `).join('');
    container.querySelectorAll('.btn-delete-credential').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await api.del(`/api/credentials/${btn.dataset.id}`);
          loadCredentials();
          toast.info('Removed', 'Credential deleted.');
        } catch (e) {
          showError('error-credential', e.message);
        }
      });
    });
  } catch (e) {
    // Silent fail
  }
}

on('btn-add-credential', 'click', async () => {
  const nameEl = document.getElementById('cred-name');
  const baseUrlEl = document.getElementById('cred-base-url');
  const authHeaderEl = document.getElementById('cred-auth-header');
  const authValueEl = document.getElementById('cred-auth-value');
  const descEl = document.getElementById('cred-desc');
  const name = nameEl ? nameEl.value.trim() : '';
  const baseUrl = baseUrlEl ? baseUrlEl.value.trim() : '';
  const authHeader = (authHeaderEl ? authHeaderEl.value.trim() : '') || 'Authorization';
  const authValue = authValueEl ? authValueEl.value.trim() : '';
  const desc = descEl ? descEl.value.trim() : '';
  showError('error-credential', '');
  if (!name || !baseUrl) { showError('error-credential', 'Name and base URL are required.'); return; }
  try {
    await api.post('/api/credentials', {
      name, base_url: baseUrl, auth_header: authHeader, auth_value: authValue, description: desc,
    });
    if (nameEl) nameEl.value = '';
    if (baseUrlEl) baseUrlEl.value = '';
    if (authValueEl) authValueEl.value = '';
    if (descEl) descEl.value = '';
    loadCredentials();
    toast.success('Added', 'Credential stored securely.');
  } catch (e) {
    showError('error-credential', e.message);
  }
});

// ─── Keyboard shortcuts ──────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  // Escape to close modals
  if (e.key === 'Escape') {
    const settingsModal = document.getElementById('settings-modal');
    const adminModal = document.getElementById('admin-modal');
    if (settingsModal && settingsModal.style.display !== 'none') {
      settingsModal.style.display = 'none';
    } else if (adminModal && adminModal.style.display !== 'none') {
      adminModal.style.display = 'none';
    }
  }
});

// Click outside modal to close
on('settings-modal', 'click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.style.display = 'none';
  }
});
on('admin-modal', 'click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.style.display = 'none';
  }
});

// ─── Init ─────────────────────────────────────────────────────────────────────

function showSettingsModal() {
  const modal = document.getElementById('settings-modal');
  if (modal) modal.style.display = 'flex';
  loadExistingConfigs();
  loadCredentials();
}

function showAdminModal() {
  const modal = document.getElementById('admin-modal');
  if (modal) modal.style.display = 'flex';
  loadAdminStats();
  loadAdminUsers();
  loadAdminIntegrations();
}

function init() {
  initTheme();
  initSidebar();
  initKeyboardShortcuts();
  initQuickAdd();
  initTouchGestures();
  CommandPalette.init();
  TaskDetailPanel.init();
  BatchOps.init();
  setupCharCounter('goal-desc', 'goal-desc-counter');

  // Dark mode toggle
  const themeBtn = document.getElementById('btn-theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  // Header auth button
  document.getElementById('btn-header-auth')?.addEventListener('click', () => Router.navigate('#/auth'));

  const token = localStorage.getItem('teb_token');
  updateUserBar();
  updateHeaderUser();
  if (token) {
    // Check admin status and update sidebar admin button
    api.get('/api/auth/me').then(me => {
      const sidebarAdmin = document.getElementById('btn-sidebar-admin');
      if (sidebarAdmin) sidebarAdmin.style.display = me && me.role === 'admin' ? '' : 'none';
    }).catch(() => {});
  }

  // Initialize router (handles initial route)
  Router.init();
}

init();

// ─── Daily Check-in ───────────────────────────────────────────────────────────

on('btn-checkin', 'click', submitCheckin);

async function submitCheckin() {
  const doneEl = document.getElementById('checkin-done');
  const blockersEl = document.getElementById('checkin-blockers');
  const done = doneEl ? doneEl.value.trim() : '';
  const blockers = blockersEl ? blockersEl.value.trim() : '';
  if (!done && !blockers) return;

  const btn = document.getElementById('btn-checkin');
  if (btn) btn.disabled = true;
  try {
    const res = await api.post(`/api/goals/${currentGoalId}/checkin`, {
      done_summary: done,
      blockers: blockers,
    });
    // Show coaching feedback
    const fb = document.getElementById('checkin-feedback');
    if (fb) { fb.textContent = res.coaching; fb.style.display = 'block'; }
    // Clear inputs
    if (doneEl) doneEl.value = '';
    if (blockersEl) blockersEl.value = '';
    // Refresh history and nudge
    loadCheckinHistory();
    loadNudge();
    toast.success('Check-in submitted', 'Keep up the momentum!');
  } catch (e) {
    showError('error-tasks', e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadCheckinHistory() {
  if (!currentGoalId) return;
  try {
    const checkins = await api.get(`/api/goals/${currentGoalId}/checkins?limit=5`);
    const container = document.getElementById('checkin-history');
    if (!checkins.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:var(--text-xs)">No check-ins yet.</p>';
      return;
    }
    container.innerHTML = checkins.map(ci => {
      const date = new Date(ci.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      return `<div class="checkin-history-item">
        <span class="checkin-history-date">${date}</span>
        <span class="checkin-history-mood mood-${ci.mood}">${ci.mood}</span>
        <div>${escHtml(ci.done_summary || ci.blockers)}</div>
      </div>`;
    }).join('');
  } catch (e) {
    // Silent fail for history
  }
}

// ─── Nudge system ─────────────────────────────────────────────────────────────

async function loadNudge() {
  if (!currentGoalId) return;
  try {
    const res = await api.get(`/api/goals/${currentGoalId}/nudge`);
    const banner = document.getElementById('nudge-banner');
    if (!banner) return;
    if (res.nudge) {
      const nudgeMsg = document.getElementById('nudge-message');
      if (nudgeMsg) nudgeMsg.textContent = res.nudge.message;
      banner.style.display = 'flex';
      banner.dataset.nudgeId = res.nudge.id;
    } else {
      banner.style.display = 'none';
    }
  } catch (e) {
    // Silent fail
  }
}

on('btn-nudge-ack', 'click', async () => {
  const banner = document.getElementById('nudge-banner');
  if (!banner) return;
  const nudgeId = banner.dataset.nudgeId;
  if (nudgeId) {
    try {
      await api.post(`/api/nudges/${nudgeId}/acknowledge`, {});
    } catch (e) { /* ignore */ }
  }
  banner.style.display = 'none';
});

// ─── Outcome Metrics ──────────────────────────────────────────────────────────

async function loadOutcomeMetrics() {
  if (!currentGoalId) return;
  try {
    const metrics = await api.get(`/api/goals/${currentGoalId}/outcomes`);
    const container = document.getElementById('outcome-metrics-list');
    if (!metrics.length) {
      container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-md)">
          <div class="empty-state-icon">📊</div>
          <div class="empty-state-desc">No outcome metrics yet. Add one to track real results.</div>
        </div>`;
      return;
    }
    container.innerHTML = metrics.map(m => {
      const pct = Math.min(m.achievement_pct, 100);
      const r = 28, circ = 2 * Math.PI * r, filled = (pct / 100) * circ;
      const ringColor = pct >= 100 ? 'var(--color-success, #22c55e)' : pct >= 50 ? 'var(--color-warning, #f59e0b)' : 'var(--color-info, #3b82f6)';
      return `
      <div class="outcome-metric-card">
        <svg viewBox="0 0 64 64" class="outcome-ring">
          <circle cx="32" cy="32" r="${r}" fill="none" stroke="var(--border, #334155)" stroke-width="6" />
          <circle cx="32" cy="32" r="${r}" fill="none" stroke="${ringColor}" stroke-width="6"
            stroke-dasharray="${filled} ${circ - filled}" stroke-dashoffset="${circ * 0.25}"
            stroke-linecap="round" transform="rotate(-90 32 32)" />
          <text x="32" y="32" text-anchor="middle" dominant-baseline="central"
            class="outcome-ring-text">${pct}%</text>
        </svg>
        <div class="outcome-metric-info">
          <div class="outcome-metric-label">${escHtml(m.label)}</div>
          <div class="outcome-metric-values">${m.current_value}${m.unit ? ' ' + escHtml(m.unit) : ''} / ${m.target_value}${m.unit ? ' ' + escHtml(m.unit) : ''}</div>
        </div>
        <button class="outcome-metric-update" data-id="${m.id}" data-current="${m.current_value}">Update</button>
      </div>`;
    }).join('');
    // Bind update buttons
    container.querySelectorAll('.outcome-metric-update').forEach(btn => {
      btn.addEventListener('click', async () => {
        const metricId = btn.dataset.id;
        const current = btn.dataset.current;
        const val = prompt(`New value (current: ${current}):`, current);
        if (val === null) return;
        const num = parseFloat(val);
        if (isNaN(num)) return;
        try {
          await api.patch(`/api/outcomes/${metricId}`, { current_value: num });
          loadOutcomeMetrics();
          toast.success('Updated', 'Metric value saved.');
        } catch (e) {
          showError('error-tasks', e.message);
        }
      });
    });
  } catch (e) {
    // Silent fail
  }
}

on('btn-suggest-outcomes', 'click', async () => {
  if (!currentGoalId) return;
  try {
    const suggestions = await api.get(`/api/goals/${currentGoalId}/outcome_suggestions`);
    for (const s of suggestions) {
      await api.post(`/api/goals/${currentGoalId}/outcomes`, {
        label: s.label,
        target_value: s.target_value,
        unit: s.unit || '',
      });
    }
    loadOutcomeMetrics();
    toast.success('Added', 'Suggested metrics are now being tracked.');
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

on('btn-add-outcome', 'click', async () => {
  if (!currentGoalId) return;
  const label = prompt('Metric name (e.g. "Revenue earned", "Chapters completed"):');
  if (!label || !label.trim()) return;
  const target = prompt('Target value:', '10');
  if (target === null) return;
  const unit = prompt('Unit (e.g. "$", "chapters", "hours"):', '');
  try {
    await api.post(`/api/goals/${currentGoalId}/outcomes`, {
      label: label.trim(),
      target_value: parseFloat(target) || 0,
      unit: unit || '',
    });
    loadOutcomeMetrics();
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

// ─── ROI Dashboard ────────────────────────────────────────────────────────────

async function loadRoiDashboard() {
  if (!currentGoalId) return;
  const panel = document.getElementById('roi-dashboard-panel');
  if (!panel) return;

  try {
    const roi = await api.get(`/api/goals/${currentGoalId}/roi`);

    // Summary cards
    const cards = document.getElementById('roi-summary-cards');
    const roiClass = roi.net_profit >= 0 ? 'roi-positive' : 'roi-negative';
    const roiDisplay = roi.roi_percent === null ? 'N/A' : `${roi.roi_percent}%`;
    cards.innerHTML = `
      <div class="roi-card">
        <div class="roi-card-value roi-negative">$${roi.total_spent.toFixed(2)}</div>
        <div class="roi-card-label">Total Spent</div>
      </div>
      <div class="roi-card">
        <div class="roi-card-value roi-positive">$${roi.total_earned.toFixed(2)}</div>
        <div class="roi-card-label">Total Earned</div>
      </div>
      <div class="roi-card">
        <div class="roi-card-value ${roiClass}">$${roi.net_profit.toFixed(2)}</div>
        <div class="roi-card-label">Net Profit</div>
      </div>
      <div class="roi-card">
        <div class="roi-card-value ${roiClass}">${roiDisplay}</div>
        <div class="roi-card-label">ROI</div>
      </div>
      ${roi.pending_requests > 0 ? `<div class="roi-card roi-card-warn">
        <div class="roi-card-value">${roi.pending_requests}</div>
        <div class="roi-card-label">Pending Approvals</div>
      </div>` : ''}
      ${roi.failed_transactions > 0 ? `<div class="roi-card roi-card-error">
        <div class="roi-card-value">${roi.failed_transactions}</div>
        <div class="roi-card-label">Failed Transactions</div>
      </div>` : ''}
    `;

    // Spending by category breakdown
    const breakdown = document.getElementById('roi-spending-breakdown');
    const cats = Object.entries(roi.spending_by_category);
    if (cats.length > 0) {
      breakdown.innerHTML = `
        <h4>Spending by Category</h4>
        <div class="roi-bar-chart">
          ${cats.map(([cat, amt]) => {
            const pct = roi.total_spent > 0 ? ((amt / roi.total_spent) * 100).toFixed(0) : 0;
            return `<div class="roi-bar-row">
              <span class="roi-bar-label">${escHtml(cat)}</span>
              <div class="roi-bar-track">
                <div class="roi-bar-fill" style="width:${pct}%"></div>
              </div>
              <span class="roi-bar-amount">$${amt.toFixed(2)}</span>
            </div>`;
          }).join('')}
        </div>
      `;
    } else {
      breakdown.innerHTML = '<p class="sub">No spending data yet.</p>';
    }

    // Spending timeline
    const timeline = document.getElementById('roi-timeline');
    if (roi.spending_timeline && roi.spending_timeline.length > 0) {
      const maxAmt = Math.max(...roi.spending_timeline.map(d => d.amount));
      timeline.innerHTML = `
        <h4>Spending Timeline</h4>
        <div class="roi-timeline-chart">
          ${roi.spending_timeline.map(d => {
            const h = maxAmt > 0 ? ((d.amount / maxAmt) * 100).toFixed(0) : 0;
            return `<div class="roi-timeline-bar" title="$${d.amount} on ${d.date}">
              <div class="roi-timeline-fill" style="height:${h}%"></div>
              <span class="roi-timeline-label">${d.date.slice(5)}</span>
            </div>`;
          }).join('')}
        </div>
      `;
    } else {
      timeline.innerHTML = '';
    }

    // Earnings breakdown
    const earnings = document.getElementById('roi-earnings');
    if (roi.earnings_breakdown && roi.earnings_breakdown.length > 0) {
      earnings.innerHTML = `
        <h4>Earnings Sources</h4>
        ${roi.earnings_breakdown.map(e => {
          const pct = e.target_value > 0 ? Math.min(100, ((e.current_value / e.target_value) * 100)).toFixed(0) : 0;
          return `<div class="roi-earning-row">
            <span>${escHtml(e.label)}</span>
            <div class="progress-bar-bg" style="flex:1;margin:0 8px">
              <div class="progress-bar-fill" style="width:${pct}%"></div>
            </div>
            <span>$${e.current_value.toFixed(2)} / $${e.target_value.toFixed(2)}</span>
          </div>`;
        }).join('')}
      `;
    } else {
      earnings.innerHTML = '';
    }

    // Budget utilization
    const budgets = document.getElementById('roi-budgets');
    if (roi.budget_summary && roi.budget_summary.length > 0) {
      budgets.innerHTML = `
        <h4>Budget Utilization</h4>
        ${roi.budget_summary.map(b => `
          <div class="roi-budget-row">
            <span class="roi-budget-cat">${escHtml(b.category)}</span>
            <div class="progress-bar-bg" style="flex:1;margin:0 8px">
              <div class="progress-bar-fill ${b.utilization_pct > 90 ? 'warn' : ''}" style="width:${Math.min(100, b.utilization_pct)}%"></div>
            </div>
            <span class="roi-budget-nums">$${b.spent_total.toFixed(2)} / $${b.total_limit.toFixed(2)} (${b.utilization_pct}%)</span>
          </div>
        `).join('')}
      `;
    } else {
      budgets.innerHTML = '';
    }

    // Show/hide panel based on data
    const hasData = roi.total_spent > 0 || roi.total_earned > 0 || roi.pending_requests > 0;
    panel.style.display = hasData ? 'block' : 'none';
  } catch (e) {
    console.warn('ROI dashboard load failed:', e);
    panel.style.display = 'none';
  }
}


// ─── Platform Insights ────────────────────────────────────────────────────────

async function loadPlatformInsights() {
  const panel = document.getElementById('platform-insights-panel');
  if (!panel) return;

  try {
    const data = await api.get('/api/platform/insights');
    const content = document.getElementById('platform-insights-content');
    let html = '';

    // Goal type success rates
    const types = data.goal_type_insights || [];
    if (types.length > 0) {
      html += `<h4>Goal Success Rates</h4><div class="roi-bar-chart">`;
      for (const t of types) {
        html += `<div class="roi-bar-row">
          <span class="roi-bar-label">${escHtml(t.goal_type)} (${t.total_goals})</span>
          <div class="roi-bar-track">
            <div class="roi-bar-fill" style="width:${t.completion_rate}%"></div>
          </div>
          <span class="roi-bar-amount">${t.completion_rate}%</span>
        </div>`;
      }
      html += `</div>`;
    }

    // Task stats
    const ts = data.task_stats;
    if (ts && ts.total > 0) {
      html += `<h4>Platform Task Stats</h4>
        <div class="platform-task-stats">
          <span>✅ ${ts.done} done</span>
          <span>⏭ ${ts.skipped} skipped</span>
          <span>❌ ${ts.failed} failed</span>
          <span>📊 ${ts.completion_rate}% completion</span>
        </div>`;
    }

    // Proven paths
    const paths = data.proven_paths || [];
    if (paths.length > 0) {
      html += `<h4>Proven Success Paths</h4><ul class="platform-paths">`;
      for (const p of paths) {
        html += `<li><strong>${escHtml(p.goal_type)}</strong> — ${escHtml(p.outcome_summary || 'completed')} <span class="badge">${p.times_reused}× reused</span></li>`;
      }
      html += `</ul>`;
    }

    // Popular services
    const svcs = data.popular_services || [];
    if (svcs.length > 0) {
      html += `<h4>Popular Services</h4><div class="platform-services">`;
      for (const s of svcs) {
        html += `<span class="platform-service-tag">${escHtml(s.service)} <small>(${s.use_count}×, $${s.total_spent})</small></span> `;
      }
      html += `</div>`;
    }

    content.innerHTML = html || '<p class="sub">Not enough data yet. Keep working on goals!</p>';
    panel.style.display = 'block';
  } catch (e) {
    console.warn('Platform insights load failed:', e);
    panel.style.display = 'none';
  }
}

// ─── Admin Panel ──────────────────────────────────────────────────────────────

on('btn-admin', 'click', () => {
  showAdminModal();
});

on('btn-close-admin', 'click', () => {
  const modal = document.getElementById('admin-modal');
  if (modal) modal.style.display = 'none';
});

async function loadAdminStats() {
  const grid = document.getElementById('admin-stats-grid');
  showSkeleton(grid, 8, 'stat');
  try {
    const stats = await api.get('/api/admin/stats');
    grid.innerHTML = [
      { label: 'Total Users', value: stats.total_users },
      { label: 'Total Goals', value: stats.total_goals },
      { label: 'Total Tasks', value: stats.total_tasks },
      { label: 'Total Executions', value: stats.total_executions },
      { label: 'Active Goals', value: stats.active_goals },
      { label: 'Completed Goals', value: stats.goals_done },
      { label: 'Completed Tasks', value: stats.tasks_done },
      { label: 'Approved Spending', value: stats.spending_approved },
    ].map(s => `
      <div class="admin-stat-card">
        <div class="admin-stat-value" data-target="${s.value}">0</div>
        <div class="admin-stat-label">${escHtml(s.label)}</div>
      </div>
    `).join('');
    // Animate counters
    grid.querySelectorAll('.admin-stat-value').forEach(el => {
      animateCounter(el, parseInt(el.dataset.target, 10) || 0);
    });
  } catch (e) {
    grid.innerHTML = `<p class="error">${escHtml(e.message)}</p>`;
  }
}

async function loadAdminUsers() {
  showError('admin-users-error', '');
  try {
    const users = await api.get('/api/admin/users');
    _adminUsersCache = users;
    renderAdminUsers(users);
  } catch (e) {
    showError('admin-users-error', e.message);
  }
}

function renderAdminUsers(users) {
  const tbody = document.getElementById('admin-users-tbody');
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted)">No users found.</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(u => {
    const isLocked = u.locked_until && new Date(u.locked_until) > new Date();
    const statusLabel = isLocked
      ? `<span class="status-locked">Locked</span>`
      : `<span class="status-active">Active</span>`;
    const roleTarget = u.role === 'admin' ? 'user' : 'admin';
    const roleLabel = u.role === 'admin' ? 'Make User' : 'Make Admin';
    const roleBtn = `<button class="btn-secondary btn-xs admin-set-role" data-id="${u.id}" data-role="${roleTarget}">${roleLabel}</button>`;
    const unlockBtn = isLocked
      ? `<button class="btn-secondary btn-xs admin-unlock" data-id="${u.id}">Unlock</button>`
      : '';
    const deleteBtn = `<button class="btn-danger btn-xs admin-del-user" data-id="${u.id}">Delete</button>`;
    return `<tr>
      <td>${u.id}</td>
      <td>${escHtml(u.email)}</td>
      <td>${escHtml(u.role)}</td>
      <td>${u.created_at ? u.created_at.slice(0, 10) : ''}</td>
      <td>${statusLabel}</td>
      <td>${u.goals_count}</td>
      <td>${u.tasks_count}</td>
      <td class="admin-actions">${roleBtn}${unlockBtn}${deleteBtn}</td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('.admin-set-role').forEach(btn => {
    btn.addEventListener('click', () => adminSetRole(Number(btn.dataset.id), btn.dataset.role));
  });
  tbody.querySelectorAll('.admin-unlock').forEach(btn => {
    btn.addEventListener('click', () => adminUnlock(Number(btn.dataset.id)));
  });
  tbody.querySelectorAll('.admin-del-user').forEach(btn => {
    btn.addEventListener('click', () => adminDeleteUser(Number(btn.dataset.id)));
  });
}

// Debounced admin user search
const adminUserSearchInput = document.getElementById('admin-user-search');
if (adminUserSearchInput) {
  adminUserSearchInput.addEventListener('input', debounce(() => {
    const query = adminUserSearchInput.value.trim().toLowerCase();
    if (!query) {
      renderAdminUsers(_adminUsersCache);
      return;
    }
    const filtered = _adminUsersCache.filter(u =>
      u.email.toLowerCase().includes(query) ||
      String(u.id).includes(query)
    );
    renderAdminUsers(filtered);
  }, 200));
}

async function adminSetRole(userId, role) {
  try {
    await api.patch(`/api/admin/users/${userId}`, { role });
    loadAdminUsers();
    toast.success('Role updated', `User ${userId} is now ${role}.`);
  } catch (e) {
    showError('admin-users-error', e.message);
  }
}

async function adminUnlock(userId) {
  try {
    await api.patch(`/api/admin/users/${userId}`, { locked_until: 'null' });
    loadAdminUsers();
    toast.success('Unlocked', `User ${userId} has been unlocked.`);
  } catch (e) {
    showError('admin-users-error', e.message);
  }
}

async function adminDeleteUser(userId) {
  if (!confirm('Delete this user and all their data? This cannot be undone.')) return;
  try {
    await api.del(`/api/admin/users/${userId}`);
    loadAdminUsers();
    loadAdminStats();
    toast.success('Deleted', 'User and all their data removed.');
  } catch (e) {
    showError('admin-users-error', e.message);
  }
}

async function loadAdminIntegrations() {
  showError('admin-integrations-error', '');
  try {
    const integrations = await api.get('/api/admin/integrations');
    const tbody = document.getElementById('admin-integrations-tbody');
    if (!integrations.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted)">No integrations found.</td></tr>';
      return;
    }
    tbody.innerHTML = integrations.map(i => `
      <tr>
        <td>${escHtml(i.service_name)}</td>
        <td>${escHtml(i.category)}</td>
        <td><span style="font-size:var(--text-xs)">${escHtml(i.base_url)}</span></td>
        <td>${escHtml(i.auth_type)}</td>
        <td><button class="btn-danger btn-xs admin-del-integration" data-name="${escHtml(i.service_name)}">Delete</button></td>
      </tr>
    `).join('');
    tbody.querySelectorAll('.admin-del-integration').forEach(btn => {
      btn.addEventListener('click', () => adminDeleteIntegration(btn.dataset.name));
    });
  } catch (e) {
    showError('admin-integrations-error', e.message);
  }
}

async function adminDeleteIntegration(name) {
  if (!confirm(`Remove integration "${name}"? This cannot be undone.`)) return;
  try {
    await api.del(`/api/admin/integrations/${encodeURIComponent(name)}`);
    loadAdminIntegrations();
    toast.info('Removed', `Integration "${name}" deleted.`);
  } catch (e) {
    showError('admin-integrations-error', e.message);
  }
}

on('btn-admin-add-integration', 'click', async () => {
  const serviceNameEl = document.getElementById('ai-service-name');
  const serviceName = serviceNameEl ? serviceNameEl.value.trim() : '';
  if (!serviceName) { showError('admin-integrations-error', 'Service name is required.'); return; }
  const categoryEl = document.getElementById('ai-category');
  const baseUrlEl = document.getElementById('ai-base-url');
  const authTypeEl = document.getElementById('ai-auth-type');
  const authHeaderEl = document.getElementById('ai-auth-header');
  const docsUrlEl = document.getElementById('ai-docs-url');
  const capsRawEl = document.getElementById('ai-capabilities');
  const category = categoryEl ? categoryEl.value.trim() : '';
  const baseUrl = baseUrlEl ? baseUrlEl.value.trim() : '';
  const authType = authTypeEl ? authTypeEl.value : '';
  const authHeader = (authHeaderEl ? authHeaderEl.value.trim() : '') || 'Authorization';
  const docsUrl = docsUrlEl ? docsUrlEl.value.trim() : '';
  const capsRaw = capsRawEl ? capsRawEl.value.trim() : '';
  const capabilities = capsRaw ? capsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  let commonEndpoints = [];
  try {
    const epEl = document.getElementById('ai-endpoints');
    const ep = epEl ? epEl.value.trim() : '';
    if (ep) commonEndpoints = JSON.parse(ep);
  } catch (e) {
    showError('admin-integrations-error', 'Common endpoints must be valid JSON.');
    return;
  }
  try {
    await api.post('/api/admin/integrations', {
      service_name: serviceName, category, base_url: baseUrl,
      auth_type: authType, auth_header: authHeader, docs_url: docsUrl,
      capabilities, common_endpoints: commonEndpoints,
    });
    // Clear form fields
    ['ai-service-name','ai-category','ai-base-url','ai-auth-header','ai-docs-url',
     'ai-capabilities','ai-endpoints'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = id === 'ai-auth-header' ? 'Authorization' : '';
    });
    loadAdminIntegrations();
    toast.success('Added', `Integration "${serviceName}" created.`);
  } catch (e) {
    showError('admin-integrations-error', e.message);
  }
});

// ─── Progress ring animation ──────────────────────────────────────────────────

function updateProgressRing() {
  const fill = document.getElementById('progress-fill');
  if (!fill) return;
  const pct = parseInt(fill.style.width, 10) || 0;
  const label = document.getElementById('progress-label');
  if (label) {
    label.style.color = pct >= 100 ? 'var(--success)' : pct >= 50 ? 'var(--primary)' : 'var(--muted)';
    if (pct >= 100) label.style.fontWeight = '700';
  }
}

// Override setProgress to trigger ring animation
const _origSetProgress = setProgress;
setProgress = function(pct) {
  _origSetProgress(pct);
  updateProgressRing();
  // Trigger celebration at 100%
  if (pct >= 100) {
    const label = document.getElementById('progress-label');
    if (label && !label.dataset.celebrated) {
      label.dataset.celebrated = 'true';
      triggerCelebration();
    }
  }
};

// ─── Smooth input validation ──────────────────────────────────────────────────

function shakeInput(inputId) {
  const el = document.getElementById(inputId);
  if (!el) return;
  el.classList.add('input-error');
  el.addEventListener('animationend', () => el.classList.remove('input-error'), { once: true });
}

// ─── Auto-resize textareas ────────────────────────────────────────────────────

function autoResizeTextarea(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

document.querySelectorAll('textarea').forEach(ta => {
  ta.addEventListener('input', () => autoResizeTextarea(ta));
});

// ─── Connection status indicator ──────────────────────────────────────────────

let _lastOnlineState = navigator.onLine;

window.addEventListener('online', () => {
  if (!_lastOnlineState) {
    toast.success('Back online', 'Connection restored.');
    _lastOnlineState = true;
  }
});

window.addEventListener('offline', () => {
  toast.warning('Offline', 'You appear to be disconnected.');
  _lastOnlineState = false;
});

// ─── Periodic refresh for active goal ─────────────────────────────────────────

let _refreshInterval = null;

function startPeriodicRefresh() {
  stopPeriodicRefresh();
  _refreshInterval = setInterval(async () => {
    if (!currentGoalId) return;
    if (document.hidden) return; // Skip if tab not visible
    try {
      const goal = await api.get(`/api/goals/${currentGoalId}`);
      const oldDone = currentTasks.filter(t => t.status === 'done').length;
      currentTasks = goal.tasks || [];
      const newDone = currentTasks.filter(t => t.status === 'done').length;
      if (newDone !== oldDone) {
        renderTasks(currentTasks);
        updateProgress(currentTasks);
        loadProgressDetail();
        if (newDone > oldDone) {
          toast.info('Progress', `${newDone - oldDone} task(s) completed by autopilot.`);
        }
      }
    } catch (e) {
      // Silently ignore refresh failures
    }
  }, 30000); // Every 30 seconds
}

function stopPeriodicRefresh() {
  if (_refreshInterval) {
    clearInterval(_refreshInterval);
    _refreshInterval = null;
  }
}

// Start/stop periodic refresh based on autopilot status
const _autopilotToggleEl = document.getElementById('autopilot-toggle');
if (_autopilotToggleEl) {
  _autopilotToggleEl.addEventListener('change', () => {
    if (_autopilotToggleEl.checked) {
      startPeriodicRefresh();
    } else {
      stopPeriodicRefresh();
    }
  });
}

// ─── Page visibility API — pause/resume ───────────────────────────────────────

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentGoalId && autopilotEnabled) {
    // Refresh on tab return if autopilot is running
    refreshGoalView().catch(() => {});
    loadDrip().catch(() => {});
  }
});

// ─── Smart date formatting for admin table ────────────────────────────────────

function formatAdminDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  const diffDays = Math.floor((now - d) / 86400000);

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: diffDays > 365 ? 'numeric' : undefined });
}

// ─── Smooth scroll to section ─────────────────────────────────────────────────

function scrollToElement(el) {
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ─── Tab trap for modals (accessibility) ──────────────────────────────────────

function trapFocusInModal(modalEl) {
  if (!modalEl) return;
  const focusable = modalEl.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];

  modalEl.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });
}

// Initialize focus traps for modals
trapFocusInModal(document.getElementById('settings-modal'));
trapFocusInModal(document.getElementById('admin-modal'));

// ─── Batch API utility for outcome suggestions ───────────────────────────────

async function batchCreateOutcomes(goalId, suggestions) {
  const results = [];
  for (const s of suggestions) {
    try {
      const result = await api.post(`/api/goals/${goalId}/outcomes`, {
        label: s.label,
        target_value: s.target_value,
        unit: s.unit || '',
      });
      results.push(result);
    } catch (e) {
      console.warn('Failed to create outcome:', s.label, e);
    }
  }
  return results;
}

// ─── Session timeout warning ──────────────────────────────────────────────────

let _sessionWarningShown = false;

function checkSessionValidity() {
  const token = localStorage.getItem('teb_token');
  if (!token) return;

  try {
    // Decode JWT payload (base64)
    const parts = token.split('.');
    if (parts.length !== 3) return;
    const payload = JSON.parse(atob(parts[1]));
    if (payload.exp) {
      const expiresAt = payload.exp * 1000;
      const now = Date.now();
      const remaining = expiresAt - now;

      if (remaining < 0) {
        // Token expired
        localStorage.removeItem('teb_token');
        localStorage.removeItem('teb_email');
        updateUserBar();
        updateHeaderUser();
        Router.navigate('#/auth');
        toast.warning('Session expired', 'Please sign in again.');
      } else if (remaining < 300000 && !_sessionWarningShown) {
        // Less than 5 minutes remaining
        _sessionWarningShown = true;
        toast.warning('Session expiring', 'Your session will expire soon. Save your work.');
      }
    }
  } catch (e) {
    // Invalid token format — ignore
  }
}

// Check session every 60 seconds
setInterval(checkSessionValidity, 60000);
checkSessionValidity();

/* ── Phase 8 Polish: Confetti & Streak ────────────────────────────── */

function triggerConfetti() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const colors = ['#6c63ff', '#ff6584', '#43e97b', '#f9d423', '#38f9d7', '#fa709a'];
  for (let i = 0; i < 60; i++) {
    const p = document.createElement('div');
    p.className = 'confetti-piece';
    p.style.left = Math.random() * 100 + 'vw';
    p.style.top = '-10px';
    p.style.background = colors[Math.floor(Math.random() * colors.length)];
    p.style.animationDelay = Math.random() * 0.5 + 's';
    p.style.width = (6 + Math.random() * 8) + 'px';
    p.style.height = (6 + Math.random() * 8) + 'px';
    document.body.appendChild(p);
    setTimeout(() => p.remove(), 2500);
  }
}

function renderStreak(container, tasks) {
  if (!container) return;
  const days = new Set();
  (tasks || []).forEach(t => {
    if (t.status === 'done' && t.updated_at) days.add(t.updated_at.slice(0, 10));
  });
  let streak = 0;
  const d = new Date();
  while (days.has(d.toISOString().slice(0, 10))) {
    streak++;
    d.setDate(d.getDate() - 1);
  }
  container.innerHTML = '<div class="streak-badge">' +
    '<span class="streak-icon">🔥</span>' +
    '<span class="streak-count">' + streak + '</span> day streak</div>';
}

// ─── View Switching Toolbar (Phase 3, Item 4) ────────────────────────────────

const ViewSwitcher = {
  _views: [
    { key: 'list', label: 'List', icon: '📋' },
    { key: 'kanban', label: 'Kanban', icon: '📊' },
    { key: 'table', label: 'Table', icon: '📄' },
    { key: 'gantt', label: 'Gantt', icon: '📈' },
    { key: 'workload', label: 'Workload', icon: '⚖️' },
    { key: 'timeline', label: 'Timeline', icon: '🕐' },
    { key: 'calendar', label: 'Calendar', icon: '📅' },
    { key: 'mindmap', label: 'Mind Map', icon: '🧠' },
  ],

  render(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    let existing = container.querySelector('.view-switcher-toolbar');
    if (existing) existing.remove();

    const toolbar = document.createElement('div');
    toolbar.className = 'view-switcher-toolbar';

    this._views.forEach(v => {
      const btn = document.createElement('button');
      btn.className = 'view-switcher-btn' + (_currentViewType === v.key ? ' active' : '');
      btn.title = v.label;
      btn.innerHTML = `<span class="view-switcher-icon">${v.icon}</span><span class="view-switcher-label">${v.label}</span>`;
      btn.addEventListener('click', () => {
        _currentViewType = v.key;
        localStorage.setItem('teb_view_type', v.key);
        this.render(containerId);
        this.loadView(v.key);
      });
      toolbar.appendChild(btn);
    });

    // Save View button
    const saveBtn = document.createElement('button');
    saveBtn.className = 'view-switcher-btn view-save-btn';
    saveBtn.title = 'Save View';
    saveBtn.innerHTML = '<span class="view-switcher-icon">💾</span><span class="view-switcher-label">Save</span>';
    saveBtn.addEventListener('click', () => SavedViews.showSaveDialog());
    toolbar.appendChild(saveBtn);

    // Load Saved View dropdown
    const loadSelect = document.createElement('select');
    loadSelect.className = 'view-saved-select';
    loadSelect.innerHTML = '<option value="">Load saved view…</option>';
    loadSelect.addEventListener('change', async () => {
      if (loadSelect.value) {
        await SavedViews.loadView(loadSelect.value);
        loadSelect.value = '';
      }
    });
    toolbar.appendChild(loadSelect);
    SavedViews.populateDropdown(loadSelect);

    container.prepend(toolbar);
  },

  loadView(viewType) {
    const viewContainer = document.getElementById('view-render-area');
    if (!viewContainer) return;
    viewContainer.innerHTML = '';

    const tasks = currentTasks || [];

    switch (viewType) {
      case 'list':
        viewContainer.style.display = 'none';
        document.getElementById('task-list')?.style && (document.getElementById('task-list').style.display = '');
        return;
      case 'kanban':
        if (typeof KanbanView !== 'undefined') {
          KanbanView.render(tasks, viewContainer, {
            onStatusChange: async (taskId, status) => {
              try { await api.patch(`/api/tasks/${taskId}`, { status }); await refreshGoalView(); } catch(e) {}
            },
            onCardClick: (task) => { if (typeof TaskDetailPanel !== 'undefined') TaskDetailPanel.open(task); }
          });
        }
        break;
      case 'table':
        if (typeof TableView !== 'undefined') TableView.render(tasks, viewContainer);
        break;
      case 'gantt':
        if (typeof GanttView !== 'undefined') GanttView.render(tasks, viewContainer);
        break;
      case 'workload':
        if (typeof WorkloadView !== 'undefined') WorkloadView.render(tasks, viewContainer);
        break;
      case 'timeline':
        if (typeof TimelineView !== 'undefined') TimelineView.render(tasks, viewContainer);
        break;
      case 'calendar':
        if (typeof CalendarView !== 'undefined') CalendarView.render(tasks, viewContainer);
        break;
      case 'mindmap':
        if (typeof renderMindMap !== 'undefined' && currentGoalId) {
          viewContainer.id = 'mindmap-container';
          viewContainer.style.minHeight = '400px';
          api.get('/api/goals').then(goals => {
            renderMindMap('mindmap-container', goals);
          }).catch(() => {});
        }
        break;
    }

    // Hide default task list, show view container
    if (viewType !== 'list') {
      viewContainer.style.display = '';
      const taskList = document.getElementById('task-list');
      if (taskList) taskList.style.display = 'none';
    }
  },

  init() {
    // Insert view render area if not present
    const allTasksSection = document.getElementById('all-tasks-section');
    if (allTasksSection && !document.getElementById('view-render-area')) {
      const area = document.createElement('div');
      area.id = 'view-render-area';
      area.style.display = 'none';
      allTasksSection.insertBefore(area, allTasksSection.querySelector('#task-list'));
    }
    // Render toolbar into all-tasks-section
    if (allTasksSection) {
      this.render('all-tasks-section');
    }
  }
};

// ─── Saved Views (Phase 3, Item 3) ──────────────────────────────────────────

const SavedViews = {
  async populateDropdown(selectEl) {
    try {
      const views = await api.get('/api/views');
      views.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = `${v.name} (${v.view_type})`;
        selectEl.appendChild(opt);
      });
    } catch (e) { /* not logged in or no views */ }
  },

  async showSaveDialog() {
    const name = prompt('View name:');
    if (!name) return;
    try {
      await api.post('/api/views', {
        name,
        view_type: _currentViewType,
        filters: {},
        sort: {},
        group_by: '',
      });
      toast.success('View Saved', `"${name}" has been saved.`);
    } catch (e) {
      toast.error('Error', e.message);
    }
  },

  async loadView(viewId) {
    try {
      const view = await api.get(`/api/views/${viewId}`);
      _currentViewType = view.view_type || 'list';
      localStorage.setItem('teb_view_type', _currentViewType);
      ViewSwitcher.render('all-tasks-section');
      ViewSwitcher.loadView(_currentViewType);
    } catch (e) {
      toast.error('Error', e.message);
    }
  },

  async deleteView(viewId) {
    try {
      await api.del(`/api/views/${viewId}`);
      toast.success('View Deleted', 'Saved view removed.');
    } catch (e) {
      toast.error('Error', e.message);
    }
  }
};

// ─── Custom Dashboard Builder (Phase 3, Item 5) ─────────────────────────────

const DashboardBuilder = {
  _widgets: [],
  _dashboardId: null,

  async init(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = `
      <div class="dashboard-builder">
        <div class="dashboard-builder-header">
          <h3>Dashboard</h3>
          <div class="dashboard-builder-actions">
            <select class="dashboard-load-select">
              <option value="">Load dashboard…</option>
            </select>
            <button class="btn btn-secondary btn-sm dashboard-add-widget-btn">+ Add Widget</button>
            <button class="btn btn-primary btn-sm dashboard-save-btn">Save Dashboard</button>
          </div>
        </div>
        <div class="dashboard-grid" id="dashboard-grid"></div>
      </div>
    `;

    // Populate saved dashboards
    try {
      const dashboards = await api.get('/api/dashboards');
      const select = container.querySelector('.dashboard-load-select');
      dashboards.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d.id;
        opt.textContent = d.name;
        select.appendChild(opt);
      });
      select.addEventListener('change', async () => {
        if (select.value) await this.load(parseInt(select.value, 10), containerId);
      });
    } catch (e) { /* ignore */ }

    container.querySelector('.dashboard-add-widget-btn').addEventListener('click', () => {
      this.addWidget(containerId);
    });

    container.querySelector('.dashboard-save-btn').addEventListener('click', () => {
      this.save();
    });

    this.renderGrid();
  },

  addWidget(containerId) {
    const types = ['progress_chart', 'burndown', 'time_report', 'status_pie', 'task_bar'];
    const type = prompt('Widget type:\n' + types.join(', '));
    if (!type || !types.includes(type)) return;
    this._widgets.push({
      type,
      position: this._widgets.length,
      config: {},
    });
    this.renderGrid();
  },

  removeWidget(index) {
    this._widgets.splice(index, 1);
    this.renderGrid();
  },

  async renderGrid() {
    const grid = document.getElementById('dashboard-grid');
    if (!grid) return;
    grid.innerHTML = '';

    if (!this._widgets.length) {
      grid.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><div class="empty-state-title">No widgets</div><div class="empty-state-desc">Click "+ Add Widget" to get started.</div></div>';
      return;
    }

    for (let i = 0; i < this._widgets.length; i++) {
      const w = this._widgets[i];
      const cell = document.createElement('div');
      cell.className = 'dashboard-widget-cell';
      cell.innerHTML = `<div class="dashboard-widget-header"><span>${w.type.replace(/_/g, ' ')}</span><button class="dashboard-widget-remove" data-idx="${i}">✕</button></div><div class="dashboard-widget-body" id="dw-body-${i}"></div>`;
      grid.appendChild(cell);

      cell.querySelector('.dashboard-widget-remove').addEventListener('click', () => this.removeWidget(i));

      // Render widget content
      await this.renderWidgetContent(w, `dw-body-${i}`);
    }
  },

  async renderWidgetContent(widget, bodyId) {
    const body = document.getElementById(bodyId);
    if (!body || !currentGoalId) return;

    try {
      switch (widget.type) {
        case 'progress_chart': {
          const data = await api.get(`/api/goals/${currentGoalId}/timeline`);
          if (data.length && typeof Charts !== 'undefined') {
            Charts.renderLineChart(body, data.map(s => ({
              label: (s.captured_at || '').slice(5, 10),
              value: s.percentage,
            })), { title: 'Progress %', height: 200 });
          } else {
            body.textContent = 'No progress data yet.';
          }
          break;
        }
        case 'burndown': {
          const data = await api.get(`/api/goals/${currentGoalId}/burndown`);
          if (data.length && typeof Charts !== 'undefined') {
            Charts.renderLineChart(body, data.map(d => ({
              label: d.date.slice(5),
              value: d.remaining,
            })), { title: 'Burndown', height: 200 });
          } else {
            body.textContent = 'No burndown data.';
          }
          break;
        }
        case 'time_report': {
          const data = await api.get(`/api/goals/${currentGoalId}/time-report`);
          if (data.by_task && data.by_task.length && typeof Charts !== 'undefined') {
            Charts.renderBarChart(body, data.by_task.map(t => ({
              label: (t.title || '').substring(0, 12),
              value: t.total_minutes,
            })), { title: 'Time by Task (min)', height: 200 });
          } else {
            body.textContent = 'No time data.';
          }
          break;
        }
        case 'status_pie': {
          const counts = {};
          (currentTasks || []).forEach(t => { counts[t.status] = (counts[t.status] || 0) + 1; });
          const pieData = Object.entries(counts).map(([label, value]) => ({ label, value }));
          if (pieData.length && typeof Charts !== 'undefined') {
            Charts.renderPieChart(body, pieData, { title: 'Status Distribution', height: 250 });
          } else {
            body.textContent = 'No tasks.';
          }
          break;
        }
        case 'task_bar': {
          const barData = (currentTasks || []).slice(0, 10).map(t => ({
            label: (t.title || '').substring(0, 12),
            value: t.estimated_minutes || 0,
          }));
          if (barData.length && typeof Charts !== 'undefined') {
            Charts.renderBarChart(body, barData, { title: 'Task Estimates (min)', height: 200 });
          } else {
            body.textContent = 'No tasks.';
          }
          break;
        }
      }
    } catch (e) {
      body.textContent = 'Error loading widget.';
    }
  },

  async save() {
    const name = prompt('Dashboard name:', 'My Dashboard');
    if (!name) return;
    try {
      if (this._dashboardId) {
        await api.patch(`/api/dashboards/${this._dashboardId}`, { name, widgets: this._widgets });
      } else {
        const result = await api.post('/api/dashboards', { name, widgets: this._widgets });
        this._dashboardId = result.id;
      }
      toast.success('Dashboard Saved', `"${name}" saved.`);
    } catch (e) {
      toast.error('Error', e.message);
    }
  },

  async load(dashboardId, containerId) {
    try {
      const d = await api.get(`/api/dashboards/${dashboardId}`);
      this._dashboardId = d.id;
      this._widgets = d.widgets || [];
      this.renderGrid();
    } catch (e) {
      toast.error('Error', e.message);
    }
  }
};

// ═══════════════════════════════════════════════════════════════════
// Phase 8: Micro-interactions & Polish
// ═══════════════════════════════════════════════════════════════════

/* ── Level-Up Animation ─────────────────────────────────────────── */
const LevelUp = {
  XP_PER_LEVEL: 100,
  _currentLevel: parseInt(localStorage.getItem('teb_level') || '1'),
  _currentXP: parseInt(localStorage.getItem('teb_xp') || '0'),

  addXP(amount) {
    this._currentXP += amount;
    while (this._currentXP >= this.XP_PER_LEVEL) {
      this._currentXP -= this.XP_PER_LEVEL;
      this._currentLevel++;
      this.showAnimation(this._currentLevel);
    }
    localStorage.setItem('teb_level', this._currentLevel);
    localStorage.setItem('teb_xp', this._currentXP);
  },

  showAnimation(level) {
    if (document.querySelector('.levelup-overlay')) return;
    const overlay = document.createElement('div');
    overlay.className = 'levelup-overlay';
    overlay.setAttribute('role', 'alert');
    overlay.innerHTML = `
      <div class="levelup-badge">
        <div class="levelup-star">⭐</div>
        <div class="levelup-text">Level Up!</div>
        <div class="levelup-number">Level ${level}</div>
      </div>`;
    document.body.appendChild(overlay);
    SoundFX.play('levelup');
    setTimeout(() => overlay.classList.add('levelup-visible'), 50);
    setTimeout(() => {
      overlay.classList.remove('levelup-visible');
      setTimeout(() => overlay.remove(), 400);
    }, 2500);
  },

  getLevel() { return this._currentLevel; },
  getXP() { return this._currentXP; }
};

/* ── Sound Effects ──────────────────────────────────────────────── */
const SoundFX = {
  _enabled: localStorage.getItem('teb_sounds') !== 'off',
  _audioCtx: null,

  toggle() {
    this._enabled = !this._enabled;
    localStorage.setItem('teb_sounds', this._enabled ? 'on' : 'off');
    return this._enabled;
  },

  _getCtx() {
    if (!this._audioCtx) {
      try { this._audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch { return null; }
    }
    return this._audioCtx;
  },

  play(type) {
    if (!this._enabled) return;
    const ctx = this._getCtx();
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.value = 0.15;

    switch (type) {
      case 'complete':
        osc.frequency.setValueAtTime(523, ctx.currentTime);
        osc.frequency.setValueAtTime(659, ctx.currentTime + 0.1);
        osc.frequency.setValueAtTime(784, ctx.currentTime + 0.2);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.4);
        break;
      case 'notification':
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.2);
        break;
      case 'levelup':
        osc.frequency.setValueAtTime(523, ctx.currentTime);
        osc.frequency.setValueAtTime(659, ctx.currentTime + 0.15);
        osc.frequency.setValueAtTime(784, ctx.currentTime + 0.3);
        osc.frequency.setValueAtTime(1047, ctx.currentTime + 0.45);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.7);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.7);
        break;
      default:
        osc.frequency.setValueAtTime(440, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.15);
    }
  },

  isEnabled() { return this._enabled; }
};

/* ── Contextual Tooltips (first-time hints) ─────────────────────── */
const Tooltips = {
  _seen: JSON.parse(localStorage.getItem('teb_tooltips_seen') || '[]'),

  show(targetEl, message, id) {
    if (this._seen.includes(id)) return;
    const tip = document.createElement('div');
    tip.className = 'contextual-tooltip';
    tip.setAttribute('role', 'tooltip');
    tip.innerHTML = `<span>${message}</span><button class="tooltip-dismiss" aria-label="Dismiss">✕</button>`;
    document.body.appendChild(tip);

    const rect = targetEl.getBoundingClientRect();
    tip.style.top = (rect.bottom + 8) + 'px';
    tip.style.left = Math.max(8, rect.left + rect.width / 2 - 120) + 'px';
    requestAnimationFrame(() => tip.classList.add('tooltip-visible'));

    tip.querySelector('.tooltip-dismiss').addEventListener('click', () => {
      this._seen.push(id);
      localStorage.setItem('teb_tooltips_seen', JSON.stringify(this._seen));
      tip.classList.remove('tooltip-visible');
      setTimeout(() => tip.remove(), 300);
    });

    // Auto-dismiss after 8 seconds
    setTimeout(() => {
      if (tip.parentNode) {
        this._seen.push(id);
        localStorage.setItem('teb_tooltips_seen', JSON.stringify(this._seen));
        tip.classList.remove('tooltip-visible');
        setTimeout(() => tip.remove(), 300);
      }
    }, 8000);
  },

  reset() {
    this._seen = [];
    localStorage.removeItem('teb_tooltips_seen');
  }
};

/* ── High Contrast Mode ─────────────────────────────────────────── */
const HighContrast = {
  _active: localStorage.getItem('teb_high_contrast') === 'on',

  init() {
    if (this._active) document.documentElement.setAttribute('data-high-contrast', 'true');
  },

  toggle() {
    this._active = !this._active;
    if (this._active) {
      document.documentElement.setAttribute('data-high-contrast', 'true');
      localStorage.setItem('teb_high_contrast', 'on');
    } else {
      document.documentElement.removeAttribute('data-high-contrast');
      localStorage.setItem('teb_high_contrast', 'off');
    }
    return this._active;
  },

  isActive() { return this._active; }
};
HighContrast.init();

/* ── Virtual Scrolling ──────────────────────────────────────────── */
const VirtualScroll = {
  ITEM_HEIGHT: 48,
  OVERSCAN: 5,

  mount(container, items, renderItem) {
    const totalHeight = items.length * this.ITEM_HEIGHT;
    container.style.overflow = 'auto';
    container.style.position = 'relative';
    container.setAttribute('role', 'list');
    container.setAttribute('aria-label', `List of ${items.length} items`);

    const spacer = document.createElement('div');
    spacer.style.height = totalHeight + 'px';
    spacer.style.position = 'relative';
    container.innerHTML = '';
    container.appendChild(spacer);

    const viewport = container.clientHeight || 400;
    const visibleCount = Math.ceil(viewport / this.ITEM_HEIGHT);

    const render = () => {
      const scrollTop = container.scrollTop;
      const startIdx = Math.max(0, Math.floor(scrollTop / this.ITEM_HEIGHT) - this.OVERSCAN);
      const endIdx = Math.min(items.length, startIdx + visibleCount + 2 * this.OVERSCAN);

      // Remove existing rendered items
      spacer.querySelectorAll('.vs-item').forEach(el => el.remove());

      for (let i = startIdx; i < endIdx; i++) {
        const el = renderItem(items[i], i);
        el.classList.add('vs-item');
        el.style.position = 'absolute';
        el.style.top = (i * this.ITEM_HEIGHT) + 'px';
        el.style.left = '0';
        el.style.right = '0';
        el.style.height = this.ITEM_HEIGHT + 'px';
        el.setAttribute('role', 'listitem');
        spacer.appendChild(el);
      }
    };

    container.addEventListener('scroll', render, { passive: true });
    render();
    return { refresh: render, destroy: () => container.removeEventListener('scroll', render) };
  }
};

/* ── Screen Reader Live Region ──────────────────────────────────── */
const A11y = {
  _liveRegion: null,

  init() {
    if (this._liveRegion) return;
    this._liveRegion = document.createElement('div');
    this._liveRegion.setAttribute('role', 'status');
    this._liveRegion.setAttribute('aria-live', 'polite');
    this._liveRegion.setAttribute('aria-atomic', 'true');
    this._liveRegion.className = 'sr-only';
    document.body.appendChild(this._liveRegion);
  },

  announce(message) {
    if (!this._liveRegion) this.init();
    this._liveRegion.textContent = '';
    requestAnimationFrame(() => { this._liveRegion.textContent = message; });
  }
};
A11y.init();

/* ── Lazy View Loading ──────────────────────────────────────────── */
const LazyViews = {
  _loaded: new Set(),

  async load(viewName) {
    if (this._loaded.has(viewName)) return true;
    const base = window.__BASE_PATH__ || '';
    const scripts = {
      'mindmap': 'static/views/mindmap.js',
      'kanban': 'static/views/kanban.js',
      'gantt': 'static/views/gantt.js',
      'table': 'static/views/table.js',
      'timeline': 'static/views/timeline.js',
      'calendar': 'static/views/calendar.js',
      'workload': 'static/views/workload.js',
      'charts': 'static/views/charts.js',
    };
    const src = scripts[viewName];
    if (!src) return false;
    return new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = base + '/' + src;
      script.onload = () => { this._loaded.add(viewName); resolve(true); };
      script.onerror = () => resolve(false);
      document.head.appendChild(script);
    });
  }
};
