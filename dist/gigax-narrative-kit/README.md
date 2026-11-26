# GIGA X 叙事桥接 + GMGN 悬浮窗替换

把 GMGN 网页端代币卡片上的「AI 叙事」悬浮窗，替换成 GIGA X 对同一代币生成的叙事分析。

GIGA X 的叙事接口需要登录态，而 token 只存在手机 App 里；所以这里用**桥接**的方式：
浏览器里的油猴脚本调你本机的 HTTP 服务，由服务拿 token 去代调 GIGA X。

```
GMGN 页面 (Tampermonkey)
        │  GET http://127.0.0.1:8765/api/gigax/narrative?chain=bsc&address=0x...
        ▼
  桥接服务 (bridge/gigax.py)
        │  ① 需要时先向 Privy 续期：POST auth.privy.io/api/v1/sessions
        │  ② GET app-api.gigax.fun/api/data/token/narrative
        ▼
   返回 { ok, text, timeline, ... } → 脚本把悬浮窗正文替换掉
```

---

## 一、目录结构

```
gigax-narrative-kit/
├── bridge/
│   ├── gigax.py          桥接核心：续期 + 调叙事接口 + 保活（纯标准库）
│   ├── server.py         独立 HTTP 服务（不想动你现有后端时用它）
│   ├── config.json       你的配置（由 config.example.json 复制而来，不要外发）
│   └── .state/           运行时目录：captured.json（token）、securestore_dump.json
├── tools/
│   ├── gigax_securestore_dump.py   从 Android 模拟器解出 token（frida）
│   └── gigax_tls_tap.py            需要重新逆向接口时抓 HTTPS 明文（可选）
├── userscript/
│   └── gmgn-gigax-narrative.user.js
├── config.example.json
├── captured.example.json
└── README.md
```

**包里没有任何 token、App ID、设备 ID。** token 在你第一次运行时才生成，
App/Client ID 由你自己从 APK 里取（见下文）。

---

## 二、前置条件

| 用途 | 需要 |
| --- | --- |
| 长期跑桥接 | Python 3.10+（只用标准库） |
| 首次获取 token | 一台能装 GIGA X 的 Android 模拟器（已登录）、带 root、`frida` **16.x** 与 `frida-server` 同版本 |
| 浏览器 | Tampermonkey / Violentmonkey |

> `frida` 官方只提供到 3.10/3.11 的 wheel，建议单独建一个 3.10 环境装 `frida==16.7.19`，
> 再用 `config.json` 的 `fridaPython` 指过去。桥接本身不需要 frida。

---

## 三、快速开始

### 1. 填配置

```bash
cp config.example.json bridge/config.json
```

几个 ID 在 APK 的 `assets/app.config`（JSON）里，路径 `extra.privy`：

| 字段 | 从哪来 |
| --- | --- |
| `privyAppId` | `extra.privy.appId` |
| `privyClientId` | `extra.privy.androidClientId`（**必须是 `client-XXXX` 这种**） |
| `nativeAppId` | APK 包名，如 `family.giga.app` |
| `privyClientVersion` | 抓包里的 `privy-client` 头，留默认 `expo:0.74.3` 通常可用 |
| `privyCaId` | 可留空，留空会自动生成一个 UUID 并复用；续期报错时再从抓包里拷真实值 |
| `adb` / `fridaPython` | 可留空，留空自动探测 PATH 与 Android SDK |

> 解 APK 不用装 Android Studio：`unzip app.apk assets/app.config -d out` 即可。

### 2. 拿第一份 token（只需一次）

1. 启动模拟器，打开 GIGA X 并**登录**，停在任意页面别退出；
2. 起 frida-server（16.x，例如 `adb shell su -c "/data/local/tmp/fs16 -l 0.0.0.0:27044"`），
   `adb forward tcp:27044 tcp:27044`；
3. 跑：

```bash
python tools/gigax_securestore_dump.py --adb <adb路径>
```

它会在模拟器里用 App 自己的 Keystore 密钥解出 `SecureStore`，打印并落盘
`bridge/.state/securestore_dump.json`（含 access token + refresh token）。

### 3. 把 token 灌进桥接

```bash
python - <<'PY'
import base64, json, pathlib
dump = json.loads(pathlib.Path("bridge/.state/securestore_dump.json").read_text("utf-8"))
dec = lambda k: base64.b64decode(dump[k]["b64"]).decode()
state = pathlib.Path("bridge/.state") / "captured.json"
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps({
    "token": dec("key_v1-privy-token"),
    "refreshToken": dec("key_v1-privy-refresh_token"),
}, ensure_ascii=False, indent=2), "utf-8")
print("ok")
PY
```

### 4. 起桥接

```bash
python bridge/server.py --port 8765
curl http://127.0.0.1:8765/api/gigax/health
```

`hasToken=true`、`hasRefreshToken=true`、`expiresInSeconds` 为正即正常。
**从这一刻起就不用再开模拟器了**：进程每 30 分钟自动续一次，token 到期前 5 分钟内的请求也会自动续。

### 5. 装油猴脚本

Tampermonkey → 新建脚本 → 粘贴 `userscript/gmgn-gigax-narrative.user.js` → 保存。
打开 `gmgn.ai`，把鼠标移到任意代币的 AI 叙事图标上，悬浮窗正文应变成
`【GIGA X 叙事】…`。徽标悬停能看到本次用的是 `链:地址`。

---

## 四、怎么判断「这笔叙事属于哪个币」

悬浮窗 DOM 里常常**没有合约地址**，所以脚本按可信度取址，越靠前越优先（**1.7.0 定案**）：

| 优先级 | 来源（徽标 `title` 里的 `地址来源`） | 说明 |
| --- | --- | --- |
| **0** | **`data`** | **GMGN 自己写在 DOM 上的 data 属性**，见下节。同一张卡自己的、唯一的、机器可读的地址 |
| 1 | `popup` | 浮窗 DOM 内**只有一个**地址 —— 浮窗自带地址就是铁证 |
| 2 | `hover` / `hover-sym` | hover **当时**记下的那张卡片（CA + 矩形 + symbol）。浮窗是 hover 卡片弹出来的，它就是正主 |
| 3 | `pointer` | 鼠标下那张卡片（`elementsFromPoint` 穿透**所有**浮层） |
| 4 | `popup-multi` | 浮层内多个地址时，取能和 hover / 指针 / URL 对上的那个（不再盲取第一个） |
| 5 | `page-url` | 详情页 URL 的 CA（只认 `/<chain>/token/<CA>` 形态） |
| 6 | `network-ai` | 叙事类请求（narrative / analysis / social）里的地址，30 秒内有效；**且必须能在页面上找到这个币**，否则一律不用 |
| — | 放弃 | 以上全取不到 → 保留 GMGN 原文，绝不换成别的币 |

