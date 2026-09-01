"use strict";

// 历史龙头的一分钟窗口可能包含数万根 K 线。策略分析放在 Worker 内，
// 主页面只负责绘图和人工交互，避免后台补齐周期时冻结鼠标与确认按钮。
importScripts("./dragon-wave-vision.js?v=66", "./dragon-wave-engine.js?v=89");

self.addEventListener("message", (event) => {
  const payload = event.data || {};
  try {
    const result = self.DragonWaveEngine.analyzeTimeframe(payload.candles || [], payload.options || {});
    self.postMessage({ id: payload.id, ok: true, result });
  } catch (error) {
    self.postMessage({
      id: payload.id,
      ok: false,
      error: error?.message || String(error || "analysis-worker-failed"),
    });
  }
});
