(function initMarketTotalBoard(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.XingyunMarketTotalBoard = api;
})(typeof window !== "undefined" ? window : globalThis, function marketTotalBoardFactory() {
  function normalizeIdentityPart(value) {
    return String(value || "")
      .normalize("NFKC")
      .trim()
      .toLowerCase()
      .replace(/[^\p{L}\p{N}.]+/gu, "");
  }

  function normalizedMarketRealm(source) {
    const group = normalizeIdentityPart(source?.group);
    return group === "crypto" || group === "aicoin" ? "crypto" : group || "unknown";
  }

  function normalizedChain(value) {
    const chain = normalizeIdentityPart(value);
    const aliases = {
      "1": "ethereum",
      eth: "ethereum",
      "56": "bsc",
      bnb: "bsc",
      ct56: "bsc",
      bnbchain: "bsc",
      binancesmartchain: "bsc",
      "501": "solana",
      ct501: "solana",
      sol: "solana",
      "4663": "robinhood",
      robinhoodchain: "robinhood"
    };
    return aliases[chain] || chain || "unknown";
  }

  function symbolIdentity(row, source) {
    const symbol = normalizeIdentityPart(row?.symbol || row?.asset || row?.name);
    return symbol ? `asset:${normalizedMarketRealm(source)}:${symbol}` : "";
  }

  function contractIdentity(row) {
    const contract = String(row?.contractAddress || row?.address || "").trim();
    if (!contract) return "";
    const chain = normalizedChain(row?.chain || row?.chainLabel);
    const evmChains = new Set(["ethereum", "bsc", "base", "arbitrum", "optimism", "polygon", "avalanche", "robinhood"]);
    const normalizedContract = contract.startsWith("0x") || evmChains.has(chain)
      ? contract.toLowerCase()
      : contract;
    return `contract:${chain}:${normalizedContract}`;
  }

  function totalAssetIdentity(row, source) {
    return contractIdentity(row) || symbolIdentity(row, source);
  }

  function isTotalBoardSource(source) {
    return normalizedMarketRealm(source) === "crypto";
  }

  function paginateTotalBoardEntries(rows, requestedPage, pageSize = 10) {
    const entries = Array.isArray(rows) ? rows : [];
    const safePageSize = Math.max(1, Math.trunc(Number(pageSize) || 10));
    const totalCount = entries.length;
    const totalPages = Math.max(1, Math.ceil(totalCount / safePageSize));
    const page = Math.min(totalPages, Math.max(1, Math.trunc(Number(requestedPage) || 1)));
    const start = (page - 1) * safePageSize;
    return {
      page,
      pageSize: safePageSize,
      rows: entries.slice(start, start + safePageSize),
      totalCount,
      totalPages
    };
  }

  function dedupeTotalBoardEntries(sources, options = {}) {
    const matchesRow = typeof options.matchesRow === "function" ? options.matchesRow : () => true;
    const entries = [];
    (Array.isArray(sources) ? sources : []).forEach((source) => {
      (Array.isArray(source?.rows) ? source.rows : []).forEach((row) => {
        if (!row || !matchesRow(row, source)) return;
        const symbolKey = symbolIdentity(row, source);
        const contractKey = contractIdentity(row);
        if (!symbolKey && !contractKey) return;
        entries.push({ row, source, symbolKey, contractKey });
      });
    });

    const contractKeysBySymbol = new Map();
    entries.forEach((entry) => {
      if (!entry.contractKey || !entry.symbolKey) return;
      if (!contractKeysBySymbol.has(entry.symbolKey)) contractKeysBySymbol.set(entry.symbolKey, new Set());
      contractKeysBySymbol.get(entry.symbolKey).add(entry.contractKey);
    });

    const buckets = new Map();
    entries.forEach((entry) => {
      const contractMatches = contractKeysBySymbol.get(entry.symbolKey);
      const uniqueContractAlias = !entry.contractKey && contractMatches?.size === 1
        ? [...contractMatches][0]
        : "";
      const identity = entry.contractKey || uniqueContractAlias || entry.symbolKey;
      if (!buckets.has(identity)) buckets.set(identity, []);
      buckets.get(identity).push(entry);
    });

    return [...buckets.entries()].map(([identity, bucketEntries]) => ({
      identity,
      entries: bucketEntries
    }));
  }

  return {
    contractIdentity,
    dedupeTotalBoardEntries,
    isTotalBoardSource,
    normalizeIdentityPart,
    normalizedMarketRealm,
    paginateTotalBoardEntries,
    symbolIdentity,
    totalAssetIdentity
  };
});
