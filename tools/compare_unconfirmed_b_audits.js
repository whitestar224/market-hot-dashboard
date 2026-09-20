"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { classify, normalizeFixture } = require("./audit_unconfirmed_b_points.js");
const Engine = require("../dragon-wave-engine.js");
const ROOT = path.resolve(__dirname, "..");
const out = path.join(ROOT, "deliverables/2026-09-07-unconfirmed-b-audit");
const args = Object.fromEntries(process.argv.slice(2).map(arg => { const p = arg.indexOf("="); return p < 0 ? [arg.slice(2), true] : [arg.slice(2, p), arg.slice(p + 1)]; }));
const beforeLabel = args.before || "baseline", afterLabel = args.after || "after";
const before = JSON.parse(fs.readFileSync(path.join(out, `${beforeLabel}.json`), "utf8"));
const after = JSON.parse(fs.readFileSync(path.join(out, `${afterLabel}.json`), "utf8"));
if (args.checkpoint) {
  // Freeze analysis reports, not candles/feedback, before later frames update
  // the cross-timeframe context of an in-progress scan.
  fs.writeFileSync(path.join(out, `${beforeLabel}-${args.checkpoint}.json`), JSON.stringify(before, null, 2));
  fs.writeFileSync(path.join(out, `${afterLabel}-${args.checkpoint}.json`), JSON.stringify(after, null, 2));
}
const beforeSources = new Map(before.sources.map(source => [source.id, source]));
const commonSources = after.sources.filter(source => {
  const prior = beforeSources.get(source.id);
  return source.status === "complete" && prior?.status === "complete"
    && source.bars === prior.bars && source.start === prior.start && source.end === prior.end
    && JSON.stringify(source.contextIntervals) === JSON.stringify(prior.contextIntervals);
}).map(source => source.id);
const sourceIds = new Set(commonSources);
function unique(rows) {
  const result = new Map();
  for (const row of rows.filter(row => sourceIds.has(row.sourceId))) {
    const bars = row.sourceBars || beforeSources.get(row.sourceId)?.bars || 0;
    const old = result.get(row.key);
    const oldBars = old?.sourceBars || beforeSources.get(old?.sourceId)?.bars || 0;
    if (!old || bars > oldBars) result.set(row.key, row);
  }
  return result;
}
const beforeSignals = unique(before.signals), afterSignals = unique(after.signals);
const removed = [...beforeSignals.values()].filter(row => row.native && !afterSignals.get(row.key)?.native);
const added = [...afterSignals.values()].filter(row => row.native && !beforeSignals.get(row.key)?.native);
const sourcePayloads = new Map();
function hierarchyReplay(row) {
  const start = row.signal.triangleLines?.structureStartIndex ?? row.signal.triangleLines?.upper?.startIndex;
  if (!Number.isInteger(start) || start < 0 || start >= row.signal.index) return null;
  const sourceFile = row.sourceId.split("#")[0];
  if (!sourcePayloads.has(sourceFile)) sourcePayloads.set(sourceFile, JSON.parse(fs.readFileSync(path.join(ROOT, sourceFile), "utf8").replace(/^\uFEFF/, "")));
  const payload = sourcePayloads.get(sourceFile);
  const candles = Array.isArray(payload) ? normalizeFixture(sourceFile, payload, row.interval) : payload.intervals?.[row.interval]?.candles;
  if (!candles?.length) return null;
  const triangleMotherHigh = Math.max(...candles.slice(start, row.signal.index).map(candle => candle.high));
  const prior = row.signal.previousHighLevel;
  const isOuter = typeof prior === "number" && prior > 0 && Number.isFinite(triangleMotherHigh)
    ? prior >= triangleMotherHigh * (1 - 1e-10) : null;
  const hierarchy = Engine.assessExecutionHierarchy({ ...row.signal, triangleMotherHigh, trianglePreviousHighIsOuter: isOuter });
  return { triangleMotherHigh, previousHighLevel: prior, trianglePreviousHighIsOuter: isOuter, hierarchy, diagnosticOnly: "使用已存信号及当时前缀行情复核层级规则，不代替完整重跑；是否移除以完整同范围optimized结果为准。" };
}
const detail = row => ({ key: row.key, pair: row.pair, interval: row.interval, localTime: row.localTime, reviewDecision: row.reviewDecision, sourceId: row.sourceId, sourceBars: row.sourceBars, pattern: row.signal.pattern, hierarchy: row.signal.executionHierarchy, reasonsAfter: after.reviews.find(review => review.sourceId === row.sourceId && review.key === row.key)?.reasons || [] });
const labelChanges = [...beforeSignals.values()].filter(row => row.native && afterSignals.get(row.key)?.native && row.signal.pattern !== afterSignals.get(row.key).signal.pattern)
  .map(row => ({ key: row.key, pair: row.pair, interval: row.interval, localTime: row.localTime, reviewDecision: row.reviewDecision, inDocumentWindow: row.inDocumentWindow, before: row.signal.pattern, after: afterSignals.get(row.key).signal.pattern, urgent: row.signal.horizontalLaunchUrgent, horizontalLaunchInsufficientEdgeDwell: row.signal.horizontalLaunchInsufficientEdgeDwell }));
