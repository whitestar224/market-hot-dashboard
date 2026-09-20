#!/usr/bin/env node
"use strict";

// Read-only production-cache inspection. Never downloads candles, evaluates
// strategy entries, or writes to the feedback database/production cache.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const Data = require("../dragon-wave-data.js");
const Feedback = require("../dragon-wave-feedback.js");
const Cases = require("../dragon-wave-cases.js");
const Compatibility = require("./dragon_wave_cache_compatibility.js");
const ROOT = path.resolve(__dirname, "..");
const INTERVALS = Object.freeze(["5m", "15m", "1h", "4h", "1d"]);

function expectedTasks(catalog, version, intervals = INTERVALS) {
  return catalog.filter(item => item.valid === true).flatMap(item => intervals.map(interval => ({
    key: [version, Data.normalizePair(item.pair), item.start, item.end, interval, "futures", "active"].join("|"),
    caseId: item.id, pair: Data.normalizePair(item.pair), symbol: item.symbol,
    start: item.start, end: item.end, interval, market: "futures", mainWaveStage: "active",
  })));
}

function cachePath(cacheRoot, file) {
  if (typeof file !== "string" || !file || path.isAbsolute(file)) return null;
  const root = path.resolve(cacheRoot), target = path.resolve(root, file);
  return target.startsWith(root + path.sep) && /\.json\.gz$/i.test(target) ? target : null;
}

function loadFeedbackDatabase(filename) {
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(filename, { readOnly: true });
  let rows;
  try { rows = db.prepare("SELECT payload FROM feedback_documents ORDER BY updated_at ASC").all(); } finally { db.close(); }
  const merged = {}, invalidRecords = [];
  for (const row of rows) for (const [key, record] of Object.entries(JSON.parse(row.payload).records || {})) {
    if (!merged[key] || Number(record.updatedAt || 0) >= Number(merged[key].updatedAt || 0)) merged[key] = record;
  }
  const records = {};
  for (const [key, record] of Object.entries(merged)) {
    const expected = Feedback.signalKey(record.pair, { ...record.signal, interval: record.interval });
    if (key !== expected || record.pair === "TESTUSDT") invalidRecords.push({ key, expected, reason: key !== expected ? "key/pair/interval/time mismatch" : "test symbol" });
    else records[key] = record;
  }
  return { document: { version: 1, records }, deviceDocuments: rows.length, mergedRecords: Object.keys(merged).length, invalidRecords };
}

