/* app.js — teb frontend */

// ─── Auth-aware API wrapper ──────────────────────────────────────────────────

function authHeaders() {
  const token = localStorage.getItem('teb_token');
  const h = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

const api = {
  async post(url, body) {
    const r = await fetch(url, {
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
    const r = await fetch(url, { headers: authHeaders() });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async patch(url, body) {
    const r = await fetch(url, {
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
    const r = await fetch(url, { method: 'DELETE', headers: authHeaders() });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
};

// ─── State ────────────────────────────────────────────────────────────────────

let currentGoalId = null;
let currentTasks = [];
let dripMode = true; // default to drip mode
let authMode = 'login'; // 'login' or 'register'

// ─── Screen management ────────────────────────────────────────────────────────

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

function showError(elId, msg) {
  const el = document.getElementById(elId);
  if (el) el.textContent = msg || '';
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
});

document.getElementById('auth-password').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-auth-submit').click();
});

// ─── Landing screen ───────────────────────────────────────────────────────────

async function loadGoalList() {
  try {
    const goals = await api.get('/api/goals');
    const ul = document.getElementById('goal-list');
    ul.innerHTML = '';
    if (!goals.length) {
      ul.innerHTML = '<li style="color:var(--muted);font-size:.875rem">No previous goals yet.</li>';
      return;
    }
    goals.forEach(g => {
      const li = document.createElement('li');
      li.className = 'goal-item';
      li.innerHTML = `
        <span class="goal-item-title">${escHtml(g.title)}</span>
        <span class="goal-item-status status-${g.status}">${g.status}</span>
      `;
      li.addEventListener('click', () => openGoal(g.id));
      ul.appendChild(li);
    });
  } catch (e) {
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
  const btn = document.getElementById('btn-clarify-next');
  btn.disabled = true;
  try {
    const result = await api.post(`/api/goals/${goalId}/decompose`, {});
    const goal = await api.get(`/api/goals/${goalId}`);
    showTasksScreen(goal);
  } catch (e) {
    showError('error-clarify', e.message);
  } finally {
    btn.disabled = false;
  }
}

// ─── Tasks screen ─────────────────────────────────────────────────────────────

function showTasksScreen(goal) {
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
  showScreen('screen-tasks');
  // Default to drip mode
  setDripMode(true);
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
  if (tid) await patchTaskStatus(tid, 'done');
});

document.getElementById('btn-focus-start').addEventListener('click', async () => {
  const banner = document.getElementById('focus-banner');
  const tid = parseInt(banner.dataset.taskId, 10);
  if (tid) await patchTaskStatus(tid, 'in_progress');
});

function updateProgress(tasks) {
  const topLevel = tasks.filter(t => t.parent_id === null);
  if (!topLevel.length) { setProgress(0); return; }
  const done = topLevel.filter(t => t.status === 'done' || t.status === 'skipped').length;
  setProgress(Math.round((done / topLevel.length) * 100));
}

function setProgress(pct) {
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-label').textContent = pct + '% complete';
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
  try {
    const result = await api.post(`/api/goals/${currentGoalId}/decompose`, {});
    const goal = await api.get(`/api/goals/${currentGoalId}`);
    showTasksScreen(goal);
  } catch (e) {
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
  } catch (e) {
    showError('error-tasks', e.message);
  }
});

// ─── Settings Modal ───────────────────────────────────────────────────────────

document.getElementById('btn-settings').addEventListener('click', () => {
  document.getElementById('settings-modal').style.display = 'flex';
  loadExistingConfigs();
});

document.getElementById('btn-close-settings').addEventListener('click', () => {
  document.getElementById('settings-modal').style.display = 'none';
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
    showError('error-tg', '✅ Test message sent!');
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
    showError('error-wh', '✅ Test message sent!');
  } catch (e) {
    showError('error-wh', e.message);
  }
});

async function loadExistingConfigs() {
  try {
    const configs = await api.get('/api/messaging/configs');
    const container = document.getElementById('existing-configs');
    if (!configs.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:.8rem">No messaging configs yet.</p>';
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

// ─── Helpers ──────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Init ─────────────────────────────────────────────────────────────────────

function init() {
  const token = localStorage.getItem('teb_token');
  updateUserBar();
  if (token) {
    showScreen('screen-landing');
    loadGoalList();
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
      container.innerHTML = '<p style="color:var(--muted);font-size:.8rem">No check-ins yet.</p>';
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
      container.innerHTML = '<p style="color:var(--muted);font-size:.8rem">No outcome metrics yet. Add one to track real results.</p>';
      return;
    }
    container.innerHTML = metrics.map(m => `
      <div class="outcome-metric-card">
        <div class="outcome-metric-info">
          <div class="outcome-metric-label">${escHtml(m.label)}</div>
          <div class="outcome-metric-values">${m.current_value}${m.unit ? ' ' + escHtml(m.unit) : ''} / ${m.target_value}${m.unit ? ' ' + escHtml(m.unit) : ''}</div>
        </div>
        <div class="outcome-metric-bar"><div class="outcome-metric-bar-fill" style="width:${m.achievement_pct}%"></div></div>
        <div class="outcome-metric-pct">${m.achievement_pct}%</div>
        <button class="outcome-metric-update" data-id="${m.id}" data-current="${m.current_value}">Update</button>
      </div>
    `).join('');
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
