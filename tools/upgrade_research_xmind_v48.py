"""Create the V4.8 research-system XMind without overwriting the V4.7 source."""
from __future__ import annotations

import argparse
import json
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any


NAMESPACE = uuid.UUID("f60f74dd-f55e-4dad-b534-c351f2e2d151")
PATCH_TITLE = "V4.8 置顶补丁｜人物/官方前置雷达 + 市场主线 + 三速研究调度"
SHEET_TITLE = "V4.8｜人物动态前置雷达 + 市场主线 + 三速投研调度"


def stable_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def topic(title: str, *children: dict[str, Any], note: str = "", key: str = "") -> dict[str, Any]:
    identity = key or title
    result: dict[str, Any] = {
        "id": stable_id(f"topic:{identity}"),
        "class": "topic",
        "title": title,
    }
    if note:
        result["notes"] = {"plain": {"content": note}}
    if children:
        result["children"] = {"attached": list(children)}
    return result


def v48_patch() -> dict[str, Any]:
    return topic(
        PATCH_TITLE,
        topic("核心修复｜新闻通常晚于人物原始动作；战壕新币入档后，必须反向检查高影响人物与官方账号的最新原帖、引用、转发和可核验关注。"),
        topic("人物源治理｜个人只保留有能力引发币价、板块或全市场剧烈波动者；项目、交易所、公链、钱包等官方账号保留；显式剔除名单优先。"),
        topic("语义先于字符串｜名称、Ticker、CA、官方X只负责召回候选；每一条候选关系都交给快速AI判断是否真的指向该加密资产，关键词不能直接确认或直接否定。"),
        topic("归因边界｜作者正文、引用卡片、转发对象和评论区分开；回复/评论不算人物本人点名，被引用者的话不能冒充引用者本人观点。"),
        topic("身份边界｜精确CA > 币官方X/可核验关注 > Cashtag > 名称语义；同名多CA必须标歧义，不因人物提到一个词就给任一合约背书。"),
        topic("研究边界｜人物或官方动作是 TR_PRIMARY_EVENT / 注意力跃迁证据，只提高研究优先级；不自动等于 First Official、当前龙头、可执行或买入。"),
        topic("市场主线｜每轮扫链同时识别当下1–3条资金与注意力主线，判断候选是主线龙头、主线成员、分支扩散、补涨、独立催化、逆主线还是蹭热点。"),
        topic("三速并存｜快速实时=JEV主判；实时深研=Codex完整V4.8；每小时深研=默认汇总上一封闭小时增量并做完整V4.8精选。"),
        topic("提醒治理｜历史原帖只补研究不补弹窗；同一人物同一批最多一条；人物级冷却、帖子+资产持久去重；歧义或AI不确定时静默。"),
        topic("完整规则见独立 Sheet「V4.8｜人物动态前置雷达 + 市场主线 + 三速投研调度」。"),
        note="2026-09-23 战壕新币人物动态追踪、语义误报与弹窗过载复盘补丁。",
        key="v48-pinned-patch",
    )


