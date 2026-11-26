(() => {
  const TODO_KEY = "xingyunshe-todo-v1";
  const REMINDER_KEY = "xingyunshe-todo-reminded-date";
  const NINE_AM = 9;

  function todayKey(value = Date.now()) {
    const date = new Date(value);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function startOfDay(value = Date.now()) {
    const date = new Date(value);
    date.setHours(0, 0, 0, 0);
    return date.getTime();
  }

  function endOfDay(value = Date.now()) {
    const date = new Date(value);
    date.setHours(23, 59, 59, 999);
    return date.getTime();
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[char]);
  }

  function readTodoPayload() {
    try {
      const payload = JSON.parse(localStorage.getItem(TODO_KEY) || "{}");
      if (!Array.isArray(payload.tasks)) return { tasks: [], projects: [] };
      return {
        tasks: payload.tasks,
        projects: Array.isArray(payload.projects) ? payload.projects : []
      };
    } catch {
      return { tasks: [], projects: [] };
    }
  }

  function projectName(projects, projectId) {
    return projects.find((project) => project.id === projectId)?.name || "未分组";
  }

  function priorityLabel(priority) {
    if (priority === "high") return "高优先";
    if (priority === "low") return "低优先";
    return "常规";
  }

  function timeLabel(value) {
    if (!value) return "";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    }).format(new Date(value));
  }

  function dueTasksForToday() {
    const { tasks, projects } = readTodoPayload();
    const todayEnd = endOfDay();
    return tasks
      .filter((task) => task && !task.done && task.dueAt && Number(task.dueAt) <= todayEnd)
      .sort((a, b) => Number(a.dueAt || 0) - Number(b.dueAt || 0))
      .map((task) => ({
        ...task,
        projectName: projectName(projects, task.projectId),
        isOverdue: Number(task.dueAt) < startOfDay()
      }));
  }

  function hasRemindedToday() {
    return localStorage.getItem(REMINDER_KEY) === todayKey();
  }

  function markRemindedToday() {
    localStorage.setItem(REMINDER_KEY, todayKey());
  }

  function closeReminder() {
    const node = document.querySelector(".todo-reminder-popover");
    if (!node) return;
    node.classList.add("is-leaving");
    window.setTimeout(() => node.remove(), 220);
  }

  function renderReminder(tasks, reason) {
    document.querySelector(".todo-reminder-popover")?.remove();
    const count = tasks.length;
    const overdueCount = tasks.filter((task) => task.isOverdue).length;
    const visibleTasks = tasks.slice(0, 6);
    const node = document.createElement("section");
    node.className = "todo-reminder-popover";
    node.setAttribute("role", "dialog");
    node.setAttribute("aria-live", "polite");
    node.setAttribute("aria-label", "今日 Todo 提醒");
    node.innerHTML = `
      <div class="todo-reminder-card">
        <header class="todo-reminder-head">
          <div>
            <p>${reason === "schedule" ? "09:00 TODO REMINDER" : "STARTUP TODO REMINDER"}</p>
            <h2>今天需要处理 ${count} 件事</h2>
          </div>
          <button type="button" data-todo-reminder-close aria-label="关闭">×</button>
        </header>
        <div class="todo-reminder-summary">
          <span><b>${count}</b> 今日事项</span>
          <span><b>${overdueCount}</b> 逾期未完</span>
          <span><b>${todayKey().slice(5)}</b> 今日</span>
        </div>
        <div class="todo-reminder-list">
          ${visibleTasks.map((task) => `
            <article class="${task.isOverdue ? "is-overdue" : ""}">
              <i>${task.isOverdue ? "逾期" : "今日"}</i>
              <div>
                <strong>${escapeHtml(task.title || "未命名任务")}</strong>
                <span>${escapeHtml(task.projectName)} · ${escapeHtml(timeLabel(task.dueAt))} · ${escapeHtml(priorityLabel(task.priority))}</span>
              </div>
            </article>
          `).join("")}
        </div>
        ${tasks.length > visibleTasks.length ? `<p class="todo-reminder-more">还有 ${tasks.length - visibleTasks.length} 项，进入 TodoList 查看完整清单。</p>` : ""}
        <footer class="todo-reminder-actions">
          <a href="./todo.html">打开 TodoList</a>
          <button type="button" data-todo-reminder-close>今天不再提醒</button>
        </footer>
      </div>
    `;
    node.addEventListener("click", (event) => {
      if (event.target.closest("[data-todo-reminder-close]")) closeReminder();
    });
    document.body.appendChild(node);
    window.requestAnimationFrame(() => node.classList.add("is-visible"));
  }

  function showReminderOnce(reason = "startup") {
    if (hasRemindedToday()) return;
    const tasks = dueTasksForToday();
    if (!tasks.length) return;
    markRemindedToday();
    renderReminder(tasks, reason);
  }

  function msUntilNextNine() {
    const now = new Date();
    const target = new Date(now);
    target.setHours(NINE_AM, 0, 0, 0);
    if (target.getTime() <= now.getTime()) {
      target.setDate(target.getDate() + 1);
    }
    return target.getTime() - now.getTime();
  }

  function scheduleNineReminder() {
    window.setTimeout(() => {
      showReminderOnce("schedule");
      scheduleNineReminder();
    }, msUntilNextNine());
  }

  window.addEventListener("storage", (event) => {
    if (event.key === TODO_KEY && !hasRemindedToday()) {
      showReminderOnce("startup");
    }
  });

  window.addEventListener("load", () => {
    window.setTimeout(() => showReminderOnce("startup"), 350);
    scheduleNineReminder();
  });
})();
