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

// ─── Global helpers: empty state, error state, skeleton ───────────────────────

function renderEmpty(container, { icon = '📭', title = 'Nothing here yet', subtitle = '', action = null } = {}) {
  if (!container) return;
  const actionHtml = action
    ? `<div class="empty-state__action"><button class="btn btn-primary" id="_empty_action">${escHtml(action.label)}</button></div>`
    : '';
  container.innerHTML = `
    <div class="empty-state">
      <div class="empty-state__icon">${escHtml(icon)}</div>
      <p class="empty-state__title">${escHtml(title)}</p>
      ${subtitle ? `<p class="empty-state__subtitle">${escHtml(subtitle)}</p>` : ''}
      ${actionHtml}
    </div>`;
  if (action) {
    const btn = container.querySelector('#_empty_action');
    if (btn) btn.addEventListener('click', action.onClick);
  }
}

function renderError(container, message, onRetry) {
  if (!container) return;
  container.innerHTML = `
    <div class="error-state">
      <div class="error-state__icon">⚠️</div>
      <p class="error-state__message">${escHtml(message || 'Something went wrong.')}</p>
      ${onRetry ? '<button class="btn btn-secondary" id="_retry_btn">Retry</button>' : ''}
    </div>`;
  if (onRetry) {
    const btn = container.querySelector('#_retry_btn');
    if (btn) btn.addEventListener('click', onRetry);
  }
}

function renderSkeleton(container, rows = 3) {
  if (!container) return;
  container.innerHTML = Array.from({ length: rows })
    .map(() => '<div class="skeleton skeleton-row"></div>')
    .join('');
}

// ─── Onboarding Tour ──────────────────────────────────────────────────────────

class OnboardingTour {
  constructor() {
    this.steps = [
      { target: null, title: 'Welcome to teb! 🎯', body: 'teb bridges human intention and real-world outcomes. Let\'s show you around.' },
      { target: 'goal-title', title: 'Create a Goal', body: 'Type what you want to achieve — teb will break it into actionable tasks.' },
      { target: 'btn-create-goal', title: 'Decompose It', body: 'Hit this button to auto-decompose your goal into tasks.' },
      { target: 'sidebar', title: 'Switch Views', body: 'Use the sidebar to navigate between Kanban, Calendar, Timeline, and more.' },
      { target: 'sidebar-goals-list', title: 'Your Goals', body: 'All your goals appear here. Click one to dive in.' },
    ];
    this.current = 0;
    this.overlay = null;
    this.card = null;
    this.spotlight = null;
  }

  init() {
    if (localStorage.getItem('teb_onboarded')) return;
    this._createOverlay();
    this._showStep(0);
  }

  _createOverlay() {
    this.overlay = document.createElement('div');
    this.overlay.className = 'tour-overlay';
    this.overlay.addEventListener('click', (e) => { if (e.target === this.overlay) this.finish(); });
    document.body.appendChild(this.overlay);

    this.card = document.createElement('div');
    this.card.className = 'tour-card';
    document.body.appendChild(this.card);

    this.spotlight = document.createElement('div');
    this.spotlight.className = 'tour-spotlight';
    document.body.appendChild(this.spotlight);
  }

  _showStep(idx) {
    if (idx < 0 || idx >= this.steps.length) { this.finish(); return; }
    this.current = idx;
    const step = this.steps[idx];
    const isLast = idx === this.steps.length - 1;

    // Position spotlight
    if (step.target) {
      const el = document.getElementById(step.target);
      if (el) {
        const rect = el.getBoundingClientRect();
        this.spotlight.style.display = 'block';
        this.spotlight.style.left = (rect.left - 6) + 'px';
        this.spotlight.style.top = (rect.top - 6) + 'px';
        this.spotlight.style.width = (rect.width + 12) + 'px';
        this.spotlight.style.height = (rect.height + 12) + 'px';
      } else {
        this.spotlight.style.display = 'none';
      }
    } else {
      this.spotlight.style.display = 'none';
    }

    // Position card
    this.card.innerHTML = `
      <p style="font-weight:600;margin:0 0 0.5rem">${escHtml(step.title)}</p>
      <p style="margin:0;font-size:0.9rem">${escHtml(step.body)}</p>
      <div class="tour-nav">
        <span class="tour-step-counter">${idx + 1} / ${this.steps.length}</span>
        <div style="display:flex;gap:0.5rem">
          <button class="btn btn-secondary btn-sm" id="_tour_skip">Skip</button>
          <button class="btn btn-primary btn-sm" id="_tour_next">${isLast ? 'Done' : 'Next'}</button>
        </div>
      </div>`;

    // Position card near spotlight or center
    if (step.target) {
      const el = document.getElementById(step.target);
      if (el) {
        const rect = el.getBoundingClientRect();
        this.card.style.top = (rect.bottom + 16) + 'px';
        this.card.style.left = Math.max(16, Math.min(rect.left, window.innerWidth - 380)) + 'px';
      }
    } else {
      this.card.style.top = '50%';
      this.card.style.left = '50%';
      this.card.style.transform = 'translate(-50%, -50%)';
    }

    const skipBtn = this.card.querySelector('#_tour_skip');
    const nextBtn = this.card.querySelector('#_tour_next');
    if (skipBtn) skipBtn.addEventListener('click', () => this.finish());
    if (nextBtn) nextBtn.addEventListener('click', () => {
      if (isLast) this.finish();
      else this._showStep(idx + 1);
    });
  }

