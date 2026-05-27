# 星云社 · 跨市场热榜雷达

星云社是一个面向交易员和信息流研究者的跨市场热榜仪表盘。它把加密货币交易所、链上 DEX、港股、美股、A 股、交易所上新、IPO、新股新币、律动快讯、RSS/公众号、X KOL 动态和 TodoList 聚合到同一套本地优先的工作台里。

> **使用声明**：本项目仅供学习使用，如要商用，请获得本人授权；如需二次商业发行或作为商业服务的一部分使用，也请先获得作者本人授权。
> **风险提示**：页面数据和 AI 分析仅作信息聚合与研究辅助，不构成任何投资建议。

## 页面截图

| 热门榜 | 涨幅榜 |
| --- | --- |
| <img src="assets/screenshots/hot-dashboard.png" alt="星云社热门榜" width="100%"> | <img src="assets/screenshots/gainers.png" alt="星云社涨幅榜" width="100%"> |

| 成交额榜 | 新币新股 |
| --- | --- |
| <img src="assets/screenshots/turnover.png" alt="星云社成交额榜" width="100%"> | <img src="assets/screenshots/newboards.png" alt="星云社新币新股" width="100%"> |

| 上新 IPO | 律动快讯 |
| --- | --- |
| <img src="assets/screenshots/listings.png" alt="星云社上新 IPO" width="100%"> | <img src="assets/screenshots/newsflash.png" alt="星云社律动快讯" width="100%"> |

| RSS / 公众号订阅 |
| --- |
| <img src="assets/screenshots/rss.png" alt="星云社 RSS 订阅" width="100%"> |

## 功能概览

- **热门榜**：Binance、OKX、Bitget、AIcoin、OKX DEX、港股、美股、A 股同花顺热榜。
- **涨幅榜**：按交易所和市场拆分展示，不做混合榜；支持榜首异动提醒。
- **成交额榜**：按交易所和市场独立展示资金最集中的标的。
- **新币新股**：交易所新币、新合约、IPO、港美 A 新股集中展示，高热标的红色标注。
- **上新 IPO**：按发布时间聚合交易所上新、合约上线、IPO 日历和上市动态。
- **律动快讯**：独立快讯流，支持重要市场信息桌面弹窗。
- **自动简报**：读取自动化任务生成的交易简报，并支持 GitHub Raw JSON 兜底。
- **RSS / 公众号**：支持 RSS、Atom、JSON Feed，以及参考 WeWe RSS 逻辑的微信公众号订阅。
- **X KOL 追踪**：追踪指定 KOL 动态，正文和引用分开展示，支持桌面提醒。
- **TodoList**：项目分组、任务增删改查、今日提醒，按用户隔离数据。
- **账号系统**：账号密码、邮箱验证码、Google OAuth，支持用户资料和交易所 UID 绑定。
- **桌面弹窗**：市场异动、快讯、RSS、X 动态、Todo 提醒、公众号授权失效均可弹窗。
- **AI 分析**：可接入 DeepSeek、OpenAI、Moonshot、Qwen、Claude、Gemini 等兼容模型做榜单叙事解析。

## 返佣注册链接

如果这个项目对你有帮助，也欢迎通过下面的邀请链接注册交易所。广告语：**永久返手续费 20%**。

