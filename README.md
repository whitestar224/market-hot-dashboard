# 星云社 · 跨市场交易情报工作台

> Local-first cross-market trading intelligence dashboard for crypto, stocks, on-chain markets, RSS, X/KOL tracking, News Trade, AI insights, and desktop alerts.

星云社是一个面向交易员和信息流研究者的本地优先交易情报工作台。它把中心化交易所、链上 DEX、币安钱包、港股、美股、A 股、交易所上新、News Trade、律动快讯、RSS/公众号、X/KOL 和群聊线索整合在同一套界面中，并将热榜标的继续送入多周期结构监控。

如果这个项目对你有帮助，欢迎点一个 Star 支持一下，也欢迎加入 [Discord 社群](https://discord.gg/mKyCwtHW) 交流数据源、交易信息流自动化和产品建议。

> **使用声明**：本项目仅供学习、研究和个人使用；商用、二次商业发行或作为商业服务的一部分使用前，请先获得作者本人授权。
>
> **风险提示**：页面数据、结构信号和 AI 分析仅作信息聚合与研究辅助，不构成投资建议。任何链上交易都应再次核对合约、报价、手续费、滑点和授权内容。

## 监控页链上买入

监控标的旁的「买入」使用所选钱包已有余额，不托管私钥、不从交易所提币。首次默认币安钱包，其次可选 OKX、MetaMask、Bitget；可保留多个钱包连接，但一次交易只使用所选钱包。

- 默认输入 **USDT 数量**，按实时报价换算付款币最小单位；确认页显示实际扣款币、数量、燃料费、目标合约与最低到账量。
- 付款顺序固定为：同链稳定币、同链原生币、同链其他可兑换币、其他链稳定币、其他链原生币、其他链其他可跨链币；前一档不足或路线校验失败才进入下一档。
- “其他币”只扫描 Relay 当前公链目录中明确标为可桥接的常见代币，每条链最多 24 个；不会遍历陌生空投币或任意合约。找到余额后仍需通过实时价格、路由、费用、最低到账和链上模拟校验。
- 跨链候选由纯代码规则选择，优先较高最低到账、较低费用和较短用时；模型无权参与交易路线或改动地址、金额、滑点和交易内容。
- 自动滑点最高 20%，价格冲击上限 3%，路线总损耗上限 8%，普通钱包单笔最高 1000 USDT 等值；燃料费另行预留，所有普通钱包真实交易仍需本人钱包确认。
- 同链由币安 Web3 构建，当前执行范围为 Ethereum、BNB Chain 和 Base；跨链继续使用 Relay，不保证覆盖钱包里的每一种币或全市场最优。EVM 只开放已完整解码的原生币与 ERC20 付款；Solana 暂只开放已核验的 SOL 原生币付款指令。无唯一合约、无可用路由或未通过交易校验时不能执行。
- 免费免确认模式仍是单独的 Safe 限额方案，只允许预先配置的同链稳定币，不会自动扩大到原生币、其他代币或跨链调用。
- 报价、步骤领取及交易哈希保存在独立订单库；刷新、重启或提交超时不自动重复付款，可查询原订单及退款状态。主网实盘成交未代用户验证，安全检测与 AI 不代表零风险。

前端交易执行包按需加载。修改执行器后运行 `npm run monitor:build-buy`；后端新增 `monitor_buy.py`，无需额外配置交易所密钥。

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

## 常用页面

### 本地后台稳定运行

桌面版启动时会随项目服务一并启用守护：后端异常退出会在原端口自动恢复，退出桌面项目时守护和后端同时停止，不需要另装计划任务或单独运行守护。仅在不启动桌面端、需要浏览器模式时，才使用 `启动后台服务.cmd`；该窗口就是项目服务本身，关闭后不会遗留独立守护。`停止后台服务.cmd` 和 `查看服务状态.cmd` 分别用于停止与查看该模式。

守护只管理自己启动的服务进程：意外退出按 2–60 秒退避恢复；启动宽限 180 秒后，连续 6 次存活探测失败才重启。探测不等待数据库、行情或 AI。重复启动会提示端口占用，不会抢占其他服务或重复启动监控。日志按大小轮换，保存在 `.runtime-cache/service/`。直接运行 `python server.py` 仍可用于前台调试，但不带进程守护。断电、系统休眠或外部终止守护本身仍会中断运行，不能保证绝对不停机。

### 页面入口

| 页面 | 路径 | 用途 |
| --- | --- | --- |
| 热门榜 | `/` | 独立热榜、币圈去重总榜、叙事强弱排序 |
| 涨幅榜 | `/gainers.html` | 各市场涨幅排行 |
| 成交额榜 | `/turnover.html` | 各市场成交额排行 |
| 新币新股 | `/newboards.html` | 新币、新合约、Binance Alpha 与 IPO |
| 上新播报 | `/listings.html` | 交易所和项目上新时间线 |
| 监控中心 | `/price-watch.html` | News Trade、多周期结构、X、群聊、链上投研与 GMGN 战壕 |
| GMGN 战壕 | `/price-watch.html?mode=chains&chainView=trenches&trenchChain=solana` | GMGN 已迁移项目实时榜与接收历史 |
| 起爆台 | `/dragon-wave.html` | 起爆策略、独立前高监控分页与案例反馈 |
| RSS / 公众号 | `/rss.html` | 订阅源和公众号信息流 |
| TodoList | `/todo.html` | 本地任务与提醒 |

## 功能概览

### GMGN 热搜榜与战壕

- **GMGN 热搜榜**：热门榜页面直接读取 GMGN Hot Search API，支持综合榜、`1m / 5m / 1h / 6h / 24h` 周期，以及 ETH、SOL、Robinhood、ARC、Base、BSC 等链切换；榜单保留币种头像、合约、价格、热度、流动性和成交数据。
- **GMGN 战壕**：监控中心单独提供战壕子页面，只读取 GMGN 的已迁移 / Completed 项目，按开盘时间从新到旧滚动展示，默认首屏 10 个，并保留服务运行期间接收过的历史新币。六条链分别使用对应筛选条件后再合并展示；支持的链上标的点击币种名称或币安图标即可直达对应的 Binance Web3 合约页，沿用当前浏览器已有登录会话，不读取或传输本机 token。
- **叙事与原帖**：战壕标的旁显示币安 AI 叙事分析入口；X 图标、网页链接、媒体和搜索入口按需展示，鼠标悬停 X 图标时才加载对应原帖与媒体，避免一次性请求过多。
- **人物信号**：对战壕新币的重要人物点名、转发、引用和可核验关注记录做事件先行匹配；回复/评论不纳入。来源收敛为 49 位可能直接引发币价、板块或全市场剧烈波动的核心人物，同时保留 80 个项目、交易所、公链及钱包官方账号；普通 CEO、一般研究者、普通议员不进入人物弹窗通道。所有候选关系都必须经过 JEV 快速语义判断，确认原始动作确实指向该币后才进入 V4.9 人物催化账、投研和播报；普通词、工具、公司、同名项目、评论区和被引用者的话不会因字符串相同而误报。战壕页和热门榜的“人”图标悬停可查原始动态。
- **市场主线**：每轮扫链先基于同一时间截面的跨资产强弱、成交与流动性迁移、链上资金、独立事件/产品催化和跨平台注意力，识别当下 1–3 条主线及 emerging / accelerating / consensus / crowded / rotating / fading 阶段；再判断每个新币是主线龙头、主线成员、分支扩散、补涨、独立催化、逆主线、蹭热点或尚不确定。主线不是白名单，独立强催化仍可入选，主线标的也不能绕过身份、龙头、买盘、生存率和执行风险。
- **限频与故障恢复**：GMGN 公共只读请求使用全局最小间隔、按链缓存、过期缓存和 429 冷却；页面刷新、分页和多标签页不会重复请求同一份数据。网络或限频时，热搜榜可展示最后成功数据，战壕页面明确标记当前在线来源状态。
- **播报策略**：GMGN 热门榜新进只更新榜单和历史，不触发桌面弹窗或语音播报；榜单数据、头像和手动查看功能不受影响。GMGN 战壕也不会因为单纯进入榜单就自动发出交易提醒。

- **热门榜**：Binance、OKX、Bitget、AIcoin、AVE.ai、GMGN 热搜、OKX DEX、币安钱包、港股、美股和 A 股热榜保持独立卡片；额外提供仅合并币圈标的的去重总榜，每页 10 个，不混入港美股或 A 股。AVE.ai 和 GMGN 热搜榜继续刷新和展示，但新进入榜不触发桌面弹窗或语音播报，也不因榜单来源直接进入监控池。
- **叙事强弱排序**：支持近 1 小时、6 小时和 24 小时窗口，将近期热度与叙事证据结合后，在每个原始榜单内部重新排序，不改变各数据源的卡片布局。
- **币安钱包热门榜**：默认展示 24 小时 Top 10，卡片右上角可切换 5 分钟、1 小时、4 小时和 24 小时；24 小时榜新进标的可触发播报，进入过 4 小时榜的标的会进入结构监控候选池。此来源的弹窗和结构监控始终打开对应币安钱包 Token 交易页，不会被 K 线供应商改写成二级合约页。
- **涨幅榜**：按交易所和市场拆分展示，不做混合榜；支持榜首异动提醒。Binance 和 OKX 涨幅榜前 10 名会同时进入结构监控和前高监控；若 72 小时内仍未进入 AIcoin 热门榜，则自动结束该涨幅榜观察。
- **成交额榜**：按交易所和市场独立展示资金最集中的标的。
- **新币新股**：聚合交易所新币、新合约、Binance Alpha、Hyperliquid、trade.xyz、Aster，以及港美 A 新股；高热标的红色标注。
- **上新 IPO**：按发布时间聚合交易所上新、合约上线、Binance Alpha、Aster 官方公告、IPO 日历和上市动态。
- **律动快讯**：独立快讯流，支持重要市场信息桌面弹窗。
- **自动简报**：读取自动化任务生成的交易简报，并支持 GitHub Raw JSON 兜底。
- **RSS / 公众号**：支持 RSS、Atom、JSON Feed，以及参考 WeWe RSS 逻辑的微信公众号订阅。
- **X 追踪**：监控源可分为普通 KOL、明星、名人、项目创始人/联合创始人和项目官方 X；正文与引用分开展示，官方或创始人发文会在播报中标明身份。
- **群聊监控**：通过微信监控和本机 QQ OneBot 后台通道读取线索，只转发土狗、链上或项目相关信息；识别出的标的可送入结构监控，并可将原文转发到微信。
- **链上投研**：除 L0-L3 公链市场树外，新增“今日新币全量扫盘”和 GMGN 战壕：持续累积当天发现的新池及 GMGN 已迁移项目，先用流动性、交易广度、买卖结构、成交速度和多源证据分流 Meme / 项目型候选，再只把少量量化精选交给 AI 深研。内置历史 Meme 与项目型龙头回放看板；缺少真实早期快照时明确显示“待回放”，不会虚报召回率。
- **多周期结构监控**：持续跟踪 AIcoin、AVE.ai、个人 X、群聊线索、币安钱包 4 小时榜以及 Binance / OKX 涨幅榜入池标的；按 1 分钟、5 分钟、15 分钟、1 小时、4 小时和日线识别结构。链上币优先用链 ID 与合约地址定位；榜单入池本身作为观察信号，不会因单一成交额数据源暂时不足而漏监。
- **前高监控**：使用因果枢轴确认结构高点，把相邻高点锁定为同一压力位身份，并以压力区最高外沿作为触发线。若同一近期母盘整中仍有更高的已确认压力位，较低枢轴只算箱体内部波动，不产生 B 或接近提醒；同一根 K 同时穿越多个旧前高时也只保留最高外沿的一次。完成空间分离或时间分离后，只有价格从下方首次穿越母区间最高外沿才产生正式 B 点。持续处于压力位上方不会重复刷点，真实回落后由同一检测器重新武装。该策略不套用 BTC、大周期共振或主升环境许可；起爆台的“前高监控”分页直接复用同一批本地 K 线和原盘面，便于逐根肉眼复核。
- **News Trade**：按时间倒序展示事件卡片，突出涉及标的；包含 AI 叙事强度、Meme 潜力、催化、风险和应对建议，以及独立的事件热度、链上可交易性、安全检查、手续费/滑点预估和人工确认流程。
- **TodoList**：项目分组、任务增删改查、今日提醒，按用户隔离数据。
- **账号系统**：账号密码、邮箱验证码、Google OAuth，支持用户资料和交易所 UID 绑定。
- **桌面弹窗**：市场异动、结构信号、快讯、RSS、X 动态和 Todo 提醒可进入弹窗队列；新弹窗不会覆盖旧弹窗，过时信号不会在服务重启后集中补播。所有弹窗默认保留 2 分钟；鼠标停留时暂停计时，已经显示的价格弹窗不会因下一轮行情刷新或新弹窗到来而闪退。
- **AI 分析**：普通榜单与 News Trade 可接入 DeepSeek、OpenAI、Moonshot、Qwen、Claude、Gemini 等兼容模型；GMGN 热搜和战壕的标的叙事优先使用币安 AI 叙事接口，并按标的缓存结果。API 不可用时可切换到已登录的本机 Codex CLI，并在页面明确标出 `Binance AI`、`Codex`、`AI` 或 `规则` 来源。

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

需要 Python 3.11+ 和 Node.js 18+。Node.js 用于运行与起爆台完全一致的结构策略引擎；缺少 Node.js 时，结构监控不会使用降级版或旧版规则。前高监控另提供位于 `go/prior-high-monitor` 的 Go 1.22+ 原生版本，供独立服务或后续集成复用。

```powershell
git clone https://github.com/whitestar224/market-hot-dashboard.git
Set-Location market-hot-dashboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

启动本地后台服务：

```powershell
python service_guard.py start --host 127.0.0.1 --port 8765
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

## 结构监控与起爆台策略

“监控 → 多周期结构”和起爆台共用仓库中的当前策略实现，不维护第二套监控专用规则。服务端通过 `tools/dragon_wave_monitor_bridge.js` 调用 `dragon-wave-engine.js`、`dragon-wave-cases.js`、`dragon-wave-data.js`、`dragon-wave-feedback.js` 和 `dragon-wave-vision.js`，因此后续策略优化会自动同步到结构监控。

链上标的使用“链 ID + 合约地址”作为第一身份，行情按 Binance Wallet K 线、可选的 OKX OnchainOS DEX K 线、GeckoTerminal OHLCV 轮换；DexScreener 用于解析主池并聚合同一合约的流动性与 24 小时成交额。Robinhood Chain 的 `4663`、BSC、Ethereum、Base、Solana 等常用链均有显式映射。点击链上标的弹窗“查看”会使用合约地址直接打开对应的币安钱包 Token 页面。极新代币尚未形成日线时会保留已经可用的分钟、小时和 4 小时数据，不再因为单个周期历史不足让整张卡片失败。

默认扫描 1 分钟、5 分钟、15 分钟、1 小时、4 小时和日线；1 小时、4 小时识别出的三角、降楔等有效结构突破按 B 点处理。主升浪或主升浪预期、人工反馈、多周期共振以及已确认案例的回归保护，也都由同一策略引擎统一判定。

Docker 镜像已内置 Node.js；直接本地运行时请确保 `node --version` 可用，也可以通过 `DRAGON_WAVE_NODE_BINARY` 指定 Node.js 可执行文件。结构监控接口为 `/api/price-structures`，起爆台页面为 `/dragon-wave.html`。

## News Trade 与 AI 分析

News Trade 会将快讯、X、项目官方/创始人动态和链上热门标的聚合成主题卡，并按入池时间从新到旧排列。每张卡片将规则计算与 AI 判断分开显示：

- **事件层**：事件热度、传播速度、跨平台扩散、大瓜、新奇反差、群体参与、符号传播和后续剧情。
- **AI 层**：置信度、叙事强度、Meme 机会、核心判断、催化、风险和当前应对；当前页按需分析并缓存，等待期间显示“AI 分析中”。
- **链上层**：候选标的关联度、流动性、成交量、交易笔数、合约安全和退出能力。
- **执行层**：报价、网络费、平台费、跨链费、价格冲击、建议滑点和最低可得金额。AI 不会绕过安全检查、时效门槛或人工确认。

模型 API 不可用时，开发环境可通过本机 Codex CLI 继续完成榜单与 News Trade 分析。Codex CLI 是本机入口，但仍使用已登录账号的云端额度，并非免费的离线模型。普通分析进程不浏览网页；解释推文任务仅开放 Codex 原生公开网页搜索，并使用单独的轻量配置在同一次请求里搜索中文推文和生成兜底中文解读。所有备用进程都使用临时只读目录，不调用项目工具，也不会继承项目内的 API 密钥。分析结果会明确标记来源，便于区分原帖摘录、AI 解读和规则兜底。

系统还会每 30 分钟进行一次只读自检，结合数据源健康、近期错误、监控覆盖、去重、持久化、性能、AI 额度和交互体验，筛选当前确有证据、可小范围实施的改进项。只有出现新的可执行建议时才会弹窗，同一建议在冷却期内不会反复提醒；点击“确认优化”后，本机 Codex 才会在隔离副本中修改并验证，最多涉及 8 个文件，且源文件在执行期间发生变化时不会强行覆盖。验证通过后才回写项目并保留最近 3 份备份，包含后端改动时会提示下次重启生效。

## X 与群聊信息流

X 官方 API 当前按费用政策默认硬停用，日常追踪使用 RSS、FxTwitter 和公开时间线。未来只有在用户明确批准后才允许个人账号进入付费路径：先比较账号累计发帖数，仅按增量读取，排除引用推文，并受每日 0.10 USD 与 300 次请求的代码硬上限约束；其他 KOL、项目方和 Aster 不得进入该付费路径。

项目官方 X 和创始人/联合创始人的动态会带身份标签进入播报，并送入 News Trade 判断其是否存在潜在 Meme 机会。普通 KOL、明星和名人仍保持各自分类，不与项目方身份混淆。

群聊监控只保留土狗、链上、代币或项目相关信息；闲聊不会转发。符合条件的 QQ/微信消息保留原文，提取到的标的可加入结构监控，并可按配置转发到微信目标会话。

## 配置说明

敏感配置只放在本地 `.env` 或 `.env.production`，不要提交到 GitHub。

常用环境变量：

- `XINGYUN_FIELD_ENCRYPTION_KEY`：本地数据库敏感字段加密密钥。
- `DEEPSEEK_API_KEY` / `LLM_API_KEY`：榜单和 News Trade AI 分析的首选 API。
- `CODEX_CLI_FALLBACK`：本机 AI 备用通道，默认在开发环境开启。模型 API 缺少密钥、超时、限额或故障时，改用已登录的本机 `codex exec`。
- `CODEX_CLI_TIMEOUT` / `CODEX_CLI_FAILURE_COOLDOWN`：Codex CLI 超时和失败熔断时间，默认为 90 秒和 300 秒。
- `CODEX_CLI_MODEL` / `CODEX_CLI_REASONING_EFFORT`：备用模型与推理强度，默认使用 `gpt-5.6-luna` 和 `low`。
- `CODEX_CLI_CA_MODEL` / `CODEX_CLI_CA_REASONING_EFFORT`：群聊新 CA 的身份与叙事检索专用配置，默认使用 `gpt-6-astra` 和 `high`；该通道开启只读联网搜索、不设固定分析时限，完成前页面保持“分析中”。
- `TYPESAFE_API_KEY` / `TYPESAFE_DEFAULT_MODEL`：JEV 是快速判断通道的主模型，默认 `jev-latest`；只要 JEV 可用，最终优先级和展示结论都以 JEV 为准。Key 只保存在忽略提交的本地 `.env`，请求由后端直连 `api.typesafe.ai`，不会下发到浏览器；新币主判使用 `JEV_DECISION_TIMEOUT_SECONDS` / `JEV_DECISION_CONCURRENCY`，人物推文与币种的语义指向使用独立的 `JEV_SEMANTIC_TIMEOUT_SECONDS` / `JEV_SEMANTIC_CONCURRENCY`（默认 10 秒、串行 1）。后者按“推文 + 合约”缓存，普通词、工具、公司或同名项目不会因为字符串相同而成为人物信号；AI 不可用或不确定时默认不弹窗。
- `rapid_decision_training.py`：从持久化的 V4.9 深研结果导出按发现时间切分的训练 JSONL，不随机打散，防止未来数据穿越。标签不足时仅积累和影子对比；达到 300 条且每类至少 30 条后才训练下游校准器。
- `CODEX_CLI_ONCHAIN_MODEL` / `CODEX_CLI_ONCHAIN_REASONING_EFFORT`：Codex 完整 V4.9 深研默认使用 `gpt-6-astra` 和 `high` 并开启只读联网检索。投研页同时保留“快速实时（JEV 主判）”“Codex 实时深研”“Codex 每小时深研”三档，默认是每小时档；旧版二档设置会自动迁移到该默认值，之后的手动选择会持久保存。V4.9 XMind 保存在 `C:\Users\ZhuanZ1\Desktop\交易\社区\框架\链上投研体系_V4.9.xmind`，旧版文件保留不覆盖。
- `ONCHAIN_HOURLY_RESEARCH_SETTLE_SECONDS` / `ONCHAIN_HOURLY_RESEARCH_BATCH_SIZE` / `ONCHAIN_HOURLY_RESEARCH_CONCURRENCY` / `ONCHAIN_HOURLY_RESEARCH_MAX_ROWS`：每小时档默认在整点后等待 300 秒，只领取上一封闭小时首次接收的 GMGN 战壕新币；默认两路 Codex 并发、每路每批 4 个，持续跑完并持久记录小时游标。并发可配置为 1–5 路，重启不会重复完成批次，Codex 暂忙则保留重试。`ONCHAIN_HOURLY_RESEARCH_ALERT_MAX_AGE_MINUTES` 默认 90 分钟，只允许当前上一小时的高潜结果提醒，历史补算不补弹窗。
- `CODEX_CLI_EXPLANATION_MODEL` / `CODEX_CLI_EXPLANATION_REASONING_EFFORT` / `CODEX_CLI_EXPLANATION_TIMEOUT_SECONDS`：解释推文专用配置，默认 `gpt-5.6-sol`、`medium`；比普通后台分析提高一档，但不使用更高的 Astra / high 档位。超时默认值为 `0`，表示后台不设固定分析上限，完成后再显示结果。
- `CODEX_CLI_MAX_ROWS_PER_REQUEST`：每批最多交给 Codex CLI 解析的榜单行数，默认 24；页面按批次渐进回填，避免后面的榜单被遗漏或整页长时间等待。
- `SELF_OPTIMIZATION_INTERVAL_SECONDS`：系统只读自检间隔，默认 1800 秒；`SELF_OPTIMIZATION_SUGGESTION_COOLDOWN_SECONDS` 控制同类建议的去重冷却期。
- `SELF_OPTIMIZATION_CODEX_TIMEOUT_SECONDS` / `SELF_OPTIMIZATION_MAX_CHANGED_FILES`：用户确认后，隔离优化的最长执行时间和文件数量上限，默认 1800 秒与 8 个文件。
- `XINGYUN_DISABLE_SELF_OPTIMIZATION`：设为 `1` 可完全停用系统自检；自检不会自动确认或直接改代码。
- `LLM_API_BILLING_COOLDOWN`：首选模型接口出现余额或鉴权错误后，多久内直接改走本机 Codex，默认 1800 秒，避免每分钟重复等待失败接口。
- Windows 下会自动继承当前系统代理供 Codex CLI 连接模型服务，但不会把项目 API Key 或其他业务密钥传给备用进程。
- `EMAIL_SMTP_*`：邮箱验证码登录。
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`：Google OAuth 登录。
- `X_BEARER_TOKEN`：X KOL 官方 API。
- `WECHAT_*`：微信公众号订阅授权。
- `OKX_*` / `BITGET_*` / `AICOIN_*`：交易所和客户端数据源配置。
- `AVE_X_AUTH` / `AVE_UDID`：AVE.ai 热门榜授权；`AVE_HOT_ALERT_INTERVAL_SECONDS` 控制独立的新进检测与监控入池间隔，默认 30 秒。
- `OKX_DEX_API_KEY` / `OKX_DEX_SECRET_KEY` / `OKX_DEX_PASSPHRASE`：可选的 OKX OnchainOS 行情密钥，用于增加链上合约 K 线备用源；未配置时自动跳过。
- `CHAIN_ECOSYSTEM_REFRESH_SECONDS`：链上投研后台扫描间隔，默认 300 秒；数据源连续失败时会自动退避。
- `CHAIN_ECOSYSTEM_VISIBLE_PROJECTS`：潜在发行池最多下发到页面的精选项目数，默认 80；总项目数仍单独展示，避免原始发现过多拖慢页面。
- `CHAIN_ECOSYSTEM_UI_CACHE_SECONDS`：链上投研完整页面快照默认保留 30 秒；过期后先返回最后可信结果，再在后台更新，避免开盘采集写入时卡住页面。
- `ONCHAIN_RESEARCH_REFRESH_SECONDS`：每条链独立的新池扫描间隔，默认 60 秒；每链最多一个任务，失败只退避该链。首轮筛选先落库并交给后台 AI，再补全行情。
- `ONCHAIN_RESEARCH_NETWORKS`：扫链只读取币安战壕与 GMGN 战壕的“已迁移/Completed”标的，默认网络为 `eth,solana,robinhood,arc,base,bsc`。币安战壕当前只在其官方支持的 SOL、BSC、Base 上取数，其余链由 GMGN 覆盖。
- `ONCHAIN_RESEARCH_NEW_POOL_PAGES`：每条链冷启动最多回溯 6 页；遇到已有池立即停止，后续通常只读第 1 页，减少开盘漏扫。页数限制仍然存在，并非全链无遗漏保证。
- `GMGN_PROXY_URL`：可选的 GMGN 独享固定出口 HTTP(S) 代理。配置后，热搜、战壕页面和后台扫描统一走该出口；为空时使用系统直连/TUN 路由，并忽略失效的 `HTTP_PROXY/HTTPS_PROXY` 环境变量。
- `GMGN_READONLY_KEY_MODE`：默认 `public`，使用项目公共只读 Key，且不会自动读取本机个人 Key；只有显式设为 `personal` 时才读取 `GMGN_API_KEY` 或 GMGN CLI 配置。
- `GMGN_READONLY_MIN_INTERVAL_SECONDS`：所有 GMGN 公共只读请求的全局最小间隔，默认 1 秒。
- `GMGN_TRENCHES_CACHE_TTL_SECONDS`：战壕按链复用缓存的时间，默认 60 秒；翻页、筛选和多标签页不会重复打到 GMGN。
- `GMGN_READONLY_STALE_SECONDS`：热搜等低频榜单在限频或网络异常时允许继续展示最后成功数据的时间，默认 600 秒。战壕实时数据不读磁盘旧响应。429 冷却状态会保存在 `.runtime-cache`，服务重启不会立即再次撞限频。
- `TRENCH_PERSON_REQUEST_MIN_INTERVAL_SECONDS`：人物动态公开源的全局最小请求间隔，默认 45 秒，每次只读一个账号；49 位核心人物与 80 个官方账号仍保持单请求串行，不会按账号数并发放大。核心人物、项目官方分层轮转，与当前战壕币叙事直接相关的来源优先，未访问来源有公平调度保护，失败时指数退避。所有召回关系都必须再经过 Jev 快速语义判断；`TRENCH_PERSON_SEMANTIC_CACHE_HOURS` 默认缓存 168 小时，失败重试间隔默认 120 秒，避免同一推文与合约反复请求。可用 `TRENCH_PERSON_SOURCE_LIMIT`、`TRENCH_PERSON_DEFAULT_REFRESH_SECONDS`、`TRENCH_PERSON_OFFICIAL_REFRESH_SECONDS`、`TRENCH_PERSON_SECONDARY_REFRESH_SECONDS`、`TRENCH_PERSON_RELEVANT_REFRESH_SECONDS` 和 `TRENCH_PERSON_POST_MAX_AGE_SECONDS` 调整。该通道只用免费公开时间线，不会扩大已限定为个人账号的 X 付费 API 权限。
- `ONCHAIN_RESEARCH_ENRICH_LIMIT`：每条链每轮交给 DEX Screener 补全的数据量，默认 90。
- `ONCHAIN_BSC_RPC_URLS`：BSC 只读 RPC 备用节点列表；每 5 秒增量读取 Flap / Four.meme 的创建事件，保存区块游标，失败后从原位置继续补读。
- `XINGYUN_DISABLE_CHAIN_ECOSYSTEM_MONITOR`：设为 `1` 可暂停链上投研后台扫描。
- `GITHUB_TOKEN`：可选，仅用于提高手动添加项目仓库的 GitHub 公共接口额度。

## 链上投研

在“监控 → 链上投研”中可查看三阶段公链列表、L0-L3 细分市场、每个市场 Top5、潜在发币池及证据来源。今日投研严格以页面 GMGN 战壕榜的新币为候选池，并提供三种并存模式：快速实时、Codex 实时深研、Codex 每小时深研。V4.9 会对每个 CA 反查最近 0–72 小时强热点，并把官方身份、热点炒作机会、执行安全拆成三本独立账；非官方但高度贴合热点且有真实买盘/传播/入口承接的标的可升级为 P0/P1 并参与龙头竞争，但不能越过致命执行风险。普通结果静默入档，只有体系明确判断真正值得看的正向标的才弹窗，投研弹窗不做语音。

研究清单只展示 AI 复核且有具体叙事证据的标的，每页 12 个，显示总数并可翻页；分页不裁掉候选或影响后台分析。每个候选先生成整套 V4.9 事实快照，再进行联网深研：新增 Hotspot-Derived CA、0–72h 热点双向扫描、Mapping Fit、P0/P1 hotspot override、同热点全量竞争 CA 与动态 Current Real Leader，同时继续覆盖市场主线、人物催化、Meta 家族、生存率、四条生命线、执行和风险。官方性只决定身份描述，不再否决热点机会；假官方与致命合约风险仍由独立账本阻断执行。

律动快讯明确提及某链代币时，会把同链同名且有行情的候选加入题材复核。新闻未给合约时明确标为关联待核验，不能仅凭同名推定官方身份；这些待核验关系不会触发强机会弹窗。桌面弹窗只允许证据受支持、完整研究卡明确判定为“大金狗潜力”或“龙头潜力”，且机会分、龙头分和解释完整度同时过线的候选；普通 strong、watch、纯量价爆发、资料不足和重复机会不播报。机会与执行风险相互独立：高潜候选即使因合约风险被标为 BLOCK 也可作研究提醒，但弹窗会明确显示“仅研究，不可直接执行”，并打开本地投研页而不是交易页。

历史复盘使用版本化案例集，并且只允许使用上线后 24 小时内真实保存的快照计算召回率。目标召回率是至少 80%，但在收集或导入足够的早期快照前，页面会显示“待回放”，不会用当前结果倒推历史命中。公开新池接口存在分页和限流边界，因此这里的“全量”指本服务持续运行期间对配置网络当天发现池子的完整累积；上游不可用时会保留既有快照并自动退避。

公链生态扫描仍使用 GeckoTerminal、DEX Screener、DefiLlama、Blockscout 和 GitHub 的公开接口，并对链、细分市场、项目、资产和变化预警逐条生成 AI 研判；也可以手动添加公链、项目或证据，系统会把二者合并后重新评估。

桌面端只推送四类高价值变化：公链阶段升级、新细分市场、Top1 连续两轮确认变更，以及流动性/成交量/交易笔数显著放大。代币形成有效交易只更新市场状态和排名，不弹窗、不播报。首次成功扫描仅建立基线，不补发历史提醒；某个数据源失败时保留上一份完整快照，也不会据此触发阶段或龙头变化。

## QQ 后台群消息通道

QQ 群监控默认通过本机 NapCat / OneBot 11 接口工作，不需要 QQ 窗口保持可见。系统使用 WebSocket 接收实时群消息，并定时调用 `get_group_msg_history` 回补服务重启或短暂断线期间的消息；群名、发送人过滤、币种提取、去重、结构监控入池和微信转发仍由原有业务链路处理。

安全约束：HTTP 与 WebSocket 必须只监听 `127.0.0.1`，两个接口使用相同 Token，禁止将端口暴露到局域网或公网。运行配置位于 `.env`：`QQ_ONEBOT_HTTP_URL`、`QQ_ONEBOT_WS_URL`、`QQ_ONEBOT_TOKEN`。确认 OneBot 连通后保持 `QQ_UI_FALLBACK_ENABLED=0`，避免重新依赖窗口或 OCR。

`QQ_ONEBOT_RECOVERY_ENABLED` 默认保持为 `0`。这样 OneBot 断线只会进入安全重连等待，不会结束、隐藏启动或抢占用户手动登录的 QQ。`QQ_UI_FALLBACK_ENABLED=1` 时会临时读取当前已打开的目标群窗口；后台接口恢复后自动切回 OneBot。使用独立监控号时可同时设置 `QQ_ONEBOT_RECOVERY_ENABLED=1` 与 `QQ_NAPCAT_ALLOW_PARALLEL_MANUAL_QQ=1`，允许日常 QQ 保持普通登录，同时只恢复由 NapCat 托管的监控号；恢复脚本仍只管理 NapCat 自己的进程树。两号并行时应关闭窗口备用读取，避免读取到日常账号的聊天窗口。

## 钉钉热门币监控机器人

钉钉推送是独立进程：它复用价格监控接口已经计算好的 AICoin 热门币、最近 7 日前高和预警轮次，但拥有单独的 Webhook、加签密钥和发送状态。Discord 是否配置或发送成功不会影响钉钉。

1. 在目标钉钉群中添加“自定义机器人”，安全设置选择“加签”，保存 Webhook 和 `SEC...` 开头的密钥。创建流程见[钉钉开放平台文档](https://open.dingtalk.com/document/orgapp/custom-robot-access)。
2. 把下面配置加入本地 `.env`：

```dotenv
DINGTALK_PRICE_WATCH_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=你的令牌
DINGTALK_PRICE_WATCH_SECRET=SEC你的加签密钥
```

3. 保持主行情监控服务运行，先发送连接测试：

```powershell
.\start-dingtalk-price-watch.ps1 -TestMessage
```

4. 测试成功后启动持续监控：

```powershell
.\start-dingtalk-price-watch.ps1
```

只检查一轮可使用 `-Once`。首次启动默认记录当前预警轮次但不补发旧消息；如需推送当前仍在前高附近的信号，可首次使用 `-SendExisting`。独立发送状态保存在 `.runtime-cache/dingtalk-price-watch-state.json`，钉钉发送失败时不会推进状态，下一轮会自动重试。

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

- **中心化与衍生品市场**：Binance、OKX、Bitget、Gate、HTX、AIcoin、Aster、Hyperliquid、trade.xyz。
- **链上市场与行情**：币安钱包、币安 Web3、GMGN Hot Search / Trenches、OKX DEX / OnchainOS、DEX Screener、GeckoTerminal，以及各链可用的交易对和 K 线接口。
- **股票市场**：富途、同花顺、东方财富，以及公开的港股、美股和 A 股榜单。
- **新闻与社区**：BlockBeats、RSS、微信公众号、X / Twitter，以及用户本地授权的微信或 QQ 群聊。
- **链上投研**：DefiLlama、Blockscout、GitHub、GeckoTerminal 和 DEX Screener。

不同数据源稳定性和可访问性会受网络环境、接口变动、地区限制、登录状态和限流策略影响。项目会在可用数据源之间轮换并保留最近有效缓存，但不会把估算值伪装成官方数据，也不保证任何第三方数据源持续可用。

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
