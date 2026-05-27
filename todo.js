const TODO_STORAGE_KEY = "xingyunshe-todo-v1";
const TODO_API = "/api/todo-state";
let todoSaveTimer = 0;

const todoState = {
  projects: [],
  tasks: [],
  filter: "all",
  search: "",
  activeProjectId: "all",
  editingTaskId: "",
  user: null,
  loaded: false,
  dirty: false,
  saving: false
};

const todoStatus = document.querySelector("#todoStatus");
const todoCount = document.querySelector("#todoCount");
const todoOpen = document.querySelector("#todoOpen");
const todoToday = document.querySelector("#todoToday");
const todoProjects = document.querySelector("#todoProjects");
const todoSearch = document.querySelector("#todoSearch");
const todoFilters = document.querySelector("#todoFilters");
const todoProjectList = document.querySelector("#todoProjectList");
const todoCurrentProject = document.querySelector("#todoCurrentProject");
const todoCurrentProjectTag = document.querySelector("#todoCurrentProjectTag");
const todoTaskList = document.querySelector("#todoTaskList");
const todoTaskForm = document.querySelector("#todoTaskForm");
const todoTaskProject = document.querySelector("#todoTaskProject");
const todoTaskSubmit = document.querySelector("#todoTaskSubmit");
const cancelEditBtn = document.querySelector("#cancelEditBtn");
const clearDoneBtn = document.querySelector("#clearDoneBtn");
const quickAddBtn = document.querySelector("#quickAddBtn");
const newProjectBtn = document.querySelector("#newProjectBtn");
const clockNode = document.querySelector("#clock");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

function setStatus(text, mode = "normal") {
  if (!todoStatus) return;
  todoStatus.textContent = text;
  todoStatus.dataset.mode = mode;
}

function uid(prefix = "id") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function parseDue(value) {
  if (!value) return 0;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : 0;
}

function isToday(timestamp) {
  if (!timestamp) return false;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const due = new Date(timestamp);
  due.setHours(0, 0, 0, 0);
  return due.getTime() === now.getTime();
}

function defaultPayload() {
  const coreProject = uid("project");
  const riskProject = uid("project");
  const reviewProject = uid("project");
  const now = Date.now();

  return {
    projects: [
      { id: coreProject, name: "龙头逻辑list", createdAt: now - 3000 },
      { id: riskProject, name: "交易系统教学", createdAt: now - 2000 },
      { id: reviewProject, name: "复盘追踪", createdAt: now - 1000 }
    ],
    tasks: [
      {
        id: uid("task"),
        projectId: coreProject,
        title: "BTC 筑底开启新一轮牛市的前置信号",
        note: "监控 ETF 净流入、矿工卖压、OI 结构",
        dueAt: now + 24 * 60 * 60 * 1000,
        priority: "high",
        done: false,
        createdAt: now - 80_000
      },
      {
        id: uid("task"),
        projectId: coreProject,
        title: "PixVerse、AI 视频生成第一股，上市后重点关注",
        note: "结合新股榜和律动快讯做联动跟踪",
        dueAt: now + 48 * 60 * 60 * 1000,
        priority: "normal",
        done: false,
        createdAt: now - 70_000
      },
      {
        id: uid("task"),
        projectId: coreProject,
        title: "现在我 4 倍杠杆是总体半仓，8 倍杠杆是满仓",
        note: "确认趋势前只做分批，不在情绪峰值追加风险。",
        dueAt: 0,
        priority: "high",
        done: false,
        createdAt: now - 65_000
      },
      {
        id: uid("task"),
        projectId: coreProject,
        title: "深刻意识到目前自己最应该做的事情是积累",
        note: "把每天的市场观察写成结构化笔记，减少临盘随意判断。",
        dueAt: 0,
        priority: "normal",
        done: false,
        createdAt: now - 63_000
      },
      {
        id: uid("task"),
        projectId: riskProject,
        title: "仓位控制：4 倍杠杆主体半仓，8 倍仅在确认满仓",
        note: "单笔止损写入执行清单",
        dueAt: 0,
        priority: "high",
        done: false,
        createdAt: now - 60_000
      },
      {
        id: uid("task"),
        projectId: reviewProject,
        title: "港股打新节奏：胜宏极高估值的复盘记录",
        note: "总结情绪、换手与承接结构",
        dueAt: now + 3 * 24 * 60 * 60 * 1000,
        priority: "normal",
        done: true,
        createdAt: now - 50_000
      },
      {
        id: uid("task"),
        projectId: coreProject,
        title: "intel 的热度相当高，等大分歧做延续观察",
        note: "记录情绪过热后的回撤强度和承接位置。",
        dueAt: now + 4 * 24 * 60 * 60 * 1000,
        priority: "normal",
        done: false,
        createdAt: now - 45_000
      },
      {
        id: uid("task"),
        projectId: coreProject,
        title: "cerebras 五月上市，AI 芯片股重点关注",
        note: "联动新股榜、IPO 信息和美股热度。",
        dueAt: now + 5 * 24 * 60 * 60 * 1000,
        priority: "normal",
        done: false,
        createdAt: now - 40_000
      }
    ]
  };
}

