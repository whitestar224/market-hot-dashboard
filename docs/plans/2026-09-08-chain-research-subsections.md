# Chain Research Subsections Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将链上投研长页面拆为五个无整页刷新的子板块，并保留现有数据、AI 分析和公链操作。

**Architecture:** 前端在现有 `renderChainEcosystem` 上增加轻量视图状态和子导航，把当前模板拆成五个职责单一的渲染函数。后端在同一响应中增加受限的过滤候选列表，不增加新的轮询请求。

**Tech Stack:** 原生 JavaScript、CSS、Python、SQLite、Node.js test runner、Python unittest。

---

### Task 1: 固定子板块契约

**Files:**
- Modify: `tests/price_watch_chain_ecosystem.test.js`
- Modify: `tests/test_chain_ecosystem_monitor.py`

**Steps:**
1. 增加五个子板块、默认视图和 URL 状态的静态契约测试。
2. 增加后端 `filtered` 列表有数量上限并保留过滤原因的测试。
3. 运行相关测试并确认新增断言先失败。

### Task 2: 补充受限过滤候选数据

**Files:**
- Modify: `chain_ecosystem_monitor.py`
- Test: `tests/test_chain_ecosystem_monitor.py`

**Steps:**
1. 从当天候选中选择分数最高的过滤标的，限制下发数量。
2. 在 `dailyResearch` 中返回 `filtered`，不改变现有 `selected` 和 `watching`。
3. 运行 Python 链上投研测试并确认通过。

### Task 3: 拆分五个前端视图

**Files:**
- Modify: `price-watch.js`
- Test: `tests/price_watch_chain_ecosystem.test.js`

**Steps:**
1. 增加 `chainSubMode` 状态、允许值和 `chainView` URL 持久化。
2. 将今日精选、全量扫盘、历史复盘、公链生态和系统状态拆成独立模板。
3. 增加子板块点击处理，仅重新渲染本地已有数据。
4. 运行 JavaScript 语法检查和相关 Node 测试。

### Task 4: 建立清晰的投研工作台样式

**Files:**
- Modify: `styles.css`
- Modify: `price-watch.html`
- Test: `tests/price_watch_chain_ecosystem.test.js`

**Steps:**
1. 增加子导航、视图标题、扫描列表和系统状态样式。
2. 调整桌面端宽度平衡与窄屏单列布局。
3. 更新静态资源版本，避免浏览器继续使用旧缓存。
4. 运行相关测试与 `git diff --check`。

### Task 5: 真实页面验证

**Files:**
- Verify: `price-watch.html`

**Steps:**
1. 在本地浏览器打开链上投研并验证默认进入“今日最值得研究”。
2. 逐一切换五个子板块，确认 URL 更新且页面没有整页刷新。
3. 检查桌面和窄屏布局、Robinhood Chain 状态与公链切换。
4. 运行相关 Python、Node 测试并记录结果。
