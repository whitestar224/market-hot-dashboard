# Confirmed Buy Causal Hierarchy Optimization

> **For Codex:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Use the latest confirmed/denied feedback to make mature consolidation breakouts the primary execution route, keep horizontal launch as a child structure, and prevent high scores or auxiliary patterns from manufacturing buy signals.

**Architecture:** Add a causal hierarchy assessment after each candidate has been fully described but before score-based execution. The hierarchy separates execution foundations, confirmation boosters, and explicit exceptions. Existing permanent manual confirmations remain immutable; the new hierarchy governs native strategy signals and retained candidates. Multi-timeframe evidence may upgrade confidence only after a valid parent structure exists.

**Tech Stack:** Vanilla JavaScript strategy engine and dashboard, Node test runner, Python HTTP/API service and unittest suite.

---

## Design decisions

1. **盘整突破是一级结构。** A native high-confidence execution normally requires a mature occupied mother consolidation, a real outer boundary/previous high, and a causal cross from below. Horizontal launch is a subtype of this route, not an independent permission.
2. **辅助结构不能单独开仓。** Triangle, trendline, pivot, volume, EMA90 reclaim, and near-previous-high labels add confidence and explain the trade; score alone cannot turn them into a buy.
3. **分数只排序，不授予资格。** A 95-point candidate that fails the causal hierarchy is rejected or retained as a quiet candidate. A lower score may still execute when the primary structure and an explicit exception are valid.
4. **成熟度以区间中心而非死阈值表达。** Roughly 40–60 candles is the aesthetic center for 5m/15m/1h. Shorter structures need a qualified prior advance plus a higher-timeframe/main-wave anchor or an explicit mature-structure exception. Permanent confirmed samples are never removed.
5. **多周期只做共振。** Cross-frame alignment upgrades grade and priority after the current-frame boundary is independently valid. It cannot fabricate a consolidation or previous high.
6. **硬否决优先。** Mother-box interior, unrecovered selloff, high-level distribution, rising wedge/channel, rushed stair-step, opened beyond trigger, launch distance above 7%, and duplicate recross without reset remain earlier than score.
7. **双层召回继续保留。** A true boundary cross that misses only soft quality gates remains in `retainedCandidates`; it never paints B, triggers popup/voice, or bypasses a permanent denial.
8. **二次突破是提示层。** 当第一次外沿突破没有明显量能、只完成试盘，随后价格回到边界附近但没有破坏母结构，再次从线下突破且量能/速度改善时，生成独立的 `secondaryBreakoutHints`。盘面用红色 B 标记其重要性；它不进入正式 `signals`、不计入买点数、不触发自动执行，但允许独立的红色弹窗和语音防洗提示。
9. **原生 1 小时平台按价格带向左合并。** 当末端局部十余根与更早 K 线在同一压力高度持续交易时，原生 1 小时识别应沿重复触点组向左恢复完整母平台；平台前最后一根拉升 K 只负责证明前置动量，不计入盘整起点。该合并只读取突破 K 及其以前的数据。
10. **相邻周期只在双方各自有结构时共振。** 5 分钟成熟母平台可以补足 15 分钟短盘整的分辨率，但 15 分钟自身仍须具备拐点与真实前高；15 分钟成熟母平台也可以为随后 5 分钟放量再突破提供母区间。该许可不机械投影到 1 小时。
11. **急拉后的短消化是独立窄路径。** 已经进入陡峭主升、完成 12–22 根高质量短消化并从线下突破真实前高时，不强制套用约 40 根横盘；急促抬低点、母箱体噪声、风险结构和离线追价仍是一票否决。
12. **母箱体优先于末端子通道。** 上升通道若只是完整母箱体中后段的内部路径，且最终 K 同时突破母箱体真实外沿与通道上轨，应按母箱体突破处理；没有更早母平台、重复触点组或真实外沿突破时，通道末端仍禁止追入。

## Tasks

### Task 1: Lock the causal hierarchy with regression tests

**Files:**
- Modify: `tests/dragon_wave_engine.test.js`

- Add a mature mother-platform + previous-high example that receives a core execution permit.
- Add an auxiliary-only high-score example that is refused despite trendline/pivot/volume confluence.
- Add an explicit mature triangle/EMA90 or declared-main-wave exception example.
- Assert horizontal launch is reported as a child label and not a standalone execution permission.

### Task 2: Implement the hierarchy assessor

**Files:**
- Modify: `dragon-wave-engine.js`

- Add a pure `assessExecutionHierarchy(signal)` helper.
- Return tier, permit, primary foundation, boosters, missing causal prerequisites, and maturity metadata.
- Export the helper for deterministic testing.

### Task 3: Put hierarchy before score-based execution

**Files:**
- Modify: `dragon-wave-engine.js`

- Attach the hierarchy to evaluated signals.
- Update `isHighCertaintyEntry` and context gates so score can rank only permitted candidates.
- Preserve explicit mature structure and main-wave exceptions without weakening hard vetoes.
- Keep non-permitted true boundary crosses in the quiet retained-candidate lane when appropriate.

### Task 4: Surface the reason and version the strategy

**Files:**
- Modify: `dragon-wave.js`
- Modify: `dragon-wave.html`
- Modify: `server.py`
- Modify: `tests/dragon_wave_page.test.js`

- Show the hierarchy tier and missing prerequisite in explanations/ledger where applicable.
- Render secondary-breakout hints as red B markers below the candle; keep filtered red crosses above candles so the two meanings remain visually distinct.
- Bump cache and API strategy versions together.

### Task 5: Verify and restart

**Files:**
- Test: `tests/dragon_wave_engine.test.js`
- Test: `tests/dragon_wave_page.test.js`
- Test: Python test suite under `tests/`

- Run focused Node tests, then the full Node suite.
- Run the full Python unittest suite.
- Restart the local API service and verify the health/version response.
