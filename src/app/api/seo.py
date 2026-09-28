"""从 server.py 抽取的模块（Phase 3 拆分）。

来源: server.py 第 52322-52595 行
本文件内容由 tools/extract_module.py 机械搬移，未做任何语义修改。
"""

from __future__ import annotations
import html
import json
import re
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse
from app.core.config import env_value
from app.core.paths import ROOT


SEO_PUBLIC_PAGES: dict[str, dict[str, str]] = {
    "/index.html": {
        "title": "星云社 - 加密货币、港股、美股、A股跨市场热门榜",
        "description": "星云社聚合 Binance、OKX、Bitget、AICoin、富途、同花顺等市场热门榜，追踪加密货币、港股、美股、A股热点异动。",
        "keywords": "加密货币热门榜,币圈热门榜,港股热门榜,美股热门榜,A股热门榜,OKX热门榜,Binance热门榜,Bitget热门榜",
    },
    "/gainers.html": {
        "title": "涨幅榜 - Binance / OKX / Bitget / 港美 A 股实时涨幅排行",
        "description": "查看加密货币交易所、港股、美股和 A 股的实时涨幅榜，快速发现市场强势标的和异动机会。",
        "keywords": "涨幅榜,币安涨幅榜,OKX涨幅榜,Bitget涨幅榜,港股涨幅榜,美股涨幅榜,A股涨幅榜",
    },
    "/turnover.html": {
        "title": "成交额榜 - 加密货币、港股、美股、A股成交额排行",
        "description": "按交易所和市场查看成交额榜，聚焦资金最集中的加密货币、港股、美股和 A 股标的。",
        "keywords": "成交额榜,加密货币成交额榜,港股成交额榜,美股成交额榜,A股成交额榜,交易热度",
    },
    "/newboards.html": {
        "title": "新币新股榜 - 交易所新币榜与港美 A 股新股榜",
        "description": "追踪 Binance、OKX、Bitget 新币榜，以及港股、美股和 A 股新股上市动态。",
        "keywords": "新币榜,新股榜,交易所新币,港股新股,美股新股,A股新股",
    },
    "/listings.html": {
        "title": "上新 IPO - 交易所上新、IPO 日历、港美 A 股上市信息",
        "description": "集中查看交易所新币上线、IPO 日历、港股美股上市与 A 股新股动态。",
        "keywords": "交易所上新,新币上线,IPO日历,港股上市,美股上市,A股IPO",
    },
    "/newsflash.html": {
        "title": "聚合快讯 - 多源币圈快讯与市场新闻时间线",
        "description": "聚合 BlockBeats、方程式新闻、吴说区块链及扩展订阅源，并自动去除重复事件。",
        "keywords": "聚合快讯,律动快讯,方程式新闻,吴说区块链,币圈快讯,市场快讯",
    },
    "/briefs.html": {
        "title": "自动简报 - 星云社市场热点与交易情报简报",
        "description": "自动汇总市场热门榜、成交额、涨幅异动和核心票热度，形成可快速阅读的交易情报简报。",
        "keywords": "市场简报,交易简报,币圈简报,热点简报,自动简报",
    },
    "/rss.html": {
        "title": "RSS 订阅 - 微信公众号与市场信息源聚合",
        "description": "订阅 RSS 和微信公众号信息源，聚合市场、科技、IPO、宏观和加密货币相关内容。",
        "keywords": "RSS订阅,微信公众号订阅,信息流,市场信息源,WeWe RSS",
    },
    "/legal.html": {
        "title": "星云社站务说明 - 用户协议、隐私政策、风险提示与数据来源",
        "description": "查看星云社用户协议、隐私政策、风险提示、免责声明、数据来源说明、关于我们和联系方式。",
        "keywords": "星云社用户协议,星云社隐私政策,风险提示,免责声明,数据来源说明,关于星云社,联系我们",
    },
}

SEO_NOINDEX_PATHS = {
    "/admin.html",
    "/login.html",
    "/profile.html",
    "/strategy.html",
    "/todo.html",
    "/xwatch.html",
}


def normalized_page_path(path: str) -> str:
    if path in {"", "/"}:
        return "/index.html"
    return path if path.startswith("/") else f"/{path}"