def v48_sheet() -> dict[str, Any]:
    root = topic(
        "V4.8｜战壕新币人物/官方动作前置雷达：先于新闻发现，AI语义确证，分层研究与克制提醒",
        topic(
            "00｜目标与边界",
            topic("目标｜捕捉可能先于媒体新闻出现的原始动作：重要人物点名/引用/转发/关注，以及官方账号对新Meme、新机制或新资产的明确提及。"),
            topic("输入｜以GMGN六链战壕新币和持续跟踪资产为候选池；人物动作不能凭空生成一个未经定位的可交易CA。"),
            topic("输出｜人物催化账 + 原帖证据 + 语义指向 + 身份映射 + 市场影响 + 下一验证条件；与叙事、龙头、执行、风险账分开。"),
            topic("禁止｜不得把评论区、他人引用内容、同名普通词、工具名、公司名或泛行业讨论伪装成对某币的点名。"),
        ),
        topic(
            "01｜人物源与官方源治理",
            topic("P0个人源｜只保留其本人原始表态足以显著影响单币、板块、生态或全市场波动的人。"),
            topic("官方源｜项目方、交易所、公链、钱包、Launchpad及关键基础设施官方账号保留；官方提到一个Meme本身可以成为分发催化。"),
            topic("角色分层｜crypto founder / market mover / technology leader / political figure / project official；层级只影响轮询优先级，不替代语义判断。"),
            topic("显式剔除｜用户指定移除的人物必须从默认源、保存源和X追踪投影同时排除；历史缓存不得让其重新进入提醒。"),
            topic("动态维护｜按真实误报、漏报和市场影响复盘升降级；不是名人百科，也不追求数量最大。"),
        ),
        topic(
            "02｜允许的动作与归因",
            topic("Own Post / 点名｜只读取作者自己的正文；正文明确提及资产、CA、Cashtag、官方账号或可被AI确认为该币的独特名称。"),
            topic("Quote / 引用｜分别保存作者评论与被引用原文；AI必须判断作者是否在谈该资产，不能把引用卡片内容直接算作作者观点。"),
            topic("Repost / 转发｜仅记录可核验的原转发对象；普通RSS扩展或二次拼接文本不得伪造成转发。"),
            topic("Follow / 新关注｜只有数据源明确提供关注动作且目标正好是候选币官方X时成立；无法核验的关注截图不成立。"),
            topic("Reply / Comment｜回复、评论区和他人留言默认不进入人物信号；除非未来建立可证明的作者本人动作类型，仍需独立规则。"),
        ),
        topic(
            "03｜候选召回与AI语义确证",
            topic("召回层｜CA、官方X、status链接、Cashtag、Ticker、币名及语义近似只用于构造“推文×具体链×具体合约”候选对。"),
            topic("无关键词裁决｜关键词黑白名单不得直接通过或淘汰；普通词也可送AI，但AI不确定即拒绝成为人物信号。"),
            topic("快速模型｜JEV为主判；快速分流只决定研究优先级，不覆盖完整投研结论。"),
            topic("模型问题｜这条原始动作是否在语义上涉及这个具体加密货币，而不是同名人物、工具、公司、地点、普通动词或泛行业话题？"),
            topic("结构化输出｜related / unrelated / uncertain；confidence；语义对象；作者立场；动作类型；依据句；歧义点；模型与版本。"),
            topic("失败关闭｜模型不可用、超时、低置信度或无法读取原文时只保留候选审计，不进入人物信号、弹窗或播报。"),
        ),
        topic(
            "04｜身份映射与同名防错",
            topic("I0 Exact Contract｜原帖明确给出同链同CA，身份强，但仍不代表人物为其官方背书。"),
            topic("I1 Official X Edge｜人物明确@、引用、转发或关注候选币的官方X；官方X本身必须来自GMGN/官网等现有证据。"),
            topic("I2 Cashtag｜$SYMBOL且AI确认指向加密资产；同Ticker多CA时保持歧义。"),
            topic("I3 Name Semantic｜独特名称经AI确认，但CA只来自战壕映射，必须标“币名命中、具体CA待复核”。"),
            topic("多CA竞争｜同一语义命中多个合约时全部保留在候选集，按流动性/成交/买家/官方边筛选研究优先级，禁止直接弹人物点名。"),
        ),
        topic(
            "05｜人物催化账 Person Catalyst Ledger",
            topic("source｜人物/官方名称、handle、角色、类别、watch tier、来源提供商。"),
            topic("action｜mention / quote / repost / follow；作者正文、引用正文和原帖URL分开保存。"),
            topic("semantic｜AI版本、related状态、置信度、语义理由、对象边界。"),
            topic("identity｜链、CA、匹配类型、身份状态、是否多CA歧义。"),
            topic("timing｜published_at / observed_at / first_matched_at / age；历史补采与实时发现分开。"),
            topic("impact｜来源影响力、动作强度、身份强度、当前注意力阶段、链上买盘响应；人物热度与资产承接不得合成一个总分。"),
            topic("conclusion｜none / watch / confirmed / ambiguous / rejected；marketImpact、nextTrigger、invalidation。"),
        ),
        topic(
            "06｜接入V4.7主体系",
            topic("注意力｜可作为TR_PRIMARY_EVENT推动A1-A4；已有CA时仍需链上承接才能进入A6，人物动作本身不能直接生成A7龙头。"),
            topic("Meta家族｜人物提到的是母题、项目、符号还是具体币必须拆开；提到母题时启动Family扫描，不把第一个同名币默认写成龙头。"),
            topic("Thesis Memory｜记录人物动作为什么可能改变传播/分发、未来需兑现什么、什么会证明只是一次性噪声。"),
            topic("四条生命线｜人物动作主要影响Narrative；只有官方产品/集成事实才可能提升Project，Token与Liquidity仍需独立证据。"),
            topic("复燃｜旧币被人物重新点火时新建事件版本；至少再有一条产品、Token结构或资金证据才升级基本面复燃。"),
            topic("执行｜人物信号永远不能覆盖不可卖、恶意授权、流动性不足或身份伪装等执行风险。"),
        ),
        topic(
            "07｜市场主线雷达 Market Mainline Ledger",
            topic("先判市场再判单币｜每轮先回答当前市场正在交易什么，再判断候选与主线的关系；禁止从候选币自己的文案反推它就是主线。"),
            topic("主线证据｜至少综合板块/多资产相对强弱、成交与流动性迁移、链上资金、独立新闻/产品催化、跨平台注意力；单一涨幅榜或单个热词不足。"),
            topic("主线输出｜primaryThemes（1–3条）、phase（emerging/accelerating/consensus/crowded/rotating/fading）、leaders、capitalAttention、evidence、as_of。"),
            topic("候选关系｜core-leader / core-member / branch-expansion / catch-up / independent-catalyst / counter-trend / theme-rub / uncertain。"),
            topic("主线≠白名单｜不属于主线的独立强催化仍可成为好标的；属于主线也不能绕过身份、龙头、买盘与执行风险。"),
            topic("拥挤与轮动｜主线进入crowded/rotating时，区分真龙续强、低位补涨、资金迁移与末端蹭热；写明下一轮动触发与失效条件。"),
            topic("数据不足｜明确status=uncertain，不用大盘涨跌或泛称AI/Meme/DeFi填空；继续保留候选，不因主线未知直接淘汰。"),
        ),
        topic(
            "08｜三种研究模式同时存在",
            topic("快速实时｜每个新币先由JEV主判研究优先级；用于低延迟分流，不冒充完整投研结论。"),
            topic("Codex实时深研｜候选入队或证据变化后立即执行完整V4.8；适合主动盯盘阶段，资源消耗最高。"),
            topic("Codex每小时深研（默认）｜整点后领取上一封闭小时首次接收的战壕增量，持久游标、可重启续跑、普通结果静默。"),
            topic("并发｜每小时模式小批次并发，默认2路、最多5路；全局限流、重试退避和持久去重优先于吞吐。"),
            topic("事件优先级｜人物/官方原始动作立即完成快速语义确证并入研究证据；完整深研时机服从当前模式。"),
        ),
        topic(
            "09｜提醒与防打扰",
            topic("历史不补弹｜服务启动前的旧帖子、缓存回放和历史补采只补研究记录，不补弹窗/语音。"),
            topic("去重｜person_handle + post_id + chain + asset_locator 持久去重；同一内容重抓不重新提醒。"),
            topic("限量｜同一人物单批最多一个最新高置信信号；人物级冷却；堆叠事件先入档再择优提醒。"),
            topic("即时人物信号门槛｜AI确认 + 高置信身份 + 非多CA歧义 + 新鲜窗口；否则只显示在页面与研究档案。"),
            topic("精选播报门槛｜完整V4.8仍只播大金狗/龙头潜力，人物动作不能绕过证据、机会、龙头、生存率和执行账。"),
        ),
        topic(
            "10｜限频与成本治理",
            topic("公开源串行轮转｜人物源扩容不能按账号数放大请求；每轮只读一个到期源，按重要度、相关性和公平性调度。"),
            topic("缓存｜推文×合约语义结果持久缓存；同一候选不重复请求模型。"),
            topic("退避｜来源失败指数退避并轮换公开提供商；不得为补数据突破GMGN/X/模型限制。"),
            topic("付费边界｜保持X官方付费API关闭；任何新增收费通道必须单独说明成本并取得用户确认。"),
        ),
        topic(
            "11｜Acceptance Tests",
            topic("普通词误配｜City Hall monitor ≠ MONITOR币；open-weights ≠ OPEN币；AI必须拒绝。"),
            topic("引用归因｜人物引用他人提到币，但本人正文只谈别的主题：不得冒充本人点名。"),
            topic("评论区隔离｜评论区别人发CA或币名：不得纳入被监控人物信号。"),
            topic("精确CA｜人物原帖明确给出同链CA：可建立confirmed催化账，但仍需独立龙头与执行判断。"),
            topic("官方提Meme｜项目/公链/交易所官方明确提及某Meme：保留并进入快速语义与研究。"),
            topic("关注事件｜只有可核验的follow记录且目标为候选官方X时成立。"),
            topic("同名多CA｜全部入候选集、标歧义、不开即时人物弹窗。"),
            topic("历史回放｜可以补研究，不得补弹窗或语音。"),
            topic("人物移除｜显式剔除后默认源、保存源、X追踪与提醒均不再出现。"),
            topic("模式一致｜快速、实时深研、每小时深研都使用同一V4.8事实账；只有时机和计算深度不同。"),
            topic("市场主线｜一批候选必须先给同一as_of下的市场主线，再分别判断关系；主线币与独立催化币都可入选，纯蹭热点不能因同词升级。"),
        ),
        note=(
            "V4.8：在V4.7 Meta扩散、生存率、Thesis Memory与四条生命线之上，"
            "加入先于新闻的人物/官方原始动作雷达与市场主线账，并把语义确证、归因、身份映射、"
            "主线关系、限频、防重复和三种研究模式写成统一规则。"
        ),
        key="v48-person-radar-root",
    )
    root["structureClass"] = "org.xmind.ui.logic.right"
    return {
        "id": stable_id("sheet:v48-person-radar"),
        "revisionId": stable_id("revision:v48-person-radar"),
        "class": "sheet",
        "rootTopic": root,
        "title": SHEET_TITLE,
        "topicOverlapping": "overlap",
        "extensions": [],
    }


