"use strict";

// This module only reads evidence and cache artifacts. Creating a certificate
// returns a detached value; callers explicitly choose whether to publish it.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const zlib = require("node:zlib");

const INTERVALS = Object.freeze(["1h", "4h", "1d", "15m", "5m"]);
const analysis = Object.freeze({ mainWaveStage: "active", mainWaveContextSource: "leader-default-main-wave",
  mainWaveContextLabel: "龙头默认主升浪环境" });
const PRODUCTION_OPTIONS = Object.freeze({ version: "v90", intervals: INTERVALS, market: "futures",
  analysis, context: Object.freeze({ preselectedLeader: true, ...analysis }) });
const REQUIRED_FIXTURES = Object.freeze([
  "husdt_1h_2025-06-29_2025-07-01.json", "husdt_5m_2025-07-02_1045.json",
  "piusdt_okx_5m_2025-02-22_1530.json", "piusdt_okx_5m_2025-02-22_1905.json",
  "mmtusdt_15m_2026-07-28_2026-07-30.json", "notusdt_1h_2024-05-20_2024-05-30.json",
  "basedusdt_binance_5m_2026-08-16_1540.json", "turbousdt_okx_15m_2024-05-23_2315.json",
  "turbousdt_okx_15m_2024-05-21_2024-05-25.json", "piusdt_okx_5m_2025-02-23_1115.json",
]);
const REQUIRED_COMPARISONS = Object.freeze([...REQUIRED_FIXTURES,
  ...["15m", "1h", "4h", "1d"].map(interval => `PI derived ${interval}`),
  ...["4h", "1d"].map(interval => `Synthetic ${interval} policy using NOT price path`),
  "PI full causal context leader-active", "PI full causal context no-leader",
]);
const KIND = "dragon-wave-direct-cache-compatibility";
const sha256 = value => crypto.createHash("sha256").update(value).digest("hex");
const hashFile = filename => sha256(fs.readFileSync(filename));
const isHash = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const stable = value => JSON.stringify(value, function (_key, item) {
  return item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item;
});
const same = (left, right) => stable(left) === stable(right);
function demand(condition, message) { if (!condition) throw new Error(`兼容凭据：${message}`); }
function sameIntervals(intervals) {
  return Array.isArray(intervals) && intervals.length === INTERVALS.length
    && new Set(intervals).size === INTERVALS.length && INTERVALS.every(interval => intervals.includes(interval));
}
function inside(root, relative) {
  demand(typeof relative === "string" && relative.length > 0 && !path.isAbsolute(relative), "证据路径必须相对于工作目录");
  const resolvedRoot = fs.realpathSync(root), target = fs.realpathSync(path.resolve(root, relative));
  demand(target.startsWith(resolvedRoot + path.sep), "证据路径越出工作目录");
  return target;
}
function fingerprint(root) {
  return { engineSha256: hashFile(path.join(root, "dragon-wave-engine.js")),
    visionSha256: hashFile(path.join(root, "dragon-wave-vision.js")) };
}
function caseKeyForRecord(key) {
  const parts = String(key).split("|");
  demand(parts.length === 7 && parts[0] === "v90" && /^[A-Z0-9]+$/.test(parts[1])
    && /^\d{4}-\d{2}-\d{2}$/.test(parts[2]) && /^\d{4}-\d{2}-\d{2}$/.test(parts[3])
    && INTERVALS.includes(parts[4]) && parts[5] === "futures" && parts[6] === "active", "任务key不属于固定生产配置");
  return [...parts.slice(0, 4), ...parts.slice(5)].join("|");
}
function keysForCase(caseKey) {
  const parts = String(caseKey).split("|");
  demand(parts.length === 6, "案例key格式不正确");
  const keys = INTERVALS.map(interval => [...parts.slice(0, 4), interval, ...parts.slice(4)].join("|"));
  keys.forEach(key => demand(caseKeyForRecord(key) === caseKey, "案例key不属于固定生产配置"));
  return keys;
}

