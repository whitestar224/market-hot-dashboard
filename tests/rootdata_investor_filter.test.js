// RootData 项目榜的「机构」下拉：客户端换板 + 与服务端字段的接线。
//
// 切机构**不发请求**：服务端把每家的「未发行 + 运营中」都塞进 investorBoards，
// 前端只从里面挑一张板 —— 和币安钱包的链下拉一个套路，所以这里也按同样的方式
// 把真实函数抽出来跑，而不是只 grep 源码。

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "app.js"), "utf8");
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

function extract(name) {
  const start = js.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `app.js 里找不到 ${name}()`);
  const end = js.indexOf("\nfunction ", start + 1);
  assert.ok(end > start, `${name}() 之后没有顶层函数，截取失败`);
  return js.slice(start, end);
}

function loadRowsHelper(selected) {
  const state = { rootdataInvestor: selected };
  const source = [extract("rootdataInvestorBoard"), extract("rootdataRowsForSource")].join("\n");
  return new Function(
    "state",
    `return (function () { ${source}\nreturn rootdataRowsForSource; })();`
  )(state);
}

const heatRows = [{ symbol: "AAA", rank: 1 }, { symbol: "BBB", rank: 2 }];
const investorRows = [{ symbol: "BitGo", rank: 1 }, { symbol: "CertiK", rank: 2 }];

const projectSource = {
  id: "rootdata-projects",
  sourceLabel: "RD",
  rows: heatRows,
  investor: "",
  investorOptions: [
    { value: "", label: "全部 · 热度榜" },
    { value: "yzi-labs", label: "YZi Labs · 未发币 82" }
  ],
  investorBoards: [
    { investor: "yzi-labs", label: "YZi Labs", total: 82, status: "ok", rows: investorRows }
  ]
};

test("没选机构时就是原来的热度榜", () => {
  const rowsFor = loadRowsHelper("");
  assert.deepEqual(rowsFor(projectSource), heatRows);
});

test("选了机构就换成那家机构的板", () => {
  const rowsFor = loadRowsHelper("yzi-labs");
  assert.deepEqual(rowsFor(projectSource), investorRows);
});

test("切到机构视图时卡片标题/副标题跟着换", () => {
  const load = (selected) => new Function(
    "state",
    `return (function () {
      ${extract("rootdataInvestorBoard")}
      ${extract("rootdataInvestorHeadline")}
      return rootdataInvestorHeadline;
    })();`
  )({ rootdataInvestor: selected });

  assert.equal(load("")(projectSource), null, "没选机构时不该改标题");
  const headline = load("yzi-labs")(projectSource);
  assert.equal(headline.title, "YZi Labs 在投项目");
  assert.match(headline.subtitle, /未发行 \+ 运营中 · 82 个/);
  assert.match(
    js,
    /const investorHeadline = String\(source\?\.id \|\| ""\) === "rootdata-projects"/,
    "visibleSources 没有用上标题覆盖"
  );
});

test("机构视图不走 sortRows：上游「最近投的靠前」不能被叙事分打乱", () => {
  // 默认「智能」排序会按叙事分重排。机构行没有 heat/change，一旦进 sortRows，
  // 某个名字撞上叙事的项目就会被顶到第 1 行（实测 Alta），其余按 rank 排。
  const block = js.slice(
    js.indexOf("const passthrough ="),
    js.indexOf("rows: passthrough ? rows : sortRows(rows, priorityScores)")
  );
  assert.match(block, /Boolean\(investorHeadline\)/, "机构视图必须算 passthrough");
});

test("机构在 payload 里找不到时退回热度榜，而不是空白卡", () => {
  const rowsFor = loadRowsHelper("gone-fund");
  assert.deepEqual(rowsFor(projectSource), heatRows);
});

test("机构板为空也退回热度榜", () => {
  const rowsFor = loadRowsHelper("yzi-labs");
  const empty = {
    ...projectSource,
    investorBoards: [{ investor: "yzi-labs", rows: [], total: 0, status: "unavailable" }]
  };
  assert.deepEqual(rowsFor(empty), heatRows);
});

