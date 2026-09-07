const test = require("node:test");
const assert = require("node:assert/strict");

const {
  dedupeTotalBoardEntries,
  isTotalBoardSource,
  paginateTotalBoardEntries,
  totalAssetIdentity
} = require("../market_total_board.js");

function source(id, group, rows, sourceLabel = id.toUpperCase()) {
  return { id, group, sourceLabel, title: id, rows };
}

test("total board deduplicates centralized-market tickers across crypto sources", () => {
  const buckets = dedupeTotalBoardEntries([
    source("binance", "crypto", [{ symbol: "ZEC", rank: 1 }]),
    source("okx", "crypto", [{ symbol: "ZEC", rank: 2 }]),
    source("aicoin", "aicoin", [{ symbol: "ZEC", rank: 3 }])
  ]);

  assert.equal(buckets.length, 1);
  assert.equal(buckets[0].entries.length, 3);
});

test("total board joins a symbol-only row to one unambiguous contract asset", () => {
  const buckets = dedupeTotalBoardEntries([
    source("wallet", "crypto", [{ symbol: "FABLE", chain: "4663", contractAddress: "0xFable" }]),
    source("aicoin", "aicoin", [{ symbol: "FABLE" }])
  ]);

  assert.equal(buckets.length, 1);
  assert.equal(buckets[0].entries.length, 2);
  assert.match(buckets[0].identity, /^contract:robinhood:/);
});

test("contract aliases from Binance Wallet and DEX sources resolve to one chain", () => {
  const contract = "Dz9mQ9NzKbCcSUgpfj3R1Bs4WGQKMHBPiVUNiw8MBonk";
  const buckets = dedupeTotalBoardEntries([
    source("wallet", "crypto", [{ symbol: "USELESS", chain: "CT_501", contractAddress: contract }]),
    source("dex", "crypto", [{ symbol: "USELESS", chain: "501", contractAddress: contract }])
  ]);

  assert.equal(buckets.length, 1);
  assert.match(buckets[0].identity, /^contract:solana:/);
});

test("same ticker with different contracts remains two assets", () => {
  const buckets = dedupeTotalBoardEntries([
    source("wallet", "crypto", [
      { symbol: "AI", chain: "56", contractAddress: "0x111" },
      { symbol: "AI", chain: "56", contractAddress: "0x222" }
    ]),
    source("aicoin", "aicoin", [{ symbol: "AI" }])
  ]);

  assert.equal(buckets.length, 3);
});

test("case-sensitive Solana contracts are never collapsed together", () => {
  const buckets = dedupeTotalBoardEntries([
    source("wallet", "crypto", [{ symbol: "CASE", chain: "Solana", contractAddress: "AbCd123" }]),
    source("dex", "crypto", [{ symbol: "CASE", chain: "Solana", contractAddress: "aBcD123" }])
  ]);

  assert.equal(buckets.length, 2);
});

test("total board accepts crypto sources and excludes all stock markets", () => {
  assert.equal(isTotalBoardSource({ group: "crypto" }), true);
  assert.equal(isTotalBoardSource({ group: "aicoin" }), true);
  assert.equal(isTotalBoardSource({ group: "cn" }), false);
  assert.equal(isTotalBoardSource({ group: "hk" }), false);
  assert.equal(isTotalBoardSource({ group: "us" }), false);
});

test("total board pagination returns ten rows and clamps invalid pages", () => {
  const rows = Array.from({ length: 23 }, (_, index) => ({ rank: index + 1 }));
  const middle = paginateTotalBoardEntries(rows, 2, 10);
  const overflow = paginateTotalBoardEntries(rows, 99, 10);

  assert.equal(middle.rows.length, 10);
  assert.equal(middle.rows[0].rank, 11);
  assert.equal(middle.totalPages, 3);
  assert.equal(overflow.page, 3);
  assert.equal(overflow.rows.length, 3);
});

test("unicode on-chain names and stock markets keep stable separate identities", () => {
  const chinese = totalAssetIdentity({ symbol: "我的女友景甜" }, { group: "crypto" });
  const hk = totalAssetIdentity({ symbol: "00700" }, { group: "hk" });
  const cn = totalAssetIdentity({ symbol: "00700" }, { group: "cn" });

  assert.equal(chinese, "asset:crypto:我的女友景甜");
  assert.notEqual(hk, cn);
});