**详情页例外（1.7.1）**：路径是 `/<chain>/token/<CA>` 时，若**指针此刻不在任何卡片上**
（图表、页面头部、空白区），直接取页面地址 —— 因为详情页的叙事浮层讲的就是这个页面的币，
而 `hover` / `bind` 那些锚点是用户**从旁边列表划过去**时留下的，必然是过期的。
（K 线是 TradingView `iframe`，指针在图表上时 `elementsFromPoint` 只拿到 `IFRAME`，
所以「指针穿透」在详情页主内容区一律为空：这条不是兜底，是主判据。）

### 第 0 优先：GMGN 的 data 属性（1.7.0，用真实浏览器实测出来的）

2026-11-19 用 `chrome-devtools-mcp` 打开真实的 `https://gmgn.ai/?chain=bsc`（战壕页，30 张卡）实测：

```html
<!-- 右侧战壕卡：一卡一个，30 个 holder / 30 个不同 CA，尺寸正好 326×144 -->
<div data-trenches-token-address="0x44b92e572cba406ad62ba2d10393eff940837777"
     data-trenches-token-chain="bsc"
     data-trenches-token-key="0x44b92e…7777-bsc">

<!-- 左侧榜单行：一行一个（虚拟列表的键） -->
<div data-row-key="bsc:0xb82535e74b03ffe4910efea1c759b6bbbf0a7777" …>
<div data-recycler-key="bsc:0xb82535e74b03ffe4910efea1c759b6bbbf0a7777" …>
```

**为什么这条是决定性的**（也是之前十几轮反复出错的真正原因）：

- 战壕卡片里 `a[href*="/token/"]` 的个数实测是 **0 / 1 / 2 个**，币名（`NVIDIASI`）是**纯文本节点**、
  根本不是链接。老版本靠代币链接找币 → 战壕区要么一无所获（退到网络嗅探，于是**全变成同一个币**），
  要么往上爬到列容器、取到**下面那张卡**的链接（IDRENT 显示成慈善CZ 就是这么来的）。
- 同一屏实测存在**同名不同币**：两张卡都叫 `PENGB Penguin Solutions`（`0x0c58…ffff` / `0xd962…ffff`）、
  都叫 `NVIDIASI`（`0x44b9…7777` / `0x407a…ffff`）。所以「靠 symbol 认币」从根上不可靠 ——
  能拿到地址就绝不靠名字。
- 虚拟列表会**复用同一个 DOM 节点**给另一个币（`data-recycler-key` 一变就换币）。所以脚本另外挂了
  一个只盯这三个属性的 `MutationObserver`：换币即重刷标色，节点上还留了 `data-gigax-ca` 做校验。
  这是「标过的卡片一会儿又没了 / 变成别的币还带着蓝底」的物理原因。

实测覆盖率（同一页面，每张卡取 4 个内缩采样点，走「指针 → `elementsFromPoint` → 取址」全链路）：

| 区域 | 采样点 | 命中 | 认错 | 说明 |
| --- | --- | --- | --- | --- |
| 右侧战壕卡 | 60 | **60** | **0** | 100% |
| 左侧榜单行 | 44 | 34 | **0** | 未命中的 10 个点全部落在 `GlobalCalloutPanel`（可拖动的喊单浮窗）上 —— 浮层盖住了列表，本来就点不到卡片 |

三条硬规矩（都是踩过坑换来的）：

- **能读 data 属性就绝不猜**：`data-trenches-token-address` / `data-row-key` = 卡片自带的地址；
  只有 GMGN 没给属性的地方（如详情页正文）才退到链接/浮层/网络。
- **向上找祖先必须从里往外停**：遇到第一个「卡片大小」且带地址的祖先就停，不能一路爬到最外层 ——
  一列里相邻两张卡会被同一个列容器包住，爬到那层就等于把两张卡当成一张。
- **浮层容器会被复用**：GMGN 换币时不一定重写正文节点，「里面还是我们写的内容」**不等于**
  「已经处理好了」。每轮都要校验「现在这个浮层讲的是哪个币」，换了就整块重来。
- **叙事请求只是佐证，不能当主证**：GMGN 会预取相邻卡片，浮窗里出现「上一个 hover 的币」
  的叙事是常态。所以它排在最后，且要能在页面上找到这个币。
- **响应回来要验币**：GIGA X 生成要几秒，这期间浮窗可能已经换成另一个币 ——
  此时丢弃该正文，让下一轮按新币重取（命中缓存，不会再打网络）。

调试：在 GMGN 页面按 `Alt+G`，控制台会打印每个悬浮窗的 DOM、
`解析结果: bsc:0x…（来源 hover）`，以及最近四条网络事件。按 **`Alt+Shift+G`** 则直接在页面上
弹一个诊断面板，逐个列出当前屏的币：有没有取到叙事、命不命中关键词、卡片找没找到、多大、
有没有背景色 —— 哪张卡没标上一眼就能看出卡在哪一步。普通使用时也可以把鼠标停在
`【GIGA X 叙事】` 徽标上，`title` 会写明用的是哪个地址、地址是怎么认出来的。

### 替换时机（1.3.0 起）

悬浮窗是 React 现插的，等轮询才发现就一定会「先闪一下 GMGN 原生文案」。所以：

1. **hover 预热**：鼠标停在卡片/AI 按钮上 180ms 后就去取一次，等浮窗真的出现时缓存已命中；
2. **DOM 观察**：出现像叙事浮层的节点立刻扫（全量扫另有 1 秒节流），不等轮询；
3. **未命中缓存就先遮住**原生正文（显示「GIGA X 叙事加载中…」），拿到结果再落地；
4. **GIGA X 说没有这个币**（`reason: "no-narrative"`）时**一个字都不动**，保留 GMGN 原文，
   不写红字、不清空；同一浮层换币后自动重判；
5. 真错误（断网/鉴权）重试 3 次，仍失败也还原文，页面上不留错误框。

> base58 地址（Solana）**绝不能小写化**，小写后是另一个地址，GIGA X 会回
> `{"code":-1,"msg":"Invalid token address"}`。脚本里这类返回按「没这个币」处理，不会报错。

### 自动预取与机会标记（1.4.0 起，1.5.0 起按批）

进页面**不需要 hover**：脚本每 0.7 秒把视野里的代币打包成一条 `/api/gigax/narratives` 请求
（一次最多 12 个、允许两批同时在飞、从上往下取），服务端 8 路并发去问 GIGA X。
实测 8 个冷币一条请求 10.6 秒、再请求一次 0.13 秒（全走缓存）。