| 交易所 | 邀请链接 |
| --- | --- |
| Binance | [立即注册 Binance](https://www.binance.com/join?ref=WF7KWSF5) |
| OKX | [立即注册 OKX](https://www.bjwebptyiou.com/join/51629076) |
| Bitget | [立即注册 Bitget](https://partner.bitgetapps.com/bg/xy8888) |

## 打赏支持

如果你想支持星云社继续维护，可以用微信打赏。非常感谢。

<img src="assets/donate-wechat.png" alt="微信打赏二维码" width="260">

## Discord 社群

欢迎加入星云社 Discord 社群，一起交流市场信息、自动化工作流和产品建议：

[加入 Discord](https://discord.gg/mKyCwtHW)

## 本地运行

需要 Python 3.11+。

```powershell
pip install -r requirements.txt
python server.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

## Docker 本地部署

1. 准备配置文件：

```powershell
Copy-Item .env.production.example .env.production
```

2. 生成字段加密密钥，写入 `.env.production` 的 `XINGYUN_FIELD_ENCRYPTION_KEY`：

```powershell
python -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

3. 按需配置 `.env.production`：

```text
XINGYUN_PUBLIC_BASE_URL=http://127.0.0.1:8765
XINGYUN_COOKIE_SECURE=0
XINGYUN_LOAD_ENV_EXAMPLE=0
DEEPSEEK_API_KEY=
EMAIL_SMTP_HOST=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

4. 启动 Docker：

```powershell
docker compose up -d --build
```

也可以使用项目脚本：

```powershell
.\docker-start.ps1
```

5. 检查服务：

```powershell
curl http://127.0.0.1:8765/api/health
```

常用命令：

```powershell
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

完整上线说明见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 配置说明

敏感配置只放在本地 `.env` 或 `.env.production`，不要提交到 GitHub。

常用环境变量：

- `XINGYUN_FIELD_ENCRYPTION_KEY`：本地数据库敏感字段加密密钥。
- `DEEPSEEK_API_KEY` / `LLM_API_KEY`：AI 榜单分析。
- `EMAIL_SMTP_*`：邮箱验证码登录。
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`：Google OAuth 登录。
- `X_BEARER_TOKEN`：X KOL 官方 API。
- `WECHAT_*`：微信公众号订阅授权。
- `OKX_*` / `BITGET_*` / `AICOIN_*`：交易所和客户端数据源配置。

## 隐私与公开仓库说明

这个仓库只提交源码、示例配置、公开素材和页面截图。以下本地文件默认被 `.gitignore` 或 `.dockerignore` 排除：

- `.env`、`.env.local`、`.env.production`
- `.runtime-cache/`
- `desktop-private/`
- `node_modules/`
- `release/`
- `dist-backend/`
- `__pycache__/`

本地数据库、微信授权 token、桌面端私有配置、AI API Key、邮箱授权码、Google OAuth Secret、交易所 Cookie/Token 都不应该进入公开仓库。

## 桌面端

项目包含 Electron 壳，可打包 Windows `.exe` 和 macOS `.app` / `.dmg`。

```powershell
npm install
npm run desktop:dev
npm run desktop:build:win
```

macOS 打包需在 macOS 环境中执行：

```bash
npm run desktop:build:mac
```

桌面端会启动本地后端并复用同一套页面和接口。桌面弹窗能力在普通网页、Windows 桌面端和 macOS 桌面端中共用同一套通知链路。

## 数据来源

项目会聚合多个公开页面、公开接口或用户本地授权后的数据源，包括但不限于：

- Binance、OKX、Bitget、OKX DEX、AIcoin
- 富途、同花顺、东方财富
- BlockBeats
- RSS / 微信公众号
- X / Twitter

不同数据源稳定性和可访问性会受网络环境、接口变动、地区访问限制影响。项目内置缓存和兜底逻辑，但不保证任何数据源持续可用。

## 安全说明

- 密码使用 PBKDF2 哈希存储。
- 用户邮箱、手机号、Google 标识、交易所 UID、模型 API Key 等敏感字段会做本地字段级加密。
- 关键登录和管理操作会写入审计日志。
- 生产环境建议开启 HTTPS，并设置 `XINGYUN_COOKIE_SECURE=1`。
- 不要把 `.runtime-cache`、`.env`、`.env.production` 传到公开仓库。

## 许可

Copyright (c) 2026 星云社。

本项目仅供学习、研究和个人使用。未经作者本人授权，不得用于商业用途、商业分发、SaaS 服务、付费产品、企业内部商业化部署或任何以盈利为目的的再发布。

如需商用授权，请通过 Discord 或 GitHub 联系作者。