def request_base_url(handler: SimpleHTTPRequestHandler) -> str:
    configured = env_value("XINGYUN_PUBLIC_BASE_URL").rstrip("/")
    if configured:
        return configured
    host = handler.headers.get("Host") or "127.0.0.1:8765"
    scheme = "https" if handler.headers.get("X-Forwarded-Proto") == "https" else "http"
    return f"{scheme}://{host}"


def canonical_url(handler: SimpleHTTPRequestHandler, page_path: str) -> str:
    path = "/" if page_path == "/index.html" else page_path
    return f"{request_base_url(handler)}{path}"


def strip_runtime_seo_tags(html_text: str) -> str:
    patterns = [
        r"<title\b[^>]*>.*?</title>",
        r"<meta\s+[^>]*(?:name|property)=[\"'](?:description|keywords|robots|og:[^\"']+|twitter:[^\"']+)[\"'][^>]*>",
        r"<link\s+[^>]*rel=[\"']canonical[\"'][^>]*>",
        r"<script\s+[^>]*type=[\"']application/ld\+json[\"'][^>]*>.*?</script>",
    ]
    cleaned = html_text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.I | re.S)
    return cleaned


def seo_schema_for_page(page_path: str, meta: dict[str, str], url: str) -> list[dict[str, Any]]:
    base = url.rsplit("/", 1)[0] if page_path != "/index.html" else url.rstrip("/")
    schema: list[dict[str, Any]] = [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "星云社",
            "url": request_base_url_for_schema(url),
            "logo": f"{request_base_url_for_schema(url)}/assets/xingyunshe-logo.png",
        },
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "星云社",
            "url": request_base_url_for_schema(url),
            "description": SEO_PUBLIC_PAGES["/index.html"]["description"],
        },
    ]
    page_type = "CollectionPage" if page_path in SEO_PUBLIC_PAGES else "WebPage"
    schema.append(
        {
            "@context": "https://schema.org",
            "@type": page_type,
            "name": meta["title"],
            "description": meta["description"],
            "url": url,
            "isPartOf": {"@type": "WebSite", "name": "星云社", "url": request_base_url_for_schema(url)},
        }
    )
    if page_path in {"/index.html", "/gainers.html", "/turnover.html", "/newboards.html", "/listings.html"}:
        schema.append(
            {
                "@context": "https://schema.org",
                "@type": "ItemList",
                "name": meta["title"],
                "description": meta["description"],
                "itemListOrder": "https://schema.org/ItemListOrderDescending",
                "url": url,
            }
        )
    return schema