叙事到手后做关键词匹配，**任意一个词命中就标**（1.7.7 起，用户定案「弱词单个也行」）：

| 组 | 关键词 |
| --- | --- |
| 强（命中 1 个即标） | 新模式 / 新机制 / 新玩法 / 新叙事 / 新范式 / 新物种 / 第一个 / 首个 / 首家 / 首例 / 首款 / 首创 / 首发 / 首次 / 地推 / 平台币 / 平台原生 / 发射台 / 发射平台 / launchpad / launch pad / bonding curve / agentic / agent id / ai agent / ai 身份 / 智能体 / 灵魂绑定 / soulbound / 代理 / agent / ai |
| 弱（单个也标） | 新 / 第一 |
| AI（命中走**白色**标记） | agent / agent id / ai agent / ai / ai 身份 / 智能体 / 代理 / agentic |
| 自定义（面板里自己填） | 你填什么就是什么，命中走**蓝色**；开关一关就全部失效 |

英文不区分大小写；纯 ASCII 的 1~2 字母词（`ai`）按整词边界匹配（`chairman`/`claim` 里含
`ai`，不能直接 includes）。悬停卡片会显示命中了哪些词；**详情页顶部那条（当前正在看的币）
不标**；持仓/仓位面板不标。

**自定义关键词面板（1.8.0）**：页面右下角 `叙事关键词` 悬浮面板，标题栏可拖拽（位置记忆）、
`–` 折叠、开关只管自定义词。输入框回车添加（可逗号/空格分隔多个），词存 GM 存储（重启不丢）；
输入框空着按退格/Delete 删最后一个，chip 聚焦后按 Delete 删该词，点 `×` 也能删。菜单里可
隐藏面板 / 清空全部。

样式用 **DeepSeek logo 那个蓝 `#4D6BFE`**：底色 `rgba(77,107,254,.30)` + **2px 实心同色内描边** + 内发光，
菜单里填充浓度可在 18/30/42/55% 循环（描边恒亮，深色卡片上也看得见）。只改底色和描边，不动文字颜色、不动布局。
AI 词走白色：`rgba(255,255,255,.12)` 极淡填充 + 2px 纯白内描边 + 白色内发光（深填充会洗掉浅色文字）。

> 卡片定位会**优先挑最外层「有背景色/背景图」的祖先**：战壕榜那种卡片里套了多层透明 div，
> 只刷最内层等于没刷（2026-11-18 实测反馈）。

标记结果会存进 `GM_setValue('hotIndex')`（24 小时），**重开页面先标上再慢慢刷新**，
不用干等网络。注意「当前正文优先」：这一轮拿到正文且不命中的币，会立刻删掉历史结论，
不许拿昨天的命中把卡片又染回来。

菜单里可以：关闭标记 / 循环改底色深浅 / 关掉自动预取 / 隐藏（清空）自定义关键词面板 /
调整浮层正文**字号**（13 → 15 → 17px 循环，默认 15px，选择会记住）。


---

## 五、接口

| 路由 | 说明 |
| --- | --- |
| `GET /api/gigax/health` | token / refresh token 状态、剩余秒数、refresh token 已用天数 |
| `GET /api/gigax/narrative?chain=bsc&address=0x…` | 取单个币的叙事；`chain` 支持 `sol/eth/bsc/base/robinhood/arc/elysium` |
| `GET /api/gigax/narratives?items=sol:CA1,bsc:0x…&workers=8` | **批量**：一条请求取多个币，服务端并发（默认 6 路）。返回 `{results:{"sol:CA":{…}}}`，命中进程内缓存的不再打 GIGA X |
| `GET /api/gigax/refresh` | 强制续期一次 |
| `OPTIONS /api/gigax/*` | 浏览器预检，返回 204 |

返回示例：

```json
{
  "ok": true,
  "reason": null,
  "text": "……叙事正文……",
  "originalText": "……原文……",
  "categoryTags": ["meme"],
  "generatedAt": "2026-11-18T09:12:05Z",
  "timeline": [{ "summary": "…", "url": "https://x.com/…" }],
  "status": "ready"
}
```

拿不到正文时 `ok: false`，`reason` 区分三种情况（**前端行为不同，别混用**）：

| `reason` | 含义 | 前端该怎么做 |
| --- | --- | --- |
| `no-narrative` | GIGA X 没这个币 / 正在生成 / 地址不合法 | 保留原文，按 `retryAfter` 秒后再看 |
| `auth` | token 失效且续期失败 | 重试几次，仍失败保留原文 |
| `error` | 网络或未预期的返回 | 同上 |

```json
{ "ok": false, "reason": "no-narrative", "status": "generating", "retryAfter": 5,
  "error": "GIGA X 暂无该币叙事" }
```


### 嵌进已有后端时

把 `bridge/gigax.py` 放进你的项目（例如 `app/api/gigax.py`），加三条路由即可：

```python
from app.api import gigax

# GET /api/gigax/health
payload = gigax.health()

# GET /api/gigax/narrative?chain=&address=
payload = gigax.fetch_narrative(chain, address)

# 启动时挂保活
gigax.start_keepalive()
```

**两个必踩的坑**：

1. **不要对这些路由启用 gzip**。油猴的 `GM_xmlhttpRequest` 不会自动解压，
   返回压缩字节会让脚本 `JSON.parse` 失败，表现为「桥接返回非 JSON」。
2. 路由前缀是 `/api/gigax/`，油猴脚本里只填**根地址**（`http://127.0.0.1:8765`），
   带不带路径脚本都会自己归一化处理。

### 让系统里其它叙事入口也走 GIGA X

榜单行、战壕卡、桌面弹窗这些「叙事分析」入口原来各打各的币安 AI。现在统一在一处收口：

```python
# 单条：GIGA X 优先，命中直接返回；没命中才按开关回落币安 / Bitget
item = exchange_ai_narrative_for_item({"sourceId": "gmgn-trenches", "chain": "sol",
                                       "contractAddress": "<CA>", "symbol": "XXX"})

# 榜单预取：同一套顺序，并把结果同时写成 exchangeAi* 与 binanceAiNarrative*
source = enrich_binance_wallet_ai_narratives(source)
```

- 链别名先过 `GIGAX_EXCHANGE_AI_CHAINS`（`56`/`bnb`/`bsc`、`CT_501`/`solana`/`sol`、
  `4663`/`robinhood`、`99901`/`elysium` 都认），**认不出的链直接跳过**，不发空请求。
