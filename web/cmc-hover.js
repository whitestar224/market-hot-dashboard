/* 榜单页「鼠标悬停浮层」：把 CoinMarketCap 的基本面补到任意一行榜单上。
 *
 * 为什么单独一个文件、而不是塞进 app.js / rankings.js：
 *   首页榜单（app.js）、涨幅榜/成交榜（rankings.js）、新币榜（newboards.js）各有一套
 *   renderRow()，往哪一边复制都要维护三份。这里用「事件委托 + data-cmc-symbol」的
 *   方式挂一次监听，谁渲染的行只要带上这个属性就自动有浮层。
 *
 * 纪律：
 *   - 后端查不到就整条不返回，这里显示「CMC 未收录」，绝不补 0。
 *   - 一次最多问 80 个 symbol，全部走服务端的整榜内存倒排，没有网络等待。
 *   - 进页面先按 DOM 预取（MutationObserver），真正悬停时基本命中缓存。
 */
(function (root) {
  "use strict";

  var ENDPOINT = "/api/cmc-fundamentals";
  var BATCH_LIMIT = 80;
  var CACHE_TTL_MS = 10 * 60 * 1000;
  var SHOW_DELAY_MS = 180;
  var HIDE_DELAY_MS = 80;
  var PREFETCH_DEBOUNCE_MS = 500;

  // ------------------------------------------------------------ 纯函数

  function normalizeSymbol(value) {
    var text = String(value == null ? "" : value).trim();
    if (!text) return "";
    var cleaned = text.replace(/^\$+/, "").replace(/\s+/g, "").toUpperCase().slice(0, 40);
    // "--"、"…" 这类占位符不值得占一次查询配额。
    return /[A-Z0-9]/.test(cleaned) ? cleaned : "";
  }

  function ratioText(item) {
    var ratio = Number(item && item.floatRatio);
    if (!isFinite(ratio) || ratio <= 0) return "";
    return ratio.toFixed(1) + "%";
  }

  /* 浮层要显示的五行；没有值的那一项直接不出现（绝不写 $0 / 0%）。 */
  function fundamentalCells(item) {
    if (!item) return [];
    var cells = [
      { label: "市值", value: String(item.mcapText || "").trim() },
      { label: "FDV", value: String(item.fdvText || "").trim() },
      { label: "流通量", value: String(item.circText || "").trim() },
      { label: "流通率", value: ratioText(item) },
      { label: "总量", value: String(item.totalText || "").trim() }
    ];
    return cells.filter(function (cell) {
      // "0"、"$0"、"0.0%" 都是「拿不到」的变体，一律当缺项。
      return cell.value && !/^\$?\s*0(?:[.,]0+)?%?$/.test(cell.value);
    });
  }

  // ------------------------------------------------------------ DOM 安装

  function install(win, doc) {
    var cache = new Map();      // SYMBOL -> { ts, item }，item 为 null 表示 CMC 未收录
    var pending = new Set();    // 等着发的 symbol
    var inFlight = new Set();   // 正在发的 symbol
    var flushTimer = null;
    var prefetchTimer = null;

    var tip = null;
    var showTimer = null;
    var hideTimer = null;
    var activeRow = null;

    function isFresh(entry) {
      return !!entry && (Date.now() - entry.ts) < CACHE_TTL_MS;
    }

    function knownSymbols() {
      var out = [];
      doc.querySelectorAll("[data-cmc-symbol]").forEach(function (node) {
        var sym = normalizeSymbol(node.getAttribute("data-cmc-symbol"));
        if (sym) out.push(sym);
      });
      return out;
    }

    function scheduleFlush() {
      if (flushTimer) return;
      flushTimer = win.setTimeout(function () {
        flushTimer = null;
        flush();
      }, 120);
    }

    function flush() {
      var batch = [];
      pending.forEach(function (sym) {
        if (inFlight.has(sym)) return;
        if (batch.length >= BATCH_LIMIT) return;
        batch.push(sym);
      });
      if (!batch.length) return;
      batch.forEach(function (sym) {
        pending.delete(sym);
        inFlight.add(sym);
      });
      var url = ENDPOINT + "?symbols=" + encodeURIComponent(batch.join(","));
      win.fetch(url, { credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (payload) {
          var items = (payload && payload.items) || {};
          var now = Date.now();
          batch.forEach(function (sym) {
            cache.set(sym, { ts: now, item: items[sym] || null });
          });
        })
        .catch(function (error) {
          // 网络抖动不写死缓存，下次悬停再问；也不弹任何提示打扰用户。
          win.console.warn("CMC fundamentals lookup failed", error);
        })
        .then(function () {
          batch.forEach(function (sym) {
            inFlight.delete(sym);
          });
          if (pending.size) scheduleFlush();
          // 数据落地时如果指针还停在这一行，直接把内容补上。
          if (activeRow && tip && tip.hidden === false) {
            var sym = normalizeSymbol(activeRow.getAttribute("data-cmc-symbol"));
            if (sym && cache.has(sym)) render(sym);
          }
        });
    }

    function want(symbol) {
      if (!symbol) return;
      if (isFresh(cache.get(symbol))) return;
      if (inFlight.has(symbol)) return;
      pending.add(symbol);
      scheduleFlush();
    }

    function prefetchVisible() {
      if (doc.hidden) return;
      var symbols = knownSymbols();
      var added = false;
      for (var i = 0; i < symbols.length; i += 1) {
        var sym = symbols[i];
        if (isFresh(cache.get(sym)) || inFlight.has(sym)) continue;
        pending.add(sym);
        added = true;
      }
      if (added) scheduleFlush();
    }

    function schedulePrefetch() {
      if (prefetchTimer) win.clearTimeout(prefetchTimer);
      prefetchTimer = win.setTimeout(function () {
        prefetchTimer = null;
        prefetchVisible();
      }, PREFETCH_DEBOUNCE_MS);
    }

    function ensureTip() {
      if (tip && tip.isConnected) return tip;
      tip = doc.createElement("div");
      tip.className = "cmc-hover-card";
      tip.setAttribute("role", "tooltip");
      tip.hidden = true;
      doc.body.appendChild(tip);
      return tip;
    }

    function cellNode(cell) {
      var box = doc.createElement("div");
      var label = doc.createElement("span");
      label.className = "cmc-hover-label";
      label.textContent = cell.label;
      var value = doc.createElement("b");
      value.className = "cmc-hover-value";
      value.textContent = cell.value;
      box.appendChild(label);
      box.appendChild(value);
      return box;
    }

    function render(symbol) {
      var el = ensureTip();
      var entry = cache.get(symbol);
      var item = entry && entry.item;
      el.textContent = "";
      el.classList.toggle("is-empty", !item);

      var head = doc.createElement("div");
      head.className = "cmc-hover-head";
      var name = doc.createElement("b");
      name.textContent = symbol;
      head.appendChild(name);
      var rank = Number(item && item.rank);
      if (isFinite(rank) && rank > 0) {
        var chip = doc.createElement("span");
        chip.className = "cmc-hover-rank";
        chip.textContent = "CMC #" + rank;
        head.appendChild(chip);
      }
      el.appendChild(head);

      var cells = fundamentalCells(item);
      if (!cells.length) {
        var note = doc.createElement("p");
        note.className = "cmc-hover-note";
        note.textContent = entry ? "CMC 未收录" : "CMC 查询中…";
        el.appendChild(note);
        return;
      }

      var grid = doc.createElement("div");
      grid.className = "cmc-hover-grid";
      cells.forEach(function (cell) {
        grid.appendChild(cellNode(cell));
      });
      el.appendChild(grid);
    }

    function position(row) {
      var rect = row.getBoundingClientRect();
      var box = tip.getBoundingClientRect();
      var margin = 8;
      var left = rect.left;
      if (left + box.width > win.innerWidth - margin) {
        left = Math.max(margin, win.innerWidth - box.width - margin);
      }
      var top = rect.bottom + 6;
      if (top + box.height > win.innerHeight - margin) {
        var above = rect.top - box.height - 6;
        top = above >= margin ? above : Math.max(margin, win.innerHeight - box.height - margin);
      }
      tip.style.left = Math.round(left) + "px";
      tip.style.top = Math.round(top) + "px";
    }

    function show(row) {
      var symbol = normalizeSymbol(row.getAttribute("data-cmc-symbol"));
      if (!symbol) return;
      activeRow = row;
      want(symbol);
      render(symbol);
      tip.hidden = false;
      position(row);
    }

    function hide() {
      activeRow = null;
      if (tip) tip.hidden = true;
    }

    function scheduleShow(row) {
      if (hideTimer) {
        win.clearTimeout(hideTimer);
        hideTimer = null;
      }
      if (showTimer) win.clearTimeout(showTimer);
      if (activeRow === row && tip && !tip.hidden) {
        // 同一行里挪动：只跟着调位置，不要闪。
        position(row);
        return;
      }
      showTimer = win.setTimeout(function () {
        showTimer = null;
        show(row);
      }, SHOW_DELAY_MS);
    }

    function scheduleHide() {
      if (showTimer) {
        win.clearTimeout(showTimer);
        showTimer = null;
      }
      if (hideTimer) win.clearTimeout(hideTimer);
      hideTimer = win.setTimeout(function () {
        hideTimer = null;
        hide();
      }, HIDE_DELAY_MS);
    }

    function rowFromEvent(event) {
      var target = event.target;
      if (!target || !target.closest) return null;
      return target.closest("[data-cmc-symbol]");
    }

    doc.addEventListener("pointerover", function (event) {
      var row = rowFromEvent(event);
      if (row) scheduleShow(row);
    }, true);

    doc.addEventListener("pointerout", function (event) {
      var row = rowFromEvent(event);
      if (!row) return;
      // 在同一行的子元素之间挪动不算离开。
      var next = event.relatedTarget;
      if (next && row.contains(next)) return;
      if (activeRow === row || !activeRow) scheduleHide();
    }, true);

    doc.addEventListener("focusin", function (event) {
      var row = rowFromEvent(event);
      if (row) scheduleShow(row);
    });

    doc.addEventListener("focusout", function (event) {
      if (rowFromEvent(event)) scheduleHide();
    });

    doc.addEventListener("keydown", function (event) {
      if (event.key === "Escape") hide();
    });

    win.addEventListener("scroll", function () {
      if (tip && !tip.hidden) hide();
    }, true);

    win.addEventListener("resize", hide);

    doc.addEventListener("visibilitychange", function () {
      if (doc.hidden) hide();
    });

    function boot() {
      if (typeof win.MutationObserver === "function") {
        var observer = new win.MutationObserver(function () {
          schedulePrefetch();
          // 行被重渲染后原节点已脱离文档，浮层跟着收掉。
          if (activeRow && !activeRow.isConnected) hide();
        });
        observer.observe(doc.body, { childList: true, subtree: true });
      }
      schedulePrefetch();
    }

    if (doc.readyState === "loading") {
      doc.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }

    return {
      prefetch: prefetchVisible,
      // 测试用：清空缓存，让用例之间互不影响。
      reset: function () {
        cache.clear();
        pending.clear();
        inFlight.clear();
        hide();
      }
    };
  }

  var api = {
    normalizeSymbol: normalizeSymbol,
    fundamentalCells: fundamentalCells,
    ratioText: ratioText,
    install: install
  };

  if (typeof document !== "undefined" && typeof window !== "undefined") {
    var runtime = install(window, document);
    api.prefetch = runtime.prefetch;
    api.reset = runtime.reset;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.XingyunCmcHover = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