def request_base_url_for_schema(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def seo_head_block(handler: SimpleHTTPRequestHandler, page_path: str) -> str:
    is_noindex = page_path in SEO_NOINDEX_PATHS
    meta = SEO_PUBLIC_PAGES.get(
        page_path,
        {
            "title": "星云社 - 跨市场热点雷达",
            "description": "星云社提供加密货币、港股、美股、A股市场热点榜单、快讯、简报和信息源聚合工具。",
            "keywords": "星云社,市场热点,交易榜单,加密货币,港股,美股,A股",
        },
    )
    url = canonical_url(handler, page_path)
    og_image = f"{request_base_url(handler)}/assets/xingyunshe-logo.png"
    robots = "noindex,nofollow" if is_noindex else "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"
    lines = [
        f"<title>{html.escape(meta['title'])}</title>",
        f"<meta name=\"description\" content=\"{html.escape(meta['description'], quote=True)}\">",
        f"<meta name=\"keywords\" content=\"{html.escape(meta.get('keywords', ''), quote=True)}\">",
        f"<meta name=\"robots\" content=\"{robots}\">",
        f"<link rel=\"canonical\" href=\"{html.escape(url, quote=True)}\">",
        f"<meta property=\"og:title\" content=\"{html.escape(meta['title'], quote=True)}\">",
        f"<meta property=\"og:description\" content=\"{html.escape(meta['description'], quote=True)}\">",
        f"<meta property=\"og:url\" content=\"{html.escape(url, quote=True)}\">",
        "<meta property=\"og:type\" content=\"website\">",
        f"<meta property=\"og:image\" content=\"{html.escape(og_image, quote=True)}\">",
        "<meta name=\"twitter:card\" content=\"summary_large_image\">",
        f"<meta name=\"twitter:title\" content=\"{html.escape(meta['title'], quote=True)}\">",
        f"<meta name=\"twitter:description\" content=\"{html.escape(meta['description'], quote=True)}\">",
    ]
    if not is_noindex:
        lines.append(
            "<script type=\"application/ld+json\">"
            + json.dumps(seo_schema_for_page(page_path, meta, url), ensure_ascii=False, separators=(",", ":"))
            + "</script>"
        )
    return "\n".join(lines)


def site_footer_html() -> str:
    return """
<footer class="site-footer" aria-label="站点底部信息">
  <div class="site-footer-inner">
    <nav class="site-footer-columns" aria-label="站务链接">
      <section>
        <h2>关于</h2>
        <a href="./legal.html#about">关于我们</a>
        <a href="./legal.html#contact">联系我们</a>
        <a href="https://discord.gg/mKyCwtHW" target="_blank" rel="noreferrer noopener">社区</a>
        <a href="https://github.com/whitestar224/market-hot-dashboard" target="_blank" rel="noreferrer noopener">GitHub</a>
      </section>
      <section>
        <h2>条款与政策</h2>
        <a href="./legal.html#terms">用户协议</a>
        <a href="./legal.html#privacy">隐私政策</a>
        <a href="./legal.html#disclaimer">免责声明</a>
      </section>
      <section>
        <h2>数据与风险</h2>
        <a href="./legal.html#risk">风险提示</a>
        <a href="./legal.html#sources">数据来源说明</a>
        <a href="./briefs.html">自动简报</a>
        <a href="./newsflash.html">律动快讯</a>
      </section>
      <section>
        <h2>产品</h2>
        <a href="./index.html">热门榜</a>
        <a href="./gainers.html">涨幅榜</a>
        <a href="./turnover.html">成交额榜</a>
        <a href="./newboards.html">新币新股</a>
        <a href="./rss.html">RSS 订阅</a>
      </section>
    </nav>
  </div>
  <div class="site-footer-bottom">
    <span>© 2026 星云社</span>
    <span>仅供信息研究，不构成投资建议。</span>
  </div>
</footer>
""".strip()


def inject_site_footer(html_text: str) -> str:
    if 'class="site-footer"' in html_text:
        return html_text
    footer = site_footer_html()
    if re.search(r"</body>", html_text, flags=re.I):
        return re.sub(r"</body>", f"{footer}\n</body>", html_text, count=1, flags=re.I)
    return f"{html_text}\n{footer}"


def refresh_stylesheet_version(html_text: str) -> str:
    try:
        version = str(int((ROOT / "styles.css").stat().st_mtime))
    except Exception:
        version = str(int(time.time()))
    return re.sub(
        r'(href=["\'][^"\']*styles\.css)(?:\?v=[^"\']*)?(["\'])',
        rf"\1?v={version}\2",
        html_text,
        flags=re.I,
    )


def inject_seo_into_html(handler: SimpleHTTPRequestHandler, html_text: str, page_path: str) -> str:
    cleaned = strip_runtime_seo_tags(html_text)
    block = seo_head_block(handler, page_path)
    if re.search(r"</head>", cleaned, flags=re.I):
        cleaned = re.sub(r"</head>", f"{block}\n</head>", cleaned, count=1, flags=re.I)
    else:
        cleaned = f"{block}\n{cleaned}"
    return refresh_stylesheet_version(inject_site_footer(cleaned))


def robots_txt(handler: SimpleHTTPRequestHandler) -> str:
    base = request_base_url(handler)
    disallow_lines = "\n".join(f"Disallow: {path}" for path in sorted(SEO_NOINDEX_PATHS))
    return (
        "User-agent: *\n"
        "Allow: /\n"
        f"{disallow_lines}\n\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


def sitemap_xml(handler: SimpleHTTPRequestHandler) -> str:
    base = request_base_url(handler)
    now = datetime.now().strftime("%Y-%m-%d")
    items = []
    for path in ["/index.html", "/gainers.html", "/turnover.html", "/newboards.html", "/listings.html", "/newsflash.html", "/briefs.html", "/rss.html", "/price-watch.html", "/legal.html"]:
        loc = f"{base}{'/' if path == '/index.html' else path}"
        priority = "1.0" if path == "/index.html" else "0.8"
        changefreq = "hourly" if path in {"/index.html", "/gainers.html", "/turnover.html", "/newsflash.html"} else "daily"
        items.append(
            f"  <url><loc>{html.escape(loc)}</loc><lastmod>{now}</lastmod><changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>"
        )
    return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n" + "\n".join(items) + "\n</urlset>\n"
