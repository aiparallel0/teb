/* app.js — teb frontend */

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
  document.getElementById(id).classList.add('active');
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
  if (token && email) {
    document.getElementById('user-email').textContent = email;
    bar.style.display = 'flex';
  } else {
    bar.style.display = 'none';
  }
}

document.getElementById('btn-auth-submit').addEventListener('click', async () => {
  const email = document.getElementById('auth-email').value.trim();
  const password = document.getElementById('auth-password').value;
  showError('error-auth', '');
  if (!email || !password) { showError('error-auth', 'Please enter email and password.'); return; }

  const btn = document.getElementById('btn-auth-submit');
  btn.disabled = true;
  try {
    const endpoint = authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const res = await api.post(endpoint, { email, password });
    localStorage.setItem('teb_token', res.token);
    localStorage.setItem('teb_email', res.user.email);
    updateUserBar();
    showScreen('screen-landing');
    loadGoalList();
    toast.success('Welcome!', authMode === 'register' ? 'Account created successfully.' : 'Signed in.');
  } catch (e) {
    showError('error-auth', e.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('auth-toggle-link').addEventListener('click', (e) => {
  e.preventDefault();
  authMode = authMode === 'login' ? 'register' : 'login';
  document.getElementById('auth-title').textContent = authMode === 'register' ? 'Create account' : 'Sign in';
  document.getElementById('btn-auth-submit').textContent = authMode === 'register' ? 'Register' : 'Sign in';
  document.getElementById('auth-toggle-text').textContent =
    authMode === 'register' ? 'Already have an account?' : "Don't have an account?";
  document.getElementById('auth-toggle-link').textContent =
    authMode === 'register' ? 'Sign in' : 'Register';
  showError('error-auth', '');
});

document.getElementById('auth-skip-link').addEventListener('click', (e) => {
  e.preventDefault();
  localStorage.removeItem('teb_token');
  localStorage.removeItem('teb_email');
  updateUserBar();
  showScreen('screen-landing');
  loadGoalList();
});

document.getElementById('btn-logout').addEventListener('click', () => {
  localStorage.removeItem('teb_token');
  localStorage.removeItem('teb_email');
  updateUserBar();
  showScreen('screen-auth');
  toast.info('Signed out', 'You have been logged out.');
});

document.getElementById('auth-password').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-auth-submit').click();
});

// ─── Landing screen ───────────────────────────────────────────────────────────

async function loadGoalList() {
  const ul = document.getElementById('goal-list');
  showSkeleton(ul, 3, 'card');
  try {
    const goals = await api.get('/api/goals');
    ul.innerHTML = '';
    if (!goals.length) {
      ul.innerHTML = `
        <li class="empty-state">
          <div class="empty-state-icon">🎯</div>
          <div class="empty-state-title">No goals yet</div>
          <div class="empty-state-desc">Enter your first goal above and we'll break it down into actionable tasks.</div>
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
      showTasksScreen(goal);
    } else {
      await startClarifyFlow(goal);
    }
  } catch (e) {
    showError('error-landing', e.message);
  }
}

document.getElementById('btn-create-goal').addEventListener('click', async () => {
  const title = document.getElementById('goal-title').value.trim();
  const desc = document.getElementById('goal-desc').value.trim();
  showError('error-landing', '');
  if (!title) { showError('error-landing', 'Please enter a goal.'); return; }

  const btn = document.getElementById('btn-create-goal');
  btn.disabled = true;
  btn.innerHTML = 'Working… <span class="spinner"></span>';

  try {
    const goal = await api.post('/api/goals', { title, description: desc });
    currentGoalId = goal.id;
    await startClarifyFlow(goal);
  } catch (e) {
    showError('error-landing', e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Get my action plan →';
  }
});

// Enter key on goal title input
document.getElementById('goal-title').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-create-goal').click();
});

// ─── Clarify screen ───────────────────────────────────────────────────────────

async function startClarifyFlow(goal) {
  document.getElementById('clarify-goal-title').textContent = goal.title;
  const q = await api.get(`/api/goals/${goal.id}/next_question`);
  if (q.done) {
    await triggerDecompose(goal.id);
    return;
  }
  showQuestion(q.question);
  showScreen('screen-clarify');
}

function showQuestion(q) {
  document.getElementById('clarify-question-text').textContent = q.text;
  document.getElementById('clarify-answer').placeholder = q.hint || '';
  document.getElementById('clarify-answer').value = '';
  document.getElementById('clarify-answer').dataset.key = q.key;
  document.getElementById('clarify-answer').focus();
}

document.getElementById('btn-clarify-next').addEventListener('click', submitClarifyAnswer);
document.getElementById('clarify-answer').addEventListener('keydown', e => {
  if (e.key === 'Enter') submitClarifyAnswer();
});

async function submitClarifyAnswer() {
  const input = document.getElementById('clarify-answer');
  const answer = input.value.trim();
  const key = input.dataset.key;
  showError('error-clarify', '');
  if (!answer) { showError('error-clarify', 'Please enter an answer (or click "Skip").'); return; }

  const btn = document.getElementById('btn-clarify-next');
  btn.disabled = true;
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
    btn.disabled = false;
  }
}

document.getElementById('btn-skip-clarify').addEventListener('click', async () => {
  await triggerDecompose(currentGoalId);
});

document.getElementById('back-from-clarify').addEventListener('click', () => {
  showScreen('screen-landing');
  loadGoalList();
});

// ─── Decompose ────────────────────────────────────────────────────────────────

async function triggerDecompose(goalId) {
  showLoading('Building your action plan…');
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
  currentGoalTitle = goal.title;
  document.getElementById('tasks-goal-title').textContent = goal.title;
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
  showScreen('screen-tasks');
  // Default to drip mode
  setDripMode(true);

  // After a fresh decompose, auto-fetch outcome suggestions
  if (freshDecompose) {
    await autoSuggestOutcomes(goal.id);
  }

  // Load proactive suggestions and service discovery in background
  loadProactiveSuggestions();
}

// ─── Drip Mode ────────────────────────────────────────────────────────────────

function setDripMode(on) {
  dripMode = on;
  document.getElementById('drip-section').style.display = on ? 'block' : 'none';
  document.getElementById('all-tasks-section').style.display = on ? 'none' : 'block';
  document.getElementById('btn-toggle-view').textContent = on ? 'Show all tasks' : 'Switch to drip mode';
  if (on) loadDrip();
}

document.getElementById('btn-toggle-view').addEventListener('click', () => {
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
      card.style.display = 'none';
      doneMsg.style.display = 'block';
      msg.textContent = res.message || '';
      return;
    }

    doneMsg.style.display = 'none';
    card.style.display = 'block';
    card.dataset.taskId = res.task.id;
    document.getElementById('drip-title').textContent = res.task.title;
    document.getElementById('drip-desc').textContent = res.task.description;
    document.getElementById('drip-meta').textContent = `~${res.task.estimated_minutes} min`;
    msg.textContent = res.message || '';

    // Skip suggestion (P2.2)
    const skipSug = document.getElementById('drip-skip-suggestion');
    if (res.skip_suggestion) {
      skipSug.textContent = res.skip_suggestion;
      skipSug.style.display = 'block';
    } else {
      skipSug.style.display = 'none';
    }

    // Stall detection (P2.3)
    const stallMsg = document.getElementById('drip-stall-msg');
    if (res.stall_detected) {
      stallMsg.textContent = res.message;
      if (res.sub_task_suggestion) {
        stallMsg.textContent += ` Suggested mini-task: "${res.sub_task_suggestion.title}"`;
      }
      stallMsg.style.display = 'block';
    } else {
      stallMsg.style.display = 'none';
    }

    // Adaptive question
    const aqSection = document.getElementById('drip-adaptive-question');
    if (res.adaptive_question) {
      document.getElementById('drip-q-text').textContent = res.adaptive_question.text;
      document.getElementById('drip-q-answer').placeholder = res.adaptive_question.hint || '';
      document.getElementById('drip-q-answer').value = '';
      document.getElementById('drip-q-answer').dataset.key = res.adaptive_question.key;
      aqSection.style.display = 'block';
    } else {
      aqSection.style.display = 'none';
    }
  } catch (e) {
    document.getElementById('drip-message').textContent = 'Could not load next task.';
  }
}

document.getElementById('btn-drip-done').addEventListener('click', async () => {
  const card = document.getElementById('drip-card');
  const tid = parseInt(card.dataset.taskId, 10);
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

document.getElementById('btn-drip-skip').addEventListener('click', async () => {
  const card = document.getElementById('drip-card');
  const tid = parseInt(card.dataset.taskId, 10);
  if (!tid) return;
  try {
    await api.patch(`/api/tasks/${tid}`, { status: 'skipped' });
    await refreshGoalView();
    loadDrip();
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

document.getElementById('btn-drip-q-submit').addEventListener('click', async () => {
  const input = document.getElementById('drip-q-answer');
  const answer = input.value.trim();
  const key = input.dataset.key;
  if (!answer || !key) return;
  try {
    await api.post(`/api/goals/${currentGoalId}/drip/clarify`, { key, answer });
    document.getElementById('drip-adaptive-question').style.display = 'none';
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
  card.className = `task-card${task.status === 'done' ? ' done-card' : ''}`;
  card.dataset.id = task.id;

  const cbClass = task.status === 'done' ? 'checked' : '';
  const hasSubtasks = subtasks.length > 0;
  const canDecompose = !hasSubtasks && task.status !== 'done' && depth < MAX_DECOMPOSE_DEPTH;

  card.innerHTML = `
    <div class="task-header">
      <div class="task-checkbox ${cbClass}" data-id="${task.id}" title="Mark done"></div>
      <div class="task-info">
        <div class="task-title">${escHtml(task.title)}</div>
        <div class="task-meta">~${task.estimated_minutes} min${hasSubtasks ? ` · ${subtasks.length} sub-tasks` : ''}</div>
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

  // Checkbox click → toggle done
  card.querySelector('.task-checkbox').addEventListener('click', () => toggleTaskDone(task));

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
  if (!currentGoalId) { banner.style.display = 'none'; return; }
  try {
    const res = await api.get(`/api/goals/${currentGoalId}/focus`);
    if (!res.focus_task) {
      banner.style.display = 'none';
      return;
    }
    const t = res.focus_task;
    document.getElementById('focus-title').textContent = t.title;
    document.getElementById('focus-desc').textContent = t.description;
    document.getElementById('focus-meta').textContent = `~${t.estimated_minutes} min`;
    banner.style.display = 'block';
    banner.dataset.taskId = t.id;
  } catch (e) {
    banner.style.display = 'none';
  }
}

async function loadProgressDetail() {
  const el = document.getElementById('progress-detail');
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

document.getElementById('btn-focus-done').addEventListener('click', async () => {
  const banner = document.getElementById('focus-banner');
  const tid = parseInt(banner.dataset.taskId, 10);
  if (tid) {
    await patchTaskStatus(tid, 'done');
    toast.success('Done!', 'Task marked as completed.');
  }
});

document.getElementById('btn-focus-start').addEventListener('click', async () => {
  const banner = document.getElementById('focus-banner');
  const tid = parseInt(banner.dataset.taskId, 10);
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
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-label').textContent = pct + '% complete';
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

document.getElementById('back-from-tasks').addEventListener('click', () => {
  showScreen('screen-landing');
  loadGoalList();
});

document.getElementById('btn-redecompose').addEventListener('click', async () => {
  if (!currentGoalId) return;
  showError('error-tasks', '');
  const btn = document.getElementById('btn-redecompose');
  btn.disabled = true;
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
    btn.disabled = false;
  }
});

document.getElementById('btn-add-task').addEventListener('click', async () => {
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

document.getElementById('autopilot-toggle').addEventListener('change', async (e) => {
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
      document.getElementById('budget-prompt').style.display = 'none';
    }
  } catch (e) {
    e.target.checked = !enabled;
    showError('error-tasks', e.message);
  }
});

async function checkBudgetPrompt() {
  try {
    const budgets = await api.get(`/api/goals/${currentGoalId}/budgets`);
    if (!budgets || !budgets.length) {
      document.getElementById('budget-prompt').style.display = 'block';
    } else {
      document.getElementById('budget-prompt').style.display = 'none';
    }
  } catch (e) {
    // Show prompt if we can't determine
    document.getElementById('budget-prompt').style.display = 'block';
  }
}

document.getElementById('btn-set-budget').addEventListener('click', async () => {
  const daily = parseFloat(document.getElementById('budget-daily').value) || 50;
  const total = parseFloat(document.getElementById('budget-total').value) || 500;
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
    document.getElementById('budget-prompt').style.display = 'none';
    toast.success('Budget set', `Daily: $${daily}, Total: $${total}`);
  } catch (e) {
    showError('error-budget', e.message);
  }
});

// ─── AI Orchestrate ───────────────────────────────────────────────────────────

document.getElementById('btn-orchestrate').addEventListener('click', async () => {
  const btn = document.getElementById('btn-orchestrate');
  const panel = document.getElementById('agent-activity-panel');
  const content = document.getElementById('agent-activity-content');

  btn.disabled = true;
  btn.textContent = '🤖 Orchestrating…';
  panel.style.display = 'block';
  content.innerHTML = '<p style="color:var(--muted)">Dispatching agents…</p>';

  try {
    const result = await api.post(`/api/goals/${currentGoalId}/orchestrate`, {});
    const handoffs = result.handoffs || [];
    const messages = result.messages || [];

    let html = '';
    if (handoffs.length) {
      html += '<div class="agent-handoffs"><strong>Agent chain:</strong> ';
      html += handoffs.map(h =>
        `<span class="agent-tag">${escHtml(h.from_agent || '')} → ${escHtml(h.to_agent || '')}</span>`
      ).join(' ');
      html += '</div>';
    }
    if (messages.length) {
      html += '<div class="agent-messages">';
      messages.slice(0, 10).forEach(m => {
        html += `<div class="agent-msg"><span class="agent-msg-from">${escHtml(m.from_agent || 'agent')}</span>: ${escHtml(m.content || '')}</div>`;
      });
      html += '</div>';
    }
    if (!html) html = '<p>Orchestration complete. Check back for task updates.</p>';
    content.innerHTML = html;
    panel.open = true;
    // Refresh tasks after orchestration
    await refreshGoalView();
    toast.success('Orchestration complete', `${handoffs.length} agent handoffs.`);
  } catch (e) {
    content.innerHTML = `<p class="error">${escHtml(e.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🤖 AI Orchestrate';
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

document.getElementById('btn-add-all-outcomes').addEventListener('click', async () => {
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
    banner.style.display = 'none';
    loadOutcomeMetrics();
    toast.success('Metrics added', 'Outcome metrics are now being tracked.');
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

document.getElementById('btn-skip-outcomes').addEventListener('click', () => {
  _pendingOutcomeSuggestions = null;
  document.getElementById('outcome-suggestions-banner').style.display = 'none';
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
        <div class="suggestion-text">${escHtml(s.content || s.message || '')}</div>
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

document.getElementById('btn-discover').addEventListener('click', async () => {
  const btn = document.getElementById('btn-discover');
  const container = document.getElementById('discovery-list');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  try {
    const params = currentGoalTitle ? `?goal_title=${encodeURIComponent(currentGoalTitle)}` : '';
    const res = await api.get(`/api/discover/services${params}`);
    const services = res.services || res || [];
    if (!services.length) {
      container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-md)">
          <div class="empty-state-icon">🔍</div>
          <div class="empty-state-desc">No matching services found.</div>
        </div>`;
    } else {
      container.innerHTML = services.slice(0, 8).map(s => `
        <div class="discovery-item">
          <div class="discovery-name">${escHtml(s.name || s.service_name || '')}</div>
          <div class="discovery-desc">${escHtml(s.description || '')}</div>
          ${s.website ? `<a href="${escHtml(s.website)}" target="_blank" rel="noopener" class="discovery-link">Visit →</a>` : ''}
        </div>
      `).join('');
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escHtml(e.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find matching services';
  }
});

// ─── Settings Modal ───────────────────────────────────────────────────────────

document.getElementById('btn-settings').addEventListener('click', () => {
  document.getElementById('settings-modal').style.display = 'flex';
  loadExistingConfigs();
  loadCredentials();
});

document.getElementById('btn-close-settings').addEventListener('click', () => {
  document.getElementById('settings-modal').style.display = 'none';
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

document.getElementById('btn-tg-save').addEventListener('click', async () => {
  const token = document.getElementById('tg-bot-token').value.trim();
  const chatId = document.getElementById('tg-chat-id').value.trim();
  showError('error-tg', '');
  if (!token || !chatId) { showError('error-tg', 'Both bot token and chat ID are required.'); return; }
  try {
    await api.post('/api/messaging/config', {
      channel: 'telegram',
      config: { bot_token: token, chat_id: chatId },
      notify_nudges: document.getElementById('notif-nudges').checked,
      notify_tasks: document.getElementById('notif-tasks').checked,
      notify_spending: document.getElementById('notif-spending').checked,
      notify_checkins: document.getElementById('notif-checkins').checked,
    });
    showError('error-tg', '');
    loadExistingConfigs();
    toast.success('Saved', 'Telegram config updated.');
  } catch (e) {
    showError('error-tg', e.message);
  }
});

document.getElementById('btn-tg-test').addEventListener('click', async () => {
  showError('error-tg', '');
  // Save first, then test
  const token = document.getElementById('tg-bot-token').value.trim();
  const chatId = document.getElementById('tg-chat-id').value.trim();
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

document.getElementById('btn-wh-save').addEventListener('click', async () => {
  const url = document.getElementById('wh-url').value.trim();
  showError('error-wh', '');
  if (!url) { showError('error-wh', 'URL is required.'); return; }
  try {
    await api.post('/api/messaging/config', {
      channel: 'webhook',
      config: { url },
      notify_nudges: document.getElementById('notif-nudges').checked,
      notify_tasks: document.getElementById('notif-tasks').checked,
      notify_spending: document.getElementById('notif-spending').checked,
      notify_checkins: document.getElementById('notif-checkins').checked,
    });
    showError('error-wh', '');
    loadExistingConfigs();
    toast.success('Saved', 'Webhook config updated.');
  } catch (e) {
    showError('error-wh', e.message);
  }
});

document.getElementById('btn-wh-test').addEventListener('click', async () => {
  showError('error-wh', '');
  const url = document.getElementById('wh-url').value.trim();
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

document.getElementById('btn-add-credential').addEventListener('click', async () => {
  const name = document.getElementById('cred-name').value.trim();
  const baseUrl = document.getElementById('cred-base-url').value.trim();
  const authHeader = document.getElementById('cred-auth-header').value.trim() || 'Authorization';
  const authValue = document.getElementById('cred-auth-value').value.trim();
  const desc = document.getElementById('cred-desc').value.trim();
  showError('error-credential', '');
  if (!name || !baseUrl) { showError('error-credential', 'Name and base URL are required.'); return; }
  try {
    await api.post('/api/credentials', {
      name, base_url: baseUrl, auth_header: authHeader, auth_value: authValue, description: desc,
    });
    document.getElementById('cred-name').value = '';
    document.getElementById('cred-base-url').value = '';
    document.getElementById('cred-auth-value').value = '';
    document.getElementById('cred-desc').value = '';
    loadCredentials();
    toast.success('Added', 'Credential stored securely.');
  } catch (e) {
    showError('error-credential', e.message);
  }
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ─── Keyboard shortcuts ──────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  // Escape to close modals
  if (e.key === 'Escape') {
    const settingsModal = document.getElementById('settings-modal');
    const adminModal = document.getElementById('admin-modal');
    if (settingsModal.style.display !== 'none') {
      settingsModal.style.display = 'none';
    } else if (adminModal.style.display !== 'none') {
      adminModal.style.display = 'none';
    }
  }
});

// Click outside modal to close
document.getElementById('settings-modal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.style.display = 'none';
  }
});
document.getElementById('admin-modal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.style.display = 'none';
  }
});

// ─── Init ─────────────────────────────────────────────────────────────────────

function init() {
  initTheme();
  setupCharCounter('goal-desc', 'goal-desc-counter');

  // Dark mode toggle
  const themeBtn = document.getElementById('btn-theme-toggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  const token = localStorage.getItem('teb_token');
  updateUserBar();
  if (token) {
    showScreen('screen-landing');
    loadGoalList();
    // Check if logged-in user is admin and show Admin button accordingly
    api.get('/api/auth/me').then(me => {
      const adminBtn = document.getElementById('btn-admin');
      if (adminBtn) adminBtn.style.display = me && me.role === 'admin' ? '' : 'none';
    }).catch(() => {});
  } else {
    showScreen('screen-auth');
  }
}

init();

// ─── Daily Check-in ───────────────────────────────────────────────────────────

document.getElementById('btn-checkin').addEventListener('click', submitCheckin);

async function submitCheckin() {
  const done = document.getElementById('checkin-done').value.trim();
  const blockers = document.getElementById('checkin-blockers').value.trim();
  if (!done && !blockers) return;

  const btn = document.getElementById('btn-checkin');
  btn.disabled = true;
  try {
    const res = await api.post(`/api/goals/${currentGoalId}/checkin`, {
      done_summary: done,
      blockers: blockers,
    });
    // Show coaching feedback
    const fb = document.getElementById('checkin-feedback');
    fb.textContent = res.coaching;
    fb.style.display = 'block';
    // Clear inputs
    document.getElementById('checkin-done').value = '';
    document.getElementById('checkin-blockers').value = '';
    // Refresh history and nudge
    loadCheckinHistory();
    loadNudge();
    toast.success('Check-in submitted', 'Keep up the momentum!');
  } catch (e) {
    showError('error-tasks', e.message);
  } finally {
    btn.disabled = false;
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
    if (res.nudge) {
      document.getElementById('nudge-message').textContent = res.nudge.message;
      banner.style.display = 'flex';
      banner.dataset.nudgeId = res.nudge.id;
    } else {
      banner.style.display = 'none';
    }
  } catch (e) {
    // Silent fail
  }
}

document.getElementById('btn-nudge-ack').addEventListener('click', async () => {
  const banner = document.getElementById('nudge-banner');
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

document.getElementById('btn-suggest-outcomes').addEventListener('click', async () => {
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

document.getElementById('btn-add-outcome').addEventListener('click', async () => {
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

// ─── Admin Panel ──────────────────────────────────────────────────────────────

document.getElementById('btn-admin').addEventListener('click', () => {
  document.getElementById('admin-modal').style.display = 'flex';
  loadAdminStats();
  loadAdminUsers();
  loadAdminIntegrations();
});

document.getElementById('btn-close-admin').addEventListener('click', () => {
  document.getElementById('admin-modal').style.display = 'none';
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

document.getElementById('btn-admin-add-integration').addEventListener('click', async () => {
  const serviceName = document.getElementById('ai-service-name').value.trim();
  if (!serviceName) { showError('admin-integrations-error', 'Service name is required.'); return; }
  const category = document.getElementById('ai-category').value.trim();
  const baseUrl = document.getElementById('ai-base-url').value.trim();
  const authType = document.getElementById('ai-auth-type').value;
  const authHeader = document.getElementById('ai-auth-header').value.trim() || 'Authorization';
  const docsUrl = document.getElementById('ai-docs-url').value.trim();
  const capsRaw = document.getElementById('ai-capabilities').value.trim();
  const capabilities = capsRaw ? capsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  let commonEndpoints = [];
  try {
    const ep = document.getElementById('ai-endpoints').value.trim();
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

// ─── Keyboard navigation for task list ────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  // Ctrl+Enter or Cmd+Enter to submit forms
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    const activeScreen = document.querySelector('.screen.active');
    if (!activeScreen) return;

    if (activeScreen.id === 'screen-landing') {
      document.getElementById('btn-create-goal').click();
      e.preventDefault();
    } else if (activeScreen.id === 'screen-clarify') {
      document.getElementById('btn-clarify-next').click();
      e.preventDefault();
    } else if (activeScreen.id === 'screen-tasks') {
      // Check-in submit if focused in check-in area
      const active = document.activeElement;
      if (active && (active.id === 'checkin-done' || active.id === 'checkin-blockers')) {
        document.getElementById('btn-checkin').click();
        e.preventDefault();
      }
    }
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
        showScreen('screen-auth');
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
