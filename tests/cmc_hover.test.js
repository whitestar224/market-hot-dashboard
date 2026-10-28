"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const cmc = require("../cmc-hover.js");

// ---------------------------------------------------------------- 极简假 DOM
// 项目里没装 jsdom，这里只实现 install() 真正用到的那几个方法：
// createElement / appendChild / textContent / classList / getAttribute / closest。
class FakeElement {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parent = null;
    this.style = {};
    this.attributes = {};
    this.hidden = false;
    this._text = "";
    this.classList = {
      set: new Set(),
      toggle: (name, on) => (on ? this.classList.set.add(name) : this.classList.set.delete(name)),
      contains: (name) => this.classList.set.has(name),
    };
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._text = String(value == null ? "" : value);
    this.children = [];
  }

  get childElementCount() {
    return this.children.length;
  }

  get isConnected() {
    return true;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  contains(node) {
    let cursor = node;
    while (cursor) {
      if (cursor === this) return true;
      cursor = cursor.parent;
    }
    return false;
  }

  closest(selector) {
    if (selector !== "[data-cmc-symbol]") return null;
    return this.getAttribute("data-cmc-symbol") ? this : null;
  }

  getBoundingClientRect() {
    return { left: 20, top: 40, right: 380, bottom: 64, width: 360, height: 24 };
  }
}

function fakeRow(symbol) {
  const row = new FakeElement("a");
  row.setAttribute("data-cmc-symbol", symbol);
  return row;
}

function makeHarness(rows, payload) {
  const calls = [];
  const body = new FakeElement("body");
  const listeners = {};
  const doc = {
    readyState: "complete",
    hidden: false,
    body,
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    querySelectorAll(selector) {
      return selector === "[data-cmc-symbol]" ? rows.slice() : [];
    },
    createElement(tag) {
      return new FakeElement(tag);
    },
  };
  const win = {
    innerWidth: 1400,
    innerHeight: 900,
    addEventListener() {},
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
    console,
    fetch: async (url) => {
      calls.push(url);
      return { ok: true, json: async () => payload };
    },
  };
  const runtime = cmc.install(win, doc);
  const emit = (type, event) => (listeners[type] || []).forEach((handler) => handler(event));
  return { runtime, doc, calls, emit };
}

