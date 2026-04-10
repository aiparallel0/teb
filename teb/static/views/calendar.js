/**
 * Calendar View (WP-03)
 * Renders tasks on a monthly calendar grid based on due_date.
 */
const CalendarView = {
  currentDate: new Date(),

  render(tasks, container) {
    container.innerHTML = '';
    const cal = document.createElement('div');
    cal.className = 'calendar-view';

    const year = this.currentDate.getFullYear();
    const month = this.currentDate.getMonth();
    const monthNames = ['January','February','March','April','May','June',
                        'July','August','September','October','November','December'];

    const header = document.createElement('div');
    header.className = 'calendar-header';
    header.innerHTML = `
      <button class="cal-nav" id="cal-prev">\u25C0</button>
      <span class="cal-month-title">${monthNames[month]} ${year}</span>
      <button class="cal-nav" id="cal-next">\u25B6</button>
    `;
    cal.appendChild(header);

    const dayNames = document.createElement('div');
    dayNames.className = 'calendar-day-names';
    ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].forEach(d => {
      const dn = document.createElement('div');
      dn.className = 'calendar-day-name';
      dn.textContent = d;
      dayNames.appendChild(dn);
    });
    cal.appendChild(dayNames);

    const grid = document.createElement('div');
    grid.className = 'calendar-grid';

    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const tasksByDate = {};
    tasks.forEach(t => {
      if (t.due_date) {
        const d = t.due_date.substring(0, 10);
        if (!tasksByDate[d]) tasksByDate[d] = [];
        tasksByDate[d].push(t);
      }
    });

    for (let i = 0; i < firstDay; i++) {
      const empty = document.createElement('div');
      empty.className = 'calendar-cell empty';
      grid.appendChild(empty);
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const cell = document.createElement('div');
      cell.className = 'calendar-cell';
      const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      const dayTasks = tasksByDate[dateStr] || [];
      const today = new Date();
      const isToday = day === today.getDate() && month === today.getMonth() && year === today.getFullYear();

      cell.innerHTML = `
        <div class="calendar-day-number ${isToday ? 'today' : ''}">${day}</div>
        <div class="calendar-day-tasks">
          ${dayTasks.map(t => `<div class="calendar-task status-${t.status}" title="${t.title}">${t.title}</div>`).join('')}
        </div>
      `;
      grid.appendChild(cell);
    }

    cal.appendChild(grid);
    container.appendChild(cal);

    const self = this;
    const prevBtn = container.querySelector('#cal-prev');
    const nextBtn = container.querySelector('#cal-next');
    if (prevBtn) prevBtn.onclick = () => {
      self.currentDate = new Date(year, month - 1, 1);
      self.render(tasks, container);
    };
    if (nextBtn) nextBtn.onclick = () => {
      self.currentDate = new Date(year, month + 1, 1);
      self.render(tasks, container);
    };
  }
};

if (typeof module !== 'undefined') module.exports = CalendarView;