def upgrade(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        sheets = json.loads(archive.read("content.json"))
        metadata = json.loads(archive.read("metadata.json"))
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]

    source_sheet_count = len(sheets)

    trench_sheet = next(
        sheet for sheet in sheets
        if str(sheet.get("title") or "").startswith("V4.7｜新资产扫链")
    )
    trench_sheet["title"] = str(trench_sheet.get("title") or "").replace("V4.7", "V4.8", 1)
    root = trench_sheet.get("rootTopic") or {}
    root["title"] = str(root.get("title") or "").replace("V4.7", "V4.8", 1)
    notes = ((root.get("notes") or {}).get("plain") or {}).get("content") or ""
    addition = (
        "V4.8：新增人物/官方原始动作前置雷达、AI语义确证、归因与身份映射、"
        "市场主线识别与候选关系、三种研究模式、限频去重和历史不补弹提醒治理。"
    )
    root["notes"] = {"plain": {"content": f"{notes.rstrip()}\n{addition}".strip()}}
    attached = ((root.setdefault("children", {})).setdefault("attached", []))
    detail_root = v48_sheet()["rootTopic"]
    attached[:] = [
        child for child in attached
        if child.get("title") not in {PATCH_TITLE, detail_root["title"]}
    ]
    attached.insert(0, v48_patch())
    attached.insert(1, detail_root)

    # Keep the workbook's native sheet topology.  Some desktop XMind builds
    # reject a hand-authored top-level sheet even when the JSON and ZIP CRCs
    # are valid.  Embedding the V4.8 detail tree in the existing, proven-good
    # trench sheet preserves every rule while remaining compatible with the
    # source workbook's own schema and theme references.
    sheets = [sheet for sheet in sheets if sheet.get("title") != SHEET_TITLE]
    # XMind 26 mutates creator.name while loading.  A string creator (for
    # example "OpenAI") therefore crashes the renderer with
    # "Cannot create property 'name' on string" even though the archive is a
    # valid ZIP.  Emit the same object shape as a workbook saved by the local
    # XMind/Vana build and drop legacy custom metadata fields.
    source_creator = metadata.get("creator")
    creator = source_creator if isinstance(source_creator, dict) else {
        "name": "Vana",
        "version": "26.01.07153",
    }
    metadata = {
        "dataStructureVersion": str(metadata.get("dataStructureVersion") or "2"),
        "creator": creator,
        "layoutEngineVersion": str(metadata.get("layoutEngineVersion") or "4"),
    }
    replacements = {
        "content.json": json.dumps(sheets, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        "metadata.json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as output:
        for item, payload in entries:
            output.writestr(item, replacements.get(item.filename, payload))
    os.replace(temporary, destination)

    with zipfile.ZipFile(destination, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("generated XMind archive failed CRC validation")
        verified = json.loads(archive.read("content.json"))
    if len(verified) != source_sheet_count:
        raise RuntimeError("V4.8 changed the source workbook sheet topology")
    verified_trench = next(
        sheet for sheet in verified
        if str(sheet.get("title") or "").startswith("V4.8｜新资产扫链")
    )
    verified_children = (((verified_trench.get("rootTopic") or {}).get("children") or {}).get("attached") or [])
    child_titles = [child.get("title") for child in verified_children]
    if child_titles.count(PATCH_TITLE) != 1 or child_titles.count(detail_root["title"]) != 1:
        raise RuntimeError("V4.8 compatibility branches were not written exactly once")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"source does not exist: {args.source}")
    if args.destination.exists() and not args.force:
        parser.error(f"destination already exists: {args.destination}; pass --force to replace it")
    upgrade(args.source, args.destination)


if __name__ == "__main__":
    main()
