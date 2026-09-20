/* Presentation-only grouping and chronology: the feed is already deduplicated. */
(function (root) {
  "use strict";
  const sections = Object.freeze([
    Object.freeze({ key: "meme", label: "事件 MEME" }),
    Object.freeze({ key: "exchange", label: "交易所公告" }),
    Object.freeze({ key: "catalyst", label: "其他催化" }),
  ]);
  const memeTemplates = new Set(["meme-catalyst", "project-x-meme", "counter-consensus-culture"]);
  const memeLabels = new Set(["热点叙事 MEME 映射", "项目官方 / 创始人 Meme 潜力", "反常识文化事件 / 负面共识破圈"]);
  const storageKey = "xingyunNewsTradeSectionV1";
  const validSection = key => sections.some(section => section.key === key);

  function sectionFor(item) {
    // Structured event metadata, never title keywords or mergedKind:
    // exchange announcements can also be enriched as News Trade cards.
    if (item?.sourceType === "listing" || item?.template === "listing-latency") return "exchange";
    if (memeTemplates.has(item?.template)) return "meme";
    if (!item?.template) {
      const label = item?.templateName || item?.eventType;
      if (label === "交易所公告延迟套利") return "exchange";
      if (memeLabels.has(label)) return "meme";
    }
    return "catalyst"; // Unknown/new types stay visible, not discarded.
  }

  function timestamp(value) {
    if (typeof value !== "number" && typeof value !== "string") return 0;
    if (typeof value === "string" && !value.trim()) return 0;
    const numeric = Number(value);
    const time = Number.isFinite(numeric)
      ? (numeric < 1e11 ? numeric * 1000 : numeric)
      : typeof value === "string" ? Date.parse(value) : 0;
    return Number.isFinite(time) && time > 0 ? time : 0;
  }

  function eventTime(item) {
    // Only published news/catalysts advance chronology. Polling, price refreshes
    // and AI re-analysis must never make an old opportunity look new.
    const evidence = [
      item, item?.latestCatalyst,
      ...(Array.isArray(item?.relatedNews) ? item.relatedNews : []),
      ...(Array.isArray(item?.informationSources) ? item.informationSources : []),
    ];
    let latest = 0;
    for (const row of evidence) {
      latest = Math.max(latest, timestamp(row?.publishedAt) || timestamp(row?.timestamp));
    }
    return latest || timestamp(item?.firstSeenAt) || timestamp(item?.enteredAt);
  }

  function newestFirst(items) {
    return (items || []).map((item, index) => ({
      item, index, time: eventTime(item),
      key: String(item?.topicKey || item?.id || item?.url || item?.title || ""),
    })).sort((left, right) => right.time - left.time
      || (left.key < right.key ? -1 : left.key > right.key ? 1 : 0)
      || left.index - right.index).map(row => row.item);
  }

  function group(items) {
    const groups = Object.fromEntries(sections.map(section => [section.key, []]));
    for (const item of newestFirst(items)) groups[sectionFor(item)].push(item);
    return groups;
  }

  function createView({ section, pageSize = 10, storage } = {}) {
    let savedSection;
    try {
      if (storage === undefined) storage = root.localStorage;
      savedSection = storage?.getItem(storageKey);
    } catch (_) { /* Privacy mode must not block the monitor. */ }
    let active = validSection(section) ? section : validSection(savedSection) ? savedSection : "meme";
    const size = Math.max(1, Math.floor(Number(pageSize) || 10));
    const pages = Object.fromEntries(sections.map(section => [section.key, 1]));

    function select(key) {
      if (!validSection(key) || key === active) return false;
      active = key;
      try { storage?.setItem(storageKey, active); } catch (_) { /* Best-effort preference only. */ }
      return true;
    }

    function snapshot(items) {
      const groups = group(items);
      const rows = groups[active];
      const pageCount = Math.max(1, Math.ceil(rows.length / size));
      pages[active] = Math.max(1, Math.min(pages[active], pageCount));
      const page = pages[active];
      return {
        section: active,
        label: sections.find(section => section.key === active).label,
        counts: Object.fromEntries(sections.map(section => [section.key, groups[section.key].length])),
        total: rows.length,
        page,
        pageCount,
        visibleItems: rows.slice((page - 1) * size, page * size),
      };
    }

    function setPage(page, items) {
      const view = snapshot(items);
      pages[active] = Math.max(1, Math.min(Math.floor(Number(page) || 1), view.pageCount));
    }

    return Object.freeze({ select, snapshot, setPage });
  }

  const api = Object.freeze({ sections, sectionFor, eventTime, newestFirst, group, createView });
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.XingyunNewsTradeSections = api;
})(typeof window !== "undefined" ? window : globalThis);