function verifyStoredResult(payload, task, options = {}) {
  const errors = [];
  if (payload?.version !== options.version || payload?.key !== task.key) errors.push("缓存版本或任务key不匹配");
  for (const field of ["pair", "interval", "start", "end", "market", "mainWaveStage"]) if (payload?.[field] !== task[field]) errors.push(`缓存字段 ${field} 不匹配`);
  const acceptedSource = options.compatibilityAssessment?.compatible === true
    && options.compatibilityAssessment.payloads?.[task.key] === payload;
  if (payload?.engineSha256 !== options.engineSha256 && !acceptedSource) errors.push("缓存引擎指纹不是当前代码且无已验证兼容凭据");
  if (payload?.contextComplete !== true || !options.intervals.every(interval => payload?.contextIntervals?.includes(interval))) errors.push("缓存不是全部周期完成后的最终上下文");
  const result = payload?.result, candles = result?.candles;
  if (!Array.isArray(candles) || !candles.length) return { valid: false, errors: [...errors, "缓存没有K线"], confirmed: [] };
  if (result.interval !== task.interval) errors.push("result.interval不匹配");
  const window = Data.buildCaseWindow(task.start, task.end, task.interval, options.now);
  const coverage = Data.assessCandleCoverage(candles, window, task.interval);
  if (!Data.isCandleCoverageAcceptable(candles, window, task.interval)) errors.push("指定文档日期的行情覆盖或连续性不足");
  let previousTime = -Infinity, invalidCandles = 0;
  for (const candle of candles) {
    const prices = [candle.open, candle.high, candle.low, candle.close];
    if (!Number.isFinite(candle.time) || candle.time <= previousTime || !prices.every(price => typeof price === "number" && Number.isFinite(price) && price > 0)
      || candle.high < Math.max(candle.open, candle.close, candle.low) || candle.low > Math.min(candle.open, candle.close, candle.high)) invalidCandles++;
    previousTime = candle.time;
  }
  if (invalidCandles) errors.push(`${invalidCandles} 根K线乱序、重复或价格非法`);
  const nativeSignals = (result.signals || []).filter(signal => !signal.manualRestored && !(signal.manualOverride && signal.strategyStatusBeforeFeedback !== "buy"));
  const nativeTimes = new Set(nativeSignals.map(signal => signal.time));
  let displayed = result;
  if (options.feedbackDocument) displayed = Feedback.applyToResult(result, task.pair, options.feedbackDocument, options.feedbackContext);
  const displayedTimes = new Set((displayed.signals || []).map(signal => signal.time));
  const candleTimes = new Set(candles.map(candle => candle.time));
  const sourceStart = Date.parse(`${task.start}T00:00:00+08:00`), sourceEnd = Date.parse(`${task.end}T23:59:59.999+08:00`);
  const confirmed = Object.entries(options.feedbackDocument?.records || {}).filter(([, record]) => record.decision === "confirmed" && record.pair === task.pair && record.interval === task.interval)
    .filter(([, record]) => record.signal?.time >= sourceStart && record.signal?.time <= sourceEnd)
    .map(([key, record]) => ({ key, pair: task.pair, interval: task.interval, time: record.signal.time, caseId: task.caseId,
      candlePresent: candleTimes.has(record.signal.time), native: nativeTimes.has(record.signal.time), displayed: displayedTimes.has(record.signal.time),
      restoredByFeedback: !nativeTimes.has(record.signal.time) && displayedTimes.has(record.signal.time) }));
  for (const record of confirmed) {
    if (!record.candlePresent) errors.push(`已确认点 ${record.key} 对应K线缺失`);
    else if (!record.displayed) errors.push(`已确认点 ${record.key} 在反馈恢复后仍不显示`);
  }
  return { valid: errors.length === 0, errors, candleCount: candles.length, firstTime: candles[0].time, lastTime: candles.at(-1).time, coverage,
    nativeSignalCount: nativeSignals.length, nativeSignalsInDocument: nativeSignals.filter(signal => signal.time >= sourceStart && signal.time <= sourceEnd).length,
    displayedSignalCount: (displayed.signals || []).length, feedbackRestoredCount: (displayed.signals || []).filter(signal => !nativeTimes.has(signal.time)).length, confirmed };
}