function emptyPayload() {
  return { projects: [], tasks: [] };
}

function isValidTodoPayload(parsed) {
  return parsed && Array.isArray(parsed.projects) && Array.isArray(parsed.tasks);
}

function todoUserStorageKey(user) {
  return user?.id ? `${TODO_STORAGE_KEY}:user:${user.id}` : TODO_STORAGE_KEY;
}

function legacyTodoPayload(user) {
  const keys = [todoUserStorageKey(user)];
  if (user?.role === "admin") keys.push(TODO_STORAGE_KEY);
  try {
    for (const key of keys) {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const parsed = JSON.parse(raw);
      if (isValidTodoPayload(parsed) && (parsed.projects.length || parsed.tasks.length)) {
        return parsed;
      }
    }
  } catch {
    return null;
  }
  return null;
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    if (response.status === 401 && payload.loginUrl) window.location.replace(payload.loginUrl);
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

async function loadState(user) {
  const payload = await apiJson(TODO_API);
  if (isValidTodoPayload(payload) && (payload.projects.length || payload.tasks.length)) return payload;

  const legacy = payload.exists ? null : legacyTodoPayload(user);
  if (legacy) {
    const saved = await apiJson(TODO_API, {
      method: "POST",
      body: JSON.stringify({ ...legacy, migratedFrom: "localStorage" })
    });
    setStatus("已把旧本地任务导入数据库", "ok");
    return saved;
  }

  return emptyPayload();
}

function currentTodoPayload() {
  return {
    projects: todoState.projects,
    tasks: todoState.tasks,
    updatedAt: Date.now()
  };
}

function persist() {
  if (!todoState.loaded || !todoState.user) return;
  todoState.dirty = true;
  todoState.saving = true;
  setStatus("数据库保存中", "loading");
  clearTimeout(todoSaveTimer);
  todoSaveTimer = window.setTimeout(saveStateToServer, 180);
}

async function saveStateToServer() {
  if (!todoState.user) return;
  const payload = currentTodoPayload();
  try {
    await apiJson(TODO_API, {
      method: "POST",
      body: JSON.stringify(payload)
    });
    todoState.dirty = false;
    todoState.saving = false;
    setStatus("数据库任务库 · 已保存", "ok");
  } catch (error) {
    todoState.saving = false;
    setStatus(error.message || "数据库保存失败", "error");
  }
}

function projectById(projectId) {
  return todoState.projects.find((project) => project.id === projectId);
}

function taskMatches(task) {
  if (todoState.activeProjectId !== "all" && task.projectId !== todoState.activeProjectId) return false;
  if (todoState.filter === "today" && !isToday(task.dueAt)) return false;
  if (todoState.filter === "upcoming" && (task.done || !task.dueAt || task.dueAt < Date.now())) return false;
  if (todoState.filter === "completed" && !task.done) return false;
  if (todoState.filter === "all" && false) return false;
  if (!todoState.search) return true;

  const project = projectById(task.projectId);
  const text = [task.title, task.note, task.priority, project?.name || ""].join(" ").toLowerCase();
  return text.includes(todoState.search.toLowerCase());
}

function taskSort(a, b) {
  if (a.done !== b.done) return a.done ? 1 : -1;
  if (a.dueAt && b.dueAt && a.dueAt !== b.dueAt) return a.dueAt - b.dueAt;
  if (a.dueAt && !b.dueAt) return -1;
  if (!a.dueAt && b.dueAt) return 1;
  return (b.createdAt || 0) - (a.createdAt || 0);
}

function visibleTasks() {
  return [...todoState.tasks].filter(taskMatches).sort(taskSort);
}

function formatDate(value) {
  if (!value) return "无截止时间";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatDateTimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offsetDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return offsetDate.toISOString().slice(0, 16);
}

function priorityLabel(priority) {
  if (priority === "high") return "高优先";
  if (priority === "low") return "低优先";
  return "常规";
}

function metrics() {
  const total = todoState.tasks.length;
  const open = todoState.tasks.filter((task) => !task.done).length;
  const today = todoState.tasks.filter((task) => isToday(task.dueAt) && !task.done).length;
  todoCount.textContent = String(total);
  todoOpen.textContent = String(open);
  todoToday.textContent = String(today);
  todoProjects.textContent = String(todoState.projects.length);
}

function projectCount(projectId) {
  return todoState.tasks.filter((task) => task.projectId === projectId && !task.done).length;
}

function renderProjectSelector() {
  const currentValue = todoTaskProject.value;
  const editingTask = todoState.tasks.find((task) => task.id === todoState.editingTaskId);
  todoTaskProject.innerHTML = todoState.projects
    .map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`)
    .join("");
  const nextValue = editingTask?.projectId || currentValue || todoState.projects[0]?.id || "";
  if (nextValue) todoTaskProject.value = nextValue;
}

function renderProjects() {
  const rows = todoState.projects.map((project) => {
      const count = projectCount(project.id);
      return `
        <button type="button" class="todo-project-row ${todoState.activeProjectId === project.id ? "active" : ""}" data-project-id="${escapeHtml(project.id)}">
          <span>${escapeHtml(project.name)}</span>
          <b>${count}</b>
        </button>
      `;
    });
  todoProjectList.innerHTML = rows.join("");
  renderProjectSelector();
}

function currentProjectName() {
  if (todoState.activeProjectId === "all") return "全部任务";
  return projectById(todoState.activeProjectId)?.name || "项目";
}

function renderTasks() {
  const tasks = visibleTasks();
  todoCurrentProject.textContent = currentProjectName();
  todoCurrentProjectTag.textContent = todoState.activeProjectId === "all" ? "收件箱 /" : "我的项目 /";
  if (!tasks.length) {
    todoTaskList.innerHTML = `
      <div class="empty-state">
        <b>当前筛选下没有任务</b>
        <span>可以新建一个任务，或者切换筛选条件。</span>
      </div>
    `;
    return;
  }

  todoTaskList.innerHTML = tasks
    .map((task) => {
      const project = projectById(task.projectId);
      const dueText = formatDate(task.dueAt);
      const overdue = task.dueAt && task.dueAt < Date.now() && !task.done;
      return `
        <article class="todo-task ${task.done ? "done" : ""}">
          <label class="todo-check">
            <input type="checkbox" data-action="toggle" data-task-id="${escapeHtml(task.id)}" ${task.done ? "checked" : ""} />
            <span></span>
          </label>
          <div class="todo-task-body">
            <h3>${escapeHtml(task.title)}</h3>
            ${task.note ? `<p>${escapeHtml(task.note)}</p>` : ""}
            <div class="todo-tags">
              <span>${escapeHtml(project?.name || "未分组")}</span>
              <span class="${overdue ? "todo-tag-warn" : ""}">${escapeHtml(dueText)}</span>
              <span>${escapeHtml(priorityLabel(task.priority))}</span>
            </div>
          </div>
          <div class="todo-task-actions">
            <button class="todo-edit" type="button" data-action="edit" data-task-id="${escapeHtml(task.id)}">编辑</button>
            <button class="todo-remove" type="button" data-action="remove" data-task-id="${escapeHtml(task.id)}">删除</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function render() {
  metrics();
  renderProjects();
  renderTasks();
  persist();
}

function saveTaskFromForm(formData) {
  const title = String(formData.get("title") || "").trim();
  if (!title) return false;
  if (!todoState.projects.length) {
    todoState.projects.push({ id: uid("project"), name: "默认项目", createdAt: Date.now() });
  }
  const dueAt = parseDue(formData.get("due"));
  const payload = {
    projectId: String(formData.get("project") || todoState.projects[0]?.id || ""),
    title,
    note: String(formData.get("note") || "").trim(),
    dueAt,
    priority: String(formData.get("priority") || "normal")
  };
  if (todoState.editingTaskId) {
    const task = todoState.tasks.find((item) => item.id === todoState.editingTaskId);
    if (!task) return false;
    Object.assign(task, payload, { updatedAt: Date.now() });
    return true;
  }
  todoState.tasks.unshift({
    id: uid("task"),
    ...payload,
    done: false,
    createdAt: Date.now()
  });
  return true;
}

function resetTaskForm() {
  todoState.editingTaskId = "";
  todoTaskForm.reset();
  todoTaskProject.value = todoState.activeProjectId !== "all" ? todoState.activeProjectId : todoState.projects[0]?.id || "";
  if (todoTaskSubmit) todoTaskSubmit.textContent = "保存任务";
  if (cancelEditBtn) cancelEditBtn.hidden = true;
}

function startEditTask(taskId) {
  const task = todoState.tasks.find((item) => item.id === taskId);
  if (!task) return;
  todoState.editingTaskId = task.id;
  todoTaskForm.elements.title.value = task.title || "";
  todoTaskForm.elements.due.value = formatDateTimeLocal(task.dueAt);
  todoTaskForm.elements.project.value = task.projectId || todoState.projects[0]?.id || "";
  todoTaskForm.elements.priority.value = task.priority || "normal";
  todoTaskForm.elements.note.value = task.note || "";
  if (todoTaskSubmit) todoTaskSubmit.textContent = "保存修改";
  if (cancelEditBtn) cancelEditBtn.hidden = false;
  setStatus("正在编辑任务", "normal");
  todoTaskForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  todoTaskForm.elements.title.focus();
}

function addProject(name) {
  const trimmed = String(name || "").trim();
  if (!trimmed) return;
  const exists = todoState.projects.some((project) => project.name === trimmed);
  if (exists) return;
  const project = { id: uid("project"), name: trimmed, createdAt: Date.now() };
  todoState.projects.push(project);
  if (todoState.activeProjectId === "all") {
    todoTaskProject.value = project.id;
  }
}

function toggleTask(taskId) {
  const task = todoState.tasks.find((item) => item.id === taskId);
  if (!task) return;
  task.done = !task.done;
}

function removeTask(taskId) {
  todoState.tasks = todoState.tasks.filter((task) => task.id !== taskId);
  if (todoState.editingTaskId === taskId) resetTaskForm();
}

function clearCompleted() {
  todoState.tasks = todoState.tasks.filter((task) => !task.done);
  if (todoState.editingTaskId && !todoState.tasks.some((task) => task.id === todoState.editingTaskId)) resetTaskForm();
}

function updateClock() {
  const now = new Date();
  clockNode.textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(now);
}

todoTaskForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const wasEditing = Boolean(todoState.editingTaskId);
  const saved = saveTaskFromForm(new FormData(todoTaskForm));
  if (!saved) return;
  render();
  resetTaskForm();
  setStatus(wasEditing ? "任务已更新" : "任务已保存", "ok");
});