- 只有 `ok` 且真有正文才算命中；`no-narrative` / `auth` / `error` 一律继续兜底，
  不会把「还在生成」误当成「这个币没叙事」。
- 去重键 `exchange_ai_address_key()`：**EVM 折叠大小写，base58 绝不折叠**。
- 想彻底切断币安：`EXCHANGE_AI_NARRATIVE_LEGACY_FALLBACK=0`。代价是 GIGA X 没覆盖
  的币（新币还没生成、只有 ticker 的中心化行情行）会直接显示「暂无叙事」。

---

## 六、常见问题

**`桥接返回非 JSON（HTTP 200）`**
路由前缀写错，打到了静态文件服务上（拿到的是 `index.html`）。检查脚本里的桥接地址与
服务端路由是否一致。

**叙事和卡片上的币对不上**
按 `Alt+G` 看 `解析结果 … 来源 xxx`。若来源是 `popup-multi`，说明这个浮层 DOM 里有多个地址，
可以把那一行和 DOM 片段发出来调选择器；若是 `pointer` 且鼠标位置比较特殊，把鼠标停在卡片上再打开。

**`Invalid app client ID`**
`privyClientId` 填成了 JWT 里的 `app_client_id`（内部 id）。要填 `client-XXXX` 形式的
`androidClientId`。

**`Invalid nativeAppID` / `Origin not allowed`**
`nativeAppId` 没填或不是包名；`Origin` 需与服务端保持一致（脚本走本机 HTTP，一般无此问题）。

**`{"code":-1,"msg":"Invalid token"}`**
access token 不对（常见于误用了 `privy_access_token` / `pat`）。必须用 `aud` 等于 App ID 的那个
access token；桥接里已做校验，遇到 pat 会直接报错而不是静默返回错内容。

**过了 30 天突然全挂**
Privy 的 refresh token 默认 30 天到期。`/api/gigax/health` 里 `refreshTokenAgeDays ≥ 25`
时保活线程会每天提示一次。这时重登一次 App、再跑一遍第 2 步即可。

**模拟器上的时间被我改过？**
`--force-http1`/抓包时可能会调时钟。恢复：`settings put global auto_time 1` 再 `date` 对一次。

---

## 七、安全与合规

- `bridge/.state/captured.json` 里是**你本人账号的凭据**，等于账号密码，**不要提交、不要外发**；
  本包不包含它，示例见 `captured.example.json`。
- 桥接服务默认只监听 `127.0.0.1`。确实要给局域网设备用再加 `--host 0.0.0.0`，
  并且清楚这意味着同网段任何人都能拿你的 token 调接口。
- 这套东西依赖对 GIGA X / GMGN 客户端的逆向与自动化调用，**可能违反对方服务条款**，
  也可能随时因对方改接口而失效；自用、自担风险，别用于批量抓取或对外提供服务。

---

## 八、版本

- userscript 1.10.5 / bridge 1.1.0
- **1.10.5：币名只取标题，不再带标签**（用户 2026-11-26：「弹窗里的币名，要用前面这个，
  而不是全部那一长串的，比如刚刚弹的 CICI」）。
  - 卡片标题区是并列的 `<span>`，`textContent` 没有分隔符，`CiCi` + 标签
    `Binance AI Act` 拼出来就是 `CiCiBinance AI Act`。
  - 纯字符串切不干净：同一套驼峰规则会把 `希望HOPE` 切成 `希望`、把
    `DemoStaking Pool (USD)` 切成 `Demo` —— 那些都是真页面上的正确币名。
  - 改成**按字号取**：`readCardName()` 在卡片内找「字号最大 + 字重最高」的叶子文本，
    作为 `name_text` 一起回传，后端优先用它。每卡最多看 12 个候选，5 秒一轮。
  - 后端 `display_name_from_card_text()` 只保留两种**不会误伤**的剥离：`Tax 2%` 之后
    切掉、整串重复折叠（`ELONCOINELONCOIN` → `ELONCOIN`）。
- **1.10.4：详情页 + 战壕同屏也能回传**（用户 2026-11-26 全景截图：
  「战壕就在详情页旁边，我的布局就是这样的」）。
  - 根因：`onTrenchPage()` 见到 URL 是 `/token/<CA>` 就 `return false`，整页不扫。
    但用户的多窗格布局是「左列战壕 + 中间 K 线详情 + 右边又几列战壕」，
    于是一条都不回传（菜单显示 `DOM 扫描 0 轮 / 卡片 0 张`）。
  - 修法：**先看 DOM，再看 URL**。屏上有 ≥3 张 `[data-trenches-token-address]` 卡片
    就认为战壕在屏上，不管 URL 是 `/token/` 还是 `/trenches`。
  - 详情页 URL 下额外两道保险：不许退回 `data-recycler-key` / `data-row-key`
    （那是持仓面板，一卡一个不同 CA），且卡片必须同时带 `data-trenches-token-chain`。
  - 可见地址缓存 TTL 30 秒 → **15 分钟**：做单时在详情页停留久，切过去不该立刻断。
  - 菜单多一行「战壕可见地址缓存：N 个」。
- **1.10.3：只回传「你页面上真的显示了的币」**（用户 2026-11-26：
  「战壕里都没有出现这个币，怎么又弹窗」→ 补一句「我页面是被过滤过的，根本没有这两个币」）。
  - 根因：GMGN 战壕的「目标设置 / P1 P2 P3 / 平台白名单」是**前端过滤**，
    `/v1/trenches` 响应里**仍然带着被筛掉的币**。1.10.1 起把整个响应当数据源，
    于是弹出了用户页面上根本没有的币（实测 `NBIANCE` / `BIANCEBIANCE`）。
  - 修法：**DOM 决定「有哪些币」，嗅探只补字段**。`scanTrenchDom()` 把每张渲染过的
    卡片记进 `trenchDomVisibleAt`（30 秒 TTL）；`reportTrenchRows()` 里凡是不在这张表里的
    地址一律丢弃。页面上没渲染过 = 用户看不见 = 一个字都不传。
  - 菜单多一行「页面外过滤：N 行」，能直观看到接口里有多少条被你自己的筛选挡掉。
- **1.10.2：一识别到就上报（不再等定时扫）+ 断流修复**（用户 2026-11-25：
  「要一识别命中到就弹出提示」）。
  - `MutationObserver` 盯着战壕列表：新卡片一插进 DOM 就扫（800ms 硬间隔防抖，
    页面每秒改价不会把主线程吃掉）。原来只靠 5 秒定时扫，最坏白等 5 秒。
  - **60 秒心跳**：内容没变也每 60 秒重发一次。原来「内容没变不发」，行情安静三分钟
    后端就判 `stale`，整条链路等于瞎了（实测 `batches` 停在 5、新一轮新币全没进来）。
  - 菜单状态多一行「最后一批：bsc 12 秒前 / solana 3 秒前」，断流一眼看出来。
