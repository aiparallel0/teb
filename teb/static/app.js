/* app.js — teb frontend */

const api = {
  async post(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async patch(url, body) {
    const r = await fetch(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: 'DELETE' });
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

// ─── Screen management ────────────────────────────────────────────────────────

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

function showError(elId, msg) {
  const el = document.getElementById(elId);
  if (el) el.textContent = msg || '';
}

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
      // Resume clarifying or start fresh
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
  loadFocusTask();
  loadProgressDetail();
  showScreen('screen-tasks');
}

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

// ─── Helpers ──────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Init ─────────────────────────────────────────────────────────────────────

showScreen('screen-landing');
loadGoalList();