function buildReport(manifest, options) {
  const intervals = options.intervals || INTERVALS, tasks = expectedTasks(options.catalog || Cases, options.version, intervals);
  const exists = options.exists || fs.existsSync;
  // Tasks are grouped by case. Retain only the current case's five decoded
  // payloads, rather than every legacy result for the whole production catalog.
  let assessedCaseKey = null, assessedCase = null;
  const report = { schema: 1, version: options.version, generatedAt: options.now || Date.now(), manifestGeneratedAt: manifest?.generatedAt || 0,
    engineSha256: options.engineSha256, mode: options.verifyResults ? "verify-results" : "manifest-and-files",
    expectedCases: (options.catalog || Cases).filter(item => item.valid === true).length, intervals, expectedTasks: tasks.length,
    manifestStatus: manifest?.status || null, currentTask: manifest?.status?.currentTask || null,
    manifestRunning: manifest?.status?.state === "running", completed: false,
    compatibility: { available: options.compatibility?.available === true,
      evidenceId: options.compatibility?.evidenceId || null, error: options.compatibility?.error || null },
    feedback: options.feedbackSummary || null, tasks: [], unexpectedRecordKeys: [], errors: [...(options.errors || [])] };
  if (!manifest) report.errors.push("生产manifest不存在或无法读取");
  else if (manifest.version !== options.version) report.errors.push("manifest版本与请求版本不符");
  const expectedKeys = new Set(tasks.map(task => task.key));
  report.unexpectedRecordKeys = Object.keys(manifest?.records || {}).filter(key => !expectedKeys.has(key));
  for (const task of tasks) {
    const record = manifest?.records?.[task.key], failure = manifest?.failures?.[task.key];
    const filename = cachePath(options.cacheRoot, record?.file);
    const entry = { ...task, state: "missing", errors: [], file: record?.file || null, failure: failure || null,
      filePresent: Boolean(filename && exists(filename)), engineMatches: Boolean(record && record.engineSha256 === options.engineSha256),
      engineCompatible: false, sourceEngineSha256: record?.engineSha256 || null,
      contextComplete: record?.contextComplete === true, contextIntervals: record?.contextIntervals || [],
      generatedAt: record?.generatedAt || null, candleCount: record?.candleCount || null };
    let compatibilityAssessment = null;
    if (entry.filePresent && !entry.engineMatches && options.compatibility?.available) {
      const caseKey = Compatibility.caseKeyForRecord(task.key);
      if (assessedCaseKey !== caseKey) {
        assessedCaseKey = caseKey;
        assessedCase = options.compatibility.assessCase(manifest, caseKey, intervals);
      }
      compatibilityAssessment = assessedCase;
      entry.engineCompatible = compatibilityAssessment.compatible === true;
      if (entry.engineCompatible) entry.compatibilityEvidenceId = compatibilityAssessment.evidenceId;
    }
    if (!record) { entry.state = failure ? "failed" : "missing"; }
    else if (!filename) { entry.state = "invalid"; entry.errors.push("缓存文件路径非法"); }
    else if (!entry.filePresent) { entry.state = "missing-file"; entry.errors.push("manifest记录存在，但gzip文件缺失"); }
    else if (!entry.engineMatches && !entry.engineCompatible) {
      entry.state = "stale-engine";
      entry.errors.push("缓存引擎指纹缺失或过期，未通过明确兼容验证");
      const reason = compatibilityAssessment?.error || options.compatibility?.error;
      if (reason) entry.errors.push(reason);
    }
    else if (record.contextComplete !== true || !intervals.every(interval => record.contextIntervals?.includes(interval))) { entry.state = failure ? "failed" : "partial-context"; entry.errors.push("尚未落盘全部周期共同完成的最终上下文"); }
    else {
      entry.state = "ready";
      for (const field of ["pair", "interval", "start", "end", "market", "mainWaveStage"]) if (record[field] !== task[field]) { entry.state = "invalid"; entry.errors.push(`manifest任务字段 ${field} 不匹配`); }
      if (entry.state === "ready" && options.verifyResults) {
        try {
          const payload = entry.engineCompatible ? compatibilityAssessment.payloads[task.key]
            : options.readResult ? options.readResult(filename) : JSON.parse(zlib.gunzipSync(fs.readFileSync(filename)).toString("utf8"));
          entry.verification = verifyStoredResult(payload, task, { ...options, intervals, compatibilityAssessment });
          if (!entry.verification.valid) { entry.state = "verification-failed"; entry.errors.push(...entry.verification.errors); }
        } catch (error) { entry.state = "verification-failed"; entry.errors.push(error.message); }
      }
    }
    report.tasks.push(entry);
  }
  const states = {};
  for (const task of report.tasks) states[task.state] = (states[task.state] || 0) + 1;
  const confirmed = report.tasks.flatMap(task => task.verification?.confirmed || []), uniqueConfirmed = new Map();
  for (const record of confirmed) {
    const previous = uniqueConfirmed.get(record.key);
    if (!previous) uniqueConfirmed.set(record.key, { ...record, caseIds: [record.caseId] });
    else { previous.caseIds.push(record.caseId); previous.candlePresent &&= record.candlePresent; previous.native &&= record.native; previous.displayed &&= record.displayed; previous.restoredByFeedback ||= record.restoredByFeedback; }
  }
  report.confirmedResults = [...uniqueConfirmed.values()];
  const expectedConfirmed = Object.entries(options.feedbackDocument?.records || {}).filter(([, record]) => record.decision === "confirmed")
    .filter(([, record]) => tasks.some(task => task.pair === record.pair && task.interval === record.interval
      && record.signal?.time >= Date.parse(`${task.start}T00:00:00+08:00`) && record.signal?.time <= Date.parse(`${task.end}T23:59:59.999+08:00`)));
  report.confirmedUnchecked = expectedConfirmed.filter(([key]) => !uniqueConfirmed.has(key)).map(([key, record]) => ({ key, pair: record.pair, interval: record.interval, time: record.signal.time }));
  report.summary = { stateCounts: states, writtenFiles: report.tasks.filter(task => task.filePresent).length,
    writtenCurrentEngine: report.tasks.filter(task => task.filePresent && task.engineMatches).length,
    writtenCompatibleEngine: report.tasks.filter(task => task.filePresent && task.engineCompatible).length,
    readyCompatibleEngine: report.tasks.filter(task => task.state === "ready" && task.engineCompatible).length,
    readyFullContext: states.ready || 0, ready: states.ready || 0, failed: report.tasks.filter(task => ["failed", "invalid", "verification-failed"].includes(task.state)).length,
    missingOrIncomplete: report.tasks.filter(task => !["ready", "failed", "invalid", "verification-failed"].includes(task.state)).length,
    readyPercent: tasks.length ? (states.ready || 0) / tasks.length * 100 : 0,
    verifiedTasks: report.tasks.filter(task => task.verification?.valid).length,
    confirmedExpectedInCatalog: options.feedbackDocument ? expectedConfirmed.length : null,
    confirmedNotYetChecked: options.feedbackDocument ? report.confirmedUnchecked.length : null,
    confirmedInVerifiedCoverage: uniqueConfirmed.size, confirmedNative: report.confirmedResults.filter(record => record.native).length,
    confirmedRestoredByFeedback: report.confirmedResults.filter(record => record.restoredByFeedback).length,
    confirmedCandleMissing: report.confirmedResults.filter(record => !record.candlePresent).length,
    confirmedNotDisplayed: report.confirmedResults.filter(record => !record.displayed).length };
  // A producer that stopped with 425 files or wrote state=complete despite
  // failures must not make this report say the full catalog is complete.
  report.completed = tasks.length > 0 && report.errors.length === 0 && report.summary.ready === tasks.length
    && manifest?.version === options.version && manifest?.status?.state === "complete" && manifest?.status?.fullBatch === true
    && (!options.verifyResults || report.confirmedUnchecked.length === 0);
  report.state = report.completed ? "complete" : report.manifestRunning ? "running" : "partial";
  report.exitCode = report.completed ? 0 : 2;
  return report;
}