  finish() {
    localStorage.setItem('teb_onboarded', '1');
    if (this.overlay && this.overlay.parentNode) this.overlay.parentNode.removeChild(this.overlay);
    if (this.card && this.card.parentNode) this.card.parentNode.removeChild(this.card);
    if (this.spotlight && this.spotlight.parentNode) this.spotlight.parentNode.removeChild(this.spotlight);
  }
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
      <span class="toast-icon" aria-hidden="true">${icons[type] || 'ℹ'}</span>
      <div class="toast-body">
        <div class="toast-title">${escHtml(title)}</div>
        ${message ? `<div class="toast-message">${escHtml(message)}</div>` : ''}
      </div>
      <button class="toast-close" aria-label="Dismiss notification">&times;</button>
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

// Helper: switch to a named view (kanban, calendar, timeline, gantt, table, workload, mindmap)
// Ensures the all-tasks-section is visible, drip-section hidden, and the correct ViewSwitcher view is loaded.
function _switchToView(viewKey) {
  const dripSection = document.getElementById('drip-section');
  const allTasksSection = document.getElementById('all-tasks-section');
  if (dripSection) dripSection.style.display = 'none';
  if (allTasksSection) allTasksSection.style.display = 'block';
  _currentViewType = viewKey;
  localStorage.setItem('teb_view_type', viewKey);
  // ViewSwitcher is declared as `const` at line ~3407, after this function and
  // after the Router whose init() fires immediately at page load. Accessing
  // ViewSwitcher synchronously here hits the temporal dead zone (TDZ) and
  // throws "Cannot access 'ViewSwitcher' before initialization". Deferring to
  // the next event-loop turn (setTimeout 0) ensures the entire script —
  // including the ViewSwitcher declaration — has been fully evaluated first.
  setTimeout(() => {
    ViewSwitcher.init();
    ViewSwitcher.loadView(viewKey);
  }, 0);
}

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
      _switchToView('kanban');
    },
    '/calendar': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Calendar'}]);
      _switchToView('calendar');
    },
    '/timeline': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Timeline'}]);
      _switchToView('timeline');
    },
    '/gantt': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Gantt'}]);
      _switchToView('gantt');
    },
    '/table': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Table'}]);
      _switchToView('table');
    },
    '/workload': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Workload'}]);
      _switchToView('workload');
    },
    '/mindmap': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: currentGoalTitle || 'Goal', href: currentGoalId ? `#/goal/${currentGoalId}` : '#/home'}, {text:'Mind Map'}]);
      _switchToView('mindmap');
    },
    '/dashboard': () => {
      showScreen('screen-tasks');
      updateBreadcrumbs([{text:'Home', href:'#/home'}, {text:'Dashboard'}]);
      // Initialize dashboard builder in the all-tasks-section area
      const section = document.getElementById('all-tasks-section');
      if (section) {
        section.style.display = 'block';
        document.getElementById('drip-section') && (document.getElementById('drip-section').style.display = 'none');
        // DashboardBuilder is declared as `const` after init() fires (~line 3622).
        // Defer to the next event-loop turn to avoid a TDZ ReferenceError when
        // the page loads with #/dashboard as the initial hash.
        setTimeout(() => DashboardBuilder.init('all-tasks-section'), 0);
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
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    sidebar.classList.toggle('mobile-open');
    sidebar.classList.toggle('sidebar--open');
    // Create/remove overlay for mobile
    let overlay = document.querySelector('.sidebar-overlay');
    if (sidebar.classList.contains('sidebar--open')) {
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'sidebar-overlay';
        overlay.addEventListener('click', () => {
          sidebar.classList.remove('mobile-open', 'sidebar--open');
          if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
        });
        document.body.appendChild(overlay);
      }
    } else {
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }
  });

  // Close sidebar on mobile when clicking a link
  document.querySelectorAll('.sidebar-link, .sidebar-goal-link').forEach(el => {
    el.addEventListener('click', () => {
      const sidebar = document.getElementById('sidebar');
      if (sidebar) { sidebar.classList.remove('mobile-open', 'sidebar--open'); }
      const overlay = document.querySelector('.sidebar-overlay');
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    });
  });

  // Admin button in sidebar
  document.getElementById('btn-sidebar-admin')?.addEventListener('click', () => {
    Router.navigate('#/admin');
    const sidebar = document.getElementById('sidebar');
    if (sidebar) { sidebar.classList.remove('mobile-open', 'sidebar--open'); }
    const overlay = document.querySelector('.sidebar-overlay');
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
  });

  // Escape key closes mobile sidebar
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const sidebar = document.getElementById('sidebar');
      if (sidebar) { sidebar.classList.remove('mobile-open', 'sidebar--open'); }
      const overlay = document.querySelector('.sidebar-overlay');
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }
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
      await api.patch(`/api/tasks/${taskId}`, {
        status: statusEl ? statusEl.value : 'todo',
        notes: descEl ? descEl.value : '',
        due_date: (dueEl ? dueEl.value : '') || null,
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
      await api.del(`/api/tasks/${this._currentTask.id}`);
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
      try { await api.patch(`/api/tasks/${id}`, { status }); }
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
      try { await api.del(`/api/tasks/${id}`); }
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

    // Escape: close panels and modals
    if (e.key === 'Escape') {
      if (CommandPalette._visible) { CommandPalette.hide(); return; }
      if (TaskDetailPanel._currentTask) { TaskDetailPanel.close(); return; }
      const settingsModal = document.getElementById('settings-modal');
      if (settingsModal && settingsModal.style.display !== 'none') { settingsModal.style.display = 'none'; return; }
      const adminModal = document.getElementById('admin-modal');
      if (adminModal && adminModal.style.display !== 'none') { adminModal.style.display = 'none'; return; }
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
          api.patch(`/api/tasks/${taskId}`, { status: newStatus })
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

    // ?: show keyboard shortcuts help
    if (e.key === '?') {
      e.preventDefault();
      ShortcutHelp.toggle();
    }

    // g then h: go home
    if (e.key === 'g') {
      const _waitForSecond = (ev) => {
        document.removeEventListener('keydown', _waitForSecond);
        if (ev.key === 'h') { Router.navigate('#/home'); }
        else if (ev.key === 'd') { Router.navigate('#/dashboard'); }
      };
      setTimeout(() => document.removeEventListener('keydown', _waitForSecond), 1000);
      document.addEventListener('keydown', _waitForSecond);
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
      await api.post('/api/tasks', { goal_id: currentGoalId, title, description: '', estimated_minutes: 30 });
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
        api.patch(`/api/tasks/${taskId}`, { status: 'done' })
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
  // Update sidebar user area
  updateSidebarUser();
}

function updateSidebarUser() {
  const email = localStorage.getItem('teb_email');
  const area = document.getElementById('sidebar-user-area');
  const avatar = document.getElementById('sidebar-avatar');
  const name = document.getElementById('sidebar-user-name');
  if (!area) return;
  if (email) {
    area.style.display = 'flex';
    const initial = email.charAt(0).toUpperCase();
    if (avatar) avatar.textContent = initial;
    // Safely extract username from email
    const atIdx = email.indexOf('@');
    const displayName = atIdx > 0 ? email.substring(0, atIdx) : 'User';
    if (name) name.textContent = displayName;
  } else {
    area.style.display = 'none';
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

// ─── Elapsed time formatting ──────────────────────────────────────────────────

function formatElapsedTime(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

// ─── Debounce ─────────────────────────────────────────────────────────────────

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

const debouncedRefreshGoalView = debounce(() => refreshGoalView(), 300);

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
    try { new OnboardingTour().init(); } catch (_) { /* non-critical */ }
    loadXpBar();
    startAutopilotPolling();
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
  stopAutopilotPolling();
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

    // Update goal stats overview
    updateGoalStats(goals);

    if (!goals.length) {
      ul.innerHTML = `
        <li class="empty-state-large">
          <div class="empty-state-icon">📌</div>
          <div class="empty-state-title">No goals yet</div>
          <div class="empty-state-desc">Define your first objective above and let AI break it into actionable steps.</div>
        </li>`;
      return;
    }
    goals.forEach(g => {
      const li = document.createElement('li');
      li.className = 'goal-item';
      // Calculate progress if tasks are available
      const tasks = g.tasks || [];
      const topLevel = tasks.filter(t => t.parent_id === null);
      const done = topLevel.filter(t => t.status === 'done' || t.status === 'skipped').length;
      const pct = topLevel.length ? Math.round((done / topLevel.length) * 100) : 0;
      const statusLabel = g.status.replace('_', ' ');
      li.innerHTML = `
        <div style="flex:1;min-width:0">
          <span class="goal-item-title">${escHtml(g.title)}</span>
          <div style="display:flex;align-items:center;gap:var(--space-sm);margin-top:.35rem;flex-wrap:wrap">
            <span class="goal-item-status status-${g.status}">${statusLabel}</span>
            <span style="font-size:var(--text-xs);color:var(--muted)">${timeAgo(g.created_at)}</span>
            ${topLevel.length ? `<span style="font-size:var(--text-xs);color:var(--muted)">· ${topLevel.length} tasks</span>` : ''}
          </div>
          ${topLevel.length ? `<div class="goal-item-progress">
            <div class="goal-item-progress-bar"><div class="goal-item-progress-fill" style="width:${pct}%"></div></div>
            <span class="goal-item-progress-text">${pct}%</span>
          </div>` : ''}
        </div>
        <button class="goal-item-delete" title="Delete goal" aria-label="Delete goal" data-goal-id="${g.id}">✕</button>
      `;
      li.querySelector('.goal-item-delete').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm('Delete this goal and all its tasks? This cannot be undone.')) return;
        try {
          await api.del('/api/goals/' + g.id);
          toast.info('Deleted', 'Goal removed.');
          loadGoalList();
        } catch (err) {
          toast.error('Error', err.message);
        }
      });
      li.addEventListener('click', () => openGoal(g.id));
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = '';
    console.warn('Could not load goal list', e);
  }
}

function updateGoalStats(goals) {
  const container = document.getElementById('goal-stats-overview');
  if (!container) return;
  if (!goals || !goals.length) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'grid';
  const total = goals.length;
  const completed = goals.filter(g => g.status === 'done').length;
  const active = goals.filter(g => g.status === 'in_progress' || g.status === 'decomposed').length;
  let totalTasks = 0;
  goals.forEach(g => {
    const tasks = g.tasks || [];
    totalTasks += tasks.filter(t => t.parent_id === null).length;
  });
  const totalEl = document.getElementById('stat-total-goals');
  const completedEl = document.getElementById('stat-completed-goals');
  const activeEl = document.getElementById('stat-active-goals');
  const tasksEl = document.getElementById('stat-total-tasks');
  if (totalEl) animateCounter(totalEl, total);
  if (completedEl) animateCounter(completedEl, completed);
  if (activeEl) animateCounter(activeEl, active);
  if (tasksEl) animateCounter(tasksEl, totalTasks);
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

// Quick-capture: expand/collapse description panel
on('btn-expand-desc', 'click', () => {
  const panel = document.getElementById('goal-desc-panel');
  const btn = document.getElementById('btn-expand-desc');
  if (!panel || !btn) return;
  const visible = panel.style.display !== 'none';
  panel.style.display = visible ? 'none' : 'block';
  btn.textContent = visible ? '+ Add details' : '− Hide details';
});

// Enter key on goal title input
on('goal-title', 'keydown', e => {
  if (e.key === 'Enter') {
    const btn = document.getElementById('btn-create-goal');
    if (btn) btn.click();
  }
});

// ─── Clarify screen ───────────────────────────────────────────────────────────

let _clarifyStep = 0;
let _clarifyTotal = 0;

async function startClarifyFlow(goal) {
  const titleEl = document.getElementById('clarify-goal-title');
  if (titleEl) titleEl.textContent = goal.title;
  _clarifyStep = 0;
  _clarifyTotal = goal.answers ? Object.keys(goal.answers).length : 0;
  const q = await api.get(`/api/goals/${goal.id}/next_question`);
  if (q.done) {
    await triggerDecompose(goal.id);
    return;
  }
  _clarifyStep = 1;
  if (q.total) _clarifyTotal = q.total;
  else _clarifyTotal = Math.max(_clarifyTotal, 4);
  showQuestion(q.question);
  updateClarifyProgress();
  showScreen('screen-clarify');
}

function updateClarifyProgress() {
  const indicator = document.getElementById('clarify-step-indicator');
  if (indicator) indicator.textContent = `${_clarifyStep} / ${_clarifyTotal}`;
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
      _clarifyStep++;
      showQuestion(res.next_question);
      updateClarifyProgress();
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
  // Show decomposition progress banner instead of generic loading
  const banner = document.getElementById('decompose-progress-banner');
  if (banner) { banner.style.display = 'flex'; banner.classList.add('active'); }
  showLoading('Decomposing your goal…');
  try {
    await api.post(`/api/goals/${goalId}/decompose`, {});
    const goal = await api.get(`/api/goals/${goalId}`);
    hideLoading();
    if (banner) { banner.style.display = 'none'; banner.classList.remove('active'); }
    await showTasksScreen(goal, /* freshDecompose */ true);
    // Animate task cards in with stagger
    requestAnimationFrame(() => {
      const cards = document.querySelectorAll('.task-card');
      cards.forEach((card, i) => {
        card.style.opacity = '0';
        card.style.transform = 'translateY(12px)';
        setTimeout(() => {
          card.style.transition = 'opacity 200ms ease, transform 200ms ease';
          card.style.opacity = '1';
          card.style.transform = 'translateY(0)';
        }, i * 50);
      });
    });
  } catch (e) {
    hideLoading();
    if (banner) { banner.style.display = 'none'; banner.classList.remove('active'); }
    showError('error-clarify', e.message);
  }
}

// ─── Streak badge ─────────────────────────────────────────────────────────────

async function renderStreakBadge(goalId, container) {
  if (!container) return;
  // Remove any existing streak badge
  const existing = container.querySelector('.streak-badge-auto');
  if (existing) existing.remove();
  try {
    const checkins = await api.get(`/api/goals/${goalId}/checkins`);
    if (!Array.isArray(checkins) || checkins.length === 0) return;

    const sorted = checkins.slice().sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    let streak = 0;
    let cursor = new Date();
    cursor.setHours(0, 0, 0, 0);

    for (const c of sorted) {
      const d = new Date(c.created_at);
      d.setHours(0, 0, 0, 0);
      const diff = Math.round((cursor - d) / 86400000);
      if (diff <= 1) { streak++; cursor = d; }
      else break;
    }

    if (streak === 0) return;
    const lastCheckin = new Date(sorted[0].created_at);
    const hoursSince = (Date.now() - lastCheckin.getTime()) / 3600000;
    const atRisk = hoursSince > 24;
    const badge = document.createElement('span');
    badge.className = 'streak-badge streak-badge-auto';
    badge.title = atRisk ? 'No check-in in 24h — streak at risk' : `${streak}-day streak`;
    badge.textContent = atRisk ? `⚠️ ${streak}d streak at risk` : `🔥 ${streak} day streak`;
    container.appendChild(badge);
  } catch (_) { /* non-critical */ }
}

// ─── Tasks screen ─────────────────────────────────────────────────────────────

async function showTasksScreen(goal, freshDecompose) {
  currentGoalId = goal.id;
  currentGoalTitle = goal.title;
  const goalTitleEl = document.getElementById('tasks-goal-title');
  if (goalTitleEl) goalTitleEl.textContent = goal.title;
  // Render streak badge next to goal title
  if (goalTitleEl) renderStreakBadge(goal.id, goalTitleEl.parentElement || goalTitleEl);
  currentTasks = goal.tasks || [];
  renderTasks(currentTasks);
  updateProgress(currentTasks);
  loadDrip();
  loadFocusTask();
  loadProgressDetail();

  // Stage 2: deferred secondary data (2s timeout)
  const _ric = window.requestIdleCallback || ((cb) => setTimeout(cb, 100));
  _ric(() => {
    loadCheckinHistory();
    loadOutcomeMetrics();
    loadNudge();
    loadAutopilotStatus();
  }, { timeout: 2000 });

  // Stage 3: background analytics (5s timeout)
  _ric(() => {
    loadRoiDashboard();
    loadPlatformInsights();
    loadGamification();
    loadAgentActivity();
  }, { timeout: 5000 });
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

  // Goal title inline edit (contenteditable)
  if (goalTitleEl) {
    goalTitleEl.setAttribute('contenteditable', 'true');
    goalTitleEl.setAttribute('spellcheck', 'false');
    goalTitleEl.classList.add('goal-title-editable');
    goalTitleEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); goalTitleEl.blur(); }
      if (e.key === 'Escape') { goalTitleEl.textContent = currentGoalTitle; goalTitleEl.blur(); }
    });
    goalTitleEl.addEventListener('blur', async () => {
      const newTitle = goalTitleEl.textContent.trim();
      if (newTitle && newTitle !== currentGoalTitle && currentGoalId) {
        try {
          await api.patch(`/api/goals/${currentGoalId}`, { title: newTitle });
          currentGoalTitle = newTitle;
          updateBreadcrumbs([{text:'Home', href:'#/home'}, {text: newTitle}]);
        } catch (e) {
          goalTitleEl.textContent = currentGoalTitle;
          toast.error('Error', e.message);
        }
      }
    });
  }

  // Goal progress summary bar
  const topLevelTasks = currentTasks.filter(t => t.parent_id === null);
  const doneTasks = topLevelTasks.filter(t => t.status === 'done' || t.status === 'skipped').length;
  const totalTaskCount = topLevelTasks.length;
  const pctDone = totalTaskCount ? Math.round((doneTasks / totalTaskCount) * 100) : 0;
  const summaryBar = document.getElementById('goal-progress-summary');
  if (summaryBar) {
    summaryBar.style.display = totalTaskCount ? 'block' : 'none';
    summaryBar.innerHTML = `<div class="goal-progress-bar-track"><div class="goal-progress-bar-fill" style="width:${pctDone}%"></div></div><span class="goal-progress-label">${doneTasks} of ${totalTaskCount} tasks done · ${pctDone}%</span>`;
  }

  // Load ROI summary card
  loadRoiSummaryCard();
  // Load streak for check-in prompt
  loadStreakCheckinPrompt();
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
        currentTasks.every(t => t.status === 'done');
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

    // Initialize focus timer for this task
    DripTimer.init();

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

// ─── Drip Focus Timer ─────────────────────────────────────────────────────────

const DripTimer = {
  _interval: null,
  _seconds: 0,
  _running: false,

  init() {
    const timerEl = document.getElementById('drip-timer');
    if (timerEl) timerEl.style.display = 'flex';
    this.reset();
    on('btn-drip-timer-toggle', 'click', () => this.toggle());
    on('btn-drip-timer-reset', 'click', () => this.reset());
  },

  toggle() {
    if (this._running) this.pause();
    else this.start();
  },

  start() {
    this._running = true;
    const btn = document.getElementById('btn-drip-timer-toggle');
    if (btn) { btn.textContent = '⏸ Pause'; btn.classList.add('active'); btn.setAttribute('aria-label', 'Pause focus timer'); }
    this._interval = setInterval(() => {
      this._seconds++;
      this._updateDisplay();
    }, 1000);
  },

  pause() {
    this._running = false;
    clearInterval(this._interval);
    const btn = document.getElementById('btn-drip-timer-toggle');
    if (btn) { btn.textContent = '▶ Start'; btn.classList.remove('active'); btn.setAttribute('aria-label', 'Start focus timer'); }
  },

  reset() {
    this.pause();
    this._seconds = 0;
    this._updateDisplay();
  },

  _updateDisplay() {
    const display = document.getElementById('drip-timer-display');
    if (!display) return;
    const m = Math.floor(this._seconds / 60);
    const s = this._seconds % 60;
    display.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  },

  stop() {
    this.pause();
    return this._seconds;
  }
};

// BUG-06: Don't parseInt task IDs — they may be UUIDs/strings
on('btn-drip-done', 'click', async () => {
  const card = document.getElementById('drip-card');
  const tid = card ? card.dataset.taskId : null;
  if (!tid) return;
  // Stop timer and log time
  const elapsed = DripTimer.stop();
  try {
    await api.patch(`/api/tasks/${tid}`, { status: 'done' });
    const elapsedStr = elapsed > 0 ? formatElapsedTime(elapsed) : '';
    toast.success('Task completed!', elapsed > 0 ? `Great job — took ${elapsedStr}.` : 'Great job — keep it up.');
    showCelebration('✅');
    await refreshGoalView();
    loadDrip();
    loadXpBar();
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
  card.dataset.status = task.status;

  const cbClass = task.status === 'done' ? 'checked' : '';
  const hasSubtasks = subtasks.length > 0;
  const canDecompose = !hasSubtasks && task.status !== 'done' && depth < MAX_DECOMPOSE_DEPTH;
  const subtaskDone = hasSubtasks ? subtasks.filter(s => s.status === 'done').length : 0;
  const subtaskPct = hasSubtasks ? Math.round((subtaskDone / subtasks.length) * 100) : 0;

  // Build tags HTML if available
  const tags = task.tags || [];
  const tagsHtml = tags.length ? `<div class="kanban-card-tags">${tags.map(t => `<span class="tag">${escHtml(t)}</span>`).join('')}</div>` : '';

  // Due date display
  const dueHtml = task.due_date ? `<span class="due-date" title="Due ${task.due_date}">📅 ${task.due_date}</span>` : '';

  // Priority dot
  const priority = task.priority || 'normal';
  const dotHtml = `<span class="priority-dot priority-dot--${escHtml(priority)}" title="Priority: ${escHtml(priority)}"></span>`;

  card.innerHTML = `
    <div class="task-header">
      <input type="checkbox" class="task-select-checkbox" data-id="${task.id}" title="Select for batch" aria-label="Select task" />
      <div class="task-checkbox ${cbClass}" data-id="${task.id}" title="Mark done"></div>
      <div class="task-info">
        <div class="task-title task-title-editable" contenteditable="true" data-task-id="${task.id}" spellcheck="false">${dotHtml} ${escHtml(task.title)}</div>
        <div class="task-meta">
          <span class="task-time-pill">⏱ ${task.estimated_minutes}m</span>
          ${dueHtml}
          ${hasSubtasks ? `<span style="font-size:var(--text-xs);color:var(--muted)">${subtasks.length} sub-tasks</span>` : ''}
        </div>
        ${tagsHtml}
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
        await api.patch(`/api/tasks/${task.id}`, { title: newTitle });
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
      const tid = cb.dataset.id;
      const sub = currentTasks.find(t => String(t.id) === String(tid));
      if (sub) toggleTaskDone(sub);
    });
  });

  // Sub-task break-down buttons
  card.querySelectorAll('.btn-break-down-sub').forEach(btn => {
    btn.addEventListener('click', () => decomposeTask(btn.dataset.id));
  });

  // Sub-task delete buttons
  card.querySelectorAll('.btn-delete-sub').forEach(btn => {
    btn.addEventListener('click', () => deleteTask(btn.dataset.id));
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
    // Award XP for task completion
    if (typeof LevelUp !== 'undefined') LevelUp.addXP(10);
    if (typeof SoundFX !== 'undefined') SoundFX.play('complete');
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
  // Optimistic DOM update
  const card = document.querySelector(`.task-card[data-id="${taskId}"]`);
  const prevStatus = card ? card.dataset.status : null;
  if (card) {
    card.dataset.status = status;
    if (status === 'done') {
      card.classList.add('done-card', 'task-completing');
      const titleEl = card.querySelector('.task-title');
      if (titleEl) titleEl.classList.add('task-title-strikethrough');
      // Show brief completion badge
      const badge = document.createElement('span');
      badge.className = 'task-done-badge';
      badge.textContent = '✓';
      card.querySelector('.task-header')?.appendChild(badge);
      setTimeout(() => { if (badge.parentNode) badge.remove(); }, 1500);
    } else {
      card.classList.remove('done-card', 'task-completing');
      const titleEl = card.querySelector('.task-title');
      if (titleEl) titleEl.classList.remove('task-title-strikethrough');
    }
    // Update checkbox visual
    const cb = card.querySelector('.task-checkbox');
    if (cb) cb.classList.toggle('checked', status === 'done');
    // Update status select
    const sel = card.querySelector('.task-status-select');
    if (sel) sel.value = status;
  }
  // Update progress bar immediately
  const idx = currentTasks.findIndex(t => String(t.id) === String(taskId));
  if (idx !== -1) currentTasks[idx].status = status;
  updateProgress(currentTasks);
  // Update kanban column counts
  updateKanbanCounts();

  try {
    await api.patch(`/api/tasks/${taskId}`, { status });
    await refreshGoalView();
  } catch (e) {
    // Revert optimistic update on failure
    if (card && prevStatus) {
      card.dataset.status = prevStatus;
      card.classList.toggle('done-card', prevStatus === 'done');
    }
    if (idx !== -1) currentTasks[idx].status = prevStatus || 'todo';
    updateProgress(currentTasks);
    renderError(document.getElementById('task-list'), e.message, () => refreshGoalView());
    showError('error-tasks', e.message);
  }
}

function updateKanbanCounts() {
  document.querySelectorAll('.kanban-column').forEach(col => {
    const status = col.dataset.status;
    if (!status) return;
    const count = currentTasks.filter(t => t.status === status && t.parent_id === null).length;
    const badge = col.querySelector('.kanban-col-count');
    if (badge) badge.textContent = count;
  });
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

on('btn-delete-goal', 'click', async () => {
  if (!currentGoalId) return;
  if (!confirm('Delete this goal and all its tasks? This cannot be undone.')) return;
  try {
    await api.del('/api/goals/' + currentGoalId);
    toast.info('Deleted', 'Goal removed.');
    currentGoalId = null;
    currentGoalTitle = '';
    currentTasks = [];
    Router.navigate('#/home');
  } catch (e) {
    toast.error('Error', e.message);
  }
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
  const toggle = e.target;                 // capture before any catch block can shadow `e`
  const enabled = toggle.checked;
  // Bug guard: currentGoalId is null when no goal has been opened yet; without
  // this check the fetch URL becomes /api/goals/null/auto-execute → 422.
  if (!currentGoalId) {
    if (toggle) toggle.checked = !enabled; // revert the UI change
    showError('error-tasks', 'No goal selected. Open a goal before using autopilot.');
    return;
  }
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
  } catch (err) {
    // Renamed from `e` to `err` so the outer `e` (the change event) is not
    // shadowed. Previously `e.target` inside the catch referred to the Error
    // object, which has no `.target`, causing:
    //   TypeError: Cannot set properties of undefined (setting 'checked')
    if (toggle) toggle.checked = !enabled;
    showError('error-tasks', err.message);
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

  if (btn) { btn.disabled = true; btn.textContent = 'Orchestrating…'; }
  if (panel) panel.style.display = 'block';
  if (content) content.innerHTML = '<div class="agent-loading"><div class="loading-spinner-sm"></div><span>Dispatching agents…</span></div>';

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
    if (content) content.innerHTML = `<p class="error">${escHtml(e.message)}</p>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Orchestrate'; }
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

// ─── XP bar in sidebar footer ─────────────────────────────────────────────────

async function loadXpBar() {
  const container = document.getElementById('xp-bar-container');
  if (!container) return;
  try {
    const data = await api.get('/api/users/me/xp');
    const xp = data.total_xp || 0;
    const level = data.level || 1;
    const pct = Math.min(100, Math.round(((xp % 100) / 100) * 100));
    container.innerHTML = `
      <div class="xp-bar-label">
        <span>Level ${escHtml(String(level))}</span>
        <span>${escHtml(String(xp % 100))} / 100 XP</span>
      </div>
      <div class="xp-bar-track">
        <div class="xp-bar-fill" id="xp-bar-fill" style="width: 0%"></div>
      </div>`;
    requestAnimationFrame(() => {
      const fill = document.getElementById('xp-bar-fill');
      if (fill) fill.style.width = pct + '%';
    });
  } catch (_) { /* non-critical, fail silently */ }
}

// ─── Autopilot status badge ──────────────────────────────────────────────────

let _autopilotPollInterval = null;
const AUTOPILOT_POLL_INTERVAL_MS = 30000;

async function loadAutopilotBadge() {
  const badge = document.getElementById('autopilot-badge');
  if (!badge) return;
  try {
    const data = await api.get('/api/auto-execute/status');
    const running = data.running === true;
    const dotClass = running ? 'autopilot-dot--running' : 'autopilot-dot--idle';
    const label = running ? 'Autopilot ON' : 'Autopilot OFF';
    badge.innerHTML = `<span class="autopilot-dot ${escHtml(dotClass)}"></span><span>${escHtml(label)}</span>`;
    badge.title = running
      ? `Tasks queued: ${data.queued || 0} · Executed today: ${data.executed_today || 0}`
      : 'Autopilot is idle';
  } catch (_) {
    if (badge) badge.innerHTML = '';
  }
}

function startAutopilotPolling() {
  if (_autopilotPollInterval) return;
  loadAutopilotBadge();
  _autopilotPollInterval = setInterval(loadAutopilotBadge, AUTOPILOT_POLL_INTERVAL_MS);
}

function stopAutopilotPolling() {
  if (_autopilotPollInterval) {
    clearInterval(_autopilotPollInterval);
    _autopilotPollInterval = null;
  }
  const badge = document.getElementById('autopilot-badge');
  if (badge) badge.innerHTML = '';
}

// ─── Agent activity ──────────────────────────────────────────────────────────

const AGENT_COLORS = {
  coordinator: '#6366f1', marketing: '#ec4899', web_dev: '#14b8a6',
  outreach: '#f97316', research: '#eab308', finance: '#22c55e'
};
const AGENT_ROLES = {
  coordinator: 'Strategy & delegation', marketing: 'Positioning, content, SEO',
  web_dev: 'Technical setup & deploy', outreach: 'Cold outreach & campaigns',
  research: 'Competitive analysis', finance: 'Budgeting & pricing'
};

async function loadAgentActivity() {
  if (!currentGoalId) return;
  const panel = document.getElementById('agent-activity-panel');
  const content = document.getElementById('agent-activity-content');
  if (!panel || !content) return;
  try {
    const data = await api.get('/api/goals/' + currentGoalId + '/agent-activity');
    const handoffs = data.handoffs || [];
    const messages = data.messages || [];
    const agents = data.agents_involved || [];
    const tasksByAgent = data.tasks_by_agent || {};
    panel.style.display = 'block';
    let html = '';

    // Agent roster: all 6 agents as cards
    html += '<div class="agent-roster">';
    ['coordinator','marketing','web_dev','outreach','research','finance'].forEach(name => {
      const involved = agents.includes(name);
      const tc = tasksByAgent[name] || 0;
      const color = AGENT_COLORS[name] || '#94a3b8';
      const role = AGENT_ROLES[name] || '';
      const stCls = involved ? (tc > 0 ? 'agent-status-done' : 'agent-status-running') : 'agent-status-idle';
      const stLabel = involved ? (tc > 0 ? 'done' : 'running') : 'idle';
      html += '<div class="agent-roster-card" data-agent="' + escHtml(name) + '" style="border-left:3px solid ' + color + '"><div class="agent-roster-name">' + escHtml(name.replace('_',' ')) + '</div><div class="agent-roster-role">' + escHtml(role) + '</div><div class="agent-roster-status ' + stCls + '"><span class="agent-status-dot"></span> ' + escHtml(stLabel) + (tc ? ' · ' + tc + ' tasks' : '') + '</div></div>';
    });
    html += '</div>';

    // Delegation chain
    if (handoffs.length) {
      html += '<div class="agent-delegation-chain"><h4 class="agent-section-title">Delegation Chain</h4><div class="agent-timeline">';
      handoffs.forEach(h => {
        const icon = h.status === 'completed' ? '✅' : h.status === 'failed' ? '❌' : '⏳';
        html += '<div class="agent-timeline-item"><div class="agent-timeline-icon">' + icon + '</div><div class="agent-timeline-body"><div class="agent-timeline-title"><span class="agent-badge agent-from">' + escHtml(h.from_agent||'') + '</span><span class="agent-arrow">→</span><span class="agent-badge agent-to">' + escHtml(h.to_agent||'') + '</span></div>' + (h.input_summary ? '<div class="agent-timeline-input">' + escHtml(h.input_summary) + '</div>' : '') + (h.output_summary ? '<div class="agent-timeline-output">' + escHtml(h.output_summary) + '</div>' : '') + '</div></div>';
      });
      html += '</div></div>';
    } else {
      html += '<div class="agent-empty-delegation"><p class="empty-state__subtitle">Run orchestration to see agent delegation.</p></div>';
    }

    // Messages
    if (messages.length) {
      html += '<div class="agent-messages-section"><h4 class="agent-section-title">Agent Messages</h4>';
      messages.slice(0,15).forEach(m => {
        const t = m.created_at ? timeAgo(m.created_at) : '';
        html += '<div class="agent-msg-card"><div class="agent-msg-header"><span class="agent-badge agent-from">' + escHtml(m.from_agent||'') + '</span><span class="agent-arrow">→</span><span class="agent-badge agent-to">' + escHtml(m.to_agent||'') + '</span>' + (t ? '<span class="agent-msg-time">' + escHtml(t) + '</span>' : '') + '</div><div class="agent-msg-content">' + escHtml(m.content||'') + '</div></div>';
      });
      html += '</div>';
    }

    if (!handoffs.length && !messages.length && !agents.length) html = '<p class="agent-empty">Run orchestration to see agent activity.</p>';
    content.innerHTML = html;

    // Wire agent card clicks for detail drawer
    content.querySelectorAll('.agent-roster-card').forEach(card => {
      card.addEventListener('click', () => openAgentDrawer(card.dataset.agent, data));
    });
  } catch (e) {
    if (panel) panel.style.display = 'none';
  }
}

function openAgentDrawer(agentName, actData) {
  let drawer = document.getElementById('agent-detail-drawer');
  if (!drawer) {
    drawer = document.createElement('div');
    drawer.id = 'agent-detail-drawer';
    drawer.className = 'agent-drawer';
    document.body.appendChild(drawer);
  }
  const color = AGENT_COLORS[agentName] || '#94a3b8';
  const role = AGENT_ROLES[agentName] || '';
  const hoffs = (actData.handoffs || []).filter(h => h.from_agent === agentName || h.to_agent === agentName);
  const msgs = (actData.messages || []).filter(m => m.from_agent === agentName || m.to_agent === agentName);
  drawer.innerHTML = '<div class="agent-drawer-header" style="border-bottom:2px solid ' + color + '"><h3>' + escHtml(agentName.replace('_',' ')) + '</h3><p class="agent-drawer-role">' + escHtml(role) + '</p><button class="agent-drawer-close" id="agent-drawer-close-btn">&times;</button></div><div class="agent-drawer-body"><h4>Handoffs (' + hoffs.length + ')</h4>' + (hoffs.length ? hoffs.map(h => '<div class="agent-drawer-item"><span class="agent-badge">' + escHtml(h.from_agent) + '</span> → <span class="agent-badge">' + escHtml(h.to_agent) + '</span>' + (h.input_summary ? '<p class="agent-drawer-text">' + escHtml(h.input_summary) + '</p>' : '') + '</div>').join('') : '<p class="empty-state__subtitle">No handoffs</p>') + '<h4>Messages (' + msgs.length + ')</h4>' + (msgs.length ? msgs.map(m => '<div class="agent-drawer-item"><strong>' + escHtml(m.from_agent) + '</strong>: ' + escHtml(m.content||'') + '</div>').join('') : '<p class="empty-state__subtitle">No messages</p>') + '</div>';
  drawer.classList.add('open');
  const closeBtn = drawer.querySelector('#agent-drawer-close-btn');
  if (closeBtn) closeBtn.addEventListener('click', () => drawer.classList.remove('open'));
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


// ─── ROI Summary Card (Feature Area 4) ───────────────────────────────────────

async function loadRoiSummaryCard() {
  const container = document.getElementById('roi-summary-inline');
  if (!container || !currentGoalId) return;
  try {
    const [budgets, spending, outcomes] = await Promise.all([
      api.get(`/api/goals/${currentGoalId}/budgets`).catch(() => []),
      api.get(`/api/goals/${currentGoalId}/spending`).catch(() => ({ total_spent: 0 })),
      api.get(`/api/goals/${currentGoalId}/outcomes`).catch(() => []),
    ]);
    const totalBudget = Array.isArray(budgets) ? budgets.reduce((s, b) => s + (b.total_limit || 0), 0) : 0;
    const totalSpent = spending.total_spent || 0;
    const totalEarned = Array.isArray(outcomes) ? outcomes.filter(o => o.unit === '$').reduce((s, o) => s + (o.current_value || 0), 0) : 0;
    const netRoi = totalEarned - totalSpent;

    if (totalBudget === 0 && totalSpent === 0 && totalEarned === 0) {
      renderEmpty(container, { icon: '💰', title: 'No financial data', subtitle: 'Set a budget and track revenue to see your ROI.' });
      return;
    }
    container.innerHTML = `<div class="roi-inline-card">
      <div class="roi-inline-item"><span class="roi-inline-label">Budget</span><span class="roi-inline-value">$${totalBudget.toFixed(0)}</span></div>
      <div class="roi-inline-item"><span class="roi-inline-label">Spent</span><span class="roi-inline-value roi-spent">$${totalSpent.toFixed(0)}</span></div>
      <div class="roi-inline-item"><span class="roi-inline-label">Earned</span><span class="roi-inline-value roi-earned">$${totalEarned.toFixed(0)}</span></div>
      <div class="roi-inline-item"><span class="roi-inline-label">Net ROI</span><span class="roi-inline-value ${netRoi >= 0 ? 'roi-positive' : 'roi-negative'}">${netRoi >= 0 ? '+' : ''}$${netRoi.toFixed(0)}</span></div>
    </div>`;
  } catch (_) { /* non-critical */ }
}

// ─── Milestone Celebration (Feature Area 4) ──────────────────────────────────

function checkMilestoneCelebration(metric) {
  if (!metric || !metric.id) return;
  const thresholds = [25, 50, 75, 100];
  const pct = metric.target_value > 0 ? Math.round((metric.current_value / metric.target_value) * 100) : 0;
  const storageKey = 'teb_milestones_' + metric.id;
  const acknowledged = JSON.parse(localStorage.getItem(storageKey) || '[]');
  for (const t of thresholds) {
    if (pct >= t && !acknowledged.includes(t)) {
      acknowledged.push(t);
      localStorage.setItem(storageKey, JSON.stringify(acknowledged));
      triggerMilestoneConfetti();
      toast.success('Milestone reached!', `${t}% of ${metric.label}`);
      break;
    }
  }
}

function triggerMilestoneConfetti() {
  const overlay = document.createElement('div');
  overlay.className = 'milestone-confetti';
  document.body.appendChild(overlay);
  const colors = ['#3b82f6', '#8b5cf6', '#22c55e', '#f59e0b', '#ef4444', '#ec4899'];
  for (let i = 0; i < 60; i++) {
    const p = document.createElement('div');
    p.className = 'confetti-particle';
    p.style.left = Math.random() * 100 + '%';
    p.style.top = '-10px';
    p.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
    p.style.setProperty('--duration', (1 + Math.random() * 1) + 's');
    p.style.animationDelay = Math.random() * 0.3 + 's';
    p.style.width = (5 + Math.random() * 5) + 'px';
    p.style.height = (5 + Math.random() * 5) + 'px';
    overlay.appendChild(p);
  }
  setTimeout(() => overlay.remove(), 2000);
}

// ─── Streak-Aware Check-in Prompt (Feature Area 6) ───────────────────────────

async function loadStreakCheckinPrompt() {
  const prompt = document.getElementById('streak-checkin-prompt');
  if (!prompt || !currentGoalId) return;
  try {
    const checkins = await api.get(`/api/goals/${currentGoalId}/checkins?limit=1`);
    if (!Array.isArray(checkins) || checkins.length === 0) {
      prompt.style.display = 'block';
      prompt.innerHTML = '<span class="streak-prompt-text">No check-in today — how\'s it going? <a href="#" id="streak-prompt-link">→</a></span>';
    } else {
      const last = new Date(checkins[0].created_at);
      const hoursSince = (Date.now() - last.getTime()) / 3600000;
      if (hoursSince > 24) {
        prompt.style.display = 'block';
        prompt.innerHTML = '<span class="streak-prompt-text">No check-in today — how\'s it going? <a href="#" id="streak-prompt-link">→</a></span>';
      } else {
        prompt.style.display = 'none';
        return;
      }
    }
    const link = document.getElementById('streak-prompt-link');
    if (link) link.addEventListener('click', (e) => {
      e.preventDefault();
      const checkinSection = document.getElementById('checkin-section');
      if (checkinSection) checkinSection.scrollIntoView({ behavior: 'smooth' });
    });
  } catch (_) { prompt.style.display = 'none'; }
}

// ─── Nudge Banner with localStorage dismiss (Feature Area 6) ─────────────────

function isNudgeAcknowledged(nudgeId) {
  const acked = JSON.parse(localStorage.getItem('teb_nudges_acked') || '[]');
  return acked.includes(String(nudgeId));
}

function acknowledgeNudge(nudgeId) {
  const acked = JSON.parse(localStorage.getItem('teb_nudges_acked') || '[]');
  if (!acked.includes(String(nudgeId))) {
    acked.push(String(nudgeId));
    localStorage.setItem('teb_nudges_acked', JSON.stringify(acked));
  }
}

// ─── Execution Step Inspector (Feature Area 5) ──────────────────────────────

function renderExecutionStep(log) {
  const statusColor = (log.status === 'success') ? 'var(--color-success)' : 'var(--color-error)';
  const isExpanded = false;
  // Parse request/response summaries
  let method = '', url = '', statusCode = '', responseBody = '';
  const reqSummary = log.request_summary || '';
  const resSummary = log.response_summary || '';
  const methodMatch = reqSummary.match(/^(GET|POST|PUT|PATCH|DELETE)\s+(\S+)/i);
  if (methodMatch) { method = methodMatch[1]; url = methodMatch[2]; }
  const statusMatch = resSummary.match(/^(\d{3})/);
  if (statusMatch) statusCode = statusMatch[1];
  responseBody = resSummary.substring(statusCode.length).trim();

  const truncatedBody = responseBody.length > 500 ? responseBody.substring(0, 500) : responseBody;
  const needsShowMore = responseBody.length > 500;

  return `<div class="exec-step" data-expanded="false">
    <div class="exec-step-header">
      <span class="exec-step-status" style="color:${statusColor}">${log.status === 'success' ? '✓' : '✕'}</span>
      <span class="exec-step-action">${escHtml(log.action || '')}</span>
      ${method ? `<span class="exec-step-method">${escHtml(method)}</span>` : ''}
      ${statusCode ? `<span class="exec-step-code" style="color:${statusCode.startsWith('2') ? 'var(--color-success)' : statusCode.startsWith('4') || statusCode.startsWith('5') ? 'var(--color-error)' : 'var(--color-warning)'}">${escHtml(statusCode)}</span>` : ''}
      <span class="exec-step-time">${log.created_at ? timeAgo(log.created_at) : ''}</span>
    </div>
    <div class="exec-step-detail" style="display:none">
      ${url ? `<div class="exec-detail-row"><strong>URL:</strong> ${escHtml(url)}</div>` : ''}
      ${reqSummary ? `<details class="exec-detail-block"><summary>Request</summary><pre>${escHtml(reqSummary)}</pre></details>` : ''}
      <div class="exec-detail-row"><strong>Response:</strong></div>
      <pre class="exec-response-body">${escHtml(truncatedBody)}</pre>
      ${needsShowMore ? '<button class="btn-sm btn-secondary exec-show-more">Show more</button>' : ''}
    </div>
  </div>`;
}

// ─── Simple Markdown Renderer (Feature Area 6) ──────────────────────────────

function simpleMarkdown(text) {
  if (!text) return '';
  return escHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/^/, '<p>').replace(/$/, '</p>');
}

// ─── Unified Search (Feature Area 7) ────────────────────────────────────────

const UnifiedSearch = {
  _visible: false,
  _selectedIdx: -1,
  _results: [],

  init() {
    const input = document.getElementById('global-search-input');
    const panel = document.getElementById('search-results-panel');
    if (!input || !panel) return;

    const doSearch = debounce(async (query) => {
      if (!query || query.length < 2) { this.hide(); return; }
      try {
        const [goals, tasks] = await Promise.all([
          api.get(`/api/goals?q=${encodeURIComponent(query)}`).catch(() => []),
          api.get(`/api/tasks?q=${encodeURIComponent(query)}`).catch(() => []),
        ]);
        this._results = [];
        (goals || []).slice(0, 5).forEach(g => this._results.push({ type: 'goal', data: g }));
        (tasks || []).slice(0, 8).forEach(t => this._results.push({ type: 'task', data: t }));
        this.renderResults(panel);
      } catch (_) { this.hide(); }
    }, 250);

    input.addEventListener('input', () => doSearch(input.value.trim()));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); this.moveSelection(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); this.moveSelection(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); this.activateSelected(); }
      else if (e.key === 'Escape') { this.hide(); input.blur(); }
    });
    input.addEventListener('blur', () => setTimeout(() => this.hide(), 200));
  },

  renderResults(panel) {
    if (!this._results.length) { this.hide(); return; }
    this._visible = true;
    this._selectedIdx = -1;
    panel.style.display = 'block';

    const goalResults = this._results.filter(r => r.type === 'goal');
    const taskResults = this._results.filter(r => r.type === 'task');
    let html = '';
    if (goalResults.length) {
      html += '<div class="search-group-header">Goals</div>';
      goalResults.forEach((r, i) => {
        const idx = this._results.indexOf(r);
        html += `<div class="search-result-item" data-idx="${idx}">
          <span class="search-result-icon">🎯</span>
          <span class="search-result-title">${escHtml(r.data.title)}</span>
          <span class="search-result-status status-${r.data.status}">${escHtml(r.data.status)}</span>
        </div>`;
      });
    }
    if (taskResults.length) {
      html += '<div class="search-group-header">Tasks</div>';
      taskResults.forEach((r) => {
        const idx = this._results.indexOf(r);
        html += `<div class="search-result-item" data-idx="${idx}">
          <span class="search-result-icon">📋</span>
          <span class="search-result-title">${escHtml(r.data.title)}</span>
          <span class="search-result-status status-${r.data.status}">${escHtml(r.data.status)}</span>
        </div>`;
      });
    }
    panel.innerHTML = html;
    panel.querySelectorAll('.search-result-item').forEach(item => {
      item.addEventListener('mousedown', (e) => {
        e.preventDefault();
        const idx = parseInt(item.dataset.idx, 10);
        if (!isNaN(idx)) { this._selectedIdx = idx; this.activateSelected(); }
      });
    });
  },

  moveSelection(delta) {
    this._selectedIdx = Math.max(-1, Math.min(this._results.length - 1, this._selectedIdx + delta));
    const panel = document.getElementById('search-results-panel');
    if (!panel) return;
    panel.querySelectorAll('.search-result-item').forEach((item, i) => {
      const idx = parseInt(item.dataset.idx, 10);
      item.classList.toggle('selected', idx === this._selectedIdx);
    });
  },

  activateSelected() {
    if (this._selectedIdx < 0 || this._selectedIdx >= this._results.length) return;
    const r = this._results[this._selectedIdx];
    this.hide();
    if (r.type === 'goal') {
      openGoal(r.data.id);
    } else if (r.type === 'task') {
      Router.navigate(`#/goal/${r.data.goal_id}`);
    }
  },

  hide() {
    this._visible = false;
    const panel = document.getElementById('search-results-panel');
    if (panel) panel.style.display = 'none';
  }
};