- **1.10.1：回传补上 DOM 兜底 + 三段全收**（用户 2026-11-25：「没有第一时间」，
  并贴了 BSC `格罗克 / Grok` 的合约）。
  - **根因一（一次都没回传）**：1.10.0 只 hook `fetch` / `XHR`，上线后
    `/api/new-token-feed` 的 `batches` 一直是 **0**。脚本可能装得比页面发请求晚，
    也可能 GMGN 的战壕数据根本不走这两个通道。新增 `scanTrenchDom()`：5 秒一轮直接读
    已在渲染的卡片（`data-trenches-token-address` / `data-recycler-key`），**完全不依赖
    任何请求**。详情页（`/token/<CA>`）明确跳过 —— 那里的持仓面板一卡一个不同 CA。
  - **根因二（只盯一列）**：1.10.0 只取 `new_creation` 段，而用户截图那只 `grok` 在
    **「已开盘」**列（Pooled 28.84 SOL、age 1m），压根不在那一列里。现在
    `new_creation` / `near_completion` / `completed` **三段全收**，每行带 `segment`。
  - DOM 拿不到时间戳，只递卡片上的 `age_text`（`42s` / `1m` / `2h` / `1分钟`），由后端
    `age_seconds_from_text()` 反推 —— 锚在结尾匹配，`28.84 SOL` 不会被读成 84 秒。
  - 年龄徽标读不到时，只有**这一轮才冒出来的卡**按「刚出现」算（`age_guess: first-seen`）；
    首轮扫描只建基线不判定，见过的地址存 GM 存储 `newTokenSeen`（6 小时过期），
    所以刷新页面不会把满屏老币炸一遍。
  - 两条路共用 `postFeedRows()`（节流 1.5 秒 + 地址签名去重），同一批不会发两次。
  - 菜单「查看『战壕新币回传』状态」多了 DOM 扫描轮数与卡片数。
- **1.10.0：战壕「新创建」回传（给本地弹窗用）**（用户 2026-11-25：「做一个弹窗提示，如果
  bsc，sol 和 robinhood 上出现了命中关键词的新币，第一时间提示」「能用我页面的数据吗，通过
  油猴插件回传给项目」「不然币太多了」）。
  - 脚本 hook 页面的 `fetch` / `XHR`，嗅探 GMGN 自己的 `/v1/trenches` 响应，把
    `new_creation` 段（只挑 20 个字段，约 15KB）`POST` 到 `http://127.0.0.1:8765/api/new-token-feed`。
  - 为什么用页面数据：页面请求本来就是按你页面的筛选条件（目标设置 / P1 P2 P3 / 平台白名单）
    过滤过的，币比后端全量拉少一个数量级；且零 GMGN 配额、零额外延迟。
  - 节流：同链 2 秒内只发一次，内容没变不重复发；整条链路是**旁路**，失败/超时一律静默，
    不影响叙事替换。
  - 菜单新增「切换『战壕新币回传』」「查看『战壕新币回传』状态」。存 `newTokenFeedOn`。
- **1.8.0：自定义关键词面板（悬浮/可拖拽）+ 新币预取修复**（用户 2026-11-19：
  「能不能再做一个自定义关键词的填写框，然后用一个悬浮可拖拽的开关去控制，可以填写要捕获的
  叙事关键词，填了之后会被保存，也可以通过删除键去删除」「被捕获的关键词也是用蓝色标记」；
  以及「新币的识别时间太久了，这都半小时了才被标记」）。
  改动一（面板）：
  - 页面右下角多一个 `叙事关键词` 面板：标题栏可拖拽（位置记忆）、`–` 折叠、
    右上角开关只管自定义词（关掉后内置词照常）、菜单可整面板隐藏/清空。
  - 输入框回车添加（支持逗号/空格分隔多个词），存 `customKw`（GM 存储，重启不丢）；
    输入框空着按退格/Delete 删最后一个，chip 聚焦后按 Delete 删该词，点 `×` 也能删。
  - 自定义词命中走**蓝色**标记（与内置强词同权）；词本身属于 AI 组时才变白。
    面板自己也是 fixed 浮层，已在 `findFloatingRoots` / `caFromPointer` 里显式排除，
    不会被当成叙事浮层改写。
  改动二（新币延迟）：
  - **根因**：预取目标只枚举 `a[href*="/token/"]`，而战壕/新币格里 0~2 个 token 链接、
    币名是纯文本 —— 整片格子等不到第一次请求，只能等 hover（`--data-only --old` 复现：
    batch 里根本不带这张卡）。
  - 修复：`viewportTargets()` 补上 `data-trenches-token-address` / `data-row-key` /
    `data-recycler-key` 属性枚举，属性节点就是卡片本身，不会串卡。
  - 新币 10 分钟内查不到正文时，重探间隔 2 分钟 → **45 秒**；`no-narrative` 不再算
    「撞墙」冷却（原来整批 0 正文会把 12 个币全冷 2 分钟）。
  - 新增 `_diag?ev=narr` 上报「从卡片出现到拿到正文」的 p50/max 延迟，慢不慢有数可看。
  验证：`tools/gigax_hotmark_check.py --custom`（内置词 0 命中，标蓝只能来自自定义词
  「咯咯学院」）、`--custom-off`（开关关掉不标）、`--data-only`（无 token 链接的卡也进
  batch）、`--data-only --old`（复现「一个请求都不发」）；原有 6 个场景全绿。