function formatMarkdown(report) {
  const labels = { running: "仍在计算，未全部完成", complete: "全目录任务完成", partial: "未全部完成 / 有缺失" };
  const phaseLabels = { fetch: "读取行情", "persist-candles": "保存本地K线", analyze: "计算策略",
    "partial-context": "汇总已加载周期", "partial-storage": "压缩保存阶段结果",
    "full-context": "汇总完整跨周期结果", "final-storage": "压缩保存最终结果" };
  const current = report.currentTask;
  const phaseStates = { running: "进行中", complete: "完成", failed: "失败" };
  const timingLines = (current?.timings || []).map(timing => {
    const duration = Number.isFinite(timing.durationMs) ? `${(timing.durationMs / 1000).toFixed(2)}秒` : "尚未结束";
    return `- ${timing.interval || "跨周期"} · ${phaseLabels[timing.phase] || timing.phase}：${phaseStates[timing.state] || "未知"}，${duration}${timing.reused ? "（复用等价结果）" : ""}${timing.skipped ? "（无可用输入，跳过）" : ""}。`;
  });
  const bad = report.tasks.filter(task => task.state !== "ready");
  return [`# ${report.version} 全币种预计算进度`, "", `状态：${labels[report.state]}。本报告读取时间：${new Date(report.generatedAt).toISOString()}。`, "",
    `- 范围：${report.expectedCases}个案例 × ${report.intervals.length}个周期 = ${report.expectedTasks}项；1分钟未启用。`,
    `- 已落盘：${report.summary.writtenFiles}/${report.expectedTasks}；其中当前引擎结果：${report.summary.writtenCurrentEngine}项；已验证兼容的原结果：${report.summary.writtenCompatibleEngine || 0}项（保留原引擎指纹及生成时间）。`,
    `- 已完整汇总跨周期且通过当前检查：${report.summary.readyFullContext}/${report.expectedTasks}（${report.summary.readyPercent.toFixed(1)}%）。各周期先落盘，完成全周期汇总后才计入这一项。`,
    `- 当前任务：${current ? `${current.pair || current.symbol || "—"} ${current.start || ""} → ${current.end || ""}；${current.interval || (current.intervals || []).join(" / ")}；${phaseLabels[current.phase] || current.phase || "阶段未报告"}` : "生产端暂未报告当前任务"}。`,
    `- 失败或校验失败：${report.summary.failed}；缺失、旧缓存或未完成上下文：${report.summary.missingOrIncomplete}。`,
    `- 生产manifest状态：${report.manifestStatus?.state || "缺失"}；manifest显示仍在运行：${report.manifestRunning ? "是" : "否"}。不把文件数量单独当成完成证明。`,
    `- 检查模式：${report.mode === "verify-results" ? "逐文件解压，核对身份、真实覆盖、确认点对应K线和本地反馈恢复" : "轻量manifest与文件存在性；尚未解压验证行情和买点"}。`,
    "- 本工具只读取本地生产缓存和确认库，不重新运算策略、不联网、不修改确认库。", "",
    "## 当前案例分段耗时", "", ...(timingLines.length ? timingLines : ["生产端尚未报告分段耗时；不能把两条日志之间的全部时间归给某一个步骤。"]), "",
    "## 确认点校验", "",
    report.mode === "verify-results" ? `本次目录及启用周期内应核对${report.summary.confirmedExpectedInCatalog ?? "未知"}个去重确认点；已检查${report.summary.confirmedInVerifiedCoverage}；尚未检查${report.summary.confirmedNotYetChecked ?? "未知"}；原生${report.summary.confirmedNative}；依靠反馈恢复${report.summary.confirmedRestoredByFeedback}；K线缺失${report.summary.confirmedCandleMissing}；恢复后仍不显示${report.summary.confirmedNotDisplayed}。1分钟及目录日期外的确认点不在本次核对范围。` : "轻量模式未校验确认点；不能据此声明确认点都已保留。",
    "", "## 失败、缺失与未完成任务", "", "| 币种 | 案例起止日期 | 周期 | 状态 | 原因 |", "|---|---|---|---|---|",
    ...(bad.length ? bad.map(task => `| ${task.pair} | ${task.start} → ${task.end} | ${task.interval} | ${task.state} | ${[...task.errors, task.failure?.message || ""].filter(Boolean).join("；").replaceAll("|", "/").replaceAll("\n", " ") || "尚未得到可验证结果"} |`) : ["| — | — | — | 无缺失 | — |"]),
    "", "## 全局读取错误", "", ...(report.errors.length ? report.errors.map(error => `- ${error}`) : ["- 无"]), "", "详情（含成功任务）见同目录JSON。", ""].join("\n");
}