// ─── Task Filter Bar (Feature Area 7) ───────────────────────────────────────

const TaskFilter = {
  _filters: { status: [], priority: [], goalId: null },

  init() {
    const bar = document.getElementById('task-filter-bar');
    if (!bar) return;
    bar.innerHTML = `
      <div class="filter-bar">
        <select id="filter-status" class="filter-select" multiple title="Filter by status">
          <option value="">All statuses</option>
          <option value="todo">To do</option>
          <option value="in_progress">In progress</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
        </select>
        <select id="filter-priority" class="filter-select" multiple title="Filter by priority">
          <option value="">All priorities</option>
          <option value="high">High</option>
          <option value="normal">Normal</option>
          <option value="low">Low</option>
        </select>
        <button id="btn-clear-filters" class="btn-secondary btn-sm">Clear</button>
      </div>`;
    const statusSel = document.getElementById('filter-status');
    const prioritySel = document.getElementById('filter-priority');
    const clearBtn = document.getElementById('btn-clear-filters');
    if (statusSel) statusSel.addEventListener('change', () => this.apply());
    if (prioritySel) prioritySel.addEventListener('change', () => this.apply());
    if (clearBtn) clearBtn.addEventListener('click', () => this.clear());
  },

  apply() {
    const statusSel = document.getElementById('filter-status');
    const prioritySel = document.getElementById('filter-priority');
    const statuses = statusSel ? Array.from(statusSel.selectedOptions).map(o => o.value).filter(Boolean) : [];
    const priorities = prioritySel ? Array.from(prioritySel.selectedOptions).map(o => o.value).filter(Boolean) : [];

    const cards = document.querySelectorAll('.task-card');
    cards.forEach(card => {
      const st = card.dataset.status || '';
      const pr = card.querySelector('.priority-dot')?.title?.replace('Priority: ', '') || 'normal';
      const showStatus = !statuses.length || statuses.includes(st);
      const showPriority = !priorities.length || priorities.includes(pr);
      card.style.display = (showStatus && showPriority) ? '' : 'none';
    });
  },

  clear() {
    const statusSel = document.getElementById('filter-status');
    const prioritySel = document.getElementById('filter-priority');
    if (statusSel) statusSel.selectedIndex = -1;
    if (prioritySel) prioritySel.selectedIndex = -1;
    document.querySelectorAll('.task-card').forEach(card => card.style.display = '');
  }
};


