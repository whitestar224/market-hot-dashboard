const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");


test("price watch exposes chain ecosystem mode and API", () => {
  const html = fs.readFileSync("price-watch.html", "utf8");
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");

  assert.match(html, /data-watch-mode="chains"/);
  assert.match(js, /\/api\/chain-ecosystem/);
  assert.match(js, /renderChainEcosystem/);
  assert.match(js, /potentialProjects/);
  assert.match(js, /market\.candidates/);
  assert.match(js, /chain-discovery-row/);
  assert.match(js, /data-chain-select/);
  assert.match(js, /data-chain-project-form/);
  assert.doesNotMatch(js, /token_trading:\s*"发币 \/ 交易"/);
  assert.match(css, /\.price-watch-grid\.is-chains/);
  assert.match(css, /\.chain-ecosystem-console/);
  assert.match(css, /\.chain-ecosystem-sidebar/);
  assert.match(css, /\.chain-market-card/);
  assert.match(css, /\.chain-ranking-row/);
  assert.match(css, /\.chain-potential-pool/);
  assert.match(css, /\.price-watch-add-form\[hidden\]/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*\.chain-ecosystem-console/);
});

test("NFT rankings show OpenSea floor price instead of token liquidity", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  assert.match(js, /marketKey === "nft"/);
  assert.match(js, /metrics\.floorPriceNative/);
  assert.match(js, /地板价/);
  assert.match(js, /OpenSea · NFT/);
});

test("News Trade renders MEME opportunity evidence and a guarded per-candidate buy action", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  assert.match(js, /memeOpportunity/);
  assert.match(js, /data-news-trade-prepare/);
  assert.match(js, /\/api\/news-trade\/prepare/);
  assert.match(js, /class="news-trade-candidate-buy /);
  assert.match(js, /data-news-trade-contract/);
  assert.match(js, /manualIntent: true/);
  assert.match(js, /系统不主动推荐；仍可按你的手动意图买入/);
  assert.doesNotMatch(js, /eventId\s*&&\s*context\.executionEligible/);
});

test("News Trade groups ranked candidates into paginated topic cards", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");
  assert.match(js, /NEWS_TRADE_PAGE_SIZE\s*=\s*10/);
  assert.match(js, /memeCandidates/);
  assert.match(js, /TOP1 主标/);
  assert.match(js, /data-news-trade-page/);
  assert.match(css, /\.news-trade-candidate\.is-primary/);
  assert.match(css, /\.news-trade-candidate\.is-backup/);
  assert.match(css, /\.news-trade-pagination/);
  assert.match(css, /\.news-trade-dual-score/);
  assert.match(css, /\.news-trade-intel-grid/);
  assert.match(js, /eventHeatScore/);
  assert.match(js, /onchainTradeScore/);
  assert.match(js, /associationLabel/);
});

test("News Trade exposes timing and narrative score labels on every topic card", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");
  assert.match(js, /0–6h 先手理解/);
  assert.match(js, /6–24h 预发酵/);
  assert.match(js, /新奇猎奇/);
  assert.match(js, /争议性/);
  assert.match(js, /讨论度/);
  assert.match(js, /传奇性/);
  assert.match(js, /名字寓意/);
  assert.match(js, /newsTradeScoreTagsTemplate/);
  assert.match(css, /\.news-trade-score-tags/);
  assert.match(css, /\.news-trade-phase\.is-fermented/);
});

test("News Trade supports active topic discovery with a confirm-before-add preview", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");
  assert.match(js, /data-news-trade-search/);
  assert.match(js, /\/api\/news-trade\/search/);
  assert.match(js, /加入主题监控/);
  assert.match(js, /previewId/);
  assert.match(css, /\.news-trade-search-panel/);
  assert.match(css, /\.news-trade-search-preview/);
});

test("News Trade connects OKX and Binance Wallet without exposing private key material", () => {
  const js = fs.readFileSync("price-watch.js", "utf8");
  const css = fs.readFileSync("styles.css", "utf8");
  assert.match(js, /window\.okxwallet/);
  assert.match(js, /window\.binancew3w/);
  assert.match(js, /eth_requestAccounts/);
  assert.match(js, /accountsChanged/);
  assert.match(js, /wallet_switchEthereumChain/);
  assert.match(js, /walletProvider: okxWalletState\.providerKey \|\| "okx"/);
  assert.doesNotMatch(js, /eth_privateKey|助记词.*输入/);
  assert.match(css, /\.news-trade-wallet/);
});