function main() {
  const args = Object.fromEntries(process.argv.slice(2).map(arg => { const separator = arg.indexOf("="); return separator < 0 ? [arg.slice(2), true] : [arg.slice(2, separator), arg.slice(separator + 1)]; }));
  const version = args.version || "v90";
  if (!/^v\d+$/.test(version)) throw new Error("Invalid strategy version");
  const cacheRoot = path.join(ROOT, ".runtime-cache/dragon-wave-precomputed", version);
  const manifestPath = path.join(cacheRoot, "manifest.json"), errors = [];
  let manifest = null;
  try { manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")); } catch (error) { if (error.code !== "ENOENT") errors.push(`manifest读取失败：${error.message}`); }
  let feedback = null;
  if (args["verify-results"]) {
    try { feedback = loadFeedbackDatabase(path.join(ROOT, ".runtime-cache/dragon_wave_feedback.db")); } catch (error) { errors.push(`确认库读取失败：${error.message}`); }
  }
  const engineSha256 = crypto.createHash("sha256").update(fs.readFileSync(path.join(ROOT, "dragon-wave-engine.js"))).digest("hex");
  const compatibility = Compatibility.loadCompatibility({ workspaceRoot: ROOT, cacheRoot, version, engineSha256,
    productionOptions: Compatibility.PRODUCTION_OPTIONS });
  const report = buildReport(manifest, { version, cacheRoot, engineSha256, catalog: Cases, verifyResults: Boolean(args["verify-results"]), now: Date.now(), errors,
    compatibility,
    feedbackDocument: feedback?.document, feedbackContext: feedback ? Feedback.prepareApplicationContext(feedback.document) : null,
    feedbackSummary: feedback ? { deviceDocuments: feedback.deviceDocuments, mergedRecords: feedback.mergedRecords, invalidRecords: feedback.invalidRecords, eligibleRecords: Object.keys(feedback.document.records).length } : null });
  const output = path.join(ROOT, "deliverables/2026-09-07-full-precompute");
  fs.mkdirSync(output, { recursive: true });
  const name = args["verify-results"] ? "verification" : "progress";
  fs.writeFileSync(path.join(output, `${name}.json`), JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(output, `${name}.md`), formatMarkdown(report));
  console.log(JSON.stringify({ version, state: report.state, completed: report.completed, manifestRunning: report.manifestRunning, expectedTasks: report.expectedTasks, ...report.summary, report: path.join(output, `${name}.md`) }));
  process.exitCode = report.exitCode;
}

if (require.main === module) {
  try { main(); } catch (error) { console.error(error.stack || error); process.exitCode = 1; }
}
module.exports = { INTERVALS, expectedTasks, cachePath, loadFeedbackDatabase, verifyStoredResult, buildReport, formatMarkdown };