function validateParityReport(report, source, target, workspaceRoot) {
  demand(report?.passed === true, "完整结果等价验证尚未通过");
  demand(same(report.productionOptions, PRODUCTION_OPTIONS), "验证报告生产配置不匹配");
  for (const [prefix, expected] of [["old", source], ["new", target]]) {
    demand(report[`${prefix}EngineSha256`] === expected.engineSha256
      && report[`${prefix}VisionSha256`] === expected.visionSha256, `${prefix} engine/vision指纹与报告不符`);
  }
  demand(Array.isArray(report.comparisons) && report.comparisons.length === REQUIRED_COMPARISONS.length,
    "必须有18项完整结果比较");
  const comparisons = new Map(report.comparisons.map(value => [value.label, value]));
  demand(comparisons.size === REQUIRED_COMPARISONS.length
    && REQUIRED_COMPARISONS.every(label => comparisons.get(label)?.equal === true), "必须比较项缺失、重复或不等价");
  for (const fixture of REQUIRED_FIXTURES) {
    const entry = comparisons.get(fixture);
    demand(Number.isInteger(entry.candleCount) && entry.candleCount >= 40, `${fixture}没有真实分析足够K线`);
    demand(isHash(entry.fixtureSha256) && entry.fixtureSha256 === hashFile(inside(workspaceRoot, path.join("tests", "fixtures", fixture))),
      `${fixture}输入指纹不匹配`);
  }
  for (const interval of ["15m", "1h", "4h", "1d"]) {
    const entry = comparisons.get(`PI derived ${interval}`);
    demand(entry.fullBucketsOnly === true && Number.isInteger(entry.candleCount) && entry.candleCount > 0,
      `${interval}派生输入未验证完整桶`);
  }
  for (const interval of ["4h", "1d"]) {
    const entry = comparisons.get(`Synthetic ${interval} policy using NOT price path`);
    demand(entry.synthetic === true && Number.isInteger(entry.candleCount) && entry.candleCount > 30,
      `${interval}没有覆盖高周期结构分析`);
  }
  for (const label of ["leader-active", "no-leader"]) {
    const entry = comparisons.get(`PI full causal context ${label}`);
    demand(entry.rawInputsUnchanged === true, `${label}未证明raw输入未被修改`);
    demand(Number.isInteger(entry.beforeRebuildCalls) && Number.isInteger(entry.afterRebuildCalls)
      && entry.beforeRebuildCalls >= 0 && entry.afterRebuildCalls >= 0, `${label}没有真实因果重建计数`);
    if (label === "leader-active") demand(entry.beforeRebuildCalls > 0 && entry.afterRebuildCalls > 0,
      "主升共振没有执行真实因果重建");
  }
}

function validateEvidence(certificate, options) {
  const { workspaceRoot, version = "v90" } = options;
  demand(version === "v90" && certificate?.version === version && certificate.schema === 1 && certificate.kind === KIND,
    "类型或策略版本不匹配");
  demand(same(certificate.productionOptions, PRODUCTION_OPTIONS), "固定生产配置不匹配");
  demand(same(options.productionOptions || PRODUCTION_OPTIONS, PRODUCTION_OPTIONS), "调用方配置不匹配");
  for (const producer of [certificate.source, certificate.target]) demand(isHash(producer?.engineSha256)
    && isHash(producer?.visionSha256), "来源或目标指纹缺失");
  const target = fingerprint(workspaceRoot);
  demand(same(certificate.target, target), "目标不是当前engine/vision代码");
  if (options.engineSha256) demand(options.engineSha256 === target.engineSha256, "运行中的engine指纹与目标不一致");
  if (options.visionSha256) demand(options.visionSha256 === target.visionSha256, "运行中的vision指纹与目标不一致");
  demand(!same(certificate.source, certificate.target), "来源与目标相同，无需兼容凭据");
  const baselineRoot = inside(workspaceRoot, certificate.evidence?.baselineRoot);
  demand(same(fingerprint(baselineRoot), certificate.source), "保存的来源engine/vision与凭据不符");
  const reportPath = inside(workspaceRoot, certificate.evidence?.parityReportPath);
  const bytes = fs.readFileSync(reportPath);
  demand(isHash(certificate.evidence?.parityReportSha256)
    && sha256(bytes) === certificate.evidence.parityReportSha256, "最终等价报告摘要不匹配");
  validateParityReport(JSON.parse(bytes.toString("utf8")), certificate.source, certificate.target, workspaceRoot);
  demand(certificate.cases && typeof certificate.cases === "object" && !Array.isArray(certificate.cases)
    && Object.keys(certificate.cases).length > 0, "没有明确绑定的完整案例");
  for (const [caseKey, binding] of Object.entries(certificate.cases)) {
    const keys = keysForCase(caseKey);
    demand(binding.engineSha256 === certificate.source.engineSha256 && Number.isFinite(binding.completedAt)
      && binding.completedAt > 0 && binding.records && Object.keys(binding.records).length === keys.length,
    "案例来源或完成记录不正确");
    for (const key of keys) {
      const record = binding.records[key];
      demand(record?.engineSha256 === certificate.source.engineSha256 && isHash(record.gzipSha256)
        && record.file === `${sha256(key)}.json.gz` && Number.isFinite(record.generatedAt) && record.generatedAt > 0,
      "案例没有绑定全部任务key、gzip摘要及来源指纹");
    }
  }
}