const priorUnsupported = [...beforeSignals.values()].filter(row => row.native && classify(row.signal).flags.includes("unsupported-previous-high-booster"));
const afterUnsupported = [...afterSignals.values()].filter(row => row.native && classify(row.signal).flags.includes("unsupported-previous-high-booster"));
const beforeReviewed = unique(before.reviews), afterReviewed = unique(after.reviews);
const confirmedReviewed = [...beforeReviewed.values()].filter(row => row.reviewDecision === "confirmed");
const deniedReviewed = [...beforeReviewed.values()].filter(row => row.reviewDecision === "denied");
const reviewResult = {
  confirmedCoveredUnique: confirmedReviewed.length,
  confirmedNativeBefore: confirmedReviewed.filter(row => row.native).length,
  confirmedNativeAfter: confirmedReviewed.filter(row => afterReviewed.get(row.key)?.native).length,
  confirmedNativeLost: confirmedReviewed.filter(row => row.native && !afterReviewed.get(row.key)?.native).map(detail),
  confirmedDisplayedBefore: confirmedReviewed.filter(row => row.displayed).length,
  confirmedDisplayedAfter: confirmedReviewed.filter(row => afterReviewed.get(row.key)?.displayed).length,
  deniedCoveredUnique: deniedReviewed.length,
  deniedNativeBefore: deniedReviewed.filter(row => row.native).length,
  deniedNativeAfter: deniedReviewed.filter(row => afterReviewed.get(row.key)?.native).length,
};
const report = {
  generatedAt: Date.now(), beforeEngineSha256: before.engineSha256, afterEngineSha256: after.engineSha256,
  beforeStatus: before.status, afterStatus: after.status, commonSources,
  scope: "只比较相同来源、相同根数/日期、相同可用跨周期上下文的完成结果；未来收益未参与。",
  beforeNative: [...beforeSignals.values()].filter(row => row.native).length,
  afterNative: [...afterSignals.values()].filter(row => row.native).length,
  removed: removed.map(detail), added: added.map(detail), reviewResult,
  documentWindow: {
    beforeNative: [...beforeSignals.values()].filter(row => row.native && row.inDocumentWindow).length,
    afterNative: [...afterSignals.values()].filter(row => row.native && row.inDocumentWindow).length,
    beforeUnconfirmedB: [...beforeSignals.values()].filter(row => row.targetUnconfirmedBInDocument).length,
    afterUnconfirmedB: [...afterSignals.values()].filter(row => row.targetUnconfirmedBInDocument).length,
    removed: removed.filter(row => row.inDocumentWindow).map(detail),
    added: added.filter(row => row.inDocumentWindow).map(detail),
  },
  labelChanges,
  unsupportedPreviousHighBoosterBefore: priorUnsupported.map(detail),
  unsupportedPreviousHighBoosterAfter: afterUnsupported.map(detail),
};
for (const row of report.removed) {
  const replay = hierarchyReplay(beforeSignals.get(row.key));
  if (replay) {
    row.causalHierarchyReplay = replay;
    if (replay.hierarchy.permit === false) row.reasonsAfter = [...new Set([...row.reasonsAfter, ...replay.hierarchy.missingLabels])];
  }
}
const reviewRows = [...beforeSignals.values()].filter(row => row.targetUnconfirmedBInDocument).sort((a, b) => a.pair.localeCompare(b.pair) || a.interval.localeCompare(b.interval) || a.time - b.time).map(row => {
  const current = afterSignals.get(row.key), removedDetail = report.removed.find(detail => detail.key === row.key);
  const labelsChanged = labelChanges.find(change => change.key === row.key);
  const flags = classify(row.signal).flags;
  let reason = "未发现足够理由删除；未确认不等于差，本次不将它认定为已验证好机会。";
  if (!current?.native) reason = removedDetail?.reasonsAfter.length ? removedDetail.reasonsAfter.join("；") : "相同数据完整重放不再产生此原生B；未单凭低量/少触边下结论。";
  else if (labelsChanged) reason = "保留结构突破B；末段急促，不再称为横盘起飞。";
  else if (flags.includes("unsupported-previous-high-booster")) reason = "保留结构本身；去掉没有有效价格依据的前高辅助共振。";
  else if (flags.includes("quiet-young-pure-base")) reason = "量能和主动流偏弱、上沿触碰较少，列为一般候选复核；完整平台仍有独立依据，未发现足够理由直接删除。";
  return { key: row.key, pair: row.pair, interval: row.interval, time: row.time, localTime: row.localTime, reviewDecision: row.reviewDecision, beforePattern: row.signal.pattern, afterPattern: current?.signal.pattern || "", action: current?.native ? labelsChanged ? "保留B，修正子标签" : "保留，暂不新增否定" : "移除原生B", reason, relativeVolume: row.signal.relativeVolume, orderFlowScore: row.signal.orderFlowScore, consolidationBars: row.signal.consolidationBars, ceilingTouches: row.signal.ceilingTouches };
});
report.unconfirmedInDocumentReview = reviewRows;
const outputLabel = args.output || (afterLabel === "after" ? "comparison" : `comparison-${afterLabel}`);
fs.writeFileSync(path.join(out, `${outputLabel}.json`), JSON.stringify(report, null, 2));
fs.writeFileSync(path.join(out, `${outputLabel}.md`), [
  "# 同范围原生策略对照", "", report.scope, "",
  `- 完成并可比较的数据源：${commonSources.length}`,
  `- 原生 B：${report.beforeNative} → ${report.afterNative}`,
  `- 其中文档日期内原生 B：${report.documentWindow.beforeNative} → ${report.documentWindow.afterNative}；未确认 B：${report.documentWindow.beforeUnconfirmedB} → ${report.documentWindow.afterUnconfirmedB}`,
  `- 删除 / 新增：${removed.length} / ${added.length}`,
  `- 无有效前高证据却标记 previous-high：${priorUnsupported.length} → ${afterUnsupported.length}`,
  `- 覆盖已确认点：${reviewResult.confirmedCoveredUnique}；原生命中：${reviewResult.confirmedNativeBefore} → ${reviewResult.confirmedNativeAfter}`,
  `- 覆盖否定点：${reviewResult.deniedCoveredUnique}；原生买点冲突：${reviewResult.deniedNativeBefore} → ${reviewResult.deniedNativeAfter}`,
  "", "## 删除的原生 B", "", ...(removed.length ? removed.map(row => `- ${row.pair} ${row.interval} ${row.localTime}（${row.reviewDecision}）`) : ["- 无"]),
  "", "## 新增的原生 B", "", ...(added.length ? added.map(row => `- ${row.pair} ${row.interval} ${row.localTime}（${row.reviewDecision}）`) : ["- 无"]),
  "", "## 保留 B，仅修正名称", "", ...(labelChanges.length ? labelChanges.map(row => `- ${row.pair} ${row.interval} ${row.localTime}：${row.before} → ${row.after}`) : ["- 无"]),
  "", "这里只验证当前本地可用行情。人工恢复不算原生命中，不宣称全量确认库或实盘表现已验证。", "",
].join("\n"));
if (afterLabel === "optimized") {
  const escape = text => String(text ?? "").replaceAll("|", "/").replaceAll("\n", " ");
  fs.writeFileSync(path.join(out, "unconfirmed-b-review.md"), [
    "# 文档日期内未确认原生 B：逐点复核清单", "",
    `共 ${reviewRows.length} 个去重基线点位。只包含两次重放具有相同覆盖范围和跨周期上下文的来源。`,
    "未确认不等于质量差。保留表示本次未发现足够理由删除，不代表已经证明是好机会。未使用后续涨跌定性。", "",
    "| 币种 | 周期 | 北京时间 | 用户状态 | 原始模式 | 优化结果 | 可核实原因 |",
    "|---|---|---|---|---|---|---|",
    ...reviewRows.map(row => `| ${escape(row.pair)} | ${escape(row.interval)} | ${row.localTime} | ${row.reviewDecision} | ${escape(row.beforePattern)} | ${row.action} | ${escape(row.reason)} |`), "",
  ].join("\n"));
  const csvQuote = value => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const columns = ["pair", "interval", "localTime", "reviewDecision", "beforePattern", "afterPattern", "action", "reason", "relativeVolume", "orderFlowScore", "consolidationBars", "ceilingTouches"];
  fs.writeFileSync(path.join(out, "unconfirmed-b-review.csv"), "\uFEFF" + [columns.join(","), ...reviewRows.map(row => columns.map(column => csvQuote(row[column])).join(","))].join("\n"));
}
console.log(JSON.stringify({ commonSources: commonSources.length, beforeNative: report.beforeNative, afterNative: report.afterNative, removed: removed.length, added: added.length, unsupportedBefore: priorUnsupported.length, unsupportedAfter: afterUnsupported.length, reviewResult }, null, 2));