test("机构视图在卡片里固定露 10 行、其余下滑查看", () => {
  // 行必须全量下发 + 只滚行不滚表头：把「只显示 10 条」做在服务端就没法下滑了。
  const block = js.slice(
    js.indexOf("function renderBoardRows("),
    js.indexOf("function renderBoards(")
  );
  assert.match(block, /rootdataInvestorBoard\(source\)/, "renderBoardRows 没认出机构视图");
  assert.match(block, /class="rootdata-scroll"/, "机构视图没有滚动容器");
  // 表头要留在滚动区**外**（照空投榜的做法），否则一滚就没表头。
  assert.ok(
    block.indexOf("board-table-head") < block.indexOf("rootdata-scroll"),
    "表头必须在滚动容器之前渲染"
  );

  const css = fs.readFileSync(path.join(root, "styles.css"), "utf8");
  const rule = css.slice(css.indexOf(".rootdata-scroll {"), css.indexOf("}", css.indexOf(".rootdata-scroll {")));
  assert.match(rule, /max-height:\s*540px/, "540px = 10 行 × 54px");
  assert.match(rule, /overflow-y:\s*auto/);
});

test("机构视图的副标题带总数（写不下就不硬塞，滚动条自己会说话）", () => {
  const load = (selected) => new Function(
    "state",
    `return (function () {
      ${extract("rootdataInvestorBoard")}
      ${extract("rootdataInvestorHeadline")}
      return rootdataInvestorHeadline;
    })();`
  )({ rootdataInvestor: selected });
  const headline = load("yzi-labs")(projectSource);
  assert.match(headline.subtitle, /未发行 \+ 运营中 · 82 个/);
  // 卡片头只有 ~150px，副标题超过这个长度就会被 CSS 打成省略号。
  assert.ok(headline.subtitle.length <= 20, `副标题太长会被截断：${headline.subtitle}`);
});

test("rowsForSourceView 认识 rootdata-projects", () => {
  assert.match(
    js,
    /if \(sourceId === "rootdata-projects"\) return rootdataRowsForSource\(source\);/,
    "rowsForSourceView 没有接上机构视图"
  );
});

test("头部渲染出机构下拉，且 <strong> 仍是最后一个子节点", () => {
  assert.match(js, /data-role="rootdata-investor"/, "头部没有机构下拉");
  const block = js.slice(
    js.indexOf('String(source?.id || "") === "rootdata-projects"'),
    js.indexOf('String(source?.id || "") !== "binance-wallet-hot"')
  );
  assert.ok(block.length > 0, "找不到 rootdata-projects 的头部分支");
  assert.ok(
    block.indexOf("board-period-control") < block.lastIndexOf("<strong>"),
    "RD 标签必须排在最后，它锚定卡片右缘"
  );
  assert.match(block, /is-onchain-hot/, "要挂 is-onchain-hot 才拿到窄屏降级");
});

test("换机构只重渲染，不发请求", () => {
  const block = js.slice(
    js.indexOf('select[data-role="rootdata-investor"]'),
    js.indexOf('select[data-role="trench-chain"]')
  );
  assert.match(block, /saveLocalPreference\(ROOTDATA_INVESTOR_KEY/, "没有记住选择");
  assert.match(block, /queueMicrotask\(renderBoards\)/, "没有触发重渲染");
  assert.doesNotMatch(block, /fetch\(/, "换机构不该联网");
});

test("服务端把 investorOptions / investorBoards 挂在榜单卡上", () => {
  assert.match(server, /source\["investorOptions"\] = options/);
  assert.match(server, /source\["investorBoards"\] = boards/);
  // 「未发行 + 运营中」必须是服务端筛的（lssuingStatus 0 / yyStatus 1）。
  const module = fs.readFileSync(path.join(root, "rootdata_investor.py"), "utf8");
  assert.match(module, /"lssuingStatus": 0/);
  assert.match(module, /"yyStatus": 1/);
});

test("改了 app.js 就要升 ?v=", () => {
  assert.match(html, /app\.js\?v=\d+/);
});