- **1.7.9：AI 词补全 + 英文大小写不敏感 + AI 命中改用白色标记**（用户 2026-11-19：
  「0xe5881c31162488d335aab82a20cd4bf156487777，这个币里有 agent id 怎么没有被标记」、
  「关键词再加一个『代理』『agent』」「还有AI」「和AI相关的关键词，用白色标记」）。
  那条叙事（AGENT / Agent City，271 字）里明明写着「必须持有灵魂绑定的『币安 Agent ID』
  （.ai 身份，不可转让）」「AI 智能体代为执行链上签名」，却**一个词都没命中**：
  ① 词表里只有 `agentic`，没有 agent/agent id/智能体；② 英文匹配是**大小写敏感**的，
  `Agent ID`、`Launchpad`、`Agentic` 这种首字母大写永远匹配不到。
  改动：
  - 词表新增 `agent id`、`ai agent`、`ai 身份`、`智能体`、`灵魂绑定`、`soulbound`、
    `代理`、`agent`、`ai`（共 32 强词 + 2 弱词）。
  - 匹配改为**小写归一**（英文不区分大小写）。
  - 纯 ASCII 的 1~2 字母词（`ai`）走**整词边界**正则：`chairman`/`claim`/`main` 里都含 `ai`，
    直接 `includes` 会把它们全算命中（那条 AGENT 叙事里正好有 `CHAIRMAN Z`，实测会误伤）。
  - **AI 相关词命中 → 卡片白色标记**（`gigax-ai-card`）：`rgba(255,255,255,.12)` 极淡填充 +
    `2px` 纯白内描边 + 白色内发光；其余词仍是 DeepSeek 蓝。同时命中两类时以 AI 为准。
    白色不做深填充（会把浅色文字洗掉），只做「亮白一圈」。
  验证：真实叙事命中 `agent id / ai 身份 / 智能体 / 灵魂绑定 / agent / ai` → 卡片
  `aiMarked=true, blueMarked=false`；非 AI 命中（补位 `首个`）→ 蓝，`aiMarked=false`。
- **1.7.8：持仓/仓位面板不再标蓝**（用户 2026-11-19：「这个持仓位置的不用标记」）。
  「我的BSC链持仓」那张表（币种/当前买入/当前卖出/余额/未实现）也是 `/token/` 链接，
  所以照样被 `cardFromAnchor` 认成卡片标了蓝 —— 但那是「**你自己买了什么**」，
  不是「市场有什么机会」，蓝底还把盈亏数字压得更难读。
  判据只看结构：`inHoldingsPanel()` ——
  ① 有 `<table>` 祖先就查**这张表**的文本里有没有持仓专有列名
  （`当前买入/当前卖出/未实现/我的BSC链持仓`），有就不染；
  ② 万一是不带表格的 div 列表，只在「高度 ≤320 且宽 ≤90% 视口」的小祖先里找，
  爬到面板/整页尺度立刻放弃（防止把行情榜一起豁免）。
  连带修：`cardCache` 里存的如果是持仓行，就**不复用**也不缓存，
  否则同一个币在榜上的那张正经卡片会被持仓行顶替、永远标不上。
  回归 `tools/gigax_hotmark_check.py --holdings`（持仓表在前 + 只在持仓里出现的币）：
  旧语义 `TR#hd-row` 被标 `holdingsMarked=true`，1.7.8 `false` 而行情卡照标。
- **1.7.7：关键词判定简化为「任意一个词命中就标」＋标记重新默认开启**（用户 2026-11-19：
  「新和第一要的，弱词单个也行」）。
  以前是「强词 1 个 **或** 弱词 2 个（`新` + `第一` 同时出现）」，弱词这条实际上从没生效过。
  现在强词弱词同权，命中即标；`严格/宽松` 两档因此完全等价，菜单里那项已删掉。
  标记开关重新默认 **开启**（1.7.6 曾按「干脆别做标记了」默认关，这次用 `hotMarkOnMigrated`
  一次性写回 `on`，之后菜单开关照常生效）。
  **代价**：GIGA X 文案里「新」几乎无处不在，单个「新」就标 → 一屏会蓝掉一大片。
  当前词表：强词 `新模式/新机制/新玩法/新叙事/新范式/新物种/第一个/首个/首家/首例/首款/首创/
  首发/首次/地推/平台币/平台原生/发射台/发射平台/launchpad/launch pad/bonding curve/agentic`，
  弱词 `新 / 第一`（英文区分大小写，`Launchpad` 匹配不到）。
- **1.7.6：机会标记（蓝底卡片）默认关闭 + 收掉最后一条误标路径**（用户 2026-11-19：
  「0xe0bcf5d80435781aae7f6dcde202bb1ccb9c7d89，这个位置没有命中却被标记了，
  …干脆别做标记了」）。
  先说清楚那个位置发生了什么：这个地址在 **bsc / base / eth / robinhood / elysium 全链
  都是 `no-narrative`**，GIGA X 根本没写这个币 —— 浮层显示的是 GMGN 原生文案，
  脚本按设计一个字都没改（没有 `【GIGA X 叙事】` 徽标就是这个原因，不是替换失败）。
  而卡片还是蓝的，因为 1.7.5 只挡住「拿到正文但不命中」，**「明确没有正文」仍然走补位**，
  被历史结论捡回来了。
  改动：① `hotMark` **默认 `off`**，并对老安装做一次性强制关闭（`hotMarkOffMigrated`）——
  只改默认值救不了已经存了 `strict` 的机器；菜单里可以再打开。② 补位判据细分：
  `no-narrative`（**桥接明确说没有**）视为已知答案，不许补位；只有断网/超时/还没请求
  这种「不知道」才按历史结论兜。
  回归探针 `tools/gigax_hotmark_check.py` 五场景：默认关闭不标 ✓ / 旧语义误标（复现）✓ /
  真实不命中不标 ✓ / 断网兜底仍标 ✓ / 明确无叙事不标 ✓。
- **1.7.5：修「这段叙事根本没命中关键词，卡片却被标蓝了」**（用户 2026-11-19，
  `0x70c35410b062cafb251a4826e451a99859057777` 慈善CZ）。
  桥接返回的正文 110 字，用脚本同一套词表跑：**强词 0 命中、弱词 0 命中**。
  根因在 `paintHotCards()` 的**补位**：

  ```js
  for (const [key, entry] of cache) {        // 当前正文
    const hits = matchHot(entry.text);
    if (!hits.length) continue;              // ← 不命中就 continue，这个币没进 marks
    marks.set(ca, hits);
  }
  for (const ca of Object.keys(hotIndex)) {  // 上次标过的（24h）
    if (marks.has(ca)) continue;             // ← 于是这里把它又标回去了
    marks.set(ca, v.h.split('、'));
  }
  ```

  GIGA X 会**重写叙事**：上一版正文命中过（例如 `首个`/`首发`），结论被 `hotIndex` 记住 24 小时，
  于是当前正文完全不命中、卡片还是蓝的。
  修法：新增 `known` 集合（这一轮正文已经拿到手的币，含「拿到了但不命中」），补位条件改成
  `marks.has(ca) || known.has(ca)` 都不许补；并且当前正文不命中时 `dropHot(ca)` **立刻删掉**
  那条历史结论。正文取不到（`no-narrative`/断网）时补位照旧生效，不会因为这条修复丢掉标记。
  附带：新增 `paint` 诊断上报（哪一屏标了谁、凭哪个词、`[旧]` 表示来自历史结论），
  卡片悬停 title 也照旧写着命中词。
  回归探针：`tools/gigax_hotmark_check.py` —— 预置一条假命中 + 真实不命中的正文，
  `--old` 必须复现误标，现版本必须不标；`--fallback` 验证「取不到正文时仍按历史结论标」。
  实测：旧语义 `marked=true / title=GIGA X 叙事命中：首个 / paint=0x70c354=首个[旧]`，
  1.7.5 `marked=false`。
