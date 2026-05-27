(function () {
  const CRYPTO_THEMES = {
    BTC: "BTC 主线 / ETF / 宏观流动性",
    ETH: "ETH 生态 / L2 / 质押",
    SOL: "Solana 生态 / 链上活跃",
    BNB: "平台币 / BSC 生态",
    BGB: "平台币 / Bitget 生态",
    DOGE: "Meme / 社区情绪",
    SHIB: "Meme / 社区情绪",
    PEPE: "Meme / 社区情绪",
    BONK: "Meme / Solana",
    WIF: "Meme / Solana",
    FLOKI: "Meme / 社区情绪",
    XRP: "支付 / 合规叙事",
    TON: "Telegram 生态",
    HYPE: "Hyperliquid / ETF叙事 / 链上交易所",
    ZEC: "隐私币 / 抗审查叙事",
    TAO: "AI 算力 / Bittensor",
    RNDR: "AI 算力 / 渲染",
    RENDER: "AI 算力 / 渲染",
    FET: "AI Agent / ASI",
    ASI: "AI Agent",
    WLD: "AI / 身份网络",
    NEAR: "AI 公链 / 基础设施",
    ARB: "Layer2 / 以太坊扩容",
    OP: "Layer2 / 以太坊扩容",
    STRK: "Layer2 / ZK",
    MANTA: "Layer2 / ZK",
    LINK: "预言机 / RWA",
    ONDO: "RWA / 美债代币化",
    PENDLE: "收益交易 / DeFi",
    ENA: "合成美元 / DeFi",
    AAVE: "DeFi 借贷",
    UNI: "DEX / DeFi",
    CRV: "稳定币池 / DeFi",
    LDO: "流动性质押",
    JUP: "Solana DEX 聚合",
    PYTH: "预言机 / Solana",
    SEI: "高性能公链",
    SUI: "Move 公链",
    APT: "Move 公链",
    KAITO: "InfoFi / AI",
    VIRTUAL: "AI Agent",
    W: "跨链 / Wormhole",
    JTO: "Solana 质押",
    ORDI: "Bitcoin 生态 / 铭文",
    SATS: "Bitcoin 生态 / 铭文",
    RUNE: "BTCFi / 跨链流动性",
    GNS: "DeFi / 衍生品交易",
    TRX: "支付 / TRON 稳定币结算",
    BGB: "平台币 / Bitget 生态",
    RONIN: "游戏 / Ronin 生态",
    LAYER: "Solana 生态 / Restaking",
    FIDA: "Solana 生态 / Bonfida",
    QI: "BNB Chain / DeFi",
    EDEN: "链上交易 / 新币博弈",
    UP: "新币博弈 / 交易所流动性",
    GLW: "新币博弈 / 交易所流动性",
    RAVE: "新币博弈 / 交易所流动性",
    BILL: "Meme / 新币博弈",
    BSB: "Meme / 新币博弈",
    APR: "新币博弈 / 交易所流动性",
    DEGEN: "Base 生态 / Meme",
    PIGEON: "链上 Meme / 热钱轮动",
    CAPACITR: "链上新币 / Meme",
    RKC: "链上新币 / Meme",
    SATO: "链上 Meme / Solana",
    LFI: "链上新币 / Meme",
    ASTEROID: "链上 Meme / 新币博弈",
    GITLAWB: "链上新币 / Meme",
    MOLT: "链上新币 / Meme"
  };

  const COMMODITY_THEMES = {
    XAU: "黄金 / 避险资产",
    GOLD: "黄金 / 避险资产",
    XAG: "白银 / 贵金属",
    SILVER: "白银 / 贵金属",
    CL: "原油 / 能源",
    WTI: "原油 / 能源",
    OIL: "原油 / 能源",
    BRENT: "原油 / 能源"
  };

  const STOCK_THEMES = {
    NVDA: "AI 算力 / GPU",
    AMD: "AI 芯片 / 半导体",
    AVGO: "AI 网络 / 半导体",
    TSM: "先进制程 / 晶圆代工",
    ARM: "芯片架构 / AI 终端",
    SMCI: "AI 服务器",
    TSLA: "电动车 / Robotaxi",
    AAPL: "消费电子 / AI 终端",
    MSFT: "AI 云 / Copilot",
    GOOGL: "AI 搜索 / 云",
    META: "AI 广告 / 社交",
    AMZN: "云计算 / 电商",
    PLTR: "AI 软件 / 政府订单",
    MSTR: "BTC 持仓 / 加密股",
    COIN: "加密交易平台",
    HOOD: "零售交易 / 加密股",
    MU: "存储芯片 / AI 硬件",
    SNDK: "存储 / 半导体",
    INTC: "芯片制造 / 半导体",
    LITE: "光通信 / 光模块",
    RKLB: "商业航天 / 火箭发射",
    BABA: "AI 云 / 电商",
    JD: "电商 / 消费",
    PDD: "跨境电商",
    "0700": "游戏 / AI / 港股权重",
    "00700": "游戏 / AI / 港股权重",
    "9988": "AI 云 / 电商",
    "09988": "AI 云 / 电商",
    "1810": "手机 / 汽车",
    "01810": "手机 / 汽车",
    "3690": "本地生活 / 港股权重",
    "03690": "本地生活 / 港股权重",
    "0981": "国产半导体",
    "00981": "国产半导体",
    "002594": "新能源车",
    "300750": "锂电池 / 新能源",
    "600519": "白酒 / 消费",
    "601318": "保险 / 金融",
    "000858": "白酒 / 消费",
    "002371": "光模块 / AI 算力",
    "300308": "光模块 / AI 算力"
  };

  const KNOWN_EQUITY_SYMBOLS = new Set([
    "AAPL",
    "AMD",
    "AMZN",
    "ARM",
    "AVGO",
    "BABA",
    "COIN",
    "GOOGL",
    "HOOD",
    "INTC",
    "JD",
    "LITE",
    "META",
    "MSFT",
    "MSTR",
    "MU",
    "NVDA",
    "PDD",
    "PLTR",
    "RKLB",
    "SMCI",
    "SNDK",
    "TSLA",
    "TSM"
  ]);

  const GENERIC_CRYPTO_SOURCE_THEME = {
    binance: "Alpha资金 / Launchpool",
    okx: "持仓分歧 / 杠杆拥挤",
    bitget: "新币流动性 / 跟单资金",
    aicoin: "舆论扩散 / 散户情绪",
    dex: "链上资金 / Meme轮动"
  };

  const KEYWORD_THEMES = [
    [/黄金|白银|贵金属|gold|silver|xau|xag/i, "贵金属 / 避险资产"],
    [/原油|石油|能源|crude|wti|brent|oil/i, "原油 / 能源"],
    [/AI|人工智能|智能|算力|机器人|agent/i, "AI / 智能化"],
    [/芯片|半导体|光模块|晶圆|存储|集成电路/i, "半导体 / 国产替代"],
    [/新能源|锂电|电池|光伏|储能|汽车|车/i, "新能源 / 汽车链"],
    [/医药|生物|制药|医疗|创新药|pharma|bio/i, "医药生物"],
    [/黄金|有色|铜|铝|煤|矿|石油|能源/i, "资源品 / 周期"],
    [/证券|银行|保险|金融|financ/i, "金融 / 估值修复"],
    [/游戏|传媒|短剧|影视|娱乐/i, "传媒游戏"],
    [/军工|航天|卫星|低空/i, "军工 / 低空经济"],
    [/消费|食品|白酒|餐饮|旅游/i, "消费复苏"],
    [/IPO|Nasdaq|NYSE|上市|新股/i, "IPO / 新股定价"]
  ];

  function parseSignedNumber(value) {
    const match = String(value ?? "").replace(/,/g, "").match(/[+-]?\d+(?:\.\d+)?/);
    return match ? Number.parseFloat(match[0]) : 0;
  }

  function parseAmount(value) {
    if (typeof value === "number") return Number.isFinite(value) ? value : 0;
    const text = String(value ?? "").replace(/,/g, "");
    const match = text.match(/([+-]?\d+(?:\.\d+)?)/);
    if (!match) return 0;
    const number = Number.parseFloat(match[1]);
    if (!Number.isFinite(number)) return 0;
    if (/万/.test(text)) return number * 1e4;
    if (/亿/.test(text)) return number * 1e8;
    if (/\bK\b/i.test(text)) return number * 1e3;
    if (/\bM\b/i.test(text)) return number * 1e6;
    if (/\bB\b/i.test(text)) return number * 1e9;
    return number;
  }

  function normalizeSymbol(row) {
    const raw = String(row?.symbol || row?.asset || row?.name || row?.title || "").trim().toUpperCase();
    if (!raw) return "";
    const compact = raw
      .replace(/^[^A-Z0-9]+/u, "")
      .replace(/[-_/\s].*$/u, "")
      .replace(/(?:USDT|USDC|USD|BUSD|FDUSD|USDD|DAI|TUSD|PERP|SWAP)$/u, "");
    return compact || raw;
  }

  function codeKey(row) {
    const symbol = String(row?.symbol || "").replace(/\D/g, "");
    if (symbol.length >= 4) return symbol.padStart(symbol.length === 4 ? 5 : 6, "0");
    return "";
  }

  function cleanThemeText(value) {
    return String(value || "")
      .replace(/\bAI\s*coin\b/gi, " ")
      .replace(/\bAICoin\b/gi, " ")
      .replace(/\bAIcoin\b/gi, " ");
  }

  function sourceText(source, mode = "") {
    return [mode, source?.id, source?.group, source?.title, source?.subtitle, source?.sourceName, source?.sourceLabel]
      .filter(Boolean)
      .join(" ");
  }

  function sourceTheme(source, mode = "") {
    const text = sourceText(source, mode).toLowerCase();
    if (/okx[-\s]?dex|web3|onchain|链上|dex/.test(text)) return GENERIC_CRYPTO_SOURCE_THEME.dex;
    if (/binance|bn\b/.test(text)) return GENERIC_CRYPTO_SOURCE_THEME.binance;
    if (/bitget|bg\b/.test(text)) return GENERIC_CRYPTO_SOURCE_THEME.bitget;
    if (/aicoin|ai\b/.test(text)) return GENERIC_CRYPTO_SOURCE_THEME.aicoin;
    if (/okx|swap|合约/.test(text)) return GENERIC_CRYPTO_SOURCE_THEME.okx;
    return "";
  }

  function isCryptoLikeSource(source, mode = "") {
    return /crypto|aicoin|dex|binance|okx|bitget|币圈|合约|链上|web3/i.test(sourceText(source, mode));
  }

  function inferTheme(row, source, mode = "") {
    const symbol = normalizeSymbol(row);
    const code = codeKey(row);
    const text = [row?.name, row?.title, row?.symbol, row?.note, ...(row?.tags || [])].filter(Boolean).map(cleanThemeText).join(" ");

    if (COMMODITY_THEMES[symbol]) return COMMODITY_THEMES[symbol];
    if (STOCK_THEMES[symbol]) return STOCK_THEMES[symbol];
    if (code && STOCK_THEMES[code]) return STOCK_THEMES[code];
    if (KNOWN_EQUITY_SYMBOLS.has(symbol)) return "";
    if (CRYPTO_THEMES[symbol]) return CRYPTO_THEMES[symbol];

    if (isCryptoLikeSource(source, mode)) {
      const sourceFallback = sourceTheme(source, mode);
      for (const [matcher, theme] of KEYWORD_THEMES) {
        if (matcher.test(text) && !/^AI\s*\/|AI /.test(theme)) return theme;
      }
      return sourceFallback;
    }

    for (const [matcher, theme] of KEYWORD_THEMES) {
      if (matcher.test(text)) return theme;
    }

    const sourceText = [source?.title, source?.subtitle, source?.sourceName, source?.sourceLabel].join(" ");
    if (/DEX|链上|onchain|web3/i.test(sourceText)) return "链上热钱 / Meme 流动性";
    if (/新币|new crypto|上线/i.test(sourceText)) return "新币流动性 / 首发定价";
    if (/IPO|新股|上市/i.test(sourceText)) return "IPO / 新股定价";
    return "";
  }

  function sourceKind(source, mode) {
    const text = [mode, source?.title, source?.subtitle, source?.sourceName, source?.sourceLabel].join(" ");
    if (/成交额|turnover|volume/i.test(text)) return "turnover";
    if (/涨幅|gainers|领涨/i.test(text)) return "gainers";
    if (/新币|新股|IPO|上市|上线/i.test(text)) return "new";
    if (/热门|hot|热度|AIcoin/i.test(text)) return "hot";
    return "rank";
  }

  function importance(row, source, rank, mode) {
    const kind = sourceKind(source, mode);
    const change = Math.abs(parseSignedNumber(row?.change));
    const heat = Number(row?.heat || row?.heatScore || row?.score || 0);
    const amount = Math.max(
      parseAmount(row?.amount),
      parseAmount(row?.turnover),
      parseAmount(row?.metric),
      parseAmount(row?.metricLabel),
      parseAmount(row?.note)
    );
    const isHotFlag = Boolean(row?.isHot || row?.hot || row?.highHeat);

    if (isHotFlag || heat >= 90) return { ok: true, trigger: "heat" };
    if (kind === "new" && (rank <= 3 || heat >= 75)) return { ok: true, trigger: "new" };
    if (kind === "turnover" && rank <= 3) return { ok: true, trigger: "turnover" };
    if (kind === "gainers" && (rank <= 3 || change >= 8)) return { ok: true, trigger: "gainers" };
    if (kind === "hot" && (rank <= 2 || heat >= 80 || change >= 10)) return { ok: true, trigger: "hot" };
    if (rank <= 2 && (change >= 5 || amount > 0)) return { ok: true, trigger: "rank" };
    if (change >= 12) return { ok: true, trigger: "move" };
    if (amount >= 50_000_000 && rank <= 5) return { ok: true, trigger: "amount" };
    return { ok: false, trigger: "" };
  }

  function reasonFor(trigger, row, source, mode) {
    const theme = inferTheme(row, source, mode);
    if (theme) return theme.split("/")[0].trim();
    if (trigger === "turnover" || trigger === "amount") return "资金迁移";
    if (trigger === "gainers" || trigger === "move") return "事件催化";
    if (trigger === "new") return /IPO|新股|上市/i.test([mode, source?.title, source?.subtitle].join(" ")) ? "发行定价" : "首发流动性";
    if (trigger === "hot" || trigger === "heat") return "舆论发酵";
    return "叙事验证";
  }

  function buildRowInsight(row, context = {}) {
    const source = context.source || row?.source || {};
    const rank = Number(context.rank || row?.rank || 999);
    const mode = context.mode || "";
    const aiInsight = window.XingyunAiInsights?.getRowInsight(row, { source, rank, mode });
    if (aiInsight?.detail) return aiInsight;
    if (window.XingyunAiInsights?.shouldDeferFallback?.(row, { source, rank, mode })) return null;
    const imp = importance(row, source, rank, mode);
    if (!imp.ok) return null;

    const reason = reasonFor(imp.trigger, row, source, mode);
    const theme = inferTheme(row, source, mode);
    const change = Math.abs(parseSignedNumber(row?.change));
    const tone = imp.trigger === "heat" || imp.trigger === "new" || change >= 15 ? "is-hot" : "";
    return {
      reason,
      theme,
      tone,
      detail: theme && theme !== reason ? `${reason} · ${theme}` : reason
    };
  }

  window.XingyunInsights = { buildRowInsight };
})();