const PAYLOAD = {
  ok: true,
  items: {
    IRYS: {
      symbol: "IRYS",
      rank: 192,
      mcapUsd: 6070000,
      circulating: 256700000,
      total: 1000000000,
      max: 1000000000,
      floatRatio: 25.67,
      fdvUsd: 23600000,
      mcapText: "$607万",
      fdvText: "$2,360万",
      circText: "2.57亿",
      totalText: "10.00亿",
      text: "流通 2.57亿 · 流通率 25.7% · 总量 10.00亿",
    },
  },
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------- 纯函数

test("symbol 归一化去掉 $ 前缀和空白并转大写", () => {
  assert.equal(cmc.normalizeSymbol(" $ irys "), "IRYS");
  assert.equal(cmc.normalizeSymbol("bnb"), "BNB");
});

test("占位符不产生查询", () => {
  for (const value of ["", "  ", "--", "…", null, undefined]) {
    assert.equal(cmc.normalizeSymbol(value), "", `${JSON.stringify(value)} 不该被当成 symbol`);
  }
});

test("归一化后的 symbol 不超过 40 个字符", () => {
  assert.equal(cmc.normalizeSymbol("A".repeat(80)).length, 40);
});

test("五项齐全时按市值/FDV/流通量/流通率/总量排列", () => {
  const cells = cmc.fundamentalCells(PAYLOAD.items.IRYS);
  assert.deepEqual(cells.map((cell) => cell.label), ["市值", "FDV", "流通量", "流通率", "总量"]);
  assert.equal(cells[3].value, "25.7%");
});

test("缺项整格消失，绝不补 0", () => {
  const cells = cmc.fundamentalCells({
    mcapText: "$607万",
    fdvText: "",
    circText: "",
    totalText: "",
    floatRatio: 0,
  });
  assert.deepEqual(cells, [{ label: "市值", value: "$607万" }]);
});

test("字符串 0 也算缺项", () => {
  const cells = cmc.fundamentalCells({ mcapText: "0", fdvText: "$0", floatRatio: 0 });
  assert.deepEqual(cells, []);
});

test("没有条目时没有任何格子", () => {
  assert.deepEqual(cmc.fundamentalCells(null), []);
  assert.deepEqual(cmc.fundamentalCells(undefined), []);
});

test("流通率保留一位小数，异常值不显示", () => {
  assert.equal(cmc.ratioText({ floatRatio: 25.67 }), "25.7%");
  assert.equal(cmc.ratioText({ floatRatio: 100 }), "100.0%");
  assert.equal(cmc.ratioText({ floatRatio: 0 }), "");
  assert.equal(cmc.ratioText({ floatRatio: -3 }), "");
  assert.equal(cmc.ratioText({ floatRatio: "abc" }), "");
  assert.equal(cmc.ratioText(null), "");
});

// ---------------------------------------------------------------- 取数与渲染

test("预取把所有行合并成一次请求，且不超过 80 个 symbol", async () => {
  const rows = Array.from({ length: 200 }, (_, index) => fakeRow(`C${index}`));
  const harness = makeHarness(rows, { ok: true, items: {} });
  harness.runtime.prefetch();
  await sleep(200);
  assert.equal(harness.calls.length, 1, "200 行只能发第一批");
  const asked = new URL("http://x" + harness.calls[0]).searchParams.get("symbols").split(",");
  assert.equal(asked.length, 80);
  assert.equal(asked[0], "C0");
});

test("命中缓存后不再重复请求", async () => {
  const rows = [fakeRow("IRYS")];
  const harness = makeHarness(rows, PAYLOAD);
  harness.runtime.prefetch();
  await sleep(200);
  harness.runtime.prefetch();
  await sleep(200);
  assert.equal(harness.calls.length, 1);
});

test("悬停浮层显示 CMC 市值、流通量、流通率和总量", async () => {
  const row = fakeRow("IRYS");
  const harness = makeHarness([row], PAYLOAD);
  harness.runtime.prefetch();
  await sleep(200);
  harness.emit("pointerover", { target: row });
  await sleep(260);
  const card = harness.doc.body.children[0];
  assert.ok(card, "浮层应该已经挂到 body 上");
  assert.equal(card.hidden, false);
  const text = card.textContent;
  assert.match(text, /IRYS/);
  assert.match(text, /CMC #192/);
  assert.match(text, /市值\$607万/);
  assert.match(text, /流通量2\.57亿/);
  assert.match(text, /流通率25\.7%/);
  assert.match(text, /总量10\.00亿/);
});

test("CMC 未收录时只写提示，不编造 0", async () => {
  const row = fakeRow("NEWCOIN");
  const harness = makeHarness([row], { ok: true, items: {} });
  harness.runtime.prefetch();
  await sleep(200);
  harness.emit("pointerover", { target: row });
  await sleep(260);
  const card = harness.doc.body.children[0];
  assert.match(card.textContent, /CMC 未收录/);
  assert.doesNotMatch(card.textContent, /\$0|0\.0%|\b0\b/);
});

test("没有 data-cmc-symbol 的行不产生浮层", async () => {
  const plain = new FakeElement("a");
  const harness = makeHarness([], PAYLOAD);
  harness.emit("pointerover", { target: plain });
  await sleep(260);
  assert.equal(harness.doc.body.children.length, 0);
});

// ---------------------------------------------------------------- 页面接线

test("三个榜单页都引了浮层脚本，且行模板带 data-cmc-symbol", () => {
  const root = require("node:path").join(__dirname, "..");
  for (const page of ["index.html", "gainers.html", "turnover.html", "newboards.html"]) {
    const html = fs.readFileSync(require("node:path").join(root, page), "utf8");
    assert.match(html, /cmc-hover\.js\?v=\d+/, `${page} 少了浮层脚本`);
  }
  for (const script of ["app.js", "rankings.js", "newboards.js"]) {
    const source = fs.readFileSync(require("node:path").join(root, script), "utf8");
    assert.match(source, /data-cmc-symbol=/, `${script} 的行模板没挂 symbol`);
  }
});
