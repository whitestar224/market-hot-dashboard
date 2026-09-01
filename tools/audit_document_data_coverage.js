#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const Data = require("../dragon-wave-data");
const cases = require("../dragon-wave-cases");

const LOCAL_ORIGIN = process.env.DRAGON_WAVE_ORIGIN || "http://127.0.0.1:8791";
const INTERVAL = process.argv.find((arg) => arg.startsWith("--interval="))?.split("=")[1] || "1h";
const CONCURRENCY = Math.max(1, Math.min(8, Number(
  process.argv.find((arg) => arg.startsWith("--concurrency="))?.split("=")[1] || 4,
)));
const OUTPUT_STEM = process.argv.find((arg) => arg.startsWith("--output="))?.split("=")[1]
  || `document-data-coverage-${INTERVAL}`;
const SYMBOL_FILTER = new Set((
  process.argv.find((arg) => arg.startsWith("--symbols="))?.split("=")[1] || ""
).split(",").map((value) => value.trim()).filter(Boolean));
const MERGE_BASE = process.argv.find((arg) => arg.startsWith("--merge-base="))?.split("=")[1] || "";
const MERGE_OVERLAY = process.argv.find((arg) => arg.startsWith("--merge-overlay="))?.split("=")[1] || "";

global.location = { hostname: "127.0.0.1" };
const nativeFetch = global.fetch;
global.fetch = (url, options) => nativeFetch(
  String(url).startsWith("/") ? `${LOCAL_ORIGIN}${url}` : url,
  options,
);

function chinaTime(ms) {
  if (!Number.isFinite(ms)) return "--";
  return new Date(ms + 8 * 60 * 60 * 1000).toISOString().replace("T", " ").slice(0, 16);
}

function percent(value) {
  return `${(Math.max(0, Number(value) || 0) * 100).toFixed(1)}%`;
}

async function auditCase(item, index, total) {
  if (!item.valid) {
    return {
      symbol: item.symbol,
      pair: item.pair,
      start: item.start,
      end: item.sourceEnd,
      status: "invalid-range",
      reason: "目录日期无效",
      attempts: [],
    };
  }
  const window = Data.buildCaseWindow(item.start, item.end, INTERVAL);
  let result = null;
  let attempts = [];
  try {
    result = await Data.fetchCandles({
      pair: item.pair,
      interval: INTERVAL,
      window,
      provider: "auto",
      market: "futures",
    });
    attempts = result.attempts || [];
  } catch (error) {
    attempts = error?.attempts || [{ venue: "全部", message: error?.message || String(error) }];
  }
  const best = result && {
    venue: result.venue.label,
    market: result.venue.market,
    count: result.candles.length,
    first: result.candles[0]?.time,
    last: result.candles.at(-1)?.time,
    coverage: result.coverage,
  };
  const status = !best ? "unavailable" : best.coverage.completeEnough ? "complete" : "partial";
  process.stdout.write(
    `[${String(index + 1).padStart(2, "0")}/${total}] ${item.symbol.padEnd(14)} ${status.padEnd(11)}`
      + `${best ? ` ${best.venue} ${percent(best.coverage.spanCoverage)}` : ""}\n`,
  );
  return {
    symbol: item.symbol,
    pair: item.pair,
    start: item.start,
    end: item.end,
    interval: INTERVAL,
    status,
    venue: best?.venue || null,
    market: best?.market || null,
    count: best?.count || 0,
    first: best?.first || null,
    last: best?.last || null,
    firstChina: chinaTime(best?.first),
    lastChina: chinaTime(best?.last),
    coverage: best?.coverage || null,
    attempts,
  };
}

async function mapConcurrent(items, concurrency, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function run() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index, items.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, run));
  return results;
}

function buildMarkdown(results, scannedAt) {
  const complete = results.filter((item) => item.status === "complete");
  const issues = results.filter((item) => item.status !== "complete");
  const lines = [
    "# 文档龙头 K 线时间覆盖审计",
    "",
    `- 审计时间：${chinaTime(scannedAt)}（UTC+8）`,
    `- 基准周期：${INTERVAL}`,
    `- 完整：${complete.length} / ${results.length}`,
    `- 需处理：${issues.length}`,
    "- 判定：覆盖指定起止时间、连续率至少 97%，且首尾最多允许 2 根对齐误差。",
    "",
    "## 需处理的标的",
    "",
  ];
  if (!issues.length) lines.push("全部通过。", "");
  for (const item of issues) {
    const detail = item.status === "invalid-range"
      ? item.reason
      : item.status === "unavailable"
        ? "所有已接入交易所都没有返回可用数据"
        : `最佳来源 ${item.venue}，仅覆盖 ${percent(item.coverage?.spanCoverage)}，实际 ${item.firstChina} → ${item.lastChina}`;
    lines.push(`- **${item.symbol} / ${item.pair}**：${item.status}；${detail}；指定 ${item.start} → ${item.end || item.sourceEnd}`);
  }
  lines.push("", "## 完整标的", "");
  lines.push("| 标的 | 数据源 | 根数 | 指定区间覆盖 | 连续率 | 实际首根（UTC+8） | 实际末根（UTC+8） |", "|---|---:|---:|---:|---:|---|---|");
  for (const item of complete) {
    lines.push(`| ${item.symbol} | ${item.venue} | ${item.count} | ${percent(item.coverage.spanCoverage)} | ${percent(item.coverage.continuityRatio)} | ${item.firstChina} | ${item.lastChina} |`);
  }
  return `${lines.join("\n")}\n`;
}

function caseKey(item) {
  return `${item.symbol}|${item.start}|${item.end || item.sourceEnd || ""}`;
}

function writeReport(results, scannedAt) {
  const payload = {
    version: 1,
    scannedAt,
    interval: INTERVAL,
    total: results.length,
    complete: results.filter((item) => item.status === "complete").length,
    issues: results.filter((item) => item.status !== "complete").length,
    results,
  };
  const outputDir = path.resolve(__dirname, "../deliverables");
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(path.join(outputDir, `${OUTPUT_STEM}.json`), `${JSON.stringify(payload, null, 2)}\n`);
  fs.writeFileSync(path.join(outputDir, `${OUTPUT_STEM}.md`), buildMarkdown(results, scannedAt));
  console.log(`完成：${payload.complete}/${payload.total} 个标的区间完整，${payload.issues} 个需处理。`);
}

async function main() {
  if (!Data.INTERVALS[INTERVAL]) throw new Error(`不支持周期 ${INTERVAL}`);
  const scannedAt = Date.now();
  if (MERGE_BASE && MERGE_OVERLAY) {
    const base = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), MERGE_BASE), "utf8"));
    const overlay = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), MERGE_OVERLAY), "utf8"));
    const originals = new Map(base.results.map((item) => [caseKey(item), item]));
    const replacements = new Map(overlay.results.map((item) => [caseKey(item), item]));
    const results = cases
      .map((item) => replacements.get(caseKey(item)) || originals.get(caseKey(item)))
      .filter(Boolean);
    writeReport(results, scannedAt);
    return;
  }
  const selectedCases = SYMBOL_FILTER.size
    ? cases.filter((item) => SYMBOL_FILTER.has(item.symbol))
    : cases;
  const results = await mapConcurrent(selectedCases, CONCURRENCY, auditCase);
  writeReport(results, scannedAt);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
