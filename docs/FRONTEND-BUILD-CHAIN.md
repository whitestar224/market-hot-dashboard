# 前端构建链方案（Phase 4）

> 状态：方案文档（含现状评估；激进 ESM 化暂缓）
> 日期：2026-09-29

## 1. 现状：经典多脚本全局命名空间模式

20 个 HTML 页面全部使用**裸 `<script src="...">` 标签**加载 37 个 JS 文件，**没有任何
`type="module"`、没有任何 `export`/`import`**。依赖关系靠「加载顺序 + 全局变量」维护。

### 1.1 共享基础脚本（按复用面）

| 脚本 | 复用页面数 | 角色 |
|---|---|---|
| `wallets.js` / `desktop_adapter.js` / `auth.js` | 15 | 钱包状态 / 桌面桥 / 鉴权基础层 |
| `todo_reminder.js` | 13 | 待办提醒工具 |
| `notifier.js` | 9 | 通知 |
| `insights.js` / `ai_insights.js` | 4 | AI 洞察 |
| `rankings.js` / `monitor-buy*.js` | 2 | 榜单 / 买入执行 |

### 1.2 版本号缓存失效

每个 `<script>` 带 `?v=N` 手写版本号（如 `app.js?v=37`、`price-watch.js?v=107`），
靠手动递增做浏览器缓存失效。这是维护负担的来源之一。

### 1.3 唯一的构建链

`package.json` 的 `monitor:build-buy` 已示范 esbuild 用法：把 `tools/monitor-buy-executor-entry.js`
打包成单个 ESM 产物 `assets/monitor-buy-executor.js`。这是「独立打包某个可独立运行的模块」
的正确范式。

## 2. 关键判断：激进 ESM 化风险过高，暂缓

把 37 个全局命名空间脚本改成 ES 模块（`type="module"`）是**高风险迁移**：

1. **全局变量 → 模块作用域**：`wallets.js` 等基础脚本目前通过 `window.X = ...` 或裸全局
   变量向页面脚本暴露 API。改成 ESM 后这些变量不再自动共享，每个页面的依赖顺序、命名冲突
   都要逐个重验。
2. **加载顺序语义改变**：普通 `<script>` 是同步顺序执行；`type="module"` 默认 `defer`，
   执行时机推迟到 DOM 解析后，会改变大量「脚本在 DOM 就绪前就初始化」的现有假设。
3. **无测试覆盖运行时顺序**：前端没有单元测试能验证 20 个页面的全局依赖顺序，迁移只能靠
   逐页人工点检，与「不改变任何功能/页面」的铁律直接冲突。

**结论**：Phase 4 现阶段只做「基础设施 + 低风险增量」，不做全量 ESM 重写。

## 3. 低风险落地路径（推荐）

### 3.1 共享基础层打包（收益：减少请求 + 固化顺序，风险：低）

把 15 页都引入的 `wallets.js` + `desktop_adapter.js` + `auth.js` 打包成单个
`assets/base-bundle.js`（esbuild `--bundle`，保持 IIFE/全局命名空间，**不引入 ESM 语义**），
页面从 3 个 `<script>` 减为 1 个。esbuild 的 bundle 会按依赖顺序合并，且保留全局变量，
**运行时行为不变**（仍是同步顺序执行、仍是全局命名空间）。

### 3.2 版本号自动化（收益：消除手写 `?v=N`，风险：极低）

用 esbuild 的 `--hash` 或构建脚本生成 content-hash 版本号，替换手写 `?v=N`。

### 3.3 清理临时文件（已发现，风险：零）

根目录存在两个明显遗留的临时文件：`tmp_chunk_809501.js`、`tmp_gmgn_app.js`，
未被任何 HTML 引用，可安全移除（需先二次确认无引用）。

## 4. 与现有 build 链的关系

- 已有 `monitor:build-buy` 是「单入口 → 单产物」的最小范例。
- 本方案 3.1 是同一范式的推广（多基础脚本 → 单 bundle），不动页面业务脚本。
- 页面业务脚本（`app.js`/`price-watch.js`/`dragon-wave*.js` 等）保持裸 script 现状，
  等后续有明确需求（如代码分割、tree-shaking）再逐个迁移。

## 5. 落地清单（按优先级）

1. [ ] 确认 `tmp_chunk_809501.js` / `tmp_gmgn_app.js` 无引用后删除。
2. [ ] 新增 `base:build` 脚本，esbuild 打包 `wallets.js`+`desktop_adapter.js`+`auth.js` → `assets/base-bundle.js`，IIFE 保持全局命名空间。
3. [ ] 15 个页面改引 `base-bundle.js`（先 1 个页面试点，A/B 点检后铺开）。
4. [ ] 版本号 content-hash 自动化。