// ─── ROI Summary Card (Feature Area 4) ───────────────────────────────────────

// ─── Init ─────────────────────────────────────────────────────────────────────

// ─── Keyboard Shortcuts Help Overlay ──────────────────────────────────────────

const ShortcutHelp = {
  _visible: false,
  _overlay: null,

  toggle() {
    this._visible ? this.hide() : this.show();
  },

  show() {
    this._visible = true;
    if (this._overlay) { this._overlay.style.display = 'flex'; return; }
    this._overlay = document.createElement('div');
    this._overlay.className = 'shortcut-help-overlay';
    this._overlay.addEventListener('click', (e) => { if (e.target === this._overlay) this.hide(); });
    this._overlay.innerHTML = `
      <div class="shortcut-help-card">
        <div class="shortcut-help-header">
          <h3>Keyboard Shortcuts</h3>
          <button class="btn-close" aria-label="Close" id="_shortcut_close">&times;</button>
        </div>
        <div class="shortcut-help-grid">
          <div class="shortcut-group">
            <h4>Navigation</h4>
            <div class="shortcut-row"><kbd>G</kbd> <kbd>H</kbd><span>Go to Home</span></div>
            <div class="shortcut-row"><kbd>G</kbd> <kbd>D</kbd><span>Go to Dashboard</span></div>
            <div class="shortcut-row"><kbd>⌘</kbd> <kbd>K</kbd><span>Command Palette</span></div>
          </div>
          <div class="shortcut-group">
            <h4>Tasks</h4>
            <div class="shortcut-row"><kbd>J</kbd><span>Next task</span></div>
            <div class="shortcut-row"><kbd>K</kbd><span>Previous task</span></div>
            <div class="shortcut-row"><kbd>X</kbd><span>Toggle complete</span></div>
            <div class="shortcut-row"><kbd>E</kbd><span>Edit task</span></div>
            <div class="shortcut-row"><kbd>N</kbd><span>New task</span></div>
          </div>
          <div class="shortcut-group">
            <h4>General</h4>
            <div class="shortcut-row"><kbd>?</kbd><span>This help</span></div>
            <div class="shortcut-row"><kbd>Esc</kbd><span>Close panels</span></div>
            <div class="shortcut-row"><kbd>⌘</kbd> <kbd>⏎</kbd><span>Submit form</span></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(this._overlay);
    this._overlay.querySelector('#_shortcut_close').addEventListener('click', () => this.hide());
  },

  hide() {
    this._visible = false;
    if (this._overlay) this._overlay.style.display = 'none';
  }
};

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
  UnifiedSearch.init();
  TaskFilter.init();
  setupCharCounter('goal-desc', 'goal-desc-counter');

  // Wire mood selector
  document.querySelectorAll('.mood-option').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.mood-option').forEach(m => m.classList.remove('selected'));
      btn.classList.add('selected');
    });
  });

  // Wire execution step expand/collapse (delegated)
  document.addEventListener('click', (e) => {
    const header = e.target.closest('.exec-step-header');
    if (header) {
      const detail = header.nextElementSibling;
      if (detail) detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
    }
    const showMore = e.target.closest('.exec-show-more');
    if (showMore) {
      const pre = showMore.previousElementSibling;
      if (pre) pre.style.maxHeight = 'none';
      showMore.remove();
    }
  });

  // Dark mode toggle
  const themeBtn = document.getElementById('btn-theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  // Tour trigger button
  const tourBtn = document.getElementById('tour-trigger-btn');
  if (tourBtn) tourBtn.addEventListener('click', () => {
    localStorage.removeItem('teb_onboarded');
    try { new OnboardingTour().init(); } catch (_) { /* non-critical */ }
  });

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
    loadXpBar();
    startAutopilotPolling();
  }

  // Initialize router (handles initial route)
  Router.init();

  // Auto-start onboarding tour for first-time users
  try { new OnboardingTour().init(); } catch (_) { /* non-critical */ }
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

  // Get selected mood
  const moodEl = document.querySelector('.mood-option.selected');
  const mood = moodEl ? moodEl.dataset.mood : 'neutral';

  const btn = document.getElementById('btn-checkin');
  if (btn) btn.disabled = true;
  try {
    const res = await api.post(`/api/goals/${currentGoalId}/checkin`, {
      done_summary: done + (mood !== 'neutral' ? ' [mood:' + mood + ']' : ''),
      blockers: blockers,
    });
    // Show coaching feedback as rendered markdown (crossfade transition)
    const fb = document.getElementById('checkin-feedback');
    if (fb) {
      fb.innerHTML = simpleMarkdown(res.coaching || res.feedback || '');
      fb.style.display = 'block';
      fb.classList.add('fade-in');
    }
    // Clear inputs
    if (doneEl) doneEl.value = '';
    if (blockersEl) blockersEl.value = '';
    // Clear mood selection
    document.querySelectorAll('.mood-option').forEach(m => m.classList.remove('selected'));
    // Refresh history and nudge
    loadCheckinHistory();
    loadNudge();
    loadXpBar();
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
      if (isNudgeAcknowledged(res.nudge.id)) {
        banner.style.display = 'none';
        return;
      }
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
    acknowledgeNudge(nudgeId);
    try {
      await api.post(`/api/nudges/${nudgeId}/acknowledge`, {});
    } catch (e) { /* ignore */ }
  }
  banner.classList.add('nudge-slide-out');
  setTimeout(() => { banner.style.display = 'none'; banner.classList.remove('nudge-slide-out'); }, 300);
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

// ─── Gamification: XP & Achievements ──────────────────────────────────────────

async function loadGamification() {
  const panel = document.getElementById('gamification-panel');
  if (!panel) return;

  try {
    const xpData = await api.get('/api/users/me/xp');
    const xpDisplay = document.getElementById('xp-display');
    if (xpDisplay && xpData) {
      const level = xpData.level || 1;
      const xp = xpData.total_xp || 0;
      const currentLevelXP = xp % 100;
      const pct = Math.min(100, Math.round((currentLevelXP / 100) * 100));
      xpDisplay.innerHTML = `
        <div class="xp-summary">
          <div class="xp-level-badge">Level ${level}</div>
          <div class="xp-bar-wrap">
            <div class="xp-bar-bg">
              <div class="xp-bar-fill" style="width:${pct}%"></div>
            </div>
            <span class="xp-bar-label">${xp} XP total · ${currentLevelXP}/100 to next level</span>
          </div>
        </div>`;

      // Update local LevelUp state to match server
      if (typeof LevelUp !== 'undefined') {
        LevelUp._currentLevel = level;
        LevelUp._currentXP = currentLevelXP;
        localStorage.setItem('teb_level', level);
        localStorage.setItem('teb_xp', currentLevelXP);
      }
    }

    // Streak display
    const streakDisplay = document.getElementById('streak-display');
    if (streakDisplay && xpData) {
      const streak = xpData.streak_days || 0;
      if (streak > 0) {
        streakDisplay.innerHTML = `<div class="streak-badge"><span class="streak-icon">🔥</span><span class="streak-count">${streak}</span> day streak</div>`;
      } else {
        streakDisplay.innerHTML = `<div class="streak-badge"><span class="streak-icon">🔥</span>Start a streak by completing tasks daily!</div>`;
      }
    }

    panel.style.display = 'block';
  } catch (e) {
    // Hide panel if user not logged in or API fails
    if (panel) panel.style.display = 'none';
  }

  // Load achievements
  try {
    const achievements = await api.get('/api/users/me/achievements');
    const list = document.getElementById('achievements-list');
    if (list) {
      if (!achievements || !achievements.length) {
        list.innerHTML = '<div class="empty-state" style="padding:var(--space-md)"><div class="empty-state-icon">🏆</div><div class="empty-state-desc">No achievements yet. Keep completing tasks!</div></div>';
      } else {
        list.innerHTML = achievements.map(a => `
          <div class="achievement-card${a.unlocked_at ? ' unlocked' : ''}">
            <span class="achievement-icon">${a.icon || '🏆'}</span>
            <div class="achievement-info">
              <div class="achievement-name">${escHtml(a.name)}</div>
              <div class="achievement-desc">${escHtml(a.description || '')}</div>
              ${a.unlocked_at ? `<div class="achievement-date">Unlocked ${timeAgo(a.unlocked_at)}</div>` : ''}
            </div>
          </div>
        `).join('');
      }
    }
  } catch (e) {
    // Silent fail — achievements are optional
  }
}

// ─── ROI Dashboard ────────────────────────────────────────────────────────────

async function loadRoiDashboard() {
  if (!currentGoalId) return;
  const panel = document.getElementById('roi-dashboard-panel');
  if (!panel) return;

  // Show panel immediately with a loading indicator so the user knows data is coming
  const roiContent = document.getElementById('roi-dashboard-content');
  if (roiContent) {
    roiContent.innerHTML = '<div class="empty-state" style="padding:var(--space-md)"><div class="empty-state-desc">Loading ROI data…</div></div>';
  }
  panel.style.display = 'block';

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

    // Show panel — either with data or with a helpful empty-state
    const hasData = roi.total_spent > 0 || roi.total_earned > 0 || roi.pending_requests > 0;
    if (!hasData) {
      ['roi-summary-cards', 'roi-spending-breakdown', 'roi-timeline', 'roi-earnings', 'roi-budgets'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
      });
      if (roiContent) {
        roiContent.innerHTML = '<div class="empty-state"><div class="empty-state-icon">💰</div><div class="empty-state-title">No financial data yet</div><div class="empty-state-desc">Enable autopilot and approve spending requests to track ROI here.</div></div>';
      }
    }
    panel.style.display = 'block';
  } catch (e) {
    console.warn('ROI dashboard load failed:', e);
    if (roiContent) {
      roiContent.innerHTML = '<div class="empty-state"><div class="empty-state-icon">💰</div><div class="empty-state-title">No financial data yet</div><div class="empty-state-desc">Enable autopilot and approve spending requests to track ROI here.</div></div>';
    }
    panel.style.display = 'block';
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

    content.innerHTML = html || '<div class="empty-state"><div class="empty-state-icon">📡</div><div class="empty-state-title">Not enough data yet</div><div class="empty-state-desc">Insights will appear as you and others use the platform.</div></div>';
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
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
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
    { key: 'list', label: 'List', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>' },
    { key: 'kanban', label: 'Kanban', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="5" height="18" rx="1"/><rect x="9.5" y="3" width="5" height="12" rx="1"/><rect x="17" y="3" width="5" height="15" rx="1"/></svg>' },
    { key: 'table', label: 'Table', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>' },
    { key: 'gantt', label: 'Gantt', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="14" height="3" rx="1.5" fill="currentColor" opacity=".7"/><rect x="6" y="10" width="10" height="3" rx="1.5" fill="currentColor" opacity=".5"/><rect x="4" y="16" width="16" height="3" rx="1.5" fill="currentColor" opacity=".3"/></svg>' },
    { key: 'workload', label: 'Workload', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="15" y1="11" x2="23" y2="11"/><line x1="15" y1="15" x2="20" y2="15"/></svg>' },
    { key: 'timeline', label: 'Timeline', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="22"/><circle cx="12" cy="6" r="2.5" fill="currentColor"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/><circle cx="12" cy="18" r="2.5" fill="currentColor"/></svg>' },
    { key: 'calendar', label: 'Calendar', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>' },
    { key: 'mindmap', label: 'Mind Map', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="9"/><line x1="12" y1="15" x2="12" y2="22"/><line x1="2" y1="12" x2="9" y2="12"/><line x1="15" y1="12" x2="22" y2="12"/></svg>' },
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
        viewContainer.id = 'mindmap-container';
        viewContainer.style.minHeight = '400px';
        if (typeof renderMindMap !== 'undefined') {
          api.get('/api/goals').then(goals => {
            if (!goals || !goals.length) {
              viewContainer.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🗺️</div><div class="empty-state-title">No goals to map</div><div class="empty-state-desc">Create your first goal and it will appear here as a mind map.</div></div>';
            } else {
              renderMindMap('mindmap-container', goals);
            }
          }).catch(() => {
            viewContainer.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🗺️</div><div class="empty-state-title">Mind map unavailable</div><div class="empty-state-desc">Could not load goals. Please try again.</div></div>';
          });
        } else {
          viewContainer.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🗺️</div><div class="empty-state-title">Mind map unavailable</div><div class="empty-state-desc">The mind map module could not be loaded.</div></div>';
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
      if (!currentGoalId) {
        grid.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🎯</div><div class="empty-state-title">No goals yet</div><div class="empty-state-desc">Create your first goal to see dashboard widgets here.</div></div>';
      } else {
        grid.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><div class="empty-state-title">No widgets yet</div><div class="empty-state-desc">Click "+ Add Widget" to build your custom dashboard.</div></div>';
      }
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
    if (this._audioCtx.state === 'suspended') {
      this._audioCtx.resume().catch(() => {});
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

// ─── Real-time SSE Client ──────────────────────────────────────────────────────

const RealtimeClient = {
  _eventSource: null,
  _lastEventId: null,
  _reconnectAttempts: 0,
  _maxReconnectAttempts: 10,
  _baseDelay: 1000,
  _handlers: {},

  /**
   * Register a handler for a specific SSE event type.
   * Multiple handlers per event type are supported.
   */
  on(eventType, handler) {
    if (!this._handlers[eventType]) this._handlers[eventType] = [];
    this._handlers[eventType].push(handler);
    return this;
  },

  /**
   * Remove a handler for a specific event type.
   */
  off(eventType, handler) {
    if (!this._handlers[eventType]) return;
    this._handlers[eventType] = this._handlers[eventType].filter(h => h !== handler);
  },

  _dispatch(eventType, data) {
    const handlers = this._handlers[eventType] || [];
    handlers.forEach(h => {
      try { h(data); } catch (e) { console.error('SSE handler error:', e); }
    });
    // Also dispatch to wildcard handlers
    const wildcardHandlers = this._handlers['*'] || [];
    wildcardHandlers.forEach(h => {
      try { h(eventType, data); } catch (e) { console.error('SSE wildcard handler error:', e); }
    });
  },

  /**
   * Connect to the SSE stream. Requires a valid auth token.
   */
  connect() {
    if (this._eventSource) this.disconnect();

    const token = localStorage.getItem('teb_token');
    if (!token) return; // Not logged in

    let url = `${BASE_PATH}/api/events/stream`;
    if (this._lastEventId) {
      url += `?Last-Event-ID=${encodeURIComponent(this._lastEventId)}`;
    }

    // EventSource doesn't support custom headers, so pass token as query param
    url += (url.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;

    try {
      this._eventSource = new EventSource(url);
    } catch (e) {
      console.warn('SSE: EventSource not supported, falling back to polling');
      return;
    }

    this._eventSource.onopen = () => {
      this._reconnectAttempts = 0;
      console.log('SSE: connected');
    };

    this._eventSource.onerror = () => {
      if (this._eventSource && this._eventSource.readyState === EventSource.CLOSED) {
        console.warn('SSE: connection closed, attempting reconnect');
        this._scheduleReconnect();
      }
    };

    // Register SSE event listeners for known event types
    const eventTypes = [
      'task_completed', 'task_updated', 'task_created', 'task_deleted',
      'task_started', 'task_progress', 'task_assigned',
      'goal_created', 'goal_updated', 'goal_deleted',
      'goal_milestone', 'orchestration_complete',
      'execution_result', 'execution_escalated',
      'spending_request', 'checkin_nudge', 'agent_handoff',
      'report_generated', 'mention', 'new_message', 'goal_chat',
      'shutdown',
    ];

    eventTypes.forEach(et => {
      if (this._eventSource) {
        this._eventSource.addEventListener(et, (e) => {
          try {
            if (e.lastEventId) this._lastEventId = e.lastEventId;
            const data = JSON.parse(e.data);
            this._dispatch(et, data);
          } catch (err) {
            console.warn('SSE: failed to parse event data:', err);
          }
        });
      }
    });
  },

  disconnect() {
    if (this._eventSource) {
      this._eventSource.close();
      this._eventSource = null;
    }
  },

  _scheduleReconnect() {
    if (this._reconnectAttempts >= this._maxReconnectAttempts) {
      console.warn('SSE: max reconnect attempts reached, falling back to polling');
      startPeriodicRefresh();
      return;
    }
    const delay = Math.min(
      this._baseDelay * Math.pow(2, this._reconnectAttempts) + Math.random() * 1000,
      30000
    );
    this._reconnectAttempts++;
    setTimeout(() => this.connect(), delay);
  },

  /** Whether the SSE connection is currently open. */
  get connected() {
    return this._eventSource && this._eventSource.readyState === EventSource.OPEN;
  }
};

// ─── SSE event handlers for live UI updates ─────────────────────────────────

RealtimeClient.on('task_completed', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  // Update the task in our local state
  const idx = currentTasks.findIndex(t => String(t.id) === String(data.task_id));
  if (idx !== -1) {
    currentTasks[idx].status = 'done';
    renderTasks(currentTasks);
    updateProgress(currentTasks);
  }
  toast.info('Task Completed', `"${escHtml(data.task_title || '')}" is done!`);
});

RealtimeClient.on('task_updated', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  const idx = currentTasks.findIndex(t => String(t.id) === String(data.task_id));
  if (idx !== -1 && data.status) {
    currentTasks[idx].status = data.status;
    renderTasks(currentTasks);
    updateProgress(currentTasks);
  }
});

RealtimeClient.on('task_created', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  // Reload the full task list for this goal to get the new task
  loadGoalById(currentGoalId);
});

RealtimeClient.on('task_deleted', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  currentTasks = currentTasks.filter(t => String(t.id) !== String(data.task_id));
  renderTasks(currentTasks);
  updateProgress(currentTasks);
});

RealtimeClient.on('goal_updated', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  loadGoalById(currentGoalId);
});

RealtimeClient.on('orchestration_complete', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  toast.success('Orchestration Complete',
    `${data.tasks_succeeded || 0} succeeded, ${data.tasks_failed || 0} failed`);
  loadGoalById(currentGoalId);
});

RealtimeClient.on('spending_request', (data) => {
  toast.warning('Spending Approval Needed',
    `$${(data.amount || 0).toFixed(2)} — ${escHtml(data.description || '')}`);
});

RealtimeClient.on('agent_handoff', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  toast.info('Agent Handoff',
    `${escHtml(data.from_agent || '?')} → ${escHtml(data.to_agent || '?')}`);
});

RealtimeClient.on('execution_escalated', (data) => {
  toast.warning('Execution Escalated',
    `Task ${data.task_id}: ${escHtml(data.reason || 'needs review')}`);
});

RealtimeClient.on('shutdown', () => {
  toast.warning('Server Shutting Down', 'Connection will be re-established when the server restarts.');
  RealtimeClient._scheduleReconnect();
});

// Connect SSE when we have auth, disconnect on logout
(function initSSE() {
  const token = localStorage.getItem('teb_token');
  if (token) {
    RealtimeClient.connect();
    // When SSE is active, stop polling (SSE replaces it)
    stopPeriodicRefresh();
  }
})();


// ─── Block Editor (recursive block-based content) ────────────────────────────

const BlockEditor = {
  _BLOCK_TYPES: [
    { key: 'paragraph', label: 'Paragraph', icon: '¶' },
    { key: 'heading', label: 'Heading', icon: 'H' },
    { key: 'code', label: 'Code', icon: '⟨⟩' },
    { key: 'quote', label: 'Quote', icon: '❝' },
    { key: 'callout', label: 'Callout', icon: '💡' },
    { key: 'checklist_item', label: 'Checklist', icon: '☑' },
    { key: 'bullet_list', label: 'Bullet List', icon: '•' },
    { key: 'numbered_list', label: 'Numbered List', icon: '1.' },
    { key: 'divider', label: 'Divider', icon: '—' },
    { key: 'image', label: 'Image', icon: '🖼' },
  ],

  /**
   * Render the block editor into a container for a given entity.
   * @param {string} containerId - DOM id of the editor container
   * @param {string} entityType - 'tasks' or 'goals'
   * @param {number} entityId - the entity's id
   */
  async render(containerId, entityType, entityId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '<div class="block-editor-loading">Loading blocks…</div>';

    try {
      const blocks = await api.get(`/api/${entityType}/${entityId}/blocks?tree=true`);
      container.innerHTML = '';

      const editorEl = document.createElement('div');
      editorEl.className = 'block-editor';
      editorEl.dataset.entityType = entityType;
      editorEl.dataset.entityId = entityId;

      if (blocks.length === 0) {
        // Show empty state with add button
        const empty = document.createElement('div');
        empty.className = 'block-editor-empty';
        empty.innerHTML = `
          <p class="block-editor-empty-text">No content blocks yet. Click + to add one.</p>
        `;
        editorEl.appendChild(empty);
      } else {
        blocks.forEach(block => {
          editorEl.appendChild(this._renderBlock(block, entityType, entityId, 0));
        });
      }

      // Add block button
      const addBtn = document.createElement('button');
      addBtn.className = 'block-add-btn';
      addBtn.textContent = '+ Add block';
      addBtn.addEventListener('click', () => this._showBlockTypeMenu(addBtn, entityType, entityId, null));
      editorEl.appendChild(addBtn);

      container.appendChild(editorEl);
    } catch (e) {
      container.innerHTML = `<div class="block-editor-error">Failed to load blocks: ${escHtml(e.message || '')}</div>`;
    }
  },

  _renderBlock(block, entityType, entityId, depth) {
    const el = document.createElement('div');
    el.className = `block-item block-type-${escHtml(block.block_type)}`;
    el.dataset.blockId = block.id;
    el.style.marginLeft = (depth * 16) + 'px';

    // Block content rendering by type
    const contentEl = document.createElement('div');
    contentEl.className = 'block-content';

    switch (block.block_type) {
      case 'heading': {
        const level = (block.properties && block.properties.level) || 1;
        const tag = level <= 3 ? `h${level + 1}` : 'h4';
        contentEl.innerHTML = `<${tag} class="block-heading">${escHtml(block.content)}</${tag}>`;
        break;
      }
      case 'code': {
        const lang = (block.properties && block.properties.language) || '';
        contentEl.innerHTML = `<pre class="block-code"><code data-lang="${escHtml(lang)}">${escHtml(block.content)}</code></pre>`;
        break;
      }
      case 'quote':
        contentEl.innerHTML = `<blockquote class="block-quote">${escHtml(block.content)}</blockquote>`;
        break;
      case 'callout': {
        const color = (block.properties && block.properties.color) || 'blue';
        contentEl.innerHTML = `<div class="block-callout block-callout-${escHtml(color)}"><span class="block-callout-icon">💡</span><span>${escHtml(block.content)}</span></div>`;
        break;
      }
      case 'checklist_item': {
        const checked = block.properties && block.properties.checked;
        contentEl.innerHTML = `<label class="block-checklist"><input type="checkbox" ${checked ? 'checked' : ''} data-block-id="${block.id}" class="block-checkbox"><span class="${checked ? 'block-checked' : ''}">${escHtml(block.content)}</span></label>`;
        break;
      }
      case 'bullet_list':
        contentEl.innerHTML = `<div class="block-bullet">• ${escHtml(block.content)}</div>`;
        break;
      case 'numbered_list':
        contentEl.innerHTML = `<div class="block-numbered">${block.order_index + 1}. ${escHtml(block.content)}</div>`;
        break;
      case 'divider':
        contentEl.innerHTML = '<hr class="block-divider">';
        break;
      case 'image': {
        const url = (block.properties && block.properties.url) || '';
        const caption = (block.properties && block.properties.caption) || '';
        if (url) {
          contentEl.innerHTML = `<figure class="block-image"><img src="${escHtml(url)}" alt="${escHtml(caption)}" loading="lazy"><figcaption>${escHtml(caption)}</figcaption></figure>`;
        } else {
          contentEl.innerHTML = `<div class="block-image-placeholder">No image URL</div>`;
        }
        break;
      }
      default:
        contentEl.innerHTML = `<p class="block-paragraph">${escHtml(block.content)}</p>`;
    }

    // Make text content inline-editable
    if (block.block_type !== 'divider') {
      const textEl = contentEl.querySelector('p, h2, h3, h4, blockquote > *, .block-bullet, .block-numbered, .block-callout > span:last-child, .block-checklist > span');
      if (textEl) {
        textEl.contentEditable = 'true';
        textEl.addEventListener('blur', async () => {
          const newContent = textEl.textContent || '';
          if (newContent !== block.content) {
            try {
              await api.patch(`/api/blocks/${block.id}`, { content: newContent });
              block.content = newContent;
            } catch (e) {
              toast.error('Error', 'Failed to save block: ' + (e.message || ''));
            }
          }
        });
      }
    }

    // Checkbox toggle for checklist items
    if (block.block_type === 'checklist_item') {
      const cb = contentEl.querySelector('.block-checkbox');
      if (cb) {
        cb.addEventListener('change', async () => {
          try {
            const props = block.properties || {};
            props.checked = cb.checked;
            await api.patch(`/api/blocks/${block.id}`, { properties: props });
          } catch (e) {
            toast.error('Error', 'Failed to update checkbox');
          }
        });
      }
    }

    el.appendChild(contentEl);

    // Block action bar (type selector, delete, add child)
    const actions = document.createElement('div');
    actions.className = 'block-actions';
    actions.innerHTML = `
      <button class="block-action-btn block-action-drag" title="Drag">⠿</button>
      <button class="block-action-btn block-action-add" title="Add block below">+</button>
      <button class="block-action-btn block-action-delete" title="Delete">×</button>
    `;
    const addChildBtn = actions.querySelector('.block-action-add');
    if (addChildBtn) {
      addChildBtn.addEventListener('click', () => {
        this._showBlockTypeMenu(addChildBtn, entityType, entityId, block.id);
      });
    }
    const deleteBtn = actions.querySelector('.block-action-delete');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        try {
          await api.del(`/api/blocks/${block.id}`);
          el.remove();
        } catch (e) {
          toast.error('Error', 'Failed to delete block');
        }
      });
    }
    el.insertBefore(actions, contentEl);

    // Render children recursively
    if (block.children && block.children.length > 0) {
      const childrenEl = document.createElement('div');
      childrenEl.className = 'block-children';
      block.children.forEach(child => {
        childrenEl.appendChild(this._renderBlock(child, entityType, entityId, depth + 1));
      });
      el.appendChild(childrenEl);
    }

    return el;
  },

  _showBlockTypeMenu(anchorEl, entityType, entityId, parentBlockId) {
    // Remove any existing menus
    document.querySelectorAll('.block-type-menu').forEach(m => m.remove());

    const menu = document.createElement('div');
    menu.className = 'block-type-menu';
    this._BLOCK_TYPES.forEach(bt => {
      const item = document.createElement('button');
      item.className = 'block-type-menu-item';
      item.innerHTML = `<span class="block-type-icon">${bt.icon}</span> ${escHtml(bt.label)}`;
      item.addEventListener('click', async () => {
        menu.remove();
        try {
          const created = await api.post(`/api/${entityType}/${entityId}/blocks`, {
            block_type: bt.key,
            content: bt.key === 'divider' ? '' : '',
            parent_block_id: parentBlockId,
            order_index: 0,
          });
          // Re-render the editor to reflect the new block
          const editorEl = anchorEl.closest('.block-editor');
          if (editorEl) {
            const containerId = editorEl.parentElement ? editorEl.parentElement.id : null;
            if (containerId) {
              await this.render(containerId, entityType, entityId);
            }
          }
        } catch (e) {
          toast.error('Error', 'Failed to create block: ' + (e.message || ''));
        }
      });
      menu.appendChild(item);
    });

    // Position menu
    anchorEl.parentElement.appendChild(menu);

    // Close on outside click
    const closeMenu = (e) => {
      if (!menu.contains(e.target) && e.target !== anchorEl) {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      }
    };
    setTimeout(() => document.addEventListener('click', closeMenu), 0);
  },
};


// ─── Real-time SSE Client ──────────────────────────────────────────────────────

const RealtimeClient = {
  _eventSource: null,
  _lastEventId: null,
  _reconnectAttempts: 0,
  _maxReconnectAttempts: 10,
  _baseDelay: 1000,
  _handlers: {},

  /**
   * Register a handler for a specific SSE event type.
   * Multiple handlers per event type are supported.
   */
  on(eventType, handler) {
    if (!this._handlers[eventType]) this._handlers[eventType] = [];
    this._handlers[eventType].push(handler);
    return this;
  },

  /**
   * Remove a handler for a specific event type.
   */
  off(eventType, handler) {
    if (!this._handlers[eventType]) return;
    this._handlers[eventType] = this._handlers[eventType].filter(h => h !== handler);
  },

  _dispatch(eventType, data) {
    const handlers = this._handlers[eventType] || [];
    handlers.forEach(h => {
      try { h(data); } catch (e) { console.error('SSE handler error:', e); }
    });
    // Also dispatch to wildcard handlers
    const wildcardHandlers = this._handlers['*'] || [];
    wildcardHandlers.forEach(h => {
      try { h(eventType, data); } catch (e) { console.error('SSE wildcard handler error:', e); }
    });
  },

  /**
   * Connect to the SSE stream. Requires a valid auth token.
   */
  connect() {
    if (this._eventSource) this.disconnect();

    const token = localStorage.getItem('teb_token');
    if (!token) return; // Not logged in

    let url = `${BASE_PATH}/api/events/stream`;
    if (this._lastEventId) {
      url += `?Last-Event-ID=${encodeURIComponent(this._lastEventId)}`;
    }

    // EventSource doesn't support custom headers, so pass token as query param
    url += (url.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;

    try {
      this._eventSource = new EventSource(url);
    } catch (e) {
      console.warn('SSE: EventSource not supported, falling back to polling');
      return;
    }

    this._eventSource.onopen = () => {
      this._reconnectAttempts = 0;
      console.log('SSE: connected');
    };

    this._eventSource.onerror = () => {
      if (this._eventSource && this._eventSource.readyState === EventSource.CLOSED) {
        console.warn('SSE: connection closed, attempting reconnect');
        this._scheduleReconnect();
      }
    };

    // Register SSE event listeners for known event types
    const eventTypes = [
      'task_completed', 'task_updated', 'task_created', 'task_deleted',
      'task_started', 'task_progress', 'task_assigned',
      'goal_created', 'goal_updated', 'goal_deleted',
      'goal_milestone', 'orchestration_complete',
      'execution_result', 'execution_escalated',
      'spending_request', 'checkin_nudge', 'agent_handoff',
      'report_generated', 'mention', 'new_message', 'goal_chat',
      'shutdown',
    ];

    eventTypes.forEach(et => {
      if (this._eventSource) {
        this._eventSource.addEventListener(et, (e) => {
          try {
            if (e.lastEventId) this._lastEventId = e.lastEventId;
            const data = JSON.parse(e.data);
            this._dispatch(et, data);
          } catch (err) {
            console.warn('SSE: failed to parse event data:', err);
          }
        });
      }
    });
  },

  disconnect() {
    if (this._eventSource) {
      this._eventSource.close();
      this._eventSource = null;
    }
  },

  _scheduleReconnect() {
    if (this._reconnectAttempts >= this._maxReconnectAttempts) {
      console.warn('SSE: max reconnect attempts reached, falling back to polling');
      startPeriodicRefresh();
      return;
    }
    const delay = Math.min(
      this._baseDelay * Math.pow(2, this._reconnectAttempts) + Math.random() * 1000,
      30000
    );
    this._reconnectAttempts++;
    setTimeout(() => this.connect(), delay);
  },

  /** Whether the SSE connection is currently open. */
  get connected() {
    return this._eventSource && this._eventSource.readyState === EventSource.OPEN;
  }
};

// ─── SSE event handlers for live UI updates ─────────────────────────────────

RealtimeClient.on('task_completed', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  // Update the task in our local state
  const idx = currentTasks.findIndex(t => String(t.id) === String(data.task_id));
  if (idx !== -1) {
    currentTasks[idx].status = 'done';
    renderTasks(currentTasks);
    updateProgress(currentTasks);
  }
  toast.info('Task Completed', `"${escHtml(data.task_title || '')}" is done!`);
});

RealtimeClient.on('task_updated', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  const idx = currentTasks.findIndex(t => String(t.id) === String(data.task_id));
  if (idx !== -1 && data.status) {
    currentTasks[idx].status = data.status;
    renderTasks(currentTasks);
    updateProgress(currentTasks);
  }
});

RealtimeClient.on('task_created', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  // Reload the full task list for this goal to get the new task
  loadGoalById(currentGoalId);
});

RealtimeClient.on('task_deleted', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  currentTasks = currentTasks.filter(t => String(t.id) !== String(data.task_id));
  renderTasks(currentTasks);
  updateProgress(currentTasks);
});

RealtimeClient.on('goal_updated', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  loadGoalById(currentGoalId);
});

RealtimeClient.on('orchestration_complete', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  toast.success('Orchestration Complete',
    `${data.tasks_succeeded || 0} succeeded, ${data.tasks_failed || 0} failed`);
  loadGoalById(currentGoalId);
});

RealtimeClient.on('spending_request', (data) => {
  toast.warning('Spending Approval Needed',
    `$${(data.amount || 0).toFixed(2)} — ${escHtml(data.description || '')}`);
});

RealtimeClient.on('agent_handoff', (data) => {
  if (!currentGoalId || data.goal_id !== currentGoalId) return;
  toast.info('Agent Handoff',
    `${escHtml(data.from_agent || '?')} → ${escHtml(data.to_agent || '?')}`);
});

RealtimeClient.on('execution_escalated', (data) => {
  toast.warning('Execution Escalated',
    `Task ${data.task_id}: ${escHtml(data.reason || 'needs review')}`);
});

RealtimeClient.on('shutdown', () => {
  toast.warning('Server Shutting Down', 'Connection will be re-established when the server restarts.');
  RealtimeClient._scheduleReconnect();
});

// Connect SSE when we have auth, disconnect on logout
(function initSSE() {
  const token = localStorage.getItem('teb_token');
  if (token) {
    RealtimeClient.connect();
    // When SSE is active, stop polling (SSE replaces it)
    stopPeriodicRefresh();
  }
})();


// ─── Block Editor (recursive block-based content) ────────────────────────────

const BlockEditor = {
  _BLOCK_TYPES: [
    { key: 'paragraph', label: 'Paragraph', icon: '¶' },
    { key: 'heading', label: 'Heading', icon: 'H' },
    { key: 'code', label: 'Code', icon: '⟨⟩' },
    { key: 'quote', label: 'Quote', icon: '❝' },
    { key: 'callout', label: 'Callout', icon: '💡' },
    { key: 'checklist_item', label: 'Checklist', icon: '☑' },
    { key: 'bullet_list', label: 'Bullet List', icon: '•' },
    { key: 'numbered_list', label: 'Numbered List', icon: '1.' },
    { key: 'divider', label: 'Divider', icon: '—' },
    { key: 'image', label: 'Image', icon: '🖼' },
  ],

  /**
   * Render the block editor into a container for a given entity.
   * @param {string} containerId - DOM id of the editor container
   * @param {string} entityType - 'tasks' or 'goals'
   * @param {number} entityId - the entity's id
   */
  async render(containerId, entityType, entityId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '<div class="block-editor-loading">Loading blocks…</div>';

    try {
      const blocks = await api.get(`/api/${entityType}/${entityId}/blocks?tree=true`);
      container.innerHTML = '';

      const editorEl = document.createElement('div');
      editorEl.className = 'block-editor';
      editorEl.dataset.entityType = entityType;
      editorEl.dataset.entityId = entityId;

      if (blocks.length === 0) {
        // Show empty state with add button
        const empty = document.createElement('div');
        empty.className = 'block-editor-empty';
        empty.innerHTML = `
          <p class="block-editor-empty-text">No content blocks yet. Click + to add one.</p>
        `;
        editorEl.appendChild(empty);
      } else {
        blocks.forEach(block => {
          editorEl.appendChild(this._renderBlock(block, entityType, entityId, 0));
        });
      }

      // Add block button
      const addBtn = document.createElement('button');
      addBtn.className = 'block-add-btn';
      addBtn.textContent = '+ Add block';
      addBtn.addEventListener('click', () => this._showBlockTypeMenu(addBtn, entityType, entityId, null));
      editorEl.appendChild(addBtn);

      container.appendChild(editorEl);
    } catch (e) {
      container.innerHTML = `<div class="block-editor-error">Failed to load blocks: ${escHtml(e.message || '')}</div>`;
    }
  },

  _renderBlock(block, entityType, entityId, depth) {
    const el = document.createElement('div');
    el.className = `block-item block-type-${escHtml(block.block_type)}`;
    el.dataset.blockId = block.id;
    el.style.marginLeft = (depth * 16) + 'px';

    // Block content rendering by type
    const contentEl = document.createElement('div');
    contentEl.className = 'block-content';

    switch (block.block_type) {
      case 'heading': {
        const level = (block.properties && block.properties.level) || 1;
        const tag = level <= 3 ? `h${level + 1}` : 'h4';
        contentEl.innerHTML = `<${tag} class="block-heading">${escHtml(block.content)}</${tag}>`;
        break;
      }
      case 'code': {
        const lang = (block.properties && block.properties.language) || '';
        contentEl.innerHTML = `<pre class="block-code"><code data-lang="${escHtml(lang)}">${escHtml(block.content)}</code></pre>`;
        break;
      }
      case 'quote':
        contentEl.innerHTML = `<blockquote class="block-quote">${escHtml(block.content)}</blockquote>`;
        break;
      case 'callout': {
        const color = (block.properties && block.properties.color) || 'blue';
        contentEl.innerHTML = `<div class="block-callout block-callout-${escHtml(color)}"><span class="block-callout-icon">💡</span><span>${escHtml(block.content)}</span></div>`;
        break;
      }
      case 'checklist_item': {
        const checked = block.properties && block.properties.checked;
        contentEl.innerHTML = `<label class="block-checklist"><input type="checkbox" ${checked ? 'checked' : ''} data-block-id="${block.id}" class="block-checkbox"><span class="${checked ? 'block-checked' : ''}">${escHtml(block.content)}</span></label>`;
        break;
      }
      case 'bullet_list':
        contentEl.innerHTML = `<div class="block-bullet">• ${escHtml(block.content)}</div>`;
        break;
      case 'numbered_list':
        contentEl.innerHTML = `<div class="block-numbered">${block.order_index + 1}. ${escHtml(block.content)}</div>`;
        break;
      case 'divider':
        contentEl.innerHTML = '<hr class="block-divider">';
        break;
      case 'image': {
        const url = (block.properties && block.properties.url) || '';
        const caption = (block.properties && block.properties.caption) || '';
        if (url) {
          contentEl.innerHTML = `<figure class="block-image"><img src="${escHtml(url)}" alt="${escHtml(caption)}" loading="lazy"><figcaption>${escHtml(caption)}</figcaption></figure>`;
        } else {
          contentEl.innerHTML = `<div class="block-image-placeholder">No image URL</div>`;
        }
        break;
      }
      default:
        contentEl.innerHTML = `<p class="block-paragraph">${escHtml(block.content)}</p>`;
    }

    // Make text content inline-editable
    if (block.block_type !== 'divider') {
      const textEl = contentEl.querySelector('p, h2, h3, h4, blockquote > *, .block-bullet, .block-numbered, .block-callout > span:last-child, .block-checklist > span');
      if (textEl) {
        textEl.contentEditable = 'true';
        textEl.addEventListener('blur', async () => {
          const newContent = textEl.textContent || '';
          if (newContent !== block.content) {
            try {
              await api.patch(`/api/blocks/${block.id}`, { content: newContent });
              block.content = newContent;
            } catch (e) {
              toast.error('Error', 'Failed to save block: ' + (e.message || ''));
            }
          }
        });
      }
    }

    // Checkbox toggle for checklist items
    if (block.block_type === 'checklist_item') {
      const cb = contentEl.querySelector('.block-checkbox');
      if (cb) {
        cb.addEventListener('change', async () => {
          try {
            const props = block.properties || {};
            props.checked = cb.checked;
            await api.patch(`/api/blocks/${block.id}`, { properties: props });
          } catch (e) {
            toast.error('Error', 'Failed to update checkbox');
          }
        });
      }
    }

    el.appendChild(contentEl);

    // Block action bar (type selector, delete, add child)
    const actions = document.createElement('div');
    actions.className = 'block-actions';
    actions.innerHTML = `
      <button class="block-action-btn block-action-drag" title="Drag">⠿</button>
      <button class="block-action-btn block-action-add" title="Add block below">+</button>
      <button class="block-action-btn block-action-delete" title="Delete">×</button>
    `;
    const addChildBtn = actions.querySelector('.block-action-add');
    if (addChildBtn) {
      addChildBtn.addEventListener('click', () => {
        this._showBlockTypeMenu(addChildBtn, entityType, entityId, block.id);
      });
    }
    const deleteBtn = actions.querySelector('.block-action-delete');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        try {
          await api.del(`/api/blocks/${block.id}`);
          el.remove();
        } catch (e) {
          toast.error('Error', 'Failed to delete block');
        }
      });
    }
    el.insertBefore(actions, contentEl);

    // Render children recursively
    if (block.children && block.children.length > 0) {
      const childrenEl = document.createElement('div');
      childrenEl.className = 'block-children';
      block.children.forEach(child => {
        childrenEl.appendChild(this._renderBlock(child, entityType, entityId, depth + 1));
      });
      el.appendChild(childrenEl);
    }

    return el;
  },

  _showBlockTypeMenu(anchorEl, entityType, entityId, parentBlockId) {
    // Remove any existing menus
    document.querySelectorAll('.block-type-menu').forEach(m => m.remove());

    const menu = document.createElement('div');
    menu.className = 'block-type-menu';
    this._BLOCK_TYPES.forEach(bt => {
      const item = document.createElement('button');
      item.className = 'block-type-menu-item';
      item.innerHTML = `<span class="block-type-icon">${bt.icon}</span> ${escHtml(bt.label)}`;
      item.addEventListener('click', async () => {
        menu.remove();
        try {
          const created = await api.post(`/api/${entityType}/${entityId}/blocks`, {
            block_type: bt.key,
            content: bt.key === 'divider' ? '' : '',
            parent_block_id: parentBlockId,
            order_index: 0,
          });
          // Re-render the editor to reflect the new block
          const editorEl = anchorEl.closest('.block-editor');
          if (editorEl) {
            const containerId = editorEl.parentElement ? editorEl.parentElement.id : null;
            if (containerId) {
              await this.render(containerId, entityType, entityId);
            }
          }
        } catch (e) {
          toast.error('Error', 'Failed to create block: ' + (e.message || ''));
        }
      });
      menu.appendChild(item);
    });

    // Position menu
    anchorEl.parentElement.appendChild(menu);

    // Close on outside click
    const closeMenu = (e) => {
      if (!menu.contains(e.target) && e.target !== anchorEl) {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      }
    };
    setTimeout(() => document.addEventListener('click', closeMenu), 0);
  },
};


// ─── Service Worker Registration + Offline Queue ─────────────────────────────

(function initServiceWorker() {
  if (!('serviceWorker' in navigator)) return;

  navigator.serviceWorker.register('./static/sw.js', { scope: './' })
    .then(function(reg) { console.log('SW registered:', reg.scope); })
    .catch(function(err) { console.warn('SW registration failed:', err); });

  // Listen for messages from the service worker
  navigator.serviceWorker.addEventListener('message', function(event) {
    var data = event.data;
    if (!data) return;

    if (data.type === 'offline-queue-replay') {
      if (typeof showToast === 'function') {
        showToast('Replaying ' + data.count + ' queued request(s)…', 'info');
      }
    }
    if (data.type === 'offline-queue-conflict') {
      if (typeof showToast === 'function') {
        showToast('Conflict detected for offline request — please review', 'warning');
      }
    }
    if (data.type === 'offline-queue-complete') {
      if (typeof showToast === 'function') {
        var msg = data.replayed + ' request(s) replayed';
        if (data.failed > 0) msg += ', ' + data.failed + ' failed';
        showToast(msg, data.failed > 0 ? 'warning' : 'success');
      }
    }
  });

  // Replay offline queue when coming back online
  window.addEventListener('online', function() {
    if (navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: 'replay-queue' });
    }
    if (typeof showToast === 'function') {
      showToast('Back online — syncing changes…', 'info');
    }
  });

  window.addEventListener('offline', function() {
    if (typeof showToast === 'function') {
      showToast('You are offline — changes will be saved and synced when reconnected', 'warning');
    }
  });
})();