todoTaskList.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const taskId = target.dataset.taskId;
  if (target.dataset.action === "edit") {
    startEditTask(taskId);
    return;
  }
  if (target.dataset.action === "remove") {
    removeTask(taskId);
    render();
  }
});

todoTaskList.addEventListener("change", (event) => {
  const checkbox = event.target.closest('input[data-action="toggle"]');
  if (!checkbox) return;
  toggleTask(checkbox.dataset.taskId);
  render();
});

todoProjectList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-project-id]");
  if (!button) return;
  todoState.activeProjectId = button.dataset.projectId;
  render();
});

todoFilters.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]");
  if (!button) return;
  todoState.filter = button.dataset.filter;
  todoFilters.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderTasks();
});

todoSearch.addEventListener("input", (event) => {
  todoState.search = event.target.value.trim();
  renderTasks();
});

clearDoneBtn.addEventListener("click", () => {
  clearCompleted();
  render();
});

cancelEditBtn?.addEventListener("click", () => {
  resetTaskForm();
  setStatus("已取消编辑", "normal");
});

quickAddBtn.addEventListener("click", () => {
  document.querySelector("#todoTaskTitle").focus();
});

newProjectBtn.addEventListener("click", () => {
  const name = window.prompt("请输入新项目名称");
  addProject(name);
  render();
});

window.addEventListener("beforeunload", () => {
  if (!todoState.loaded || !todoState.user || !todoState.dirty || !navigator.sendBeacon) return;
  const blob = new Blob([JSON.stringify(currentTodoPayload())], { type: "application/json" });
  navigator.sendBeacon(TODO_API, blob);
});

async function boot() {
  const user = window.XingyunAuthReady ? await window.XingyunAuthReady : window.XingyunCurrentUser;
  if (!user) return;
  todoState.user = user;
  setStatus("正在读取数据库任务", "loading");
  const payload = await loadState(user);
  todoState.projects = payload.projects;
  todoState.tasks = payload.tasks;
  todoState.activeProjectId = todoState.projects[0]?.id || "all";
  updateClock();
  setInterval(updateClock, 1000);
  render();
  todoState.loaded = true;
  setStatus("数据库任务库 · 已加载", "ok");
}

boot().catch(() => setStatus("登录状态读取失败", "error"));