- **1.7.4：修「浮窗会出现两个版本，先是大字、过一会儿变成很小的字」＋字号可调**。
  这不是样式问题，是**一秒级的判定死循环**。桥接访问日志里的 `_diag?ev=` 埋点看得很清楚：

  ```
  ev=render shown=4oduvmdiWG… hov=4oduvmdiWG/hover  ptr=m4ckeq8s8j
  ev=stale  shown=4oduvmdiWG… hov=4oduvmdiWG/hover  ptr=m4ckeq8s8j
  ev=render …  ev=stale …        （每 1~6 秒一轮，16 秒抖了 4 次）
  ```

  `hov` 说「浮层讲的就是它」，`ptr` 却指向另一枚币 —— 因为**浮层盖住了指针**：
  `caFromPointer()` 会把浮层跳过再往下面穿透，穿透到的永远是「浮层背后碰巧挡着的
  那张卡片/列表行」。`staleTarget()` 的 C 判据拿它当「换币」，于是每一轮
  `resetRoot()` 把 GMGN 原生文案还原（原生 ~11px = 用户说的「字体很小的版本」），
  下一轮又从缓存重绘（15px）—— 用户看到的就是「两个版本在一闪一闪」。
  改动三处：① `caFromPointer()` 在「指针最上面那层就是浮层」时直接返回 `null`
  （穿透答案此刻毫无意义）；② `staleTarget()` 里 `hoverTarget()` 的**纯几何**判据
  （`why='hover'`）说同币时，在 C 判据之前就 `return false`；③ 正文包一层
  `.gigax-narrative-body` 并显式给字号（默认 **15px**，行高 1.62），容器被 React 冲掉
  会就地按缓存重排，不会掉回原生小字；菜单可循环 **13 → 15 → 17px**。
  另外 `renderNarrative()` 变成幂等（同币同文不重写 innerHTML），不再「闪一下」。
  回归探针：`tools/gigax_flicker_check.py`（假浮层 + 真桥接，注入真实 userscript 跑）
  —— `--old` 打回 1.7.3 语义必须复现 `stale>0`，现版本必须 `stale=0 且替换后只有一版`。
  实测对照：旧语义 16 秒 `stale=4/render=5`，1.7.4 `stale=0/render=1`。
- **1.7.3：修「有时候不显示 GIGA X 叙事」**（浮层显示原生文案，没有 `【GIGA X 叙事】` 徽标）。
  根因是**浮层识别门槛**，跟取址无关：深扫要求「叶子节点文本 ≥30 字」才当浮层看，
  而 GMGN 这个浮层里唯一带锚点词的那行是页脚
  `免责声明：人工智能生成内容，投资前请自行研究。`——实测**只有 23 字**，
  上面那段正文（AI 生成的币种介绍）又不含任何锚点词；加上组件没挂
  `tooltip/popover/popper` 之类类名，两条识别路径同时落空 → 脚本一次都没碰过这个浮层。
  改动：① 深扫下限 30 → **10 字**；② 补一道**尺寸闸**（`isPopupSized`：不超过视口 95%×90%），
  防止把 fixed 的 app shell 当浮层改写正文；③ 新增 `nodetect` 上报——页面上明明有锚点文字
  却没认出浮层时，把它的层级链（tag/class/position/尺寸）送到桥接访问日志，下次不用猜。
  真机验证：注入一个同构假浮层（正文无锚点 + 23 字免责声明），**旧逻辑认不出、新逻辑认得出**；
  页面主体里的静态免责声明仍不被误判。
  附带：修徽标里的 symbol 乱码（`DCTY28829(18/11)10h $7.49万-4.45%` → 只取开头币名）。
- **1.7.2：详情页规则收紧到「只有指针真的落在 data 卡片上才认别的币」**（用户实测
  「第一次 hover 对、第二次变成 COST」，而 hotdog 正好是 **COST 池子里的币**）。
  真机上模拟出的四条路径（`https://gmgn.ai/sol/token/Euj4…KbX6`）：

  | 场景 | 1.7.1 的结果 | 1.7.2 的结果 |
  | --- | --- | --- |
  | 指针在 K 线图表上（TradingView iframe） | **`CZEB3WNZuF2Y…` 另一个币** | 页面币 ✓ |
  | 头部有「配对币」`/token/` 链接 | 视祖先容器而定，可能取到配对币 | 页面币 ✓ |
  | 浮层盖住左侧列表行 | `9hjB…`（列表行的币） | 页面币 ✓ |
  | 指针直接落在左侧列表行 | `9hjB…` | `9hjB…`（该行，正确） |

  根因：详情页上「页面自己的币」**没有任何 data 属性、也没有指向自己的链接**，而
  `caFromElement` 的「卡片祖先」兜底会从图表/浮层里爬到一个带别的币链接的容器；
  再加上 hover 锚点、GMGN 为配对币发的叙事预取、上一轮的 bind，四条路都指向 COST。
  现在 `pointerOnDataCard()` 是详情页唯一放行条件（浮层挡在最上面也算「没落在卡片上」），
  `staleTarget` 同步：详情页显示的不是页面币、指针又不在 data 卡片上 → 立刻整块重来。
- **1.7.1：修「代币详情页弹出别的币」**（hotdog 页显示 COST）。
  实测 `https://gmgn.ai/sol/token/Euj4…KbX6`（hotdog）：
  ① 页面自己的币在 DOM 里**没有任何 data 属性、也没有指向自己的 token 链接**，
  带 `data-row-key` 的只有**左侧列表里的别的币**；
  ② **K 线是 TradingView 的 `iframe`**，指针落在图表上时 `elementsFromPoint` 只拿到 `IFRAME`，
  穿不进任何卡片 —— 于是「hover 锚点」成了唯一还活着的判据，而它是用户**从旁边列表划过去**
  时留下的（`noteHover` 老写法在取不到卡片时直接 `return`，锚点永不作废）。
  三处改动：① `noteHover` 记 `hoverMissAt`，指针落到非卡片区域即作废 `hoverRec`；
  ② `resolveTarget` 新增 **0a：详情页取页面地址**；③ `staleTarget` 里「详情页显示的就是
  页面这个币」永不算过期。
