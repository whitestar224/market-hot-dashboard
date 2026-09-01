(function bootDragonWavePage() {
  "use strict";

  const Engine = window.DragonWaveEngine;
  const Data = window.DragonWaveData;
  const Cases = window.DragonWaveCases;
  const Feedback = window.DragonWaveFeedback;
  const Vision = window.DragonWaveVision;
  if (!Engine || !Data || !Cases || !Feedback || !Vision) return;

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  // 1 分钟暂时退出默认工作区：它不参与首屏读取、共振过门或交互绘制。
  // 需要复盘时改为一次性静态图片，避免长历史窗口持续拖慢看板。
  const intervals = Object.keys(Data.INTERVALS).filter((interval) => interval !== "1m");
  const FEEDBACK_STORAGE_KEY = "dragon-wave-feedback-v1";
  const DRAWING_STORAGE_KEY = "dragon-wave-structure-drawings-v1";
  const DEVICE_STORAGE_KEY = "dragon-wave-feedback-device-v1";
  const ANALYSIS_CONTEXT_STORAGE_KEY = "dragon-wave-analysis-context-v1";
  const MARKET_CACHE_DB = "dragon-wave-market-cache-v1";
  const MARKET_CACHE_STORE = "analyzed-timeframes";
  const MARKET_CANDLE_CACHE_STORE = "market-candles";
  const MARKET_CACHE_SCHEMA = 2;
  const MARKET_CACHE_LIMIT = 48;
  const MARKET_CANDLE_CACHE_LIMIT = 640;
  const STRATEGY_CACHE_VERSION = "v89";
  const ANALYSIS_WORKER_URL = new URL("./dragon-wave-analysis-worker.js?v=89", window.location.href);
  const ANALYSIS_WORKER_COUNT = Math.max(1, Math.min(2, Number(navigator.hardwareConcurrency) || 2));
  const VISUAL_RANGE_MIN_BARS = 12;
  const TUT_DISPLAY_CUTOFF = new Date("2026-08-05T13:00:00+08:00").getTime();
  const SPK_DISPLAY_CUTOFF = new Date("2025-07-22T07:00:00+08:00").getTime();
  const STRUCTURE_TAG_LABELS = Object.freeze({
    horizontalLaunch: "横盘起飞",
    trendlineBreakout: "趋势线突破",
    triangle: "三角",
    box: "箱体",
    fallingWedge: "降楔",
    pivot: "拐点",
    previousHighBreakout: "前高突破",
    consolidationBreakout: "盘整突破",
    ema90Pullback: "回踩90均线",
    volumeBreakout: "放量突破",
    nearPreviousHighConsolidation: "前高附近做盘整",
    newCoinNotFalling: "新币不跌",
    mainWaveActive: "主升浪阶段",
    mainWaveExpected: "主升浪预期",
  });
  const INITIAL_VISIBLE_COUNTS = Object.freeze({ "1m": 220, "5m": 190, "15m": 170, "1h": 140, "4h": 120, "1d": 110 });
  const SCREENSHOT_CASE = Object.freeze({
    id: "screenshot-ake",
    symbol: "AKE",
    pair: "AKEUSDT",
    live: true,
    screenshot: true,
    source: "附件截图",
    label: "AKE · 附件截图标的 · 实时",
  });
  const TUT_REFERENCE = Object.freeze({
    ...Cases.find((item) => item.symbol === "TUT"),
    golden: true,
    label: "TUT · 起爆黄金样本 · 15分钟",
  });

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function formatPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "—";
    if (number >= 1000) return number.toLocaleString("en-US", { maximumFractionDigits: 2 });
    if (number >= 10) return number.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
    if (number >= 1) return number.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
    if (number >= 0.01) return number.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
    return number.toPrecision(6).replace(/0+$/, "").replace(/\.$/, "");
  }

  function formatCompact(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(number);
  }

  function formatDateTime(value, includeYear = false) {
    if (!value) return "—";
    const options = includeYear
      ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("zh-CN", options).format(new Date(value)).replace(/\//g, "-");
  }

  function toLocalInput(timestamp) {
    const date = new Date(timestamp - new Date(timestamp).getTimezoneOffset() * 60_000);
    return date.toISOString().slice(0, 16);
  }

  function parseInputTime(value) {
    const parsed = new Date(value).getTime();
    return Number.isFinite(parsed) ? parsed : Date.now();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function signalDisplayAllowed(item, pair) {
    const normalizedPair = Data.normalizePair(pair || "");
    if (item?.interval === "1m" && !Engine.isOneMinuteHorizontalBase(item)) return false;
    if (normalizedPair === "TUTUSDT" && Number(item?.time) < TUT_DISPLAY_CUTOFF) return false;
    if (normalizedPair === "SPKUSDT" && Number(item?.time) < SPK_DISPLAY_CUTOFF) return false;
    return true;
  }

  function applyFeedbackPolicy(result, pair, preparedContext = null) {
    return Engine.enforceIntervalStructurePolicy(Feedback.applyToResult(result, pair, state.feedback, preparedContext));
  }

  function resultForDisplay(result, pair) {
    if (!result) return result;
    const signals = (result.signals || []).filter((item) => signalDisplayAllowed(item, pair));
    const pending = (result.pending || []).filter((item) => signalDisplayAllowed(item, pair));
    const secondaryBreakoutHints = (result.secondaryBreakoutHints || []).filter((item) => signalDisplayAllowed(item, pair));
    const retainedCandidates = (result.retainedCandidates || []).filter((item) => signalDisplayAllowed(item, pair));
    const rejected = (result.rejected || []).filter((item) => signalDisplayAllowed(item, pair));
    return {
      ...result,
      signals,
      pending,
      secondaryBreakoutHints,
      retainedCandidates,
      rejected,
      stats: {
        ...(result.stats || {}),
        signalCount: signals.length,
        pendingCount: pending.length,
        secondaryBreakoutHintCount: secondaryBreakoutHints.length,
        retainedCandidateCount: retainedCandidates.length,
        rejectedCount: rejected.length,
      },
    };
  }

  class WaveChart {
    constructor(card, onCrosshair, onFeedback) {
      this.card = card;
      this.interval = card.dataset.interval;
      this.canvas = $("canvas", card);
      this.ctx = this.canvas.getContext("2d");
      this.surface = $(".chart-surface", card);
      this.tooltip = $(".chart-tooltip", card);
      this.feedbackPopover = $(".chart-feedback-popover", card);
      this.rangeHint = document.createElement("div");
      this.rangeHint.className = "chart-range-hint";
      this.rangeHint.setAttribute("role", "status");
      this.rangeHint.setAttribute("aria-live", "polite");
      this.surface.append(this.rangeHint);
      this.rangeHintTimer = null;
      this.visualLearningPanel = document.createElement("aside");
      this.visualLearningPanel.className = "chart-visual-learning";
      this.visualLearningPanel.dataset.chartVisualLearning = "";
      this.visualLearningPanel.hidden = true;
      this.visualLearningPanel.innerHTML = '<em>VISUAL PRECHECK</em><strong data-chart-visual-score>等待视觉样本</strong><small data-chart-visual-detail>只读取触发前K线</small>';
      this.feedbackPopover?.querySelector(".chart-certainty")?.before(this.visualLearningPanel);
      this.empty = $(".chart-empty", card);
      this.onCrosshair = onCrosshair;
      this.onFeedback = onFeedback;
      this.result = null;
      this.venue = null;
      this.visibleCount = INITIAL_VISIBLE_COUNTS[this.interval] || 140;
      this.offset = 0;
      this.hoverIndex = null;
      this.externalTime = null;
      this.drag = null;
      this.drawTool = "pan";
      this.drawStart = null;
      this.drawPreview = null;
      this.geometry = null;
      this.annotationKey = "default";
      this.annotationSets = new Map([[this.annotationKey, []]]);
      this.annotations = this.annotationSets.get(this.annotationKey);
      this.viewStates = new Map();
      this.showRejected = false;
      this.pair = "";
      this.selectedFeedbackKey = "";
      this.selectedFeedbackItem = null;
      this.feedbackPress = null;
      this.visualRangeDraft = null;
      this.renderFrame = null;
      this.signalByIndex = new Map();
      this.timeIndex = new Map();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.surface);
      this.bindEvents();
      this.bindFeedbackPopover();
    }

    bindEvents() {
      this.canvas.addEventListener("wheel", (event) => {
        if (!this.result?.candles.length) return;
        event.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        const ratio = clamp((event.clientX - rect.left - 8) / Math.max(rect.width - 76, 1), 0, 1);
        const oldCount = Math.min(this.visibleCount, this.result.candles.length);
        const oldEnd = this.result.candles.length - this.offset;
        const anchor = oldEnd - oldCount + ratio * oldCount;
        const factor = event.deltaY > 0 ? 1.13 : 0.88;
        this.visibleCount = Math.round(clamp(this.visibleCount * factor, 28, Math.min(720, this.result.candles.length)));
        const newEnd = Math.round(anchor + (1 - ratio) * this.visibleCount);
        this.offset = clamp(this.result.candles.length - newEnd, 0, Math.max(0, this.result.candles.length - this.visibleCount));
        this.render();
      }, { passive: false });

      this.canvas.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        if (this.visualRangeDraft) {
          this.feedbackPress = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
          event.preventDefault();
          return;
        }
        if (this.drawTool === "feedback") {
          this.feedbackPress = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
          event.preventDefault();
          return;
        }
        this.canvas.setPointerCapture(event.pointerId);
        if (this.drawTool !== "pan") {
          this.beginDrawing(event);
          return;
        }
        this.drag = { x: event.clientX, y: event.clientY, offset: this.offset, moved: false };
      });
      this.canvas.addEventListener("pointerup", (event) => {
        if (this.visualRangeDraft) {
          const press = this.feedbackPress;
          this.feedbackPress = null;
          if (!press || Math.hypot(event.clientX - press.x, event.clientY - press.y) < 8) {
            this.selectVisualRangeDraft(event);
          }
          return;
        }
        if (this.drawTool === "feedback") {
          const press = this.feedbackPress;
          this.feedbackPress = null;
          if (!press || Math.hypot(event.clientX - press.x, event.clientY - press.y) < 8) {
            if (this.hitCandle(event)) this.selectCandleFeedbackAt(event);
          }
          return;
        }
        if (this.drawStart) this.finishDrawing(event);
        const chartClick = this.drawTool === "pan" && this.drag && !this.drag.moved;
        this.drag = null;
        if (chartClick) {
          const existing = this.hitFeedbackSignal(event);
          if (existing) this.selectFeedbackAt(event);
          else if (this.hitCandle(event)) this.selectCandleFeedbackAt(event);
          else this.closeFeedbackPopover();
        }
      });
      this.canvas.addEventListener("pointercancel", () => {
        this.drag = null;
        this.feedbackPress = null;
        this.drawStart = null;
        this.drawPreview = null;
        this.render();
      });
      this.canvas.addEventListener("pointerleave", () => {
        this.canvas.classList.remove("is-feedback-target", "is-visual-range-target");
        if (!this.drag) {
          this.hoverIndex = null;
          this.tooltip.classList.remove("is-visible");
          this.render();
        }
      });
      this.canvas.addEventListener("pointermove", (event) => {
        if (!this.result?.candles.length) return;
        const rect = this.canvas.getBoundingClientRect();
        if (this.visualRangeDraft) {
          const point = this.eventPoint(event);
          if (!point) return;
          this.hoverIndex = point.index;
          this.canvas.classList.toggle("is-visual-range-target", Boolean(this.visualRangeDraftTargetAt(event)));
          this.externalTime = null;
          this.onCrosshair(this.result.candles[this.hoverIndex].time, this);
          this.render();
          return;
        }
        if (this.drawStart) {
          const point = this.eventPoint(event);
          if (point) {
            this.drawPreview = { type: this.drawTool, start: this.drawStart, end: point, preview: true };
            this.render();
          }
          return;
        }
        if (this.drawTool === "feedback") {
          const point = this.eventPoint(event);
          if (!point) return;
          this.hoverIndex = point.index;
          this.canvas.classList.toggle("is-feedback-target", Boolean(this.hitCandle(event)));
          this.externalTime = null;
          this.onCrosshair(this.result.candles[this.hoverIndex].time, this);
          this.render();
          return;
        }
        if (this.drawTool !== "pan") return;
        if (this.drag) {
          if (Math.hypot(event.clientX - this.drag.x, event.clientY - this.drag.y) >= 4) this.drag.moved = true;
          if (!this.drag.moved) return;
          const barsPerPixel = this.visibleCount / Math.max(rect.width - 76, 1);
          const dragLeftBars = Math.round((this.drag.x - event.clientX) * barsPerPixel);
          this.offset = clamp(this.drag.offset - dragLeftBars, 0, Math.max(0, this.result.candles.length - this.visibleCount));
          this.render();
          return;
        }
        const bounds = this.visibleBounds();
        const ratio = clamp((event.clientX - rect.left - 8) / Math.max(rect.width - 76, 1), 0, .999);
        this.hoverIndex = clamp(bounds.start + Math.floor(ratio * (bounds.end - bounds.start)), bounds.start, bounds.end - 1);
        this.canvas.classList.toggle("is-feedback-target", Boolean(this.hitFeedbackSignal(event) || this.hitCandle(event)));
        this.externalTime = null;
        this.onCrosshair(this.result.candles[this.hoverIndex].time, this);
        this.render();
      });
      this.canvas.addEventListener("dblclick", () => {
        if (this.drawTool !== "pan") return;
        this.visibleCount = INITIAL_VISIBLE_COUNTS[this.interval] || 140;
        this.offset = 0;
        this.render();
      });
    }

    bindFeedbackPopover() {
      if (!this.feedbackPopover) return;
      $("[data-chart-feedback-close]", this.feedbackPopover).addEventListener("click", () => this.closeFeedbackPopover());
      $$('[data-chart-feedback-action]', this.feedbackPopover).forEach((button) => button.addEventListener("click", () => {
        const item = this.selectedFeedbackSignal();
        if (!item || !this.onFeedback) return;
        const decision = button.dataset.chartFeedbackAction;
        const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
        const current = state.feedback.records?.[key];
        const reviewedTags = current && current.decision !== "cleared"
          ? current.structureTags
          : Feedback.normalizeStructureTags(item.manualStructureTags).length
            ? item.manualStructureTags
            : this.strategyStructurePrecheck(item);
        this.onFeedback({
          ...item,
          venue: this.venue?.label || "",
          predictedStructureTags: current?.predictedStructureTags || this.strategyStructurePrecheck(item),
        }, decision, null, reviewedTags);
        // 当前 K 线先立即更新；全周期权重重算与两路永久保存由后台合并处理。
        this.applyOptimisticFeedback(item, decision);
        if (decision === "confirmed") this.closeFeedbackPopover();
        else {
          this.refreshFeedbackPopover();
          this.render();
        }
      }));
      $$('[data-chart-certainty-grade]', this.feedbackPopover).forEach((button) => button.addEventListener("click", () => {
        const item = this.selectedFeedbackSignal();
        if (!item || !this.onFeedback) return;
        const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
        const current = state.feedback.records?.[key];
        const decision = current && current.decision !== "cleared" ? current.decision : "pending";
        this.onFeedback(
          {
            ...item,
            venue: this.venue?.label || "",
            predictedStructureTags: current?.predictedStructureTags || this.strategyStructurePrecheck(item),
          },
          decision,
          button.dataset.chartCertaintyGrade,
          current?.structureTags || this.strategyStructurePrecheck(item),
        );
        this.refreshFeedbackPopover();
        this.render();
      }));
      $$('[data-chart-structure-tag]', this.feedbackPopover).forEach((button) => button.addEventListener("click", () => {
        const item = this.selectedFeedbackSignal();
        if (!item || !this.onFeedback) return;
        const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
        const current = state.feedback.records?.[key];
        const decision = current && current.decision !== "cleared" ? current.decision : "pending";
        const tags = Feedback.normalizeStructureTags(
          current?.structureTags
          || item.manualStructureTags
          || this.strategyStructurePrecheck(item),
        );
        const selectedTag = button.dataset.chartStructureTag;
        const nextTags = tags.includes(selectedTag)
          ? tags.filter((tag) => tag !== selectedTag)
          : [...tags, selectedTag];
        this.onFeedback(
          {
            ...item,
            venue: this.venue?.label || "",
            predictedStructureTags: current?.predictedStructureTags || this.strategyStructurePrecheck(item),
          },
          decision,
          null,
          nextTags,
        );
        this.refreshFeedbackPopover();
        this.render();
      }));
      $$('[data-chart-visual-range-adjust]', this.feedbackPopover).forEach((button) => button.addEventListener("click", async () => {
        if (button.dataset.chartVisualRangeAdjust === "expand") this.beginVisualRangeExpansion();
        else await this.adjustVisualRangeSelection(button.dataset.chartVisualRangeAdjust);
      }));
      $("[data-chart-visual-range-commit]", this.feedbackPopover).addEventListener("click", async () => {
        await this.commitVisualRangeExpansion();
      });
      $("[data-chart-visual-range-cancel]", this.feedbackPopover).addEventListener("click", () => {
        this.cancelVisualRangeExpansion({ close: true, restorePan: true });
      });
      $("[data-chart-visual-range-reset]", this.feedbackPopover).addEventListener("click", async () => {
        await this.resetVisualRangeSelection();
      });
      $("[data-chart-feedback-clear]", this.feedbackPopover).addEventListener("click", () => {
        const item = this.selectedFeedbackSignal();
        if (!item || !this.onFeedback) return;
        this.onFeedback({ ...item, venue: this.venue?.label || "" }, "cleared");
        this.closeFeedbackPopover();
      });
    }

    feedbackSignals(includeHiddenRejected = false) {
      if (!this.result) return [];
      return [
        ...(this.result.signals || []),
        ...(this.result.secondaryBreakoutHints || []),
        ...(this.result.pending || []),
        ...((this.showRejected || includeHiddenRejected) ? (this.result.rejected || []) : []),
      ];
    }

    buyMarkerY(signal) {
      if (!this.geometry || !this.result?.candles.length) return 0;
      const { plot, priceBottom, yAt } = this.geometry;
      const row = this.result.candles[signal?.index];
      const anchorPrice = Number(row?.low ?? signal?.price ?? signal?.level);
      // B 固定落在该 K 线最低点下方；不再使用鼠标点击价，避免长阳实体吞掉标记。
      return clamp(yAt(anchorPrice) + 4, plot.top + 4, priceBottom - 22);
    }

    strategyStructurePrecheck(item) {
      const tags = Feedback.inferStructureTags(item);
      (item.visualLearning?.suggestedStructureTags || []).forEach((tag) => tags.push(tag));
      const nearbyStructures = [];
      const structures = this.result?.structures || [];
      // 结构数组按时间生成。只回看目标 K 线附近 12 根，避免在每次打开/刷新
      // 确认框时扫描整段历史里的全部视觉结构。
      for (let cursor = structures.length - 1; cursor >= 0; cursor -= 1) {
        const structure = structures[cursor];
        if (structure.index > item.index) continue;
        if (item.index - structure.index > 12) break;
        if ((structure.triangleLines?.upper?.startIndex ?? structure.index) < item.index) {
          nearbyStructures.push(structure);
        }
      }
      nearbyStructures.forEach((structure) => {
        if (structure.structureShape === "falling-wedge") tags.push("fallingWedge");
        if (["converging-triangle", "ascending-triangle"].includes(structure.structureShape)) tags.push("triangle");
        if (structure.trendline || structure.triangleLines?.upper) tags.push("trendlineBreakout");
      });
      this.manualDrawingStructureFor(item).forEach((analysis) => {
        analysis.tags.forEach((tag) => tags.push(tag));
      });
      return Feedback.normalizeStructureTags(tags);
    }

    applyOptimisticFeedback(item, decision) {
      if (!this.result || !item) return;
      const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
      const withoutKey = (rows = []) => rows.filter((row) => (
        (row.feedbackKey || Feedback.signalKey(this.pair, row)) !== key
      ));
      const record = state.feedback.records?.[key];
      const optimistic = {
        ...item,
        feedbackKey: key,
        manualDecision: decision,
        manualConfirmed: decision === "confirmed",
        manualOverride: decision === "confirmed",
        manualCertaintyGrade: record?.certaintyGrade || item.manualCertaintyGrade || "",
        manualStructureTags: record?.structureTags || item.manualStructureTags || [],
        status: decision === "confirmed" ? "buy" : decision === "pending" ? "pending" : "filtered",
      };
      const signals = withoutKey(this.result.signals);
      const pending = withoutKey(this.result.pending);
      const rejected = withoutKey(this.result.rejected);
      if (decision === "confirmed") signals.push(optimistic);
      else if (decision === "pending") pending.push(optimistic);
      else if (decision === "denied") rejected.push(optimistic);
      this.result = {
        ...this.result,
        signals,
        pending,
        rejected,
        stats: {
          ...(this.result.stats || {}),
          signalCount: signals.length,
          pendingCount: pending.length,
          rejectedCount: rejected.length,
        },
      };
      this.rebuildSignalIndex();
      this.selectedFeedbackItem = optimistic;
    }

    selectedFeedbackSignal() {
      if (!this.selectedFeedbackKey) return null;
      // 盘面点选时已经保存了当前对象。优先复用它，避免确认弹窗每次重绘都
      // 扫描数千条 rejected 记录。
      if (this.selectedFeedbackItem
        && (this.selectedFeedbackItem.feedbackKey || Feedback.signalKey(this.pair, this.selectedFeedbackItem)) === this.selectedFeedbackKey) {
        return this.selectedFeedbackItem;
      }
      const generated = this.feedbackSignals(true).find((item) => (
        (item.feedbackKey || Feedback.signalKey(this.pair, item)) === this.selectedFeedbackKey
      ));
      if (generated) return generated;
      return null;
    }

    visualStructureStartIndex(item, automatic = false) {
      if (!item || !this.result?.candles?.length) return -1;
      const endIndex = Number.isFinite(Number(item.index))
        ? Number(item.index)
        : this.result.candles.findIndex((row) => Number(row.time) === Number(item.time));
      if (endIndex < VISUAL_RANGE_MIN_BARS) return -1;
      if (!automatic && Number.isFinite(Number(item.visualStructureStartTime))) {
        const byTime = this.timeIndex.get(Number(item.visualStructureStartTime)) ?? -1;
        if (byTime >= 0 && byTime <= endIndex - VISUAL_RANGE_MIN_BARS) return byTime;
      }
      const causalContextIndex = Number.isFinite(Number(item.causalContextStartTime))
        ? (this.timeIndex.get(Number(item.causalContextStartTime)) ?? -1)
        : Number(item.causalContextStartIndex);
      const candidate = automatic && Number.isFinite(causalContextIndex) && causalContextIndex >= 0
        ? causalContextIndex
        : !automatic && Number.isFinite(Number(item.visualStructureStartIndex))
        ? Number(item.visualStructureStartIndex)
        : item.visualStructureSource !== "manual" && Number.isFinite(causalContextIndex) && causalContextIndex >= 0
          ? causalContextIndex
          : item.visualStructureSource !== "manual" && Number.isFinite(Number(item.visualStructureStartIndex))
            ? Number(item.visualStructureStartIndex)
          : item.triangleLines?.structureStartIndex
            ?? item.triangleLines?.upper?.startIndex
            ?? item.trendline?.structureStartIndex
            ?? item.trendline?.startIndex
            ?? endIndex - Math.min(Math.max(Number(item.consolidationBars) || 40, VISUAL_RANGE_MIN_BARS), 240);
      return clamp(Math.trunc(candidate), 0, endIndex - VISUAL_RANGE_MIN_BARS);
    }

    visualRangeItem(item, startIndex, source) {
      if (!item || startIndex < 0) return item;
      const endIndex = Number(item.index);
      const visualSignature = Vision.buildVisualSignature(this.result.candles, endIndex, {
        interval: this.interval,
        triggerPrice: item.triggerPrice || item.selectedPrice || item.price || item.level,
        ema90: this.result.indicators?.ema90,
        structureStartIndex: startIndex,
        structureSource: source,
      });
      return {
        ...item,
        visualStructureStartIndex: startIndex,
        visualStructureStartTime: this.result.candles[startIndex]?.time || null,
        visualStructureBars: endIndex - startIndex,
        visualStructureSource: source,
        visualSignature,
      };
    }

    async persistVisualRangeItem(item) {
      if (!item || !this.onFeedback) return;
      const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
      const current = state.feedback.records?.[key];
      const decision = current && current.decision !== "cleared" ? current.decision : "pending";
      const grade = current?.certaintyGrade || item.manualCertaintyGrade || null;
      const tags = current?.structureTags || item.manualStructureTags || this.strategyStructurePrecheck(item);
      this.selectedFeedbackItem = item;
      this.selectedFeedbackKey = key;
      this.feedbackPopover.classList.add("is-saving");
      try {
        await this.onFeedback({
          ...item,
          venue: this.venue?.label || "",
          predictedStructureTags: current?.predictedStructureTags || this.strategyStructurePrecheck(item),
        }, decision, grade, tags);
      } finally {
        this.feedbackPopover.classList.remove("is-saving");
        this.refreshFeedbackPopover();
        this.render();
      }
    }

    async adjustVisualRangeSelection(direction) {
      const item = this.selectedFeedbackSignal();
      if (!item) return;
      const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
      const stored = state.feedback.records?.[key]?.signal || null;
      const currentItem = stored
        ? { ...item, ...stored, index: Number(item.index), time: Number(item.time), feedbackKey: key }
        : item;
      const endIndex = Number(item.index);
      const startIndex = this.visualStructureStartIndex(currentItem);
      if (startIndex < 0 || direction !== "contract") return;
      const currentBars = endIndex - startIndex;
      const step = Math.max(8, Math.round(currentBars * 0.3));
      const nextStart = Math.min(endIndex - VISUAL_RANGE_MIN_BARS, startIndex + step);
      if (nextStart === startIndex) return;
      await this.persistVisualRangeItem(this.visualRangeItem(currentItem, nextStart, "manual"));
    }

    beginVisualRangeExpansion() {
      const item = this.selectedFeedbackSignal();
      if (!item) return;
      const key = item.feedbackKey || Feedback.signalKey(this.pair, item);
      const stored = state.feedback.records?.[key]?.signal || null;
      const currentItem = stored
        ? { ...item, ...stored, index: Number(item.index), time: Number(item.time), feedbackKey: key }
        : item;
      const currentStartIndex = this.visualStructureStartIndex(currentItem);
      if (currentStartIndex <= 0) return;
      this.visualRangeDraft = {
        feedbackKey: key,
        item: currentItem,
        endIndex: Number(item.index),
        currentStartIndex,
        draftStartIndex: null,
      };
      this.canvas.classList.add("is-visual-range-picking");
      this.canvas.classList.remove("is-feedback-target");
      this.showVisualRangeHint("请向左点选结构起始 K 线", 3600);
      this.refreshFeedbackPopover();
      this.render();
    }

    showVisualRangeHint(message, duration = 2600) {
      if (!this.rangeHint) return;
      clearTimeout(this.rangeHintTimer);
      this.rangeHint.textContent = message;
      this.rangeHint.classList.add("is-visible");
      this.rangeHintTimer = setTimeout(() => {
        this.rangeHint.classList.remove("is-visible");
        this.rangeHintTimer = null;
      }, duration);
    }

    hideVisualRangeHint() {
      clearTimeout(this.rangeHintTimer);
      this.rangeHintTimer = null;
      this.rangeHint?.classList.remove("is-visible");
    }

    visualRangeDraftTargetAt(event) {
      if (!this.visualRangeDraft) return null;
      const point = this.hitCandle(event);
      if (!point) return null;
      return point.index < this.visualRangeDraft.currentStartIndex
        && point.index <= this.visualRangeDraft.endIndex - VISUAL_RANGE_MIN_BARS
        ? point
        : null;
    }

    selectVisualRangeDraft(event) {
      const point = this.visualRangeDraftTargetAt(event);
      if (!point || !this.visualRangeDraft) return;
      this.visualRangeDraft.draftStartIndex = point.index;
      this.hoverIndex = point.index;
      this.showVisualRangeHint("起始 K 线已选，请在反馈卡片中确认", 2200);
      this.refreshFeedbackPopover();
      this.render();
    }

    restoreDefaultPanTool() {
      state.drawTool = "pan";
      $$('[data-draw-tool]').forEach((button) => {
        button.classList.toggle("is-active", button.dataset.drawTool === "pan");
      });
      state.charts.forEach((chart) => chart.setDrawTool("pan"));
    }

    cancelVisualRangeExpansion({ close = false, restorePan = false } = {}) {
      this.visualRangeDraft = null;
      this.feedbackPress = null;
      this.canvas.classList.remove("is-visual-range-picking", "is-visual-range-target");
      this.hideVisualRangeHint();
      if (restorePan) this.restoreDefaultPanTool();
      if (close) this.closeFeedbackPopover();
      else {
        this.refreshFeedbackPopover();
        this.render();
      }
    }

    async commitVisualRangeExpansion() {
      const draft = this.visualRangeDraft;
      if (!draft || !Number.isFinite(draft.draftStartIndex)) return;
      await this.persistVisualRangeItem(this.visualRangeItem(draft.item, draft.draftStartIndex, "manual"));
      this.visualRangeDraft = null;
      this.canvas.classList.remove("is-visual-range-picking", "is-visual-range-target");
      this.hideVisualRangeHint();
      this.restoreDefaultPanTool();
      this.closeFeedbackPopover();
    }

    async resetVisualRangeSelection() {
      if (this.visualRangeDraft) this.cancelVisualRangeExpansion();
      const item = this.selectedFeedbackSignal();
      if (!item) return;
      const startIndex = this.visualStructureStartIndex(item, true);
      const source = item.manualCandleSelection ? "auto" : "strategy";
      await this.persistVisualRangeItem(this.visualRangeItem(item, startIndex, source));
    }

    hitFeedbackSignal(event) {
      if (!this.geometry || !this.result?.candles.length) return null;
      const rect = this.canvas.getBoundingClientRect();
      const screenX = event.clientX - rect.left;
      const screenY = event.clientY - rect.top;
      const { start, end, step, xAt, yAt } = this.geometry;
      const candidates = this.feedbackSignals(false).filter((item) => item.index >= start && item.index < end);
      let best = null;
      let bestDistance = Infinity;
      candidates.forEach((item) => {
        const x = xAt(item.index);
        const markerPrice = item.status === "filtered"
          ? this.result.candles[item.index]?.high
          : item.price || item.level;
        const y = item.status === "buy" || item.secondaryBreakoutHint
          ? this.buyMarkerY(item) + 10
          : yAt(markerPrice) + (item.status === "filtered" ? -7 : 0);
        const xDistance = Math.abs(screenX - x);
        const yDistance = Math.abs(screenY - y);
        if (xDistance > Math.max(12, step * 0.8) || yDistance > 18) return;
        const distance = Math.hypot(xDistance, yDistance);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = item;
        }
      });
      return best;
    }

    hitCandle(event) {
      if (!this.geometry || !this.result?.candles.length) return null;
      const point = this.eventPoint(event);
      if (!point) return null;
      const row = this.result.candles[point.index];
      if (!row) return null;
      const rect = this.canvas.getBoundingClientRect();
      const screenX = event.clientX - rect.left;
      const screenY = event.clientY - rect.top;
      const { plot, priceBottom, candleWidth, xAt, yAt } = this.geometry;
      if (screenX < plot.left || screenX > plot.right || screenY < plot.top || screenY > priceBottom) return null;
      const xRadius = Math.max(4, candleWidth / 2 + 3);
      const top = Math.min(yAt(row.high), yAt(row.low)) - 5;
      const bottom = Math.max(yAt(row.high), yAt(row.low)) + 5;
      if (Math.abs(screenX - xAt(point.index)) > xRadius || screenY < top || screenY > bottom) return null;
      return { ...point, row };
    }

    selectFeedbackAt(event) {
      const item = this.hitFeedbackSignal(event);
      if (!item) {
        this.closeFeedbackPopover();
        return;
      }
      this.selectedFeedbackKey = item.feedbackKey || Feedback.signalKey(this.pair, item);
      this.selectedFeedbackItem = item;
      this.hoverIndex = null;
      this.externalTime = null;
      this.tooltip.classList.remove("is-visible");
      this.refreshFeedbackPopover();
      this.render();
    }

    buildManualFeedbackSignal(index, clickedPrice) {
      const row = this.result?.candles?.[index];
      if (!row) return null;
      const sameCandleStrategyItem = [
        ...(this.result.signals || []),
        ...(this.result.pending || []),
        ...(this.result.rejected || []),
        ...(this.result.structures || []),
      ].filter((item) => item.index === index && !item.manualCandleSelection)
        .sort((a, b) => (
          Number(Boolean(b.executionHierarchy?.permit)) - Number(Boolean(a.executionHierarchy?.permit))
          || (b.consolidationBars || 0) - (a.consolidationBars || 0)
          || (b.score || 0) - (a.score || 0)
        ))[0] || null;
      const prior = this.result.candles.slice(Math.max(0, index - 60), index);
      if (!prior.length) return null;
      const priorHigh = Math.max(...prior.map((item) => item.high));
      const priorLow = Math.min(...prior.map((item) => item.low));
      const meanClose = prior.reduce((sum, item) => sum + item.close, 0) / prior.length;
      const priorRangePercent = (priorHigh - priorLow) / Math.max(meanClose, 1e-8) * 100;
      const priorDriftPercent = Math.abs(prior.at(-1).close - prior[0].close) / Math.max(meanClose, 1e-8) * 100;
      const volumeWindow = prior.slice(-21, -1);
      const meanVolume = volumeWindow.length
        ? volumeWindow.reduce((sum, item) => sum + item.volume, 0) / volumeWindow.length
        : prior.at(-1).volume;
      const priorVolumeRatio = prior.at(-1).volume / Math.max(meanVolume, 1e-8);
      const decisionIndex = Math.max(0, index - 1);
      const ema90AtDecision = Number(this.result.indicators?.ema90?.[decisionIndex]) || 0;
      const atrAtDecision = Number(this.result.indicators?.atr?.[decisionIndex]) || Math.max((priorHigh - priorLow) / Math.max(prior.length, 1), 1e-8);
      const selectedPrice = clamp(Number(clickedPrice) || row.open, row.low, row.high);
      const consolidated = prior.length >= 20 && priorRangePercent <= 12 && priorDriftPercent <= 6;
      const breaksPriorHigh = row.open <= priorHigh && Math.max(row.open, selectedPrice) >= priorHigh;
      const foundationTypes = consolidated ? ["base"] : ["manualReview"];
      const auxiliaryTypes = breaksPriorHigh ? ["previousHigh"] : [];
      const orderedHighs = prior.map((item) => item.high).sort((a, b) => a - b);
      const edgePosition = (orderedHighs.length - 1) * 0.9;
      const edgeLower = Math.floor(edgePosition);
      const edgeUpper = Math.ceil(edgePosition);
      const outerEdgeLevel = orderedHighs[edgeLower]
        + (orderedHighs[edgeUpper] - orderedHighs[edgeLower]) * (edgePosition - edgeLower);
      const edgeTolerance = Math.max(atrAtDecision * 0.35, outerEdgeLevel * 0.0015);
      const touchIndexes = prior
        .map((item, priorIndex) => (item.high >= outerEdgeLevel - edgeTolerance ? priorIndex : -1))
        .filter((priorIndex) => priorIndex >= 0);
      const touchGroups = touchIndexes.reduce((groups, priorIndex) => {
        if (!groups.length || priorIndex - groups.at(-1).at(-1) > 2) groups.push([priorIndex]);
        else groups.at(-1).push(priorIndex);
        return groups;
      }, []);
      const bodyContainment = prior.filter((item) => Math.max(item.open, item.close) <= outerEdgeLevel + edgeTolerance).length
        / Math.max(prior.length, 1);
      const recentRanges = prior.slice(-10).map((item) => Math.max(item.high - item.low, 1e-8));
      const earlierRanges = prior.slice(Math.max(0, prior.length - 30), Math.max(0, prior.length - 10))
        .map((item) => Math.max(item.high - item.low, 1e-8));
      const recentRangeMean = recentRanges.reduce((sum, value) => sum + value, 0) / Math.max(recentRanges.length, 1);
      const earlierRangeMean = earlierRanges.reduce((sum, value) => sum + value, 0) / Math.max(earlierRanges.length, 1);
      const compressionRatioAtDecision = recentRangeMean / Math.max(earlierRangeMean, 1e-8);
      const launchDistancePercent = Math.max(0, (outerEdgeLevel - row.open) / Math.max(row.open, 1e-8) * 100);
      const outerEdgeConfirmed = consolidated && touchGroups.length >= 2 && bodyContainment >= 0.9;
      const outerEdgeScore = Math.round(clamp(42
        + touchGroups.length * 10
        + bodyContainment * 25
        + Number(compressionRatioAtDecision <= 1) * 8, 0, 100));
      const rhythmScore = Math.round(clamp(82
        - priorDriftPercent * 3
        - Math.max(0, compressionRatioAtDecision - 1) * 24
        + Number(compressionRatioAtDecision <= 0.85) * 7, 0, 100));
      const orderFlowScore = Math.round(clamp(48
        + Math.min(priorVolumeRatio, 2) * 12
        + Number(prior.at(-1).close > prior.at(-1).open) * 7, 0, 100));
      const certaintyScore = Math.round(clamp(45
        + Number(outerEdgeConfirmed) * 16
        + Number(breaksPriorHigh) * 14
        + Number(row.open >= ema90AtDecision) * 8
        + Number(launchDistancePercent <= 7) * 8
        + Number(compressionRatioAtDecision <= 1) * 7, 0, 100));
      const contextTokens = [
        "manual-missed",
        consolidated ? "prior-consolidation" : "non-base-context",
        breaksPriorHigh ? "prior-high-cross" : "inside-prior-range",
        row.open >= ema90AtDecision ? "above-ema90" : "below-ema90",
        priorVolumeRatio >= 1.35 ? "prior-volume-expansion" : "normal-prior-volume",
        touchGroups.length >= 2 ? "repeated-outer-edge" : "single-outer-edge",
        compressionRatioAtDecision <= 0.85 ? "pre-break-compression" : "normal-pre-break-range",
        launchDistancePercent <= 7 ? "attached-to-trigger-edge" : "far-from-trigger-edge",
      ];
      const inferredCausalStart = sameCandleStrategyItem?.causalContextStartIndex
        ?? sameCandleStrategyItem?.impulseStartIndex
        ?? sameCandleStrategyItem?.visualStructureStartIndex;
      const fallbackVisualStart = index - (consolidated ? prior.length : Math.min(40, index));
      const visualStructureStartIndex = clamp(
        Number.isFinite(Number(inferredCausalStart)) ? Math.trunc(Number(inferredCausalStart)) : fallbackVisualStart,
        0,
        Math.max(0, index - VISUAL_RANGE_MIN_BARS),
      );
      const visualSignature = Vision.buildVisualSignature(this.result.candles, index, {
        interval: this.interval,
        triggerPrice: selectedPrice,
        ema90: this.result.indicators?.ema90,
        structureStartIndex: visualStructureStartIndex,
        structureSource: "auto",
      });
      return {
        id: `manual-${this.interval}-${row.time}`,
        time: row.time,
        decisionTime: row.time,
        interval: this.interval,
        index,
        pattern: "人工补标 · 遗漏起爆点",
        patternKey: "manualMissed",
        foundationTypes,
        auxiliaryTypes,
        confluence: [...foundationTypes, ...auxiliaryTypes],
        status: "pending",
        manualCandleSelection: true,
        manualSource: "chart-candle-picker",
        featureCutoff: "selected-candle-intrabar-no-future-bars",
        contextTokens,
        visualSignature,
        visualStructureStartIndex,
        visualStructureStartTime: this.result.candles[visualStructureStartIndex]?.time || null,
        visualStructureBars: index - visualStructureStartIndex,
        visualStructureSource: "auto",
        structureStartIndex: sameCandleStrategyItem?.structureStartIndex
          ?? sameCandleStrategyItem?.triangleLines?.structureStartIndex
          ?? sameCandleStrategyItem?.horizontalStructureStartIndex
          ?? null,
        impulseStartIndex: sameCandleStrategyItem?.impulseStartIndex ?? null,
        impulseStartTime: sameCandleStrategyItem?.impulseStartTime ?? null,
        causalContextStartIndex: visualStructureStartIndex,
        causalContextStartTime: this.result.candles[visualStructureStartIndex]?.time || null,
        price: selectedPrice,
        selectedPrice,
        triggerPrice: selectedPrice,
        level: selectedPrice,
        stop: row.low,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        ema90AtDecision,
        atrAtDecision,
        priorHighAtDecision: priorHigh,
        priorLowAtDecision: priorLow,
        priorRangePercent,
        priorDriftPercent,
        priorVolumeRatio,
        relativeVolume: priorVolumeRatio,
        consolidationBars: consolidated ? prior.length : 0,
        outerEdgeType: "manual-causal-platform",
        outerEdgeLevel,
        outerEdgeConfirmed,
        outerEdgeScore,
        ceilingAge: touchIndexes.length ? prior.length - 1 - touchIndexes[0] : 0,
        ceilingTouches: touchIndexes.length,
        platformTouchGroups: touchGroups.length,
        launchDistancePercent,
        compressionRatioAtDecision,
        rhythmScore,
        structureQuality: clamp(
          bodyContainment * 0.55
          + Math.min(touchGroups.length / 3, 1) * 0.25
          + Number(compressionRatioAtDecision <= 1) * 0.2,
          0,
          1,
        ),
        certaintyScore,
        orderFlowScore,
        score: certaintyScore,
        aboveEma90: row.open >= ema90AtDecision,
        breaksPriorHigh,
        evidence: [
          "用户从盘面直接选择遗漏 K 线，等待人工确认",
          `仅保存该 K 线及此前可见上下文；前序振幅 ${priorRangePercent.toFixed(2)}%`,
          `因果结构快照：外沿触点组 ${touchGroups.length}，主体包络 ${(bodyContainment * 100).toFixed(1)}%，压缩比 ${compressionRatioAtDecision.toFixed(2)}`,
          breaksPriorHigh ? "所选 K 线盘中越过此前 60 根最高点" : "所选 K 线位于此前结构区间内",
        ],
        reasons: [],
      };
    }

    selectCandleFeedbackAt(event) {
      const point = this.hitCandle(event);
      if (!point) return;
      // 隐藏的过滤噪声不能抢占人工补标；只复用盘面当前真正可见的策略标记。
      const existing = this.feedbackSignals(false).find((item) => item.index === point.index);
      const item = existing || this.buildManualFeedbackSignal(point.index, point.price);
      if (!item || !signalDisplayAllowed(item, this.pair)) return;
      this.selectedFeedbackKey = item.feedbackKey || Feedback.signalKey(this.pair, item);
      this.selectedFeedbackItem = item;
      this.hoverIndex = null;
      this.externalTime = null;
      this.tooltip.classList.remove("is-visible");
      this.refreshFeedbackPopover();
      this.render();
    }

    closeFeedbackPopover() {
      this.visualRangeDraft = null;
      this.feedbackPress = null;
      this.canvas?.classList.remove("is-visual-range-picking", "is-visual-range-target");
      this.hideVisualRangeHint();
      this.selectedFeedbackKey = "";
      this.selectedFeedbackItem = null;
      this.feedbackPopover?.classList.remove("is-visible", "is-saving");
      this.feedbackPopover?.setAttribute("aria-hidden", "true");
      this.render();
    }

    refreshFeedbackPopover() {
      if (!this.feedbackPopover) return;
      const item = this.selectedFeedbackSignal();
      if (!item) {
        this.selectedFeedbackKey = "";
        this.feedbackPopover.classList.remove("is-visible");
        this.feedbackPopover.setAttribute("aria-hidden", "true");
        return;
      }
      const storedRecord = state.feedback.records?.[this.selectedFeedbackKey] || null;
      const record = storedRecord?.decision === "cleared" ? null : storedRecord;
      $("[data-chart-feedback-title]", this.feedbackPopover).textContent = `${item.interval} · ${item.pattern}`;
      $("[data-chart-feedback-meta]", this.feedbackPopover).textContent = `${formatDateTime(item.time, true)} · ${formatPrice(item.price || item.level)}`;
      const visualStartIndex = this.visualStructureStartIndex(record?.signal || item);
      const activeDraft = this.visualRangeDraft?.feedbackKey === this.selectedFeedbackKey
        ? this.visualRangeDraft
        : null;
      const displayedStartIndex = Number.isFinite(activeDraft?.draftStartIndex)
        ? activeDraft.draftStartIndex
        : visualStartIndex;
      const visualBars = displayedStartIndex >= 0 ? Number(item.index) - displayedStartIndex : 0;
      const visualSource = String(record?.signal?.visualStructureSource || item.visualStructureSource || "auto");
      $("[data-chart-visual-range-label]", this.feedbackPopover).textContent = displayedStartIndex >= 0
        ? `${formatDateTime(this.result.candles[displayedStartIndex]?.time, true)} 起 · ${visualBars} 根 · ${activeDraft ? "待确认预览" : visualSource === "manual" ? "人工范围" : "自动范围"}`
        : "历史不足，暂不能建立结构范围";
      $$('[data-chart-visual-range-adjust]', this.feedbackPopover).forEach((button) => {
        button.disabled = Boolean(activeDraft) || visualStartIndex < 0
          || (button.dataset.chartVisualRangeAdjust === "expand"
            ? visualStartIndex <= 0
            : visualBars <= VISUAL_RANGE_MIN_BARS);
      });
      $("[data-chart-visual-range-reset]", this.feedbackPopover).disabled = Boolean(activeDraft) || visualStartIndex < 0 || visualSource !== "manual";
      const draftPanel = $("[data-chart-visual-range-confirm]", this.feedbackPopover);
      draftPanel.hidden = !activeDraft;
      if (activeDraft) {
        $("[data-chart-visual-range-draft-label]", draftPanel).textContent = Number.isFinite(activeDraft.draftStartIndex)
          ? `已选 ${formatDateTime(this.result.candles[activeDraft.draftStartIndex]?.time, true)} · 共 ${visualBars} 根`
          : "请在当前起点左侧点选目标K线";
        $("[data-chart-visual-range-commit]", draftPanel).disabled = !Number.isFinite(activeDraft.draftStartIndex);
      }
      $("[data-chart-feedback-detail]", this.feedbackPopover).textContent = record?.decision === "confirmed"
        ? "已作为正样本永久锁定；下轮优化会提升同类结构权重。"
        : record?.decision === "pending"
          ? "已标记待定；永久保留复盘状态，但不进入正负样本学习。"
        : record?.decision === "denied"
          ? "已彻底否定并进入永久黑名单；后续策略调整不得重新加入。"
          : item.visualPreconfirmed
            ? "视觉原型认为这是高相似结构；目前只标记V，需由你确认后才进入正样本。"
          : item.manualCandleSelection
            ? "这是从 K 线直接补标的遗漏点；确认后会永久显示，并作为下一轮优化正样本。"
          : item.status === "filtered"
            ? `策略当前过滤：${item.reasons?.[0] || "确定性不足"}`
            : "直接确认或否定；结果会同步到右侧记录并永久保存。";
      const visualLearning = item.visualLearning || record?.signal?.visualLearning;
      const visualPanel = $("[data-chart-visual-learning]", this.feedbackPopover);
      visualPanel.hidden = !visualLearning?.positiveSampleCount;
      if (!visualPanel.hidden) {
        $("[data-chart-visual-score]", visualPanel).textContent = `正样本 ${visualLearning.positiveSimilarity}% · 反例 ${visualLearning.negativeSimilarity}%`;
        const visualTagText = (visualLearning.suggestedStructureTags || []).length
          ? `；视觉结构 ${visualLearning.suggestedStructureTags.map((tag) => STRUCTURE_TAG_LABELS[tag] || tag).join(" + ")}（${visualLearning.structureTagConfidence}%）`
          : "";
        $("[data-chart-visual-detail]", visualPanel).textContent = `${visualLearning.positivePairCount} 个龙头 / ${visualLearning.positiveSampleCount} 个正样本${visualTagText}；${visualLearning.reason}`;
      }
      $$('[data-chart-feedback-action]', this.feedbackPopover).forEach((button) => {
        const active = record?.decision === button.dataset.chartFeedbackAction;
        button.classList.toggle("is-active", active);
        button.textContent = button.dataset.chartFeedbackAction === "confirmed"
          ? (active ? "已确认" : "确认")
          : button.dataset.chartFeedbackAction === "pending"
            ? (active ? "待定中" : "待定")
            : (active ? "已彻底否定" : "彻底否定");
      });
      $$('[data-chart-certainty-grade]', this.feedbackPopover).forEach((button) => {
        button.classList.toggle("is-active", record?.certaintyGrade === button.dataset.chartCertaintyGrade);
      });
      const structureTags = Feedback.normalizeStructureTags(record?.structureTags || item.manualStructureTags);
      const predictedStructureTags = Feedback.normalizeStructureTags(
        record?.predictedStructureTags || item.predictedStructureTags || this.strategyStructurePrecheck(item),
      );
      const structureReview = record?.structureReview
        || (record ? Feedback.compareStructureTags(predictedStructureTags, structureTags) : null);
      const structureLabel = (tag) => STRUCTURE_TAG_LABELS[tag] || tag;
      const predictionLabel = predictedStructureTags.length
        ? predictedStructureTags.map(structureLabel).join(" + ")
        : "暂未形成明确结构判断";
      $("[data-chart-structure-prediction]", this.feedbackPopover).textContent = predictionLabel;
      const reviewRow = $("[data-chart-structure-review]", this.feedbackPopover);
      if (!record) {
        reviewRow.textContent = "浅色虚线为策略预确认；可直接确认，也可点选修正。";
        reviewRow.className = "is-pending";
      } else if (structureReview?.exact) {
        reviewRow.textContent = "人工校验与策略预确认完全一致。";
        reviewRow.className = "is-match";
      } else {
        const added = (structureReview?.addedByUser || []).map(structureLabel);
        const removed = (structureReview?.removedByUser || []).map(structureLabel);
        reviewRow.textContent = [
          added.length ? `人工补充：${added.join("、")}` : "",
          removed.length ? `人工纠正：去除 ${removed.join("、")}` : "",
        ].filter(Boolean).join("；") || "已完成人工校验。";
        reviewRow.className = "is-diff";
      }
      $$('[data-chart-structure-tag]', this.feedbackPopover).forEach((button) => {
        button.classList.toggle("is-suggested", predictedStructureTags.includes(button.dataset.chartStructureTag));
        button.classList.toggle("is-active", structureTags.includes(button.dataset.chartStructureTag));
        button.title = predictedStructureTags.includes(button.dataset.chartStructureTag)
          ? "策略预确认结构；点击可人工增删"
          : "点击加入人工结构确认";
      });
      $("[data-chart-feedback-clear]", this.feedbackPopover).hidden = !record;
      this.feedbackPopover.classList.add("is-visible");
      this.feedbackPopover.setAttribute("aria-hidden", "false");
      requestAnimationFrame(() => this.positionFeedbackPopover());
    }

    positionFeedbackPopover() {
      const item = this.selectedFeedbackSignal();
      if (!item || !this.geometry || !this.feedbackPopover?.classList.contains("is-visible")) return;
      const markerPrice = item.price || item.level || this.result.candles[item.index]?.close;
      const anchorX = this.geometry.xAt(item.index);
      const anchorY = this.geometry.yAt(markerPrice);
      const width = this.feedbackPopover.offsetWidth || 286;
      const height = this.feedbackPopover.offsetHeight || 142;
      const surfaceWidth = this.surface.clientWidth;
      const surfaceHeight = this.surface.clientHeight;
      const left = clamp(anchorX + 15, 8, Math.max(8, surfaceWidth - width - 8));
      const above = anchorY - height - 14;
      const top = clamp(above >= 8 ? above : anchorY + 18, 8, Math.max(8, surfaceHeight - height - 8));
      this.feedbackPopover.style.left = `${Math.round(left)}px`;
      this.feedbackPopover.style.top = `${Math.round(top)}px`;
    }

    setDrawTool(tool) {
      this.drawTool = tool;
      this.drawStart = null;
      this.drawPreview = null;
      this.drag = null;
      this.card.classList.toggle("is-drawing", !["pan", "feedback"].includes(tool));
      this.card.classList.toggle("is-feedback-picking", tool === "feedback");
      this.canvas.classList.remove("is-feedback-target");
      this.tooltip.classList.remove("is-visible");
      if (tool !== "pan") this.closeFeedbackPopover();
      this.render();
    }

    clearAnnotations() {
      this.annotations.splice(0, this.annotations.length);
      this.drawStart = null;
      this.drawPreview = null;
      this.persistAnnotations();
      this.refreshFeedbackPopover();
      this.render();
    }

    loadPersistedAnnotations(key) {
      try {
        const document = JSON.parse(localStorage.getItem(DRAWING_STORAGE_KEY) || "{}");
        const rows = Array.isArray(document?.[key]) ? document[key] : [];
        return rows.slice(-80).filter((item) => (
          item && ["trend", "horizontal", "ellipse"].includes(item.type)
          && Number.isFinite(Number(item.start?.time))
          && Number.isFinite(Number(item.start?.price))
          && Number.isFinite(Number(item.end?.time))
          && Number.isFinite(Number(item.end?.price))
        ));
      } catch (_error) {
        return [];
      }
    }

    persistAnnotations() {
      if (!this.annotationKey) return;
      try {
        const document = JSON.parse(localStorage.getItem(DRAWING_STORAGE_KEY) || "{}");
        document[this.annotationKey] = this.annotations.slice(-80).map((annotation) => ({
          ...annotation,
          preview: false,
        }));
        localStorage.setItem(DRAWING_STORAGE_KEY, JSON.stringify(document));
      } catch (_error) {
        // 浏览器存储不可用时仍保留本次会话内画线，不影响看盘。
      }
    }

    eventPoint(event) {
      if (!this.geometry || !this.result?.candles.length) return null;
      const rect = this.canvas.getBoundingClientRect();
      const { plot, priceBottom, priceMax, priceRange, start, end, step } = this.geometry;
      const screenX = clamp(event.clientX - rect.left, plot.left, plot.right);
      const screenY = clamp(event.clientY - rect.top, plot.top, priceBottom);
      const absoluteIndex = clamp(start + Math.floor((screenX - plot.left) / Math.max(step, 1e-8)), start, end - 1);
      const priceRatio = (screenY - plot.top) / Math.max(priceBottom - plot.top, 1);
      return {
        index: absoluteIndex,
        time: this.result.candles[absoluteIndex].time,
        price: priceMax - priceRatio * priceRange,
        screenX,
        screenY,
      };
    }

    snapDrawingPoint(point) {
      if (!point || !this.geometry || !this.result?.candles.length) return point;
      const rows = this.result.candles;
      const fields = ["high", "low", "close", "open"];
      const candidates = [];
      for (let cursor = Math.max(0, point.index - 1); cursor <= Math.min(rows.length - 1, point.index + 1); cursor += 1) {
        fields.forEach((anchorType) => {
          const price = Number(rows[cursor][anchorType]);
          if (!Number.isFinite(price)) return;
          const x = this.geometry.xAt(cursor);
          const y = this.geometry.yAt(price);
          candidates.push({
            index: cursor,
            time: rows[cursor].time,
            price,
            anchorType,
            anchorDistancePx: Math.hypot(point.screenX - x, point.screenY - y),
            screenX: x,
            screenY: y,
          });
        });
      }
      const nearest = candidates.sort((a, b) => a.anchorDistancePx - b.anchorDistancePx)[0];
      return nearest && nearest.anchorDistancePx <= 24
        ? nearest
        : { ...point, anchorType: "free", anchorDistancePx: nearest?.anchorDistancePx ?? null };
    }

    classifyManualTrendPair(first, second) {
      if (!this.result?.candles.length || !first || !second) return null;
      const endpointIndex = (point) => this.nearestIndex(point?.time);
      const firstIndices = [endpointIndex(first.start), endpointIndex(first.end)].sort((a, b) => a - b);
      const secondIndices = [endpointIndex(second.start), endpointIndex(second.end)].sort((a, b) => a - b);
      if (firstIndices.some((value) => value == null) || secondIndices.some((value) => value == null)) return null;
      const overlapStart = Math.max(firstIndices[0], secondIndices[0]);
      const overlapEnd = Math.min(firstIndices[1], secondIndices[1]);
      const overlapBars = overlapEnd - overlapStart;
      const shorterSpan = Math.max(1, Math.min(firstIndices[1] - firstIndices[0], secondIndices[1] - secondIndices[0]));
      if (overlapBars < 12 || overlapBars / shorterSpan < 0.55) return null;

      const lineAt = (annotation, absoluteIndex) => {
        const startIndex = endpointIndex(annotation.start);
        const endIndex = endpointIndex(annotation.end);
        if (startIndex === endIndex) return Number(annotation.start.price);
        const ratio = (absoluteIndex - startIndex) / (endIndex - startIndex);
        return Number(annotation.start.price) + (Number(annotation.end.price) - Number(annotation.start.price)) * ratio;
      };
      const middleIndex = Math.round((overlapStart + overlapEnd) / 2);
      const firstMiddle = lineAt(first, middleIndex);
      const secondMiddle = lineAt(second, middleIndex);
      const upper = firstMiddle >= secondMiddle ? first : second;
      const lower = upper === first ? second : first;
      const upperStart = lineAt(upper, overlapStart);
      const upperEnd = lineAt(upper, overlapEnd);
      const lowerStart = lineAt(lower, overlapStart);
      const lowerEnd = lineAt(lower, overlapEnd);
      const startWidth = upperStart - lowerStart;
      const endWidth = upperEnd - lowerEnd;
      const atrValue = Math.max(Number(this.result.indicators?.atr?.[overlapEnd]) || 0, Math.abs(startWidth) * 0.03, 1e-8);
      if (startWidth <= atrValue * 0.35 || endWidth <= atrValue * 0.18) return null;
      const upperSlope = (upperEnd - upperStart) / overlapBars;
      const lowerSlope = (lowerEnd - lowerStart) / overlapBars;
      const flatTolerance = atrValue * 0.022;
      let structureShape = "";
      let label = "";
      let tags = [];
      if (Math.abs(upperSlope) <= flatTolerance && lowerSlope > flatTolerance) {
        structureShape = "ascending-triangle";
        label = "上升三角";
        tags = ["triangle", "trendlineBreakout"];
      } else if (upperSlope < -flatTolerance && lowerSlope > flatTolerance) {
        structureShape = "converging-triangle";
        label = "对称三角";
        tags = ["triangle", "trendlineBreakout"];
      } else if (upperSlope < -flatTolerance
        && lowerSlope < flatTolerance
        && lowerSlope > upperSlope + flatTolerance
        && endWidth < startWidth * 0.82) {
        structureShape = "falling-wedge";
        label = "下降楔形";
        tags = ["fallingWedge", "trendlineBreakout"];
      } else if (Math.abs(upperSlope) <= flatTolerance && Math.abs(lowerSlope) <= flatTolerance) {
        structureShape = "box";
        label = "箱体";
        tags = ["box", "consolidationBreakout"];
      } else if (upperSlope < lowerSlope - flatTolerance && endWidth < startWidth * 0.82) {
        structureShape = "converging-structure";
        label = "收敛结构";
        tags = ["triangle", "trendlineBreakout"];
      } else return null;

      const rows = this.result.candles.slice(overlapStart, overlapEnd + 1);
      let bodyInside = 0;
      let upperTouches = 0;
      let lowerTouches = 0;
      rows.forEach((row, offset) => {
        const absoluteIndex = overlapStart + offset;
        const ceiling = lineAt(upper, absoluteIndex);
        const floor = lineAt(lower, absoluteIndex);
        const bodyHigh = Math.max(row.open, row.close);
        const bodyLow = Math.min(row.open, row.close);
        if (bodyHigh <= ceiling + atrValue * 0.2 && bodyLow >= floor - atrValue * 0.2) bodyInside += 1;
        if (Math.abs(row.high - ceiling) <= atrValue * 0.42) upperTouches += 1;
        if (Math.abs(row.low - floor) <= atrValue * 0.42) lowerTouches += 1;
      });
      const coverage = bodyInside / Math.max(rows.length, 1);
      const priorRows = this.result.candles.slice(Math.max(0, overlapStart - 36), overlapStart + 1);
      const priorAdvanceAtr = priorRows.length
        ? (priorRows.at(-1).close - Math.min(...priorRows.map((row) => row.low))) / atrValue
        : 0;
      const priorAdvanceQualified = priorAdvanceAtr >= 0.75;
      const score = Math.round(clamp(
        coverage * 65
        + Math.min(upperTouches + lowerTouches, 8) / 8 * 20
        + Number(priorAdvanceQualified) * 15,
        0,
        100,
      ));
      return {
        id: `manual-structure-${Math.min(first.createdAt || 0, second.createdAt || 0)}-${Math.max(first.createdAt || 0, second.createdAt || 0)}`,
        source: "manual-drawing-learning",
        structureShape,
        label,
        tags,
        startIndex: overlapStart,
        endIndex: overlapEnd,
        startTime: this.result.candles[overlapStart]?.time || null,
        endTime: this.result.candles[overlapEnd]?.time || null,
        bars: overlapBars + 1,
        coverage,
        upperTouches,
        lowerTouches,
        priorAdvanceAtr,
        priorAdvanceQualified,
        upperSlopeAtrPerBar: upperSlope / atrValue,
        lowerSlopeAtrPerBar: lowerSlope / atrValue,
        score,
        causal: true,
        anchorLineIds: [upper.id, lower.id],
        labelOwnerId: upper.id,
      };
    }

    refreshManualDrawingStructures() {
      const trends = this.annotations.filter((annotation) => annotation.type === "trend");
      trends.forEach((annotation) => { delete annotation.analysis; });
      for (let right = 1; right < trends.length; right += 1) {
        for (let left = 0; left < right; left += 1) {
          const analysis = this.classifyManualTrendPair(trends[left], trends[right]);
          if (!analysis) continue;
          [trends[left], trends[right]].forEach((annotation) => {
            if (!annotation.analysis || analysis.score > annotation.analysis.score) annotation.analysis = analysis;
          });
        }
      }
    }

    manualDrawingStructureFor(item) {
      if (!item || !Number.isFinite(Number(item.index))) return [];
      const analyses = [...new Map(this.annotations
        .map((annotation) => annotation.analysis)
        .filter((analysis) => analysis
          && item.index >= analysis.startIndex
          && item.index <= analysis.endIndex + 12)
        .map((analysis) => [analysis.id, analysis])).values()];
      return analyses.sort((a, b) => b.score - a.score);
    }

    beginDrawing(event) {
      const point = this.eventPoint(event);
      if (!point) return;
      if (this.drawTool === "horizontal") {
        const snapped = this.snapDrawingPoint(point);
        this.annotations.push({ id: `horizontal-${Date.now()}`, createdAt: Date.now(), type: "horizontal", start: snapped, end: snapped });
        this.persistAnnotations();
        this.render();
        return;
      }
      if (this.drawTool === "erase") {
        this.eraseNearest(point);
        return;
      }
      this.drawStart = this.drawTool === "trend" ? this.snapDrawingPoint(point) : point;
      this.drawPreview = { type: this.drawTool, start: this.drawStart, end: point, preview: true };
    }

    finishDrawing(event) {
      const point = this.eventPoint(event);
      if (point && this.drawStart && ["trend", "ellipse"].includes(this.drawTool)) {
        const createdAt = Date.now();
        const end = this.drawTool === "trend" ? this.snapDrawingPoint(point) : point;
        this.annotations.push({ id: `${this.drawTool}-${createdAt}`, createdAt, type: this.drawTool, start: this.drawStart, end });
        this.refreshManualDrawingStructures();
        this.persistAnnotations();
      }
      this.drawStart = null;
      this.drawPreview = null;
      this.refreshFeedbackPopover();
      this.render();
    }

    annotationScreenPoint(point) {
      if (!point || !this.geometry) return null;
      const index = this.nearestIndex(point.time);
      if (index == null) return null;
      return {
        x: this.geometry.xAt(index),
        y: this.geometry.yAt(point.price),
      };
    }

    eraseNearest(point) {
      if (!this.annotations.length) return;
      let bestIndex = -1;
      let bestDistance = Infinity;
      this.annotations.forEach((annotation, index) => {
        const start = this.annotationScreenPoint(annotation.start);
        const end = this.annotationScreenPoint(annotation.end);
        if (!start || !end) return;
        let distance;
        if (annotation.type === "horizontal") distance = Math.abs(point.screenY - start.y);
        else {
          const centerX = (start.x + end.x) / 2;
          const centerY = (start.y + end.y) / 2;
          distance = Math.min(
            Math.hypot(point.screenX - start.x, point.screenY - start.y),
            Math.hypot(point.screenX - end.x, point.screenY - end.y),
            Math.hypot(point.screenX - centerX, point.screenY - centerY),
          );
        }
        if (distance < bestDistance) {
          bestDistance = distance;
          bestIndex = index;
        }
      });
      if (bestIndex >= 0 && bestDistance <= 18) {
        this.annotations.splice(bestIndex, 1);
        this.refreshManualDrawingStructures();
        this.persistAnnotations();
        this.refreshFeedbackPopover();
      }
      this.render();
    }

    visibleBounds() {
      const length = this.result?.candles.length || 0;
      const count = Math.min(this.visibleCount, length);
      const end = clamp(length - this.offset, count, length);
      return { start: Math.max(0, end - count), end };
    }

    resize() {
      const rect = this.surface.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(rect.width * dpr));
      const height = Math.max(1, Math.round(rect.height * dpr));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.render();
    }

    setLoading(message = "读取公开行情") {
      this.closeFeedbackPopover();
      this.result = null;
      this.empty.className = "chart-empty is-loading";
      $("span", this.empty).textContent = message;
      this.tooltip.classList.remove("is-visible");
      this.updateHeader(null, null);
      this.render();
    }

    setError(message) {
      this.closeFeedbackPopover();
      this.result = null;
      this.empty.className = "chart-empty is-error";
      $("span", this.empty).textContent = message;
      this.updateHeader(null, null, true);
      this.render();
    }

    setData(result, venue, focusTime, pair) {
      if (this.visualRangeDraft) {
        this.visualRangeDraft = null;
        this.feedbackPress = null;
        this.canvas.classList.remove("is-visual-range-picking", "is-visual-range-target");
      }
      if (this.result?.interval) {
        this.viewStates.set(this.result.interval, { visibleCount: this.visibleCount, offset: this.offset });
      }
      this.result = result;
      this.timeIndex = new Map(result.candles.map((row, index) => [Number(row.time), index]));
      this.venue = venue;
      this.pair = pair || "";
      this.interval = result.interval;
      if (this.selectedFeedbackItem && this.selectedFeedbackItem.interval !== result.interval) {
        this.selectedFeedbackKey = "";
        this.selectedFeedbackItem = null;
      }
      this.card.dataset.interval = result.interval;
      this.annotationKey = `${pair || "default"}:${result.interval}`;
      if (!this.annotationSets.has(this.annotationKey)) {
        this.annotationSets.set(this.annotationKey, this.loadPersistedAnnotations(this.annotationKey));
      }
      this.annotations = this.annotationSets.get(this.annotationKey);
      this.refreshManualDrawingStructures();
      this.rebuildSignalIndex();
      const savedView = this.viewStates.get(result.interval);
      this.visibleCount = savedView?.visibleCount || INITIAL_VISIBLE_COUNTS[this.interval] || 140;
      const focusIndex = Number.isFinite(focusTime) ? this.nearestIndex(focusTime) : result.candles.length - 1;
      this.focusIndex = focusIndex;
      const count = Math.min(this.visibleCount, result.candles.length);
      const desiredEnd = clamp((focusIndex ?? result.candles.length - 1) + Math.round(count * 0.34), count, result.candles.length);
      this.offset = savedView
        ? clamp(savedView.offset, 0, Math.max(0, result.candles.length - count))
        : Math.max(0, result.candles.length - desiredEnd);
      this.hoverIndex = null;
      this.externalTime = null;
      this.empty.className = "chart-empty is-hidden";
      this.updateHeader(result, venue);
      this.resize();
      this.refreshFeedbackPopover();
    }

    setRejectedVisibility(visible) {
      this.showRejected = visible;
      this.rebuildSignalIndex();
      this.render();
    }

    rebuildSignalIndex() {
      this.signalByIndex = new Map();
      if (!this.result) return;
      const rows = [
        ...(this.result.signals || []),
        ...(this.result.secondaryBreakoutHints || []),
        ...(this.result.pending || []),
        ...(this.showRejected ? (this.result.rejected || []) : []),
      ];
      rows.forEach((item) => {
        if (!this.signalByIndex.has(item.index)) this.signalByIndex.set(item.index, item);
      });
    }

    setExternalTime(time, source) {
      if (source === this || !this.result?.candles.length) return;
      this.externalTime = time;
      this.render();
    }

    updateHeader(result, venue, error = false) {
      const regime = $("[data-regime]", this.card);
      const source = $("[data-source]", this.card);
      const price = $("[data-last-price]", this.card);
      const signalCount = $("[data-signal-count]", this.card);
      const filterCount = $("[data-filter-count]", this.card);
      regime.className = "";
      if (error) {
        regime.textContent = "数据缺失";
        regime.classList.add("is-blocked");
      } else if (!result) {
        regime.textContent = "读取中";
        source.textContent = "—";
        price.textContent = "—";
        signalCount.textContent = "0 确认 · 0 候选";
        filterCount.textContent = "0 过滤";
      } else {
        const regimeIndex = this.focusIndex ?? result.candles.length - 1;
        const close = result.candles[regimeIndex]?.close;
        const ema90 = result.indicators.ema90[regimeIndex];
        const priorEma90 = result.indicators.ema90[Math.max(0, regimeIndex - 4)];
        const bullishAtFocus = close > ema90 && ema90 >= priorEma90;
        const strongAtFocus = bullishAtFocus && close >= ema90 + (result.indicators.atr[regimeIndex] || 0) * 0.6;
        regime.textContent = strongAtFocus ? "主升环境" : bullishAtFocus ? "多头观察" : "禁止追多";
        regime.classList.add(bullishAtFocus ? "is-bullish" : "is-blocked");
        source.textContent = venue.label;
        price.textContent = formatPrice(result.stats.lastPrice);
        signalCount.textContent = `${result.stats.signalCount} 买点 · ${result.stats.secondaryBreakoutHintCount || 0} 二次提示 · ${result.stats.pendingCount || 0} 预备 · ${result.stats.retainedCandidateCount || 0} 候选`;
        filterCount.textContent = `${result.stats.rejectedCount} 过滤`;
      }
    }

    nearestIndex(time) {
      if (!this.result?.candles.length) return null;
      const rows = this.result.candles;
      let low = 0;
      let high = rows.length - 1;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (rows[middle].time < time) low = middle + 1;
        else high = middle;
      }
      if (low > 0 && Math.abs(rows[low - 1].time - time) < Math.abs(rows[low].time - time)) return low - 1;
      return low;
    }

    drawAnnotations(ctx) {
      if (!this.geometry) return;
      const { plot, priceBottom, xAt, yAt } = this.geometry;
      const items = this.drawPreview ? [...this.annotations, this.drawPreview] : this.annotations;
      ctx.save();
      ctx.beginPath();
      ctx.rect(plot.left, plot.top, plot.right - plot.left, priceBottom - plot.top);
      ctx.clip();
      items.forEach((annotation) => {
        const startIndex = this.nearestIndex(annotation.start.time);
        const endIndex = this.nearestIndex(annotation.end.time);
        if (startIndex == null || endIndex == null) return;
        const x1 = xAt(startIndex);
        const y1 = yAt(annotation.start.price);
        const x2 = xAt(endIndex);
        const y2 = yAt(annotation.end.price);
        ctx.strokeStyle = annotation.preview ? "rgba(34, 211, 238, .62)" : "rgba(34, 211, 238, .96)";
        ctx.fillStyle = "rgba(34, 211, 238, .96)";
        ctx.lineWidth = annotation.preview ? 1.15 : 1.55;
        ctx.setLineDash(annotation.preview ? [5, 4] : []);
        ctx.beginPath();
        if (annotation.type === "horizontal") {
          ctx.moveTo(plot.left, y1);
          ctx.lineTo(plot.right, y1);
        } else if (annotation.type === "trend") {
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
        } else if (annotation.type === "ellipse") {
          const centerX = (x1 + x2) / 2;
          const centerY = (y1 + y2) / 2;
          ctx.ellipse(centerX, centerY, Math.max(Math.abs(x2 - x1) / 2, 2), Math.max(Math.abs(y2 - y1) / 2, 2), 0, 0, Math.PI * 2);
        }
        ctx.stroke();
        if (!annotation.preview && annotation.type !== "horizontal") {
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.arc(x1, y1, 2.2, 0, Math.PI * 2);
          ctx.arc(x2, y2, 2.2, 0, Math.PI * 2);
          ctx.fill();
          if (annotation.type === "trend") {
            const anchorLabel = (point) => ({ high: "H", low: "L", close: "C", open: "O" }[point?.anchorType] || "");
            ctx.font = "600 8px ui-monospace, monospace";
            ctx.fillStyle = "rgba(154, 239, 231, .9)";
            if (anchorLabel(annotation.start)) ctx.fillText(anchorLabel(annotation.start), x1 + 4, y1 - 4);
            if (anchorLabel(annotation.end)) ctx.fillText(anchorLabel(annotation.end), x2 + 4, y2 - 4);
          }
          if (annotation.analysis?.labelOwnerId === annotation.id) {
            const analysis = annotation.analysis;
            const caption = `${analysis.label} · 包络${Math.round(analysis.coverage * 100)}%${analysis.priorAdvanceQualified ? "" : " · 待校验前置拉升"}`;
            ctx.font = "600 9px ui-monospace, monospace";
            const captionWidth = ctx.measureText(caption).width;
            const captionX = clamp(Math.max(x1, x2) - captionWidth, plot.left + 4, plot.right - captionWidth - 4);
            const captionY = clamp(Math.min(y1, y2) - 9, plot.top + 11, priceBottom - 6);
            ctx.fillStyle = analysis.coverage >= 0.82
              ? "rgba(69, 242, 202, .95)"
              : "rgba(248, 184, 78, .95)";
            ctx.fillText(caption, captionX, captionY);
          }
        }
      });
      ctx.restore();
      ctx.setLineDash([]);
    }

    render() {
      if (this.renderFrame != null) return;
      this.renderFrame = requestAnimationFrame(() => {
        this.renderFrame = null;
        this.renderNow();
      });
    }

    renderNow() {
      const ctx = this.ctx;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = this.canvas.width / dpr;
      const height = this.canvas.height / dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      if (!this.result?.candles.length || width < 100 || height < 100) return;

      const { start, end } = this.visibleBounds();
      const rows = this.result.candles.slice(start, end);
      if (!rows.length) return;
      const plot = { left: 8, right: width - 62, top: 15, bottom: height - 25 };
      const priceBottom = plot.top + (plot.bottom - plot.top) * 0.76;
      const volumeTop = priceBottom + 8;
      let minPrice = Infinity;
      let maxPrice = -Infinity;
      let maxVolume = 1;
      rows.forEach((row) => {
        if (row.low < minPrice) minPrice = row.low;
        if (row.high > maxPrice) maxPrice = row.high;
        if (row.volume > maxVolume) maxVolume = row.volume;
      });
      const padding = Math.max((maxPrice - minPrice) * 0.08, maxPrice * 0.0004);
      const priceMin = minPrice - padding;
      const priceMax = maxPrice + padding;
      const priceRange = Math.max(priceMax - priceMin, 1e-10);
      const step = (plot.right - plot.left) / rows.length;
      const candleWidth = clamp(step * 0.68, 1, 10);
      const xAt = (absoluteIndex) => plot.left + (absoluteIndex - start + 0.5) * step;
      const yAt = (price) => plot.top + (priceMax - price) / priceRange * (priceBottom - plot.top);
      this.geometry = { plot, priceBottom, priceMin, priceMax, priceRange, start, end, step, candleWidth, xAt, yAt };

      ctx.lineWidth = 1;
      ctx.font = "8px Cascadia Code, Consolas, monospace";
      ctx.textBaseline = "middle";
      for (let line = 0; line <= 4; line += 1) {
        const ratio = line / 4;
        const y = plot.top + ratio * (priceBottom - plot.top);
        const price = priceMax - ratio * priceRange;
        ctx.strokeStyle = "rgba(151, 186, 178, .075)";
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(plot.left, y + .5);
        ctx.lineTo(plot.right, y + .5);
        ctx.stroke();
        ctx.fillStyle = "rgba(151, 176, 170, .56)";
        ctx.fillText(formatPrice(price), plot.right + 6, y);
      }
      for (let line = 0; line <= 4; line += 1) {
        const ratio = line / 4;
        const x = plot.left + ratio * (plot.right - plot.left);
        const index = clamp(start + Math.round(ratio * (rows.length - 1)), start, end - 1);
        ctx.strokeStyle = "rgba(151, 186, 178, .045)";
        ctx.beginPath();
        ctx.moveTo(x + .5, plot.top);
        ctx.lineTo(x + .5, plot.bottom);
        ctx.stroke();
        ctx.fillStyle = "rgba(151, 176, 170, .48)";
        ctx.textAlign = line === 0 ? "left" : line === 4 ? "right" : "center";
        ctx.fillText(formatDateTime(this.result.candles[index].time), x, height - 10);
      }
      ctx.textAlign = "left";

      rows.forEach((row, relativeIndex) => {
        const absoluteIndex = start + relativeIndex;
        const x = xAt(absoluteIndex);
        const up = row.close >= row.open;
        const color = up ? "#24d9a6" : "#ff5d6c";
        const openY = yAt(row.open);
        const closeY = yAt(row.close);
        const highY = yAt(row.high);
        const lowY = yAt(row.low);
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(Math.round(x) + .5, highY);
        ctx.lineTo(Math.round(x) + .5, lowY);
        ctx.stroke();
        const bodyTop = Math.min(openY, closeY);
        const bodyHeight = Math.max(1, Math.abs(closeY - openY));
        ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
        const volumeHeight = row.volume / maxVolume * (plot.bottom - volumeTop);
        ctx.globalAlpha = .22;
        ctx.fillRect(x - candleWidth / 2, plot.bottom - volumeHeight, candleWidth, volumeHeight);
        ctx.globalAlpha = 1;
      });

      const drawAverage = (values, color) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.15;
        ctx.beginPath();
        let started = false;
        for (let absoluteIndex = start; absoluteIndex < end; absoluteIndex += 1) {
          const value = values[absoluteIndex];
          if (!Number.isFinite(value)) continue;
          const x = xAt(absoluteIndex);
          const y = yAt(value);
          if (!started) { ctx.moveTo(x, y); started = true; }
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      };
      drawAverage(this.result.indicators.ema90, "rgba(248, 184, 78, .92)");

      const visibleSignals = this.result.signals.filter((item) => item.index >= start && item.index < end);
      const visibleSecondaryHints = (this.result.secondaryBreakoutHints || []).filter((item) => item.index >= start && item.index < end);
      const visiblePending = (this.result.pending || []).filter((item) => item.index >= start && item.index < end);
      const visibleRejected = this.showRejected
        ? this.result.rejected.filter((item) => item.index >= start && item.index < end)
        : [];
      const visiblePreconfirmedStructures = (this.result.structures || []).filter((item) => (
        item.index >= start && item.index < end
      ));
      const eliteStructuralLineCandidates = (this.result.interval === "1m"
        ? []
        : [...visibleSignals, ...visiblePending, ...visiblePreconfirmedStructures]).filter((signal) => {
        if (!signal.trendline && !signal.triangleLines) return false;
        if (signal.highLevelDistribution) return false;
        const upperStart = signal.triangleLines?.upper?.startIndex ?? signal.trendline?.startIndex;
        const lowerStart = signal.triangleLines?.lower?.startIndex;
        const structureStart = signal.triangleLines?.structureStartIndex
          ?? signal.trendline?.structureStartIndex
          ?? upperStart;
        // 兼容永久反馈里可能保留的旧版结构快照：凡是下轨向前跨出冲高后
        // 盘整窗口、借用拉升前低点的旧线，都不再进入盘面绘制。
        if (Number.isFinite(lowerStart)
          && Number.isFinite(structureStart)
          && lowerStart < structureStart) return false;
        if (signal.structurePreconfirmed) return true;
        if (signal.status === "buy") return Engine.isHighCertaintyEntry(signal);
        const foundations = signal.foundationTypes || [];
        return foundations.includes("triangle")
          && (signal.consolidationBars || 0) >= 40
          && (signal.structureQuality || 0) >= 0.68
          && (signal.certaintyScore || 0) >= 82;
      });
      // 同一盘整只能有一组稳定边界。将高度重叠的重复识别合并，避免每根 K 线都
      // 重新画一套趋势线；优先保留跨度、触点、确定性更高的最外沿结构。
      const structuralLineScore = (signal) => (
        (signal.consolidationBars || 0) * 1.5
        + (signal.certaintyScore || 0)
        + (signal.trendline?.touches || 0) * 12
        + Number(Boolean(signal.manualConfirmed)) * 40
      );
      const eliteStructuralLines = eliteStructuralLineCandidates
        .sort((a, b) => structuralLineScore(b) - structuralLineScore(a))
        .reduce((selected, signal) => {
          const startIndex = signal.triangleLines?.upper?.startIndex ?? signal.trendline?.startIndex ?? signal.index;
          const endIndex = signal.index;
          const overlaps = selected.some((existing) => {
            const existingStart = existing.triangleLines?.upper?.startIndex ?? existing.trendline?.startIndex ?? existing.index;
            const intersection = Math.max(0, Math.min(endIndex, existing.index) - Math.max(startIndex, existingStart));
            const smallerSpan = Math.max(1, Math.min(endIndex - startIndex, existing.index - existingStart));
            return intersection / smallerSpan >= 0.45;
          });
          if (!overlaps && selected.length < 1) selected.push(signal);
          return selected;
        }, []);
      eliteStructuralLines.forEach((signal) => {
        const lines = signal.triangleLines
          ? [signal.triangleLines.upper, signal.triangleLines.lower].filter(Boolean)
          : [signal.trendline].filter(Boolean);
        ctx.save();
        ctx.beginPath();
        ctx.rect(plot.left, plot.top, plot.right - plot.left, priceBottom - plot.top);
        ctx.clip();
        ctx.strokeStyle = "rgba(242, 247, 246, .9)";
        ctx.lineWidth = 1.25;
        ctx.setLineDash([]);
        lines.forEach((line) => {
          const lineStartIndex = line.displayStartIndex ?? line.startIndex;
          const lineStartPrice = line.displayStartPrice ?? line.startPrice;
          ctx.beginPath();
          ctx.moveTo(xAt(lineStartIndex), yAt(lineStartPrice));
          ctx.lineTo(xAt(line.endIndex ?? signal.index), yAt(line.endPrice));
          ctx.stroke();
        });
        ctx.restore();
        ctx.setLineDash([]);
      });
      visiblePending.forEach((signal) => {
        const x = xAt(signal.index);
        const y = yAt(signal.price || signal.level);
        const visual = signal.visualPreconfirmed;
        ctx.strokeStyle = visual ? "rgba(83, 200, 255, .92)" : "rgba(248, 184, 78, .8)";
        ctx.lineWidth = 1.15;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(Math.max(plot.left, x - step * 8), yAt(signal.level));
        ctx.lineTo(Math.min(plot.right, x + step * 3), yAt(signal.level));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(x, y - 5);
        ctx.lineTo(x + 5, y);
        ctx.lineTo(x, y + 5);
        ctx.lineTo(x - 5, y);
        ctx.closePath();
        ctx.stroke();
        if (visual) {
          ctx.fillStyle = "rgba(3, 19, 26, .92)";
          ctx.fillRect(x - 8, y + 8, 16, 12);
          ctx.fillStyle = "#53c8ff";
          ctx.font = "700 8px Cascadia Code, Consolas, monospace";
          ctx.textAlign = "center";
          ctx.fillText("V", x, y + 15);
        }
      });
      visibleSignals.forEach((signal) => {
        const x = xAt(signal.index);
        const y = this.buyMarkerY(signal);
        if (signal.manualDecision === "confirmed") {
          ctx.strokeStyle = "rgba(56, 246, 199, .98)";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(x, y + 5, 9, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.strokeStyle = "rgba(56, 246, 199, .35)";
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(Math.max(plot.left, x - step * 10), yAt(signal.level));
        ctx.lineTo(Math.min(plot.right, x + step * 4), yAt(signal.level));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#38f6c7";
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x - 5, y + 8);
        ctx.lineTo(x + 5, y + 8);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = "rgba(4, 17, 13, .95)";
        ctx.fillRect(x - 8, y + 9, 16, 12);
        ctx.fillStyle = "#38f6c7";
        ctx.font = "700 8px Cascadia Code, Consolas, monospace";
        ctx.textAlign = "center";
        // 人工确认只增加永久锁定圆环，不能把买点字母 B 替换掉。
        ctx.fillText("B", x, y + 15);
      });
      visibleSecondaryHints.forEach((signal) => {
        const x = xAt(signal.index);
        const y = this.buyMarkerY(signal);
        ctx.strokeStyle = "rgba(255, 93, 108, .42)";
        ctx.lineWidth = 1.1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(Math.max(plot.left, x - step * 8), yAt(signal.level));
        ctx.lineTo(Math.min(plot.right, x + step * 3), yAt(signal.level));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#ff5d6c";
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x - 5, y + 8);
        ctx.lineTo(x + 5, y + 8);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = "rgba(22, 6, 9, .95)";
        ctx.fillRect(x - 8, y + 9, 16, 12);
        ctx.fillStyle = "#ff5d6c";
        ctx.font = "700 8px Cascadia Code, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.fillText("B", x, y + 15);
      });
      visibleRejected.slice(-24).forEach((signal) => {
        const x = xAt(signal.index);
        const y = yAt(this.result.candles[signal.index].high) - 7;
        ctx.strokeStyle = "rgba(255, 93, 108, .85)";
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(x - 3, y - 3);
        ctx.lineTo(x + 3, y + 3);
        ctx.moveTo(x + 3, y - 3);
        ctx.lineTo(x - 3, y + 3);
        ctx.stroke();
      });

      const selectedSignal = this.selectedFeedbackSignal();
      if (selectedSignal) {
        const stored = state.feedback.records?.[this.selectedFeedbackKey]?.signal;
        const savedStart = this.visualStructureStartIndex(stored || selectedSignal);
        const previewStart = Number.isFinite(this.visualRangeDraft?.draftStartIndex)
          ? this.visualRangeDraft.draftStartIndex
          : savedStart;
        if (previewStart >= 0 && selectedSignal.index >= start && previewStart < end) {
          const left = Math.max(plot.left, xAt(Math.max(previewStart, start)) - step * 0.48);
          const right = Math.min(plot.right, xAt(selectedSignal.index) + step * 0.48);
          ctx.save();
          ctx.fillStyle = "rgba(83, 200, 255, .035)";
          ctx.fillRect(left, plot.top, Math.max(1, right - left), priceBottom - plot.top);
          ctx.strokeStyle = "rgba(83, 200, 255, .68)";
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(left, plot.top);
          ctx.lineTo(left, priceBottom);
          ctx.moveTo(right, plot.top);
          ctx.lineTo(right, priceBottom);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = "rgba(83, 200, 255, .92)";
          ctx.font = "700 8px Cascadia Code, Consolas, monospace";
          ctx.textAlign = "left";
          ctx.fillText("VISUAL RANGE", left + 5, plot.top + 13);
          ctx.restore();
        }
      }
      if (selectedSignal && selectedSignal.index >= start && selectedSignal.index < end) {
        const x = xAt(selectedSignal.index);
        const markerPrice = selectedSignal.price || selectedSignal.level || this.result.candles[selectedSignal.index]?.close;
        const y = selectedSignal.status === "buy" || selectedSignal.secondaryBreakoutHint ? this.buyMarkerY(selectedSignal) + 5 : yAt(markerPrice) + 6;
        const selectedConfirmed = selectedSignal.manualDecision === "confirmed";
        ctx.save();
        ctx.strokeStyle = selectedSignal.manualDecision === "denied"
          ? "rgba(255,93,108,.98)"
          : selectedConfirmed
            ? "rgba(56,246,199,.98)"
            : "rgba(248,184,78,.98)";
        ctx.fillStyle = "rgba(5,13,14,.88)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 2]);
        ctx.beginPath();
        ctx.arc(x, y, 13, 0, Math.PI * 2);
        // 已确认点不铺深色底，确保弹窗仍打开时 B 也不会被遮住。
        if (!selectedConfirmed) ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = selectedSignal.manualDecision === "denied" ? "#ff5d6c" : "#f8b84e";
        ctx.font = "700 8px Cascadia Code, Consolas, monospace";
        ctx.textAlign = "center";
        ctx.fillText("SELECT", x, y - 18);
        ctx.restore();
      }

      this.drawAnnotations(ctx);

      let activeIndex = this.selectedFeedbackKey ? null : this.hoverIndex;
      if (activeIndex == null && this.externalTime != null) activeIndex = this.nearestIndex(this.externalTime);
      if (activeIndex != null && activeIndex >= start && activeIndex < end) {
        const row = this.result.candles[activeIndex];
        const x = xAt(activeIndex);
        const y = yAt(row.close);
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = "rgba(225, 241, 237, .28)";
        ctx.beginPath();
        ctx.moveTo(x, plot.top);
        ctx.lineTo(x, plot.bottom);
        ctx.moveTo(plot.left, y);
        ctx.lineTo(plot.right, y);
        ctx.stroke();
        ctx.setLineDash([]);
        const attached = this.signalByIndex.get(activeIndex);
        const statusLabel = attached?.manualDecision === "confirmed"
          ? (attached.manualOverride ? "人工永久确认" : "策略买点 · 已永久确认")
          : attached?.manualDecision === "denied"
            ? "用户永久否定"
            : attached?.status === "buy" ? "因果买点" : attached?.secondaryBreakoutHint ? "红色B · 防洗二次突破提示" : attached?.visualPreconfirmed ? "视觉预确认V" : attached?.status === "pending" ? "预备触发" : "过滤";
        this.tooltip.innerHTML = `<b>${escapeHtml(formatDateTime(row.time, true))}</b>　O ${formatPrice(row.open)}　H ${formatPrice(row.high)}　L ${formatPrice(row.low)}　C ${formatPrice(row.close)}　V ${escapeHtml(formatCompact(row.volume))}${attached ? `　<strong>${escapeHtml(attached.pattern)} · ${escapeHtml(statusLabel)}</strong>` : ""}`;
        this.tooltip.classList.add("is-visible");
      } else {
        this.tooltip.classList.remove("is-visible");
      }
      this.positionFeedbackPopover();
    }
  }

  const state = {
    charts: new Map(),
    results: new Map(),
    cache: new Map(),
    marketCacheDbPromise: null,
    generation: 0,
    controller: null,
    leaderController: null,
    ledgerFilter: "all",
    drawTool: "pan",
    activeInterval: "15m",
    failures: new Map(),
    activeCase: null,
    liveLeaders: [],
    feedback: Feedback.emptyDocument(),
    feedbackWeights: {},
    feedbackSync: { local: "checking", account: "checking", saving: false },
    feedbackDirtyKeys: new Set(),
    feedbackWriteTimer: null,
    feedbackRefreshTimer: null,
    feedbackPersistTimer: null,
    feedbackPersistInFlight: null,
    feedbackPersistRequested: false,
    feedbackPersistForceFull: false,
    feedbackWriteIdle: null,
    analysisWorkers: [],
    analysisQueue: [],
    analysisRequestId: 0,
    analysisWorkerUnavailable: false,
    deviceId: "",
    lastFocusTime: null,
    marketContextCount: 0,
    loadingWorkspace: false,
  };

  // 文档案例、附件标的与实时龙头池中的交易对，本身就是用户已经完成的
  // 龙头预选。即使用户点过交易对输入框而进入“自定义”视图，也不能因此
  // 丢掉主升浪启动许可；自定义只改变观察窗口，不撤销这个标的的龙头身份。
  function isPreselectedLeaderPair(pair) {
    const normalizedPair = Data.normalizePair(pair || "");
    if (!normalizedPair) return false;
    if (state.activeCase && Data.normalizePair(state.activeCase.pair || "") === normalizedPair) return true;
    if (Data.normalizePair(SCREENSHOT_CASE.pair) === normalizedPair) return true;
    if (Cases.some((item) => item.valid && Data.normalizePair(item.pair) === normalizedPair)) return true;
    return state.liveLeaders.some((item) => Data.normalizePair(item.pair) === normalizedPair);
  }

  function browserDeviceId() {
    let value = localStorage.getItem(DEVICE_STORAGE_KEY) || "";
    if (!/^[A-Za-z0-9_.-]{12,96}$/.test(value)) {
      const random = window.crypto?.randomUUID?.().replace(/-/g, "")
        || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
      value = `dragon-${random}`.slice(0, 72);
      localStorage.setItem(DEVICE_STORAGE_KEY, value);
    }
    return value;
  }

  function normalizeMainWaveStage(value) {
    return ["active", "expected"].includes(value) ? value : "auto";
  }

  function readAnalysisContexts() {
    try {
      const parsed = JSON.parse(localStorage.getItem(ANALYSIS_CONTEXT_STORAGE_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  function analysisContextFor(pair = Data.normalizePair($("#symbolInput")?.value || "")) {
    const saved = readAnalysisContexts()[Data.normalizePair(pair)] || {};
    const savedStage = normalizeMainWaveStage(saved.mainWaveStage);
    const leaderDefaulted = savedStage === "auto" && isPreselectedLeaderPair(pair);
    const mainWaveStage = leaderDefaulted ? "active" : savedStage;
    return {
      mainWaveStage,
      mainWaveContextSource: leaderDefaulted ? "leader-default-main-wave" : "manual-analysis",
      mainWaveContextLabel: leaderDefaulted ? "龙头默认主升浪环境" : (
        mainWaveStage === "active" ? "人工确认主升浪阶段" : "人工给出主升浪预期"
      ),
      leaderDefaulted,
      note: String(saved.note || "").slice(0, 180),
      updatedAt: Number(saved.updatedAt) || 0,
    };
  }

  function syncHumanAnalysisControls(pair = Data.normalizePair($("#symbolInput")?.value || "")) {
    const context = analysisContextFor(pair);
    if ($("#mainWaveMode")) $("#mainWaveMode").value = context.mainWaveStage;
    if ($("#analysisNote")) $("#analysisNote").value = context.note;
    if ($("#mainWaveModeHint")) {
      $("#mainWaveModeHint").textContent = context.mainWaveStage === "active"
        ? "已确认主升：只放宽大周期与上级锚点许可，成熟结构、真实外沿和突破触发不放宽。"
        : context.mainWaveStage === "expected"
          ? "主升预期：仅对更高确定性的成熟结构放宽环境许可，普通盘整仍过滤。"
          : "策略自判；人工结论不会替代成熟结构、真实外沿和突破触发。";
    }
    return context;
  }

  function saveHumanAnalysisContext() {
    const pair = Data.normalizePair($("#symbolInput").value);
    const contexts = readAnalysisContexts();
    contexts[pair] = {
      mainWaveStage: normalizeMainWaveStage($("#mainWaveMode").value),
      note: $("#analysisNote").value.trim().slice(0, 180),
      updatedAt: Date.now(),
    };
    localStorage.setItem(ANALYSIS_CONTEXT_STORAGE_KEY, JSON.stringify(contexts));
    return syncHumanAnalysisControls(pair);
  }

  function readBrowserFeedback() {
    try {
      return Feedback.normalizeDocument(JSON.parse(localStorage.getItem(FEEDBACK_STORAGE_KEY) || "{}"));
    } catch (_error) {
      return Feedback.emptyDocument();
    }
  }

  function writeBrowserFeedback() {
    localStorage.setItem(FEEDBACK_STORAGE_KEY, JSON.stringify(state.feedback));
  }

  function flushBrowserFeedbackWrite() {
    if (state.feedbackWriteTimer) {
      clearTimeout(state.feedbackWriteTimer);
      state.feedbackWriteTimer = null;
    }
    if (state.feedbackWriteIdle != null && typeof cancelIdleCallback === "function") {
      cancelIdleCallback(state.feedbackWriteIdle);
      state.feedbackWriteIdle = null;
    }
    writeBrowserFeedback();
  }

  function queueBrowserFeedbackWrite(delay = 900) {
    if (state.feedbackWriteTimer) clearTimeout(state.feedbackWriteTimer);
    if (state.feedbackWriteIdle != null && typeof cancelIdleCallback === "function") {
      cancelIdleCallback(state.feedbackWriteIdle);
      state.feedbackWriteIdle = null;
    }
    state.feedbackWriteTimer = setTimeout(() => {
      state.feedbackWriteTimer = null;
      const commit = () => {
        state.feedbackWriteIdle = null;
        writeBrowserFeedback();
      };
      if (typeof requestIdleCallback === "function") {
        state.feedbackWriteIdle = requestIdleCallback(commit, { timeout: 2600 });
      } else setTimeout(commit, 0);
    }, delay);
  }

  function hydrateVisualFeedbackForResult(result, pair) {
    if (!result?.candles?.length) return 0;
    const normalizedPair = Data.normalizePair(pair);
    const updates = {};
    const now = Date.now();
    const indexByTime = new Map(result.candles.map((row, index) => [Number(row.time), index]));
    Object.values(state.feedback.records || {}).forEach((record) => {
      if (record.decision === "cleared"
        || record.pair !== normalizedPair
        || record.interval !== result.interval) return;
      if (record.signal?.visualSignature?.version === Vision.VERSION) return;
      const selectedTime = Number(record.signal?.time);
      const index = indexByTime.get(selectedTime) ?? -1;
      if (index < 24) return;
      const triggerPrice = Number(record.signal?.triggerPrice
        || record.signal?.selectedPrice
        || record.signal?.price
        || result.candles[index]?.open);
      const savedStartTime = Number(record.signal?.visualStructureStartTime);
      const startByTime = Number.isFinite(savedStartTime)
        ? (indexByTime.get(savedStartTime) ?? -1)
        : -1;
      const inferredStart = record.signal?.triangleLines?.structureStartIndex
        ?? record.signal?.triangleLines?.upper?.startIndex
        ?? record.signal?.trendline?.structureStartIndex
        ?? record.signal?.trendline?.startIndex
        ?? index - Math.min(Math.max(Number(record.signal?.consolidationBars) || 40, 12), 240);
      const visualStructureStartIndex = clamp(
        startByTime >= 0 ? startByTime : Math.trunc(Number(inferredStart) || 0),
        0,
        index - 12,
      );
      const visualSignature = Vision.buildVisualSignature(result.candles, index, {
        interval: result.interval,
        triggerPrice,
        ema90: result.indicators?.ema90,
        structureStartIndex: visualStructureStartIndex,
        structureSource: record.signal?.visualStructureSource || "backfill",
      });
      if (!visualSignature
        || visualSignature.causality !== "completed-candles-before-selected-index-only") return;
      updates[record.key] = {
        ...record,
        datasetVersion: Feedback.DATASET_VERSION,
        updatedAt: now,
        signal: {
          ...record.signal,
          visualSignature,
          visualStructureStartIndex,
          visualStructureStartTime: result.candles[visualStructureStartIndex]?.time || null,
          visualStructureBars: index - visualStructureStartIndex,
          visualStructureSource: record.signal?.visualStructureSource || "backfill",
          visualHydratedAt: now,
        },
      };
    });
    const count = Object.keys(updates).length;
    if (!count) return 0;
    state.feedback = Feedback.mergeDocuments(state.feedback, {
      version: 1,
      updatedAt: now,
      records: updates,
    });
    Object.keys(updates).forEach((key) => state.feedbackDirtyKeys.add(key));
    queueBrowserFeedbackWrite(1400);
    return count;
  }

  function feedbackEndpoints() {
    const quietMode = location.port === "8791";
    return {
      local: quietMode ? "/api/dragon-wave-feedback/local" : null,
      account: quietMode ? "/api/dragon-wave-feedback/account" : "/api/dragon-wave-feedback",
    };
  }

  function feedbackCounts() {
    return Object.values(state.feedback.records || {}).reduce((counts, record) => {
      if (Object.hasOwn(counts, record.decision)) counts[record.decision] += 1;
      return counts;
    }, { confirmed: 0, pending: 0, denied: 0 });
  }

  function loadedConfirmedRegressionConflicts() {
    const byKey = new Map();
    state.results.forEach(({ result }) => {
      (result?.confirmedCompatibility?.conflicts || []).forEach((item) => byKey.set(item.key, item));
    });
    return [...byKey.values()];
  }

  function loadedDeniedRegressionConflicts() {
    const byKey = new Map();
    state.results.forEach(({ result }) => {
      (result?.deniedCompatibility?.conflicts || []).forEach((item) => byKey.set(item.key, item));
    });
    return [...byKey.values()];
  }

  function updateFeedbackStatus() {
    const counts = feedbackCounts();
    const regressionConflicts = loadedConfirmedRegressionConflicts();
    const deniedRegressionConflicts = loadedDeniedRegressionConflicts();
    const totalRegressionConflicts = regressionConflicts.length + deniedRegressionConflicts.length;
    const row = $(".feedback-sync");
    if (!row) return;
    row.classList.toggle("is-saved", state.feedbackSync.local === "saved" && !state.feedbackSync.saving);
    row.classList.toggle("is-error", state.feedbackSync.local === "error");
    row.classList.toggle("has-regression-conflict", totalRegressionConflicts > 0);
    let label = state.feedbackSync.saving ? "正在永久保存…" : "浏览器已保存";
    if (!state.feedbackSync.saving && state.feedbackSync.local === "saved") label = "本机已永久保存";
    if (!state.feedbackSync.saving && state.feedbackSync.account === "saved") label = "本机 + 账号已同步";
    else if (!state.feedbackSync.saving && state.feedbackSync.account === "signed-out") label = "本机已保存 · 登录后同步";
    else if (!state.feedbackSync.saving && state.feedbackSync.local === "error") label = "浏览器已保存 · 本机库暂不可用";
    $("#feedbackSync").textContent = label;
    $("#feedbackCounts").textContent = `${counts.confirmed} 正样本 / ${counts.pending} 待定 / ${counts.denied} 反例${totalRegressionConflicts ? ` / ${totalRegressionConflicts} 集合回归冲突` : " / 正反样本回归通过"}`;
    row.title = totalRegressionConflicts
      ? [
        ...regressionConflicts.slice(0, 4).map((item) => (
          `确认遗漏 · ${item.interval} ${formatDateTime(item.time)} · ${item.strategyStatus}${item.reasons?.[0] ? ` · ${item.reasons[0]}` : ""}`
        )),
        ...deniedRegressionConflicts.slice(0, 4).map((item) => (
          `否定复活 · ${item.interval} ${formatDateTime(item.time)} · ${item.strategyStatus}`
        )),
      ].join("\n")
      : "当前已加载窗口内：确认买点全部被新策略原生命中，彻底否定点均未复活";
  }

  async function readFeedbackEndpoint(url) {
    if (!url) return null;
    const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    if (response.status === 401 || response.status === 403) {
      const error = new Error("signed-out");
      error.code = "signed-out";
      throw error;
    }
    if (!response.ok) throw new Error(`feedback-${response.status}`);
    const payload = await response.json();
    return Feedback.normalizeDocument(payload.feedback);
  }

  async function postFeedbackEndpoint(url, payload) {
    if (!url) return null;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        // POST 只需确认落库成功，不再把整个反馈库和优化数据回传到浏览器。
        // 对数百个视觉快照来说，这能避免确认后再次解析数 MB JSON。
        "X-Dragon-Wave-Compact": "1",
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    if (response.status === 401 || response.status === 403) {
      const error = new Error("signed-out");
      error.code = "signed-out";
      throw error;
    }
    if (!response.ok) throw new Error(`feedback-${response.status}`);
    const data = await response.json();
    return data.feedback ? Feedback.normalizeDocument(data.feedback) : null;
  }

  function refreshLoadedFeedback() {
    const preparedContext = Feedback.prepareApplicationContext(state.feedback);
    state.feedbackWeights = preparedContext.weights;
    state.results.forEach((item, interval) => {
      const baseResult = item.baseResult || item.result;
      state.results.set(interval, {
        ...item,
        baseResult,
        result: applyFeedbackPolicy(baseResult, Data.normalizePair($("#symbolInput").value), preparedContext),
      });
    });
    if (state.results.size && !state.loadingWorkspace) {
      renderActiveChart(state.lastFocusTime || parseInputTime($("#focusTime").value));
      renderLedger();
      const usable = [...state.results.entries()].map(([interval, item]) => ({ interval, ...item }));
      const failed = [...state.failures.keys()].map((interval) => ({ interval, ok: false }));
      updateSummary(usable, failed, state.lastFocusTime || parseInputTime($("#focusTime").value), state.marketContextCount);
    }
    updateFeedbackStatus();
  }

  function queueLoadedFeedbackRefresh(delay = 900) {
    if (state.feedbackRefreshTimer) clearTimeout(state.feedbackRefreshTimer);
    state.feedbackRefreshTimer = setTimeout(() => {
      state.feedbackRefreshTimer = null;
      const refresh = () => refreshLoadedFeedback();
      if (typeof requestIdleCallback === "function") requestIdleCallback(refresh, { timeout: 2200 });
      else refresh();
    }, delay);
  }

  function feedbackDeltaDocument(keys) {
    const records = {};
    keys.forEach((key) => {
      if (state.feedback.records?.[key]) records[key] = state.feedback.records[key];
    });
    return { version: 1, updatedAt: state.feedback.updatedAt || Date.now(), records };
  }

  function queueFeedbackPersistence(delay = 360) {
    if (state.feedbackPersistTimer) clearTimeout(state.feedbackPersistTimer);
    state.feedbackPersistTimer = setTimeout(() => {
      state.feedbackPersistTimer = null;
      void persistFeedback();
    }, delay);
  }

  async function persistFeedback({ full = false, refresh = false } = {}) {
    if (state.feedbackPersistInFlight) {
      state.feedbackPersistRequested = true;
      state.feedbackPersistForceFull = state.feedbackPersistForceFull || full;
      return state.feedbackPersistInFlight;
    }
    const keys = full ? Object.keys(state.feedback.records || {}) : [...state.feedbackDirtyKeys];
    if (!full && !keys.length) {
      queueBrowserFeedbackWrite();
      return null;
    }
    keys.forEach((key) => state.feedbackDirtyKeys.delete(key));
    const outgoingFeedback = full ? state.feedback : feedbackDeltaDocument(keys);
    // 服务端接收的是本次变更的增量；浏览器完整备份继续在空闲帧合并写入，
    // 不让数 MB 的 JSON 序列化阻塞人工连续确认。
    queueBrowserFeedbackWrite(1400);
    state.feedbackSync.saving = true;
    updateFeedbackStatus();
    const endpoints = feedbackEndpoints();
    const localPayload = { deviceId: state.deviceId, feedback: outgoingFeedback };
    const accountPayload = { feedback: outgoingFeedback };
    const runner = (async () => {
      const localTask = endpoints.local
        ? postFeedbackEndpoint(endpoints.local, localPayload).then((document) => {
          state.feedbackSync.local = "saved";
          return document;
        }).catch(() => {
          state.feedbackSync.local = "error";
          return null;
        })
        : Promise.resolve(null);
      const accountTask = postFeedbackEndpoint(endpoints.account, accountPayload).then((document) => {
        state.feedbackSync.account = "saved";
        return document;
      }).catch((error) => {
        state.feedbackSync.account = error.code === "signed-out" ? "signed-out" : "unavailable";
        return null;
      });
      const [localDocument, accountDocument] = await Promise.all([localTask, accountTask]);
      if (localDocument || accountDocument) {
        state.feedback = Feedback.mergeDocuments(state.feedback, localDocument, accountDocument);
        queueBrowserFeedbackWrite();
      }
      if (refresh || localDocument || accountDocument) queueLoadedFeedbackRefresh(60);
    })();
    state.feedbackPersistInFlight = runner;
    try {
      await runner;
    } finally {
      state.feedbackPersistInFlight = null;
      state.feedbackSync.saving = false;
      updateFeedbackStatus();
      const rerun = state.feedbackPersistRequested || state.feedbackDirtyKeys.size > 0;
      const forceFull = state.feedbackPersistForceFull;
      state.feedbackPersistRequested = false;
      state.feedbackPersistForceFull = false;
      if (rerun) {
        if (forceFull) void persistFeedback({ full: true });
        else queueFeedbackPersistence(80);
      }
    }
    return null;
  }

  async function initializeFeedback() {
    state.deviceId = browserDeviceId();
    state.feedback = readBrowserFeedback();
    state.feedbackWeights = Feedback.buildWeights(state.feedback);
    updateFeedbackStatus();
    const endpoints = feedbackEndpoints();
    const localTask = endpoints.local
      ? readFeedbackEndpoint(`${endpoints.local}?deviceId=${encodeURIComponent(state.deviceId)}`).then((document) => {
        state.feedbackSync.local = "saved";
        return document;
      }).catch(() => {
        state.feedbackSync.local = "error";
        return null;
      })
      : Promise.resolve(null);
    const accountTask = readFeedbackEndpoint(endpoints.account).then((document) => {
      state.feedbackSync.account = "saved";
      return document;
    }).catch((error) => {
      state.feedbackSync.account = error.code === "signed-out" ? "signed-out" : "unavailable";
      return null;
    });
    const [localDocument, accountDocument] = await Promise.all([localTask, accountTask]);
    state.feedback = Feedback.mergeDocuments(state.feedback, localDocument, accountDocument);
    queueBrowserFeedbackWrite(1200);
    refreshLoadedFeedback();
    await persistFeedback({ full: true });
  }

  function syncCrosshair(time, source) {
    state.charts.forEach((chart) => chart.setExternalTime(time, source));
  }

  function setupCharts() {
    const card = $("#mainChart");
    const chart = new WaveChart(card, syncCrosshair, recordFeedback);
    state.charts.set("main", chart);
    $("[data-expand]", card).addEventListener("click", () => {
      const expanded = card.classList.toggle("is-expanded");
      $("[data-expand]", card).textContent = expanded ? "×" : "↗";
      document.body.style.overflow = expanded ? "hidden" : "";
      requestAnimationFrame(() => chart.resize());
    });
  }

  function renderActiveChart(focusTime = parseInputTime($("#focusTime").value)) {
    const interval = state.activeInterval;
    const chart = state.charts.get("main");
    const loaded = state.results.get(interval);
    const pair = Data.normalizePair($("#symbolInput").value);
    const labels = {
      "1m": ["EXECUTION", "1分钟"],
      "5m": ["TRIGGER", "5分钟"],
      "15m": ["STRUCTURE", "15分钟"],
      "1h": ["SETUP", "1小时"],
      "4h": ["REGIME", "4小时"],
      "1d": ["MACRO", "日线"],
    };
    $("#mainChartRole").textContent = labels[interval][0];
    $("#mainChartInterval").textContent = labels[interval][1];
    $$('[data-timeframe]').forEach((button) => button.classList.toggle("is-active", button.dataset.timeframe === interval));
    if (loaded) chart.setData(resultForDisplay(loaded.result, pair), loaded.venue, focusTime, pair);
    else if (state.failures.has(interval)) chart.setError(state.failures.get(interval));
    else chart.setLoading(`${Data.INTERVALS[interval].label} · 读取中`);
  }

  function exportActiveChartImage() {
    const chart = state.charts.get("main");
    if (!chart?.canvas || !chart.data?.candles?.length) return;
    const pair = Data.normalizePair($("#symbolInput").value) || "DRAGON-WAVE";
    const interval = state.activeInterval || "chart";
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const button = $("#exportChartImage");
    const previousText = button.textContent;
    chart.canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${pair}-${interval}-${timestamp}.png`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      button.textContent = "✓ 图片已生成";
      window.setTimeout(() => { button.textContent = previousText; }, 1800);
    }, "image/png");
  }

  function setupCases() {
    const select = $("#caseSelect");
    select.replaceChildren();
    const custom = document.createElement("option");
    custom.value = "custom";
    custom.textContent = "自定义 / 实时行情";
    select.append(custom);

    const supplementalGroup = document.createElement("optgroup");
    supplementalGroup.label = "附件标的";
    const screenshotOption = document.createElement("option");
    screenshotOption.value = SCREENSHOT_CASE.id;
    screenshotOption.textContent = SCREENSHOT_CASE.label;
    supplementalGroup.append(screenshotOption);
    select.append(supplementalGroup);

    const liveGroup = document.createElement("optgroup");
    liveGroup.label = "实时龙头池 · 读取中";
    liveGroup.dataset.liveGroup = "true";
    const livePlaceholder = document.createElement("option");
    livePlaceholder.disabled = true;
    livePlaceholder.textContent = "正在读取 Binance / OKX / Bitget";
    liveGroup.append(livePlaceholder);
    select.append(liveGroup);

    const documentGroup = document.createElement("optgroup");
    documentGroup.label = `Word 文档样本 · ${Cases.length} 条`;
    Cases.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.symbol === "TUT" ? TUT_REFERENCE.label : item.label;
      documentGroup.append(option);
    });
    select.append(documentGroup);
    select.value = Cases[0].id;
    applyCase(TUT_REFERENCE);
    select.addEventListener("change", () => {
      const item = select.value === SCREENSHOT_CASE.id
        ? SCREENSHOT_CASE
        : state.liveLeaders.find((entry) => entry.id === select.value)
          || (select.value === TUT_REFERENCE.id ? TUT_REFERENCE : Cases.find((entry) => entry.id === select.value))
          || null;
      applyCase(item);
    });
  }

  function applyCase(item) {
    state.activeCase = item;
    if (!item) {
      $("#caseName").textContent = "自定义";
      $("#caseRange").textContent = "使用当前观察锚点";
      $("#caseNote").textContent = "自定义模式按所选交易对与时间读取公开行情，仍使用同一套结构和过滤规则。";
      $("#caseProgress").style.width = "0%";
      $("#activeContext").textContent = "自定义标的 · 多交易所自动容灾";
      syncHumanAnalysisControls();
      return;
    }
    if (item.live) {
      $("#symbolInput").value = item.pair;
      $("#focusTime").value = toLocalInput(Date.now());
      $("#caseName").textContent = item.symbol;
      $("#caseRange").textContent = item.screenshot ? "附件截图 · 当前行情" : `${item.venues.join(" / ")} · 24H +${item.changePercent.toFixed(1)}%`;
      $("#caseNote").textContent = item.screenshot
        ? "AKE 来自你提供的图表截图，不在 Word 的 86 条清单中；这里按当前行情持续扫描六周期起爆结构。"
        : `该标的由实时龙头池发现，成交额约 ${formatCompact(item.quoteVolume)} USDT。它不是历史标签，仍需通过六周期和噪声过滤后才显示买点。`;
      $("#caseProgress").style.width = "100%";
      $("#activePair").textContent = item.pair;
      $("#activeContext").textContent = `${item.symbol} · ${item.screenshot ? "附件截图标的" : `实时龙头 · ${item.venues.join(" / ")}`}`;
      syncHumanAnalysisControls(item.pair);
      return;
    }
    if (item.pair) $("#symbolInput").value = item.pair;
    $("#caseName").textContent = item.symbol;
    $("#caseRange").textContent = `${item.start} → ${item.sourceEnd}`;
    if (item.valid) {
      const start = new Date(`${item.start}T00:00:00+08:00`).getTime();
      const end = new Date(`${item.end}T23:59:00+08:00`).getTime();
      const focus = start + (end - start) * (item.golden ? .84 : .24);
      $("#focusTime").value = toLocalInput(focus);
      $("#caseNote").textContent = item.golden
        ? "TUT 同时来自 Word 主升区间与三张 15 分钟附图，是全策略的起爆结构校准样本。按你的复盘边界，盘面只显示北京时间 8 月 5 日 13:00 及之后的买点；这只是展示裁剪，不参与通用策略判断。"
        : "文档只提供主升区间；具体买点由统一规则从交易所 K 线重新计算，日期本身不是买入信号。";
      updateCaseProgress();
    } else {
      $("#focusTime").value = toLocalInput(new Date(`${item.start}T12:00:00+08:00`).getTime());
      $("#caseNote").textContent = `原文结束日期 ${item.sourceEnd} 不存在。本案例保留用于校对，但不把错误日期用于扫描。`;
      $("#caseProgress").style.width = "0%";
    }
    $("#activePair").textContent = item.pair || "需要交易对";
    $("#activeContext").textContent = item.golden
      ? `TUT 起爆黄金样本 · ${item.start} 至 ${item.sourceEnd}`
      : `${item.symbol} 文档样本 · ${item.start} 至 ${item.sourceEnd}`;
    syncHumanAnalysisControls(item.pair);
  }

  function renderLeaderUniverse() {
    const group = $("[data-live-group]");
    const selectedValue = $("#caseSelect").value;
    group.replaceChildren();
    group.label = `实时龙头池 · ${state.liveLeaders.length} 个`;
    if (!state.liveLeaders.length) {
      const option = document.createElement("option");
      option.disabled = true;
      option.textContent = "当前未发现满足涨幅与流动性的标的";
      group.append(option);
    } else {
      state.liveLeaders.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = `${item.symbol} · +${item.changePercent.toFixed(1)}% · ${item.venues.join("/")}`;
        group.append(option);
      });
    }
    if ([SCREENSHOT_CASE.id, "custom", ...Cases.map((item) => item.id), ...state.liveLeaders.map((item) => item.id)].includes(selectedValue)) {
      $("#caseSelect").value = selectedValue;
    }

    const chipItems = [TUT_REFERENCE, SCREENSHOT_CASE, ...state.liveLeaders.filter((item) => !["AKE", "TUT"].includes(item.symbol)).slice(0, 11)];
    $("#leaderChips").innerHTML = chipItems.map((item) => `<button type="button" data-leader-id="${escapeHtml(item.id)}" class="${item.screenshot || item.golden ? "is-screenshot" : ""}"><b>${escapeHtml(item.symbol)}</b><span>${item.golden ? "黄金样本" : item.screenshot ? "附图" : `+${item.changePercent.toFixed(1)}%`}</span></button>`).join("");
    $("#liveLeaderCount").textContent = String(state.liveLeaders.length);
    $$('[data-leader-id]').forEach((button) => button.addEventListener("click", () => {
      const item = button.dataset.leaderId === TUT_REFERENCE.id
        ? TUT_REFERENCE
        : button.dataset.leaderId === SCREENSHOT_CASE.id
          ? SCREENSHOT_CASE
          : state.liveLeaders.find((entry) => entry.id === button.dataset.leaderId);
      if (!item) return;
      $("#caseSelect").value = item.id;
      applyCase(item);
      loadWorkspace();
    }));
  }

  async function loadLiveLeaders() {
    state.leaderController?.abort();
    state.leaderController = new AbortController();
    const button = $("#refreshLeaders");
    button.disabled = true;
    $("#leaderStatus").textContent = "扫描 Binance / OKX / Bitget…";
    try {
      const payload = await Data.fetchLeaders({ signal: state.leaderController.signal, minChangePercent: 5, minQuoteVolume: 3_000_000, limit: 24 });
      state.liveLeaders = payload.leaders.map((item) => ({ ...item, live: true, source: "实时龙头池" }));
      renderLeaderUniverse();
      $("#leaderStatus").textContent = payload.sources.length
        ? `${payload.sources.join(" / ")} · ${state.liveLeaders.length} 个`
        : "三大交易所暂不可用 · 保留附图标的";
    } catch (error) {
      if (error?.name === "AbortError") return;
      state.liveLeaders = [];
      renderLeaderUniverse();
      $("#leaderStatus").textContent = "实时池读取失败 · 可手动输入";
    } finally {
      button.disabled = false;
    }
  }

  function updateCaseProgress() {
    const item = state.activeCase;
    if (!item?.valid) return;
    const start = new Date(`${item.start}T00:00:00+08:00`).getTime();
    const end = new Date(`${item.end}T23:59:00+08:00`).getTime();
    const focus = parseInputTime($("#focusTime").value);
    const ratio = clamp((focus - start) / Math.max(end - start, 1), 0, 1);
    $("#caseProgress").style.width = `${ratio * 100}%`;
  }

  function cacheKey(params) {
    return [params.provider, params.market, params.pair, params.interval, Math.floor(params.window.start / params.window.ms), Math.floor(params.window.end / params.window.ms)].join(":");
  }

  function openMarketCache() {
    if (!window.indexedDB) return Promise.resolve(null);
    if (state.marketCacheDbPromise) return state.marketCacheDbPromise;
    state.marketCacheDbPromise = new Promise((resolve) => {
      const request = window.indexedDB.open(MARKET_CACHE_DB, MARKET_CACHE_SCHEMA);
      request.onupgradeneeded = () => {
        const database = request.result;
        const store = database.objectStoreNames.contains(MARKET_CACHE_STORE)
          ? request.transaction.objectStore(MARKET_CACHE_STORE)
          : database.createObjectStore(MARKET_CACHE_STORE, { keyPath: "key" });
        if (!store.indexNames.contains("storedAt")) store.createIndex("storedAt", "storedAt");
        const candleStore = database.objectStoreNames.contains(MARKET_CANDLE_CACHE_STORE)
          ? request.transaction.objectStore(MARKET_CANDLE_CACHE_STORE)
          : database.createObjectStore(MARKET_CANDLE_CACHE_STORE, { keyPath: "key" });
        if (!candleStore.indexNames.contains("storedAt")) candleStore.createIndex("storedAt", "storedAt");
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    });
    return state.marketCacheDbPromise;
  }

  async function readAnalyzedCache(key, maxAge) {
    const database = await openMarketCache();
    if (!database) return null;
    return new Promise((resolve) => {
      const transaction = database.transaction(MARKET_CACHE_STORE, "readonly");
      const request = transaction.objectStore(MARKET_CACHE_STORE).get(key);
      request.onsuccess = () => {
        const record = request.result;
        resolve(record && Date.now() - record.storedAt <= maxAge ? record.value : null);
      };
      request.onerror = () => resolve(null);
    });
  }

  async function writeAnalyzedCache(key, value) {
    const database = await openMarketCache();
    if (!database) return;
    await new Promise((resolve) => {
      const transaction = database.transaction(MARKET_CACHE_STORE, "readwrite");
      const store = transaction.objectStore(MARKET_CACHE_STORE);
      store.put({ key, storedAt: Date.now(), value });
      let kept = 0;
      const cursorRequest = store.index("storedAt").openCursor(null, "prev");
      cursorRequest.onsuccess = () => {
        const cursor = cursorRequest.result;
        if (!cursor) return;
        kept += 1;
        if (kept > MARKET_CACHE_LIMIT) cursor.delete();
        cursor.continue();
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => resolve();
      transaction.onabort = () => resolve();
    });
  }

  async function readMarketCandleCache(key) {
    const database = await openMarketCache();
    if (!database) return null;
    return new Promise((resolve) => {
      const transaction = database.transaction(MARKET_CANDLE_CACHE_STORE, "readonly");
      const request = transaction.objectStore(MARKET_CANDLE_CACHE_STORE).get(key);
      request.onsuccess = () => {
        const record = request.result;
        const payload = record?.value;
        resolve(payload?.candles?.length ? payload : null);
      };
      request.onerror = () => resolve(null);
    });
  }

  async function writeMarketCandleCache(key, value) {
    const database = await openMarketCache();
    if (!database || !value?.candles?.length) return;
    await new Promise((resolve) => {
      const transaction = database.transaction(MARKET_CANDLE_CACHE_STORE, "readwrite");
      const store = transaction.objectStore(MARKET_CANDLE_CACHE_STORE);
      store.put({ key, storedAt: Date.now(), value });
      let kept = 0;
      const cursorRequest = store.index("storedAt").openCursor(null, "prev");
      cursorRequest.onsuccess = () => {
        const cursor = cursorRequest.result;
        if (!cursor) return;
        kept += 1;
        if (kept > MARKET_CANDLE_CACHE_LIMIT) cursor.delete();
        cursor.continue();
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => resolve();
      transaction.onabort = () => resolve();
    });
  }

  function analyzedCacheKey(params) {
    const windowWithMs = { ...params.window, ms: Data.INTERVALS[params.interval].ms };
    return `${STRATEGY_CACHE_VERSION}:${normalizeMainWaveStage(params.mainWaveStage)}:${cacheKey({ ...params, window: windowWithMs })}`;
  }

  function analyzedCacheMaxAge(params) {
    const historical = params.window.completeCase
      && params.window.end < Date.now() - Data.INTERVALS[params.interval].ms * 2;
    return historical ? 30 * 24 * 60 * 60_000 : 2 * 60_000;
  }

  function hasCompleteCachedCandles(payload, params) {
    return Boolean(payload?.candles?.length)
      && Data.isCandleCoverageAcceptable(payload.candles, params.window, params.interval);
  }

  async function fetchWithCache(params) {
    const key = cacheKey({ ...params, window: { ...params.window, ms: Data.INTERVALS[params.interval].ms } });
    const cached = state.cache.get(key);
    // 过往文档龙头的窗口已经封闭，K 线不会再变化。它们与策略版本解耦后永久
    // 保存在本机：以后即使升级识别逻辑，也只重算策略，不再整段回读交易所。
    // 实时龙头和自定义实时观察明确绕过持久行情缓存，始终读取交易所最新 K 线。
    if (params.historicalDocument) {
      if (cached && hasCompleteCachedCandles(cached.value, params)) {
        return { ...cached.value, localCandleCacheHit: true };
      }
      const persisted = await readMarketCandleCache(key);
      if (persisted && hasCompleteCachedCandles(persisted, params)) {
        state.cache.set(key, { storedAt: Date.now(), value: persisted });
        return { ...persisted, localCandleCacheHit: true };
      }
    }
    const value = await Data.fetchCandles(params);
    state.cache.set(key, { storedAt: Date.now(), value });
    if (params.historicalDocument) void writeMarketCandleCache(key, value);
    return { ...value, localCandleCacheHit: false };
  }

  async function readLocalPrecomputed(params) {
    if (!params.historicalDocument || !params.caseStart || !params.caseEnd) return null;
    const query = new URLSearchParams({
      version: STRATEGY_CACHE_VERSION,
      pair: Data.normalizePair(params.pair),
      start: params.caseStart,
      end: params.caseEnd,
      interval: params.interval,
      market: params.market,
      stage: normalizeMainWaveStage(params.mainWaveStage),
    });
    try {
      const response = await fetch(`/api/dragon-wave-precomputed?${query}`, {
        signal: params.signal,
        cache: "default",
        headers: { Accept: "application/json" },
      });
      if (response.status === 404) return null;
      if (!response.ok) return null;
      const value = await response.json();
      const matchesRequest = value?.version === STRATEGY_CACHE_VERSION
        && value?.pair === Data.normalizePair(params.pair)
        && value?.start === params.caseStart
        && value?.end === params.caseEnd
        && value?.interval === params.interval
        && value?.market === params.market
        && value?.mainWaveStage === normalizeMainWaveStage(params.mainWaveStage);
      if (!matchesRequest
        || !value?.result?.candles?.length
        || !Data.isCandleCoverageAcceptable(value.result.candles, params.window, params.interval)) return null;
      return value;
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      return null;
    }
  }

  async function loadAnalyzedInterval(params) {
    // 历史龙头优先读取 Node 在本机预先算好的完整结果。命中后浏览器不再
    // 拉交易所、不再跑单周期识别；人工确认/否定仍会在最终提交阶段即时叠加。
    const precomputed = await readLocalPrecomputed(params);
    if (precomputed) {
      return {
        result: precomputed.result,
        venue: precomputed.venue,
        attempts: precomputed.attempts || [],
        persistentCacheHit: true,
        localCandleCacheHit: true,
        localPrecomputedHit: true,
      };
    }
    const persistentKey = analyzedCacheKey(params);
    const cached = params.historicalDocument
      ? await readAnalyzedCache(persistentKey, analyzedCacheMaxAge(params))
      : null;
    if (cached?.result?.candles?.length
      && Data.isCandleCoverageAcceptable(cached.result.candles, params.window, params.interval)) {
      return { ...cached, persistentCacheHit: true };
    }
    const payload = await fetchWithCache(params);
    const analysisOptions = {
      interval: params.interval,
      now: Date.now(),
      mainWaveStage: normalizeMainWaveStage(params.mainWaveStage),
      mainWaveContextSource: params.mainWaveContextSource,
      mainWaveContextLabel: params.mainWaveContextLabel,
    };
    const value = {
      result: await analyzeTimeframeOffThread(payload.candles, analysisOptions, params.signal),
      venue: payload.venue,
      attempts: payload.attempts,
      localCandleCacheHit: payload.localCandleCacheHit === true,
    };
    // IndexedDB 写入不阻塞当前盘面；刷新后可直接恢复已计算结果。
    if (params.historicalDocument) void writeAnalyzedCache(persistentKey, value);
    return {
      ...value,
      persistentCacheHit: false,
      localCandleCacheHit: payload.localCandleCacheHit === true,
    };
  }

  function nextPaint() {
    return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
  }

  function analysisAbortError() {
    try {
      return new DOMException("Aborted", "AbortError");
    } catch (_error) {
      const error = new Error("Aborted");
      error.name = "AbortError";
      return error;
    }
  }

  function failAnalysisWorker(slot, error) {
    const failure = error instanceof Error ? error : new Error(String(error || "analysis-worker-failed"));
    if (slot.current) {
      slot.current.reject(failure);
      slot.current = null;
    }
    slot.busy = false;
    try { slot.worker.terminate(); } catch (_error) { /* worker already stopped */ }
    state.analysisWorkers = state.analysisWorkers.filter((item) => item !== slot);
    if (!state.analysisWorkers.length) {
      state.analysisWorkerUnavailable = true;
      state.analysisQueue.splice(0).forEach((job) => job.reject(failure));
    } else pumpAnalysisWorkers();
  }

  function pumpAnalysisWorkers() {
    state.analysisWorkers.forEach((slot) => {
      if (slot.busy) return;
      let job = state.analysisQueue.shift();
      while (job?.signal?.aborted) {
        job.reject(analysisAbortError());
        job = state.analysisQueue.shift();
      }
      if (!job) return;
      slot.busy = true;
      slot.current = job;
      try {
        slot.worker.postMessage({ id: job.id, candles: job.candles, options: job.options });
      } catch (error) {
        failAnalysisWorker(slot, error);
      }
    });
  }

  function ensureAnalysisWorkers() {
    if (state.analysisWorkerUnavailable || typeof Worker !== "function") return [];
    if (state.analysisWorkers.length) return state.analysisWorkers;
    try {
      for (let index = 0; index < ANALYSIS_WORKER_COUNT; index += 1) {
        const worker = new Worker(ANALYSIS_WORKER_URL);
        const slot = { worker, busy: false, current: null };
        worker.addEventListener("message", (event) => {
          const message = event.data || {};
          const job = slot.current;
          if (!job || message.id !== job.id) return;
          slot.current = null;
          slot.busy = false;
          if (job.signal?.aborted) job.reject(analysisAbortError());
          else if (message.ok) job.resolve(message.result);
          else job.reject(new Error(message.error || "analysis-worker-failed"));
          pumpAnalysisWorkers();
        });
        worker.addEventListener("error", (event) => failAnalysisWorker(slot, event.error || event.message));
        state.analysisWorkers.push(slot);
      }
    } catch (_error) {
      state.analysisWorkerUnavailable = true;
      state.analysisWorkers.splice(0).forEach((slot) => {
        try { slot.worker.terminate(); } catch (_stopError) { /* ignore */ }
      });
    }
    return state.analysisWorkers;
  }

  async function analyzeTimeframeOffThread(candles, options, signal) {
    if (signal?.aborted) throw analysisAbortError();
    // 很短的实时窗口直接算更快；历史窗口交给后台线程，页面保持可拖动、可确认。
    if ((candles?.length || 0) < 640 || !ensureAnalysisWorkers().length) {
      await nextPaint();
      return Engine.analyzeTimeframe(candles, options);
    }
    return new Promise((resolve, reject) => {
      state.analysisQueue.push({
        id: ++state.analysisRequestId,
        candles,
        options,
        signal,
        resolve,
        reject,
      });
      pumpAnalysisWorkers();
    }).catch(async (error) => {
      if (error?.name === "AbortError") throw error;
      state.analysisWorkerUnavailable = true;
      await nextPaint();
      return Engine.analyzeTimeframe(candles, options);
    });
  }

  function provisionalChartResult(result) {
    return {
      ...result,
      signals: [],
      pending: [],
      rejected: [],
      structures: [],
      stats: {
        ...(result.stats || {}),
        signalCount: 0,
        pendingCount: 0,
        rejectedCount: 0,
      },
    };
  }

  async function loadWorkspace() {
    const pair = Data.normalizePair($("#symbolInput").value);
    const preselectedLeader = isPreselectedLeaderPair(pair);
    const analysisContext = syncHumanAnalysisControls(pair);
    const focusTime = parseInputTime($("#focusTime").value);
    const provider = $("#providerSelect").value;
    const market = $("#marketSelect").value;
    const generation = ++state.generation;
    state.loadingWorkspace = true;
    state.controller?.abort();
    state.controller = new AbortController();
    state.results.clear();
    state.failures.clear();
    $("#loadButton").disabled = true;
    $("#loadButton span").textContent = "扫描中…";
    $("#activePair").textContent = pair;
    $("#summaryStatus").textContent = "优先恢复当前周期";
    $("#summaryHint").textContent = "主图先显示，其他周期在后台完成共振验证";
    $("#sourceRoute").classList.remove("is-error");
    $("#sourceRoute span").textContent = "读取本机行情缓存；缺失部分再连接交易所…";
    state.charts.get("main").setLoading(`${Data.INTERVALS[state.activeInterval].label} · 优先读取`);

    const buildParams = (requestPair, interval) => ({
      pair: requestPair,
      interval,
      provider,
      market,
      focusTime,
      mainWaveStage: requestPair === pair ? analysisContext.mainWaveStage : "auto",
      mainWaveContextSource: requestPair === pair ? analysisContext.mainWaveContextSource : "strategy-inference",
      mainWaveContextLabel: requestPair === pair ? analysisContext.mainWaveContextLabel : "策略自动判断",
      historicalDocument: Boolean(state.activeCase?.valid && !state.activeCase.live),
      caseStart: state.activeCase?.valid && !state.activeCase.live ? state.activeCase.start : "",
      caseEnd: state.activeCase?.valid && !state.activeCase.live ? state.activeCase.end : "",
      window: state.activeCase?.valid && !state.activeCase.live
        ? Data.buildCaseWindow(state.activeCase.start, state.activeCase.end, interval)
        : Data.buildWindow(focusTime, interval),
      signal: state.controller.signal,
    });
    const loadOne = async (interval) => {
      try {
        const loaded = await loadAnalyzedInterval(buildParams(pair, interval));
        if (generation !== state.generation) return null;
        state.results.set(interval, {
          result: provisionalChartResult(loaded.result),
          baseResult: loaded.result,
          venue: loaded.venue,
          attempts: loaded.attempts,
        });
        return { interval, ok: true, ...loaded };
      } catch (error) {
        if (error?.name === "AbortError" || generation !== state.generation) return null;
        const detail = error?.attempts?.length ? `已尝试 ${error.attempts.length} 个数据源` : "公开接口不可用";
        state.failures.set(interval, `${error.message} · ${detail}`);
        return { interval, ok: false, error };
      }
    };

    // 当前周期独占首屏优先级。它完成后立即绘制 K 线与 EMA90，但在六周期
    // 共振结束前暂不显示策略标记，避免短暂展示未经环境许可的买点。
    const activeInterval = state.activeInterval;
    const activeItem = await loadOne(activeInterval);
    if (generation !== state.generation) return;
    state.lastFocusTime = focusTime;
    renderActiveChart(focusTime);
    if (activeItem?.ok) {
      $("#summaryStatus").textContent = "主图已恢复 · 后台验证多周期";
      const restoredLocally = activeItem.localPrecomputedHit || activeItem.persistentCacheHit || activeItem.localCandleCacheHit;
      $("#summaryHint").textContent = restoredLocally
        ? activeItem.localPrecomputedHit
          ? "本机预计算命中，买点与结构正在直接恢复"
          : "已从本机行情库恢复，买点将在共振计算完成后显示"
        : "当前周期已加载，其他周期继续后台读取";
      $("#sourceRoute span").textContent = `${activeItem.localPrecomputedHit ? "本机预计算命中" : restoredLocally ? "本机行情命中" : activeItem.venue.label} · ${Data.INTERVALS[activeInterval].label}主图已显示 · 后台读取其余周期`;
    }
    await nextPaint();

    // 1 分钟暂时改为按需静态图，不再后台补齐；五个主周期一次完成过门。
    const deferredIntervals = [];
    const remainingPromise = Promise.all(intervals
      .filter((interval) => interval !== activeInterval && !deferredIntervals.includes(interval))
      .map((interval) => loadOne(interval)));
    const marketContextPromise = pair === "BTCUSDT" || preselectedLeader
      ? Promise.resolve([])
      : Promise.all(["1h", "4h"].map(async (interval) => {
        try {
          const loaded = await loadAnalyzedInterval(buildParams("BTCUSDT", interval));
          return loaded.result;
        } catch (_error) {
          return null;
        }
      }));
    let settled = [activeItem, ...(await remainingPromise)];
    if (generation !== state.generation) return;
    const fetchedMarketContext = (await marketContextPromise).filter(Boolean);
    if (generation !== state.generation) return;
    const commitSettled = async (items, final) => {
      const usable = items.filter((item) => item?.ok);
      const failed = items.filter((item) => item && !item.ok);
      usable.forEach((item) => {
        if (!item.rawResult) item.rawResult = item.result;
      });
      const marketContext = pair === "BTCUSDT"
        ? usable.filter((item) => ["1h", "4h"].includes(item.interval)).map((item) => item.rawResult)
        : preselectedLeader ? [] : fetchedMarketContext;
      state.marketContextCount = marketContext.length;
      const allLocallyPrecomputed = usable.length > 0 && usable.every((item) => item.localPrecomputedHit);
      const contextGatedResults = allLocallyPrecomputed
        ? usable.map((item) => item.rawResult)
        : Engine.applyContextGates(
          usable.map((item) => item.rawResult),
          marketContext,
          {
            preselectedLeader,
            mainWaveStage: analysisContext.mainWaveStage,
            mainWaveContextSource: analysisContext.mainWaveContextSource,
            mainWaveContextLabel: analysisContext.mainWaveContextLabel,
          },
        );
      const gatedByInterval = new Map(contextGatedResults.map((result) => [result.interval, result]));
      let hydratedVisualRecords = 0;
      gatedByInterval.forEach((result) => {
        hydratedVisualRecords += hydrateVisualFeedbackForResult(result, pair);
      });
      usable.forEach((item) => {
        const baseResult = gatedByInterval.get(item.interval) || item.rawResult;
        item.result = applyFeedbackPolicy(baseResult, pair);
        state.results.set(item.interval, { result: item.result, baseResult, venue: item.venue, attempts: item.attempts });
      });
      updateFeedbackStatus();
      renderActiveChart(focusTime);
      updateSummary(usable, failed, focusTime, marketContext.length);
      renderLedger();
      if (final) {
        $("#loadButton").disabled = false;
        $("#loadButton span").textContent = "扫描起爆点";
        $("#lastUpdated").textContent = `SYNC ${formatDateTime(Date.now(), true)}`;
        state.loadingWorkspace = false;
      }
      if (hydratedVisualRecords) await persistFeedback();
    };

    await commitSettled(settled, deferredIntervals.length === 0);
    if (generation !== state.generation || !deferredIntervals.length) return;
    // 保留分段加载结构，未来恢复 1 分钟时仍可按需接回静态图任务。
  }

  function updateSummary(usable, failed, focusTime, marketContextCount = 0) {
    const pair = Data.normalizePair($("#symbolInput").value);
    const results = usable.map((item) => resultForDisplay(item.result, pair));
    const summary = Engine.summarizeTimeframes(results, focusTime);
    const higherAvailable = usable.filter((item) => ["1h", "4h", "1d"].includes(item.interval)).length;
    // 人工恢复的历史标记只用于复盘展示，不能反向制造当时的自动开仓许可。
    const latestSignals = results.flatMap((item) => item.signals)
      .filter((item) => !item.manualOverride)
      .sort((a, b) => b.time - a.time || b.score - a.score);
    const latestPending = results.flatMap((item) => item.pending || []).sort((a, b) => b.time - a.time || b.score - a.score);
    const best = latestSignals.sort((a, b) => b.score - a.score || b.time - a.time)[0] || null;
    const bestPending = latestPending.sort((a, b) => b.score - a.score || b.time - a.time)[0] || null;
    const score = clamp(summary.higherAligned * 22 + summary.bullishCount * 4 + (best ? 20 : bestPending ? 8 : 0) - Math.min(summary.rejectedCount, 8), 0, 100);
    const permission = $("#permissionRing");
    permission.style.setProperty("--score", score);
    $("#permissionScore").textContent = String(score);
    $("#bullishCount").textContent = String(summary.bullishCount);
    $("#rejectedCount").textContent = String(summary.rejectedCount);

    let status = "环境未通过 · 暂不开仓";
    let hint = "日线、4 小时和 1 小时未形成至少两个多头许可";
    let permissionTitle = "禁止追多";
    let permissionText = "大周期未共振时，即使低周期出现突破，也只记录为观察，不把杂音当作起爆点。";
    if (higherAvailable < 2) {
      status = "数据不足 · 不生成开仓许可";
      hint = "至少需要两个大周期数据源可用";
      permissionTitle = "行情证据不足";
      permissionText = "部分历史标的可能已下架；页面不会用缺失数据补造信号。";
    } else if (summary.higherAligned >= 2 && best) {
      status = "因果买点已触发 · 原点已标记";
      hint = `${best.interval} ${best.pattern}，确定性 ${best.certaintyScore ?? "—"}，策略得分 ${best.score}`;
      permissionTitle = "预设触发已经成交";
      permissionText = "绿色点只在本根从触发线下方向上穿越时成交；成交瞬间价格高于开盘，最终收盘颜色与后续涨跌都不参与判定。";
    } else if (summary.higherAligned >= 2 && bestPending) {
      status = "结构已预备 · 等待穿越触发线";
      hint = `${bestPending.interval} ${bestPending.pattern}，触发价 ${formatPrice(bestPending.triggerPrice)}`;
      permissionTitle = "预备止损买单";
      permissionText = "琥珀结构已经预备，但价格尚未从下方向上穿越触发线。";
    } else if (summary.higherAligned >= 2) {
      status = "主升环境成立 · 等待起爆";
      hint = "等待结构先完成，再预设突破触发线";
      permissionTitle = "只等第一次有效突破";
      permissionText = "环境允许，但尚无合格结构。过滤逆势、离位过远、松散结构与已经扩张后的追高点。";
    }
    $("#summaryStatus").textContent = status;
    $("#summaryHint").textContent = hint;
    $("#permissionTitle").textContent = permissionTitle;
    $("#permissionText").textContent = permissionText;
    $("#bestSignal").textContent = best ? best.pattern : bestPending ? `${bestPending.pattern}（候选）` : "—";
    $("#bestSignalMeta").textContent = best
      ? `${best.interval} · ${formatPrice(best.price)} · 确定性 ${best.certaintyScore ?? "—"} · ${best.score} 分`
      : bestPending ? `${bestPending.interval} · 确定性 ${bestPending.certaintyScore ?? "—"} · 触发 ${formatPrice(bestPending.triggerPrice)}` : "尚无可执行结构";

    const venueLabels = [...new Set(usable.map((item) => item.venue.label))];
    const loadedBars = usable.reduce((sum, item) => sum + item.result.candles.length, 0);
    const fallbackCount = usable.reduce((sum, item) => sum + (item.attempts?.length || 0), 0);
    const persistentCacheHits = usable.filter((item) => item.persistentCacheHit).length;
    const localCandleCacheHits = usable.filter((item) => item.localCandleCacheHit).length;
    const localPrecomputedHits = usable.filter((item) => item.localPrecomputedHit).length;
    const route = venueLabels.length ? `实际来源：${venueLabels.join(" / ")}` : "没有数据源返回可用行情";
    $("#sourceRoute span").textContent = `${route} · 六周期共 ${formatCompact(loadedBars)} 根 K 线${localPrecomputedHits ? ` · 本机预计算 ${localPrecomputedHits}/${usable.length}` : ""}${persistentCacheHits && !localPrecomputedHits ? ` · 策略缓存 ${persistentCacheHits}/${usable.length}` : ""}${localCandleCacheHits && !localPrecomputedHits ? ` · 本地K线 ${localCandleCacheHits}/${usable.length}` : ""}${marketContextCount ? ` · BTC 仅作背景 ${marketContextCount}/2 周期` : " · BTC 背景数据不足（不影响龙头买点）"}${state.activeCase?.valid && !state.activeCase.live ? " · 文档龙头行情已持久化" : " · 实时龙头直读交易所"}${fallbackCount ? ` · 已自动跳过 ${fallbackCount} 次无数据响应` : ""}${failed.length ? ` · ${failed.length} 个周期缺失` : ""}`;
    $("#sourceRoute").classList.toggle("is-error", usable.length === 0);
    const contextLabel = state.activeCase?.golden
      ? "TUT 起爆黄金样本"
      : state.activeCase?.live
        ? `${state.activeCase.symbol} ${state.activeCase.screenshot ? "附件标的" : "实时龙头"}`
        : state.activeCase ? `${state.activeCase.symbol} 文档样本` : "自定义观察";
    const analysisContext = analysisContextFor(pair);
    const manualContextLabel = analysisContext.mainWaveStage === "active"
      ? " · 人工确认主升"
      : analysisContext.mainWaveStage === "expected"
        ? " · 人工主升预期"
        : "";
    $("#activeContext").textContent = `${contextLabel}${manualContextLabel} · ${venueLabels.join(" / ") || "行情不可用"}`;

    updateRule("environment", higherAvailable >= 2 ? summary.higherAligned >= 2 : null, summary.higherAligned >= 2 ? `${summary.higherAligned}/3 通过` : higherAvailable < 2 ? "证据不足" : "未通过");
    const structure = best || bestPending;
    updateRule("structure", structure ? true : null, structure ? `${structure.pattern} · 结构质量 ${Math.round((structure.structureQuality || 0) * 100)}%` : "等待结构");
    updateRule("trigger", best ? true : bestPending ? null : null, best ? `${best.interval} · 向上穿越` : bestPending ? `预备 ${formatPrice(bestPending.triggerPrice)}` : "等待结构");
    updateRule("flow", structure ? true : null, structure ? `${structure.sentimentPhase} · 订单流${structure.orderFlowScore} · ${structure.marketEmotion || `结构共振${structure.confluence?.length || 1}`}` : "等待节奏");
    updateRule("veto", summary.rejectedCount > 0 ? false : null, summary.rejectedCount > 0 ? `拦截 ${summary.rejectedCount}` : "无异常");
  }

  function updateRule(name, pass, text) {
    const row = $(`[data-rule="${name}"]`);
    row.classList.remove("is-pass", "is-veto");
    if (pass === true) row.classList.add("is-pass");
    if (pass === false && name !== "veto") row.classList.add("is-veto");
    if (name === "veto" && pass === false) row.classList.add("is-pass");
    $("em", row).textContent = text;
  }

  function ledgerItems() {
    const pair = Data.normalizePair($("#symbolInput").value);
    const generated = [...state.results.values()].flatMap(({ result, venue }) => {
      const activeTimes = new Set([
        ...(result.signals || []),
        ...(result.secondaryBreakoutHints || []),
        ...(result.pending || []),
      ].map((item) => Number(item.time)));
      const quietCandidates = (result.retainedCandidates || [])
        .filter((item) => !activeTimes.has(Number(item.time)));
      const quietCandidateTimes = new Set(quietCandidates.map((item) => Number(item.time)));
      return [
        ...(result.signals || []).map((item) => ({ ...item, venue: venue.label })),
        ...(result.secondaryBreakoutHints || []).map((item) => ({ ...item, venue: venue.label })),
        ...(result.pending || []).map((item) => ({ ...item, venue: venue.label })),
        ...quietCandidates.map((item) => ({ ...item, venue: venue.label })),
        ...(result.rejected || [])
          .filter((item) => !quietCandidateTimes.has(Number(item.time)))
          .map((item) => ({ ...item, venue: venue.label })),
      ];
    }).filter((item) => signalDisplayAllowed(item, pair))
      .map((item) => ({ ...item, feedbackKey: item.feedbackKey || Feedback.signalKey(pair, item) }));
    const generatedKeys = new Set(generated.map((item) => item.feedbackKey).filter(Boolean));
    const retained = Object.values(state.feedback.records || {})
      .filter((record) => record.decision !== "cleared"
        && record.pair === pair
        && !generatedKeys.has(record.key)
        && signalDisplayAllowed({ ...record.signal, manualDecision: record.decision }, pair))
      .map((record) => ({
        ...record.signal,
        feedbackKey: record.key,
        pair: record.pair,
        interval: record.interval,
        venue: record.venue || "永久反馈",
        status: record.decision === "confirmed" ? "buy" : record.decision === "pending" ? "pending" : "filtered",
        manualDecision: record.decision,
        manualConfirmed: record.decision === "confirmed",
        manualOverride: record.decision === "confirmed",
        manualRestored: true,
        reasons: record.decision === "denied" ? ["用户已彻底否定：永久黑名单"] : (record.signal.reasons || []),
      }));
    return [...generated, ...retained].sort((a, b) => b.time - a.time || b.score - a.score);
  }

  function feedbackRecordFor(item) {
    const key = item.feedbackKey || Feedback.signalKey(Data.normalizePair($("#symbolInput").value), item);
    const record = key ? state.feedback.records[key] || null : null;
    return record?.decision === "cleared" ? null : record;
  }

  function recordFeedback(item, decision, certaintyGrade = null, structureTags = null) {
    const pair = Data.normalizePair($("#symbolInput").value);
    const key = item.feedbackKey || Feedback.signalKey(pair, item);
    if (!key || !["confirmed", "pending", "denied", "cleared"].includes(decision)) return;
    const existing = state.feedback.records[key];
    const requestedGrade = decision === "cleared"
      ? ""
      : Feedback.normalizeCertaintyGrade(certaintyGrade ?? existing?.certaintyGrade ?? item.manualCertaintyGrade);
    const predictedStructureTags = decision === "cleared"
      ? []
      : Feedback.normalizeStructureTags(
        existing?.predictedStructureTags
        || item.predictedStructureTags
        || Feedback.inferStructureTags(item),
      );
    const requestedStructureTags = decision === "cleared"
      ? []
      : Feedback.normalizeStructureTags(
        structureTags
        ?? existing?.structureTags
        ?? item.manualStructureTags
        ?? predictedStructureTags,
      );
    const structureReview = decision === "cleared"
      ? null
      : Feedback.compareStructureTags(predictedStructureTags, requestedStructureTags);
    const existingTags = Feedback.normalizeStructureTags(existing?.structureTags);
    const visualRangeChanged = Number(existing?.signal?.visualStructureStartTime || 0) !== Number(item.visualStructureStartTime || 0)
      || String(existing?.signal?.visualStructureSource || "") !== String(item.visualStructureSource || "")
      || Number(existing?.signal?.visualSignature?.version || 0) !== Number(item.visualSignature?.version || 0);
    if (existing?.decision === decision
      && (existing?.certaintyGrade || "") === requestedGrade
      && JSON.stringify(existingTags) === JSON.stringify(requestedStructureTags)
      && !visualRangeChanged) return;
    const now = Date.now();
    const snapshot = decision === "cleared" && existing?.signal
      ? existing.signal
      : Feedback.snapshotSignal({
        ...item,
        manualCertaintyGrade: requestedGrade,
        manualStructureTags: requestedStructureTags,
        predictedStructureTags,
        structureReview,
      });
    const update = Feedback.normalizeDocument({
      version: 1,
      updatedAt: now,
      records: {
        [key]: {
          key,
          decision,
          optimizationLabel: decision === "confirmed" ? 1 : decision === "denied" ? -1 : 0,
          optimizationRole: decision === "confirmed" ? "positive" : decision === "denied" ? "negative" : decision === "pending" ? "unlabeled" : "deleted",
          datasetVersion: Feedback.DATASET_VERSION,
          createdAt: existing?.createdAt || now,
          updatedAt: now,
          pair,
          interval: item.interval,
          venue: item.venue || "",
          certaintyGrade: requestedGrade,
          structureTags: requestedStructureTags,
          predictedStructureTags,
          structureReview,
          signal: snapshot,
        },
      },
    });
    // 单条归一化后直接写入内存，避免每点一个标签都重新清洗数百条视觉快照。
    state.feedback.records[key] = update.records[key];
    state.feedback.updatedAt = Math.max(Number(state.feedback.updatedAt) || 0, now);
    state.feedbackDirtyKeys.add(key);
    queueBrowserFeedbackWrite();
    updateFeedbackStatus();
    queueLoadedFeedbackRefresh();
    queueFeedbackPersistence();
    return update.records[key];
  }

  function renderLedger() {
    const container = $("#signalLedger");
    const filter = state.ledgerFilter;
    const items = ledgerItems().filter((item) => (
      filter === "all"
      || (filter === "confirmed" ? item.manualDecision === "confirmed" : item.status === filter)
    )).slice(0, 64);
    if (!items.length) {
      container.innerHTML = `<div class="ledger-empty"><i></i><strong>当前窗口无${filter === "filtered" ? "过滤记录" : filter === "buy" ? "因果买点" : filter === "confirmed" ? "永久确认买点" : filter === "pending" ? "预备触发" : "结构信号"}</strong><span>拖动观察锚点或切换标的继续扫描</span></div>`;
      return;
    }
    container.innerHTML = items.map((item) => {
      const reasons = Array.isArray(item.reasons) ? item.reasons : [];
      const evidence = Array.isArray(item.evidence) ? item.evidence : [];
      const detail = item.secondaryBreakoutHint && item.alertOnly
        ? `${evidence.slice(-3).join("；")}；红色B只作防洗踏空提示，可弹窗和语音，不属于绿色正式买点，也不进入自动执行`
        : item.status === "candidate"
        ? `${(item.candidateReasons || item.reasons || []).join("；")}；只保留复盘，不显示B、不弹窗、不语音播报`
        : item.status === "filtered"
        ? reasons.join("；")
        : item.status === "pending"
          ? item.visualPreconfirmed
            ? `视觉预确认：正样本 ${item.visualLearning?.positiveSimilarity || 0}% / 反例 ${item.visualLearning?.negativeSimilarity || 0}%；等待人工校验，不自动买入`
          : item.manualCandleSelection
            ? "人工补标待定；已保存该 K 线及其决策时上下文，不参与正负权重"
            : `${evidence.slice(-2).join("；")}；价格尚未穿越 ${formatPrice(item.triggerPrice)}`
          : evidence.slice(-3).join("；") || (item.manualRestored ? "永久确认买点；当前行情窗口未覆盖原始 K 线" : "已记录策略买点");
      const feedbackRecord = feedbackRecordFor(item);
      const certaintyGrade = feedbackRecord?.certaintyGrade || item.manualCertaintyGrade || "";
      const structureTags = Feedback.normalizeStructureTags(feedbackRecord?.structureTags || item.manualStructureTags);
      const structureSummary = structureTags.map((tag) => STRUCTURE_TAG_LABELS[tag]).filter(Boolean).join(" / ");
      const className = `${item.secondaryBreakoutHint ? "is-secondary-hint" : item.status === "buy" ? "is-buy" : item.status === "pending" ? "is-pending" : item.status === "candidate" ? "is-candidate" : "is-filtered"} ${item.visualPreconfirmed ? "is-visual" : ""} ${feedbackRecord?.decision === "confirmed" ? "is-confirmed" : feedbackRecord?.decision === "pending" ? "is-review-pending" : feedbackRecord?.decision === "denied" ? "is-denied" : ""}`;
      const badge = feedbackRecord?.decision === "confirmed" ? "LOCKED" : item.secondaryBreakoutHint ? "B!" : item.status === "buy" ? "IGNITE" : item.visualPreconfirmed ? "VISION" : item.status === "pending" ? "WATCH" : item.status === "candidate" ? "CANDIDATE" : "VETO";
      const feedbackLabel = feedbackRecord?.decision === "confirmed"
        ? "正样本 · 永久保留并提升同类结构权重"
        : feedbackRecord?.decision === "pending"
          ? "待定 · 永久保留复盘状态，不进入权重学习"
        : feedbackRecord?.decision === "denied"
          ? "永久黑名单 · 反例参与下轮优化"
          : item.secondaryBreakoutHint
            ? "防洗二次突破提示 · 仅提醒，不自动执行"
          : item.visualPreconfirmed
            ? `视觉学习 · 正样本 ${item.visualLearning?.positiveSimilarity || 0}% / 反例 ${item.visualLearning?.negativeSimilarity || 0}%`
          : item.status === "candidate"
            ? "安静候选 · 人工确认后才会永久显示为B"
          : item.feedbackAdjustment
            ? `同类权重 ${item.feedbackAdjustment > 0 ? "+" : ""}${item.feedbackAdjustment} 分`
            : "反馈会进入下一轮优化 · 同类结构最多调整 ±6 分";
      return `<article class="ledger-item ${className}">
        <header><b>${badge}</b><strong>${escapeHtml(item.interval)} · ${escapeHtml(item.pattern)}</strong><time>${escapeHtml(formatDateTime(item.time))}</time></header>
        <p>${escapeHtml(detail)}</p>
        <footer><span>${escapeHtml(item.venue)} · ${escapeHtml(formatPrice(item.price))}</span><span>${item.manualCandleSelection ? `人工补标 · 确定性 ${escapeHtml(certaintyGrade || "未评级")}` : item.status === "filtered" ? `已过滤 · ${escapeHtml(reasons[0] || "结构事实未通过")}` : `确定性 ${Number.isFinite(Number(item.certaintyScore)) ? item.certaintyScore : "—"} · 评分 ${Number.isFinite(Number(item.score)) ? item.score : "—"}`}</span></footer>
        <div class="ledger-certainty"><span>确定性</span><button type="button" data-certainty-grade="A+" data-feedback-key="${escapeHtml(item.feedbackKey)}" class="${certaintyGrade === "A+" ? "is-active" : ""}">A+</button><button type="button" data-certainty-grade="A" data-feedback-key="${escapeHtml(item.feedbackKey)}" class="${certaintyGrade === "A" ? "is-active" : ""}">A</button><button type="button" data-certainty-grade="B" data-feedback-key="${escapeHtml(item.feedbackKey)}" class="${certaintyGrade === "B" ? "is-active" : ""}">B</button></div>
        ${structureSummary ? `<div class="ledger-structure-summary"><span>人工结构</span><b>${escapeHtml(structureSummary)}</b></div>` : ""}
        <div class="ledger-feedback"><span>${escapeHtml(feedbackLabel)}</span><button type="button" data-feedback-action="confirmed" data-feedback-key="${escapeHtml(item.feedbackKey)}" class="${feedbackRecord?.decision === "confirmed" ? "is-active" : ""}">${feedbackRecord?.decision === "confirmed" ? "已确认" : "确认"}</button><button type="button" data-feedback-action="pending" data-feedback-key="${escapeHtml(item.feedbackKey)}" class="${feedbackRecord?.decision === "pending" ? "is-active" : ""}">${feedbackRecord?.decision === "pending" ? "待定中" : "待定"}</button><button type="button" data-feedback-action="denied" data-feedback-key="${escapeHtml(item.feedbackKey)}" class="${feedbackRecord?.decision === "denied" ? "is-active" : ""}">${feedbackRecord?.decision === "denied" ? "已彻底否定" : "彻底否定"}</button>${feedbackRecord ? `<button type="button" data-feedback-action="cleared" data-feedback-key="${escapeHtml(item.feedbackKey)}">取消此标记</button>` : ""}</div>
      </article>`;
    }).join("");
  }

  function bindControls() {
    $("#loadButton").addEventListener("click", loadWorkspace);
    $("#refreshLeaders").addEventListener("click", loadLiveLeaders);
    $("#focusTime").addEventListener("change", updateCaseProgress);
    $("#symbolInput").addEventListener("change", () => {
      $("#caseSelect").value = "custom";
      applyCase(null);
      $("#activePair").textContent = Data.normalizePair($("#symbolInput").value);
      syncHumanAnalysisControls();
    });
    $("#mainWaveMode").addEventListener("change", () => {
      saveHumanAnalysisContext();
      void loadWorkspace();
    });
    $("#analysisNote").addEventListener("change", saveHumanAnalysisContext);
    $("#jumpNow").addEventListener("click", () => {
      $("#caseSelect").value = "custom";
      applyCase(null);
      $("#focusTime").value = toLocalInput(Date.now());
      loadWorkspace();
    });
    $("#showRejected").addEventListener("change", (event) => {
      state.charts.forEach((chart) => chart.setRejectedVisibility(event.target.checked));
    });
    $$('[data-timeframe]').forEach((button) => button.addEventListener("click", () => {
      state.activeInterval = button.dataset.timeframe;
      renderActiveChart();
    }));
    $$('[data-draw-tool]').forEach((button) => button.addEventListener("click", () => {
      state.drawTool = button.dataset.drawTool;
      $$('[data-draw-tool]').forEach((item) => item.classList.toggle("is-active", item === button));
      state.charts.forEach((chart) => chart.setDrawTool(state.drawTool));
    }));
    $("#clearDrawings").addEventListener("click", () => {
      state.charts.forEach((chart) => chart.clearAnnotations());
    });
    $("#exportChartImage").addEventListener("click", exportActiveChartImage);
    $("#signalLedger").addEventListener("click", (event) => {
      const certaintyButton = event.target.closest("[data-certainty-grade]");
      if (certaintyButton) {
        const item = ledgerItems().find((entry) => entry.feedbackKey === certaintyButton.dataset.feedbackKey);
        if (item) {
          const record = feedbackRecordFor(item);
          recordFeedback(item, record?.decision || "pending", certaintyButton.dataset.certaintyGrade);
        }
        return;
      }
      const button = event.target.closest("[data-feedback-action]");
      if (!button) return;
      const item = ledgerItems().find((entry) => entry.feedbackKey === button.dataset.feedbackKey);
      if (item) recordFeedback(item, button.dataset.feedbackAction);
    });
    $$("[data-ledger-filter]").forEach((button) => button.addEventListener("click", () => {
      $$("[data-ledger-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
      state.ledgerFilter = button.dataset.ledgerFilter;
      renderLedger();
    }));
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const activeFeedback = [...state.charts.values()].find((chart) => chart.selectedFeedbackKey);
      if (activeFeedback) {
        activeFeedback.closeFeedbackPopover();
        return;
      }
      if (state.drawTool !== "pan") {
        state.drawTool = "pan";
        $$('[data-draw-tool]').forEach((item) => item.classList.toggle("is-active", item.dataset.drawTool === "pan"));
        state.charts.forEach((chart) => chart.setDrawTool("pan"));
        return;
      }
      const expanded = $(".timeframe-card.is-expanded");
      if (!expanded) return;
      expanded.classList.remove("is-expanded");
      $("[data-expand]", expanded).textContent = "↗";
      document.body.style.overflow = "";
      state.charts.get("main").resize();
    });
  }

  setupCharts();
  setupCases();
  bindControls();
  window.addEventListener("pagehide", flushBrowserFeedbackWrite);
  renderLeaderUniverse();
  // 行情首屏不再等待反馈账号与实时龙头池网络请求；二者完成后会自行刷新
  // 已加载结果。这样刷新页面时，当前 K 线盘面拥有最高加载优先级。
  void initializeFeedback();
  void loadWorkspace();
  void loadLiveLeaders();
})();
