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
  showScreen('screen-tasks');
}

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
    container.appendChild(buildTaskCard(task, subtasks));
  });
}

function buildTaskCard(task, subtasks) {
  const card = document.createElement('div');
  card.className = `task-card${task.status === 'done' ? ' done-card' : ''}`;
  card.dataset.id = task.id;

  const cbClass = task.status === 'done' ? 'checked' : '';

  card.innerHTML = `
    <div class="task-header">
      <div class="task-checkbox ${cbClass}" data-id="${task.id}" title="Mark done"></div>
      <div class="task-info">
        <div class="task-title">${escHtml(task.title)}</div>
        <div class="task-meta">~${task.estimated_minutes} min${subtasks.length ? ` · ${subtasks.length} sub-tasks` : ''}</div>
      </div>
      <button class="task-expand-btn" aria-label="expand">▾</button>
    </div>
    <div class="task-body" style="display:none">
      <p class="task-desc">${escHtml(task.description)}</p>
      <select class="task-status-select" data-id="${task.id}">
        <option value="todo"${task.status === 'todo' ? ' selected' : ''}>To do</option>
        <option value="in_progress"${task.status === 'in_progress' ? ' selected' : ''}>In progress</option>
        <option value="done"${task.status === 'done' ? ' selected' : ''}>Done</option>
        <option value="skipped"${task.status === 'skipped' ? ' selected' : ''}>Skip</option>
      </select>
      ${subtasks.length ? buildSubtaskList(subtasks) : ''}
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

  // Sub-task checkboxes
  card.querySelectorAll('.subtask-cb').forEach(cb => {
    cb.addEventListener('click', () => {
      const tid = parseInt(cb.dataset.id, 10);
      const sub = currentTasks.find(t => t.id === tid);
      if (sub) toggleTaskDone(sub);
    });
  });

  return card;
}

function buildSubtaskList(subtasks) {
  const items = subtasks.map(s => `
    <div class="subtask-item">
      <div class="subtask-cb ${s.status === 'done' ? 'checked' : ''}" data-id="${s.id}"></div>
      <div>
        <div class="subtask-title">${escHtml(s.title)}</div>
        <div class="subtask-meta">~${s.estimated_minutes} min</div>
      </div>
    </div>
  `).join('');
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
    // Refresh goal
    const goal = await api.get(`/api/goals/${currentGoalId}`);
    currentTasks = goal.tasks || [];
    renderTasks(currentTasks);
    updateProgress(currentTasks);
  } catch (e) {
    showError('error-tasks', e.message);
  }
}

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