- **1.7.0：改用 GMGN 自己的 data 属性取址 —— 这是「币和叙事对不上」这个老问题的终结版。**
  前面十几版一直在**猜** DOM（看截图、看日志、看接口返回），这一版第一次用
  `chrome-devtools-mcp` 把真实战壕页加载起来读了 DOM，结论是：
  ① 战壕卡片里代币链接是 **0/1/2 个**，币名是**纯文本**不是链接 —— 靠链接找币在战壕区天然失败；
  ② GMGN 其实把地址写在属性里：`data-trenches-token-address` + `data-trenches-token-chain`
  （一卡一个，30/30 唯一）、列表行 `data-row-key="bsc:0x…"`；
  ③ 同名不同币真实存在（`PENGB`×2、`NVIDIASI`×2 各自不同 CA），所以「靠 symbol 认币」不可靠。
  于是取址优先级新增**第 0 位 `data`**，标色也直接标在属性所在节点（= 卡片本体，326×144 / 260×44）。
  另加**回收键观察**：虚拟列表把同一个节点复用给另一个币时（`data-recycler-key` 变），立刻重刷标色。
  实测：战壕区 60/60 采样点全中、**0 认错**；左侧 34/44 命中、0 认错（未命中的点全被喊单浮窗盖住）。
- 1.6.0：**修「红薯小猫弹出 PIKA」**（换币识别）与**「标记一会又没了」**（重刷请求被丢）。
  ① 新增**浮层身份识别**（`why: identity`）：浮层头部（名字 / @handle，剔除我们写入的节点、
  剥掉所有数字）与页面上各张卡片的文字比**公共前缀**，前缀最长且唯一的那张卡片就是它讲的币。
  这是「GMGN 自己写的这个浮层是谁」，比 hover 和网络嗅探都可靠，排在取址优先级第 2 位；
  同时用同一指纹做换币检测（`staleTarget`）—— 浮层头部一变就整块重来。
  ② `schedulePaint` 不再丢弃重刷请求：老写法「800ms 内再来一次就 return」会让那次重刷永远丢失，
  而 React 每次价格跳动都会换卡片节点、class 全没 —— 表现就是「刚刚还标着，一会又没了」。
  现在合并请求 + 400ms 地板 + 到点**强制执行**，并加了 3 秒保底重刷。
  ③ 版本号写进 `SCRIPT_VERSION`，诊断面板标题和叙事徽标 `title` 都会显示，便于确认装的是哪一版。
- 1.5.9：**修「不同币弹出同一段叙事」与「战壕卡片不标色」**。
  ① 浮层容器是复用的 —— GMGN 换币时只改标题数字，**不重写那段正文**，我们塞进去的上一段
  叙事会一直留着（IDRENT、红薯小猫都弹 PIKA）。现在每轮都用 `staleTarget()` 校验
  「浮层现在讲的还是不是当初那个币」，换了就整块重来；
  ② 卡片里允许出现 **2 个**代币链接（`CARD_MAX_CAS`）—— 战壕卡片自身常带配对/相关币，
  老规则「出现第二个就停」在第一层就放弃，于是右边战壕一个都不标、左边列表却正常；
  ③ `isPainted` 认「透明底 + 有边框」的盒子，战壕卡片不会再只亮内层一小块；
  ④ 新增 `Alt+Shift+G` 页面内诊断面板（每个币：有没有取到叙事 / 命不命中关键词 /
  卡片找没找到、多大、有没有背景），出问题截图即可定位。
- 1.5.8：**修「代币和叙事对不上」**（卡片 Pussy / 叙事 hotdog）。三处根因一起改：
  ① 优先级重排为「浮窗自带地址 > hover 到的卡片 > 指针 > 浮窗多地址交叉 > 详情页 URL >
  叙事请求（必须能在页面上找到这个币）」，**详情页 URL 不再压过 hover 的卡片**；
  ② 新增 hover 锚点（hover 当时记下卡片 CA + 矩形 + symbol，事后用「指针还在卡片里 /
  浮窗文字含这个 symbol / 浮窗紧贴卡片」三判据认领），解决浮窗盖住鼠标后锚点丢失；
  ③ 反查卡片时**不许越过卡片边界**（出现第二个代币链接就停），并删掉「最近 8s 任意请求」兜底。
  另加并发防护：响应回来时浮窗已换币则丢弃该正文。`GIGA X 叙事` 徽标悬停可看地址与来源。
- 1.5.5~1.5.7：修「叙事和币对不上」的早期版本（详情页 URL 优先、网络嗅探 TTL 120s→30s）、
  降低主线程抢占（锚点索引 / 缓存 / idle 回调 / 预取与扫描解耦）
- 1.5.5：修「叙事和币对不上」（hotdog 详情页浮窗显示 COST 叙事）—— resolveTarget 改为
  详情页强制用页面 URL 的币、浮窗自带唯一地址优先，网络嗅探 TTL 120s→30s
- 1.5.4：修「列表整列染蓝」的误判（第二个币只按代币链接判、不再误杀钱包地址），
  详情页/当前正在看的币也标；标记持久化 TTL 2h→24h；大幅降低对主线程的抢占
  （深扫 1s→3s 且只扫叶子、轮询 800ms→1.5s、paint 防抖 400→800ms），修 K 线图卡顿
- 1.5.3：修「列表整列染蓝」—— 卡片定位加「第二个币就停 + 尺寸上限」两道硬边界
- 1.5.2：标色换成 DeepSeek logo 那个蓝 `#4D6BFE`（比 sky-400 深、比宝蓝亮），
  战壕榜深色卡片上 30% 就够醒目；色值抽成 `HOT_RGB`/`HOT_RING` 两个常量，以后调色只改一行
- 1.5.1：底色改亮蓝 sky-400 + 2px 实心描边（12%/22% 在深色卡片上看不见）、
  卡片定位改为优先取「有背景」的祖先（战壕榜卡片原来刷在透明内层上）
- 1.5.0：批量接口 `/api/gigax/narratives`（一屏一条请求、服务端并发、30 分钟进程缓存）、
  自动预取当前屏（不用 hover）、标记结果持久化、详情页页头不再标、底色默认 22%
- 1.4.0：叙事关键词命中时把对应卡片刷浅蓝底（机会标记），菜单可关/可放宽
- 1.3.0：GMGN 地址页形状（`/<chain>/token/<CA>`）解析、Solana 地址不再小写化、
  hover 预热 + DOM 观察消除「先原生后替换」的闪烁、GIGA X 无叙事时保留原文
- 适配时间：2026-11（GIGA X 1.0.4 / `@privy-io/expo` 0.74.x）