function inspectCase(manifest, caseKey, cacheRoot, sourceEngine, expected = null) {
  demand(manifest?.version === "v90", "manifest策略版本不匹配");
  const checkpoint = manifest.cases?.[caseKey], keys = keysForCase(caseKey);
  demand(checkpoint?.state === "complete" && checkpoint.engineSha256 === sourceEngine
    && sameIntervals(checkpoint.intervals) && sameIntervals(checkpoint.completedIntervals)
    && Array.isArray(checkpoint.failedIntervals) && checkpoint.failedIntervals.length === 0
    && Number.isFinite(checkpoint.completedAt) && checkpoint.completedAt > 0, "案例尚未完整完成或来源不匹配");
  if (expected) demand(checkpoint.completedAt === expected.completedAt, "案例完成记录已改变");
  const records = {}, payloads = {};
  for (const key of keys) {
    const record = manifest.records?.[key], parts = key.split("|");
    demand(record && !manifest.failures?.[key] && record.engineSha256 === sourceEngine
      && record.contextComplete === true && sameIntervals(record.contextIntervals) && record.compactSchema === 1,
    "案例记录缺失、失败、部分共振或来源不匹配");
    const identity = { pair: parts[1], start: parts[2], end: parts[3], interval: parts[4], market: parts[5], mainWaveStage: parts[6] };
    for (const [field, value] of Object.entries(identity)) demand(record[field] === value, `manifest字段${field}不匹配`);
    const file = `${sha256(key)}.json.gz`;
    demand(record.file === file, "gzip文件名与任务key不匹配");
    const bytes = fs.readFileSync(inside(cacheRoot, file));
    const binding = { file, gzipSha256: sha256(bytes), engineSha256: sourceEngine, generatedAt: record.generatedAt };
    demand(Number.isFinite(binding.generatedAt) && binding.generatedAt > 0, "生成时间缺失");
    if (expected) demand(same(binding, expected.records[key]), "绑定的gzip内容、来源或生成时间已改变");
    const payload = JSON.parse(zlib.gunzipSync(bytes).toString("utf8"));
    demand(payload.schema === 1 && payload.version === "v90" && payload.key === key
      && payload.engineSha256 === sourceEngine && payload.generatedAt === record.generatedAt
      && payload.contextComplete === true && sameIntervals(payload.contextIntervals), "gzip身份、来源或完整状态不匹配");
    for (const [field, value] of Object.entries(identity)) demand(payload[field] === value, `gzip字段${field}不匹配`);
    demand(payload.result?.interval === identity.interval && Array.isArray(payload.result.candles)
      && payload.result.candles.length > 0 && payload.result.candles.length === record.candleCount, "gzip实际K线与记录不匹配");
    records[key] = binding;
    payloads[key] = payload;
  }
  return { binding: { engineSha256: sourceEngine, completedAt: checkpoint.completedAt, records }, payloads };
}

function createPolicy(certificate, options) {
  // Detach caller-owned objects so later edits cannot expand the accepted scope.
  const document = JSON.parse(JSON.stringify(certificate));
  validateEvidence(document, options);
  const evidenceId = sha256(stable(document));
  return {
    available: true, error: null, evidenceId,
    sourceEngineSha256: document.source.engineSha256,
    assessCase(manifest, caseKey, intervals = INTERVALS) {
      try {
        demand(sameIntervals(intervals), "请求周期不属于固定生产配置");
        const expected = document.cases[caseKey];
        demand(expected, "案例未在凭据中明确绑定");
        const inspected = inspectCase(manifest, caseKey, options.cacheRoot, document.source.engineSha256, expected);
        return { compatible: true, evidenceId, parityReportSha256: document.evidence.parityReportSha256,
          sourceEngineSha256: document.source.engineSha256, targetEngineSha256: document.target.engineSha256,
          payloads: inspected.payloads };
      } catch (error) { return { compatible: false, error: error.message }; }
    },
  };
}

function loadCompatibility(options) {
  const filename = path.join(path.dirname(options.cacheRoot), `${options.version || "v90"}.compatibility.json`);
  try { return createPolicy(JSON.parse(fs.readFileSync(filename, "utf8")), options); }
  catch (error) {
    return { available: false, error: error.code === "ENOENT" && !fs.existsSync(filename) ? null : error.message,
      assessCase: () => ({ compatible: false, error: error.message }) };
  }
}

// No writes. Publication is a separate deliberate action after reviewing this
// value. Paths in the returned evidence are relative to workspaceRoot.
function buildCertificate({ manifest, cacheRoot, workspaceRoot, baselineRoot, parityReportPath,
  expectedSource = null, now = Date.now() }) {
  const source = fingerprint(baselineRoot), target = fingerprint(workspaceRoot);
  if (expectedSource) demand(same(source, expectedSource), "来源不是指定的保存基线");
  const certificate = { schema: 1, kind: KIND, version: "v90", createdAt: new Date(now).toISOString(),
    productionOptions: JSON.parse(JSON.stringify(PRODUCTION_OPTIONS)), source, target,
    evidence: { baselineRoot: path.relative(workspaceRoot, baselineRoot),
      parityReportPath: path.relative(workspaceRoot, parityReportPath), parityReportSha256: hashFile(parityReportPath) }, cases: {} };
  for (const [key, checkpoint] of Object.entries(manifest.cases || {})) {
    if (checkpoint.state !== "complete" || checkpoint.engineSha256 !== source.engineSha256) continue;
    certificate.cases[key] = inspectCase(manifest, key, cacheRoot, source.engineSha256).binding;
  }
  validateEvidence(certificate, { workspaceRoot, cacheRoot, version: "v90" });
  return certificate;
}

module.exports = { PRODUCTION_OPTIONS, REQUIRED_FIXTURES, REQUIRED_COMPARISONS, sameIntervals,
  fingerprint, sha256, caseKeyForRecord, validateParityReport, buildCertificate, createPolicy, loadCompatibility };
