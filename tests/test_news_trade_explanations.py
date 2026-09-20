import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from news_trade_explanations import (ExplanationService, context_for, discovery_prompt, manual_search_links,
    context_identity, normalize_posts, normalized_ai_explanation, research_key, search_query, select_posts, review_prompt)
from news_trade_reader import ANALYSIS_FIELDS, centered_geometry, safe_post_url, safe_search_url


def post(pid="123456", handle="researcher", followers=1000):
    return {"id": pid, "handle": handle, "author": handle, "followers": followers,
            "publishedAt": 1788890400000, "text": "研究内容", "url": f"https://x.com/{handle}/status/{pid}"}


def verdict(pid="123456", **extra):
    return {"id": pid, "sameAsset": True, "sameEvent": True, "quality": 90, "relevance": 96,
            "evidence": 80, "promotion": False, "focus": "事件来龙去脉", "reason": "梳理本轮事件的原始依据", **extra}


class SelectionTests(unittest.TestCase):
    def test_related_project_background_and_original_event_are_eligible(self):
        rows = [{**post(), 'text': '项目的角色来自原创兔子漫画，作者在此说明了角色设定与作品背景。'},
                {**post('123457', 'original'), 'text': '今天首次公开这幅兔子作品，角色名称和作品出处可以在原文核对。'}]
        reviews = [verdict(sameEvent=False, relation='project-background', focus='项目背景', relevance=83,
                           relationEvidence='作者在此说明了角色设定与作品背景', relationReason='同一项目的背景，不是这次上榜'),
                   verdict('123457', sameAsset=False, relation='event-source', focus='事件源头',
                           relationEvidence='今天首次公开这幅兔子作品', relationReason='输入催化对应的作品发布原帖')]
        selected = select_posts(rows, {'items': reviews})
        self.assertEqual({p['id'] for p in selected}, {'123456','123457'})
        self.assertEqual({p['relation'] for p in selected}, {'project-background','event-source'})
        for change in ({'sameAsset':False}, {'relationEvidence':'伪造而非原文里的引文内容'},
                       {'relationReason':''}, {'promotion':True}, {'quality':79}, {'relevance':79}):
            self.assertEqual(select_posts(rows[:1], {'items': [{**reviews[0], **change}]}), [])
        self.assertEqual(select_posts(rows[1:], {'items': [{**reviews[1], 'sameEvent':False}]}), [])

    def test_short_substantive_post_reaches_review_instead_of_length_rejection(self):
        text='今天公开项目的角色来源与作品背景，以下是创作者的原始介绍以及后续计划。'
        source={'data':[{'id':'123456','author_id':'1','created_at':'2026-09-08T00:00:00Z','text':text}],
                'includes':{'users':[{'id':'1','username':'writer'}]}}
        rows=normalize_posts(source,now=1788864000)
        self.assertEqual(len(rows),1)
        inputs=json.loads(review_prompt({'symbol':'TEST'},rows)[1]['content'])
        self.assertEqual(inputs['posts'][0]['text'],text)

    def test_fame_cannot_bypass_quality_or_identity(self):
        rows = [post(), post("123457", "celebrity", 20000000)]
        for bad in ({"quality": 60}, {"sameEvent": False}, {"sameAsset": False}, {"promotion": True}):
            selected = select_posts(rows, {"items": [verdict(), verdict("123457", **bad)]}, ["celebrity"])
            self.assertEqual([p["id"] for p in selected], ["123456"])

    def test_known_high_quality_authors_win_close_ties(self):
        rows = [post(), post("123457", "known", 300000)]
        chosen = select_posts(rows, {"items": [verdict(), verdict("123457")]}, ["known"])
        self.assertEqual(chosen[0]["handle"], "known")
        self.assertTrue(chosen[0]["followedAuthor"])

    def test_forged_ids_urls_bad_numbers_and_truthy_strings_rejected(self):
        for change in ({"id": "999999"}, {"quality": float("nan")}, {"sameEvent": "true"}, {"evidence": 101}):
            self.assertEqual(select_posts([post()], {"items": [verdict(**change)]}), [])
        result = select_posts([post()], {"items": [verdict(url="https://evil.test", text="invented")]})
        self.assertEqual(result[0]["text"], "研究内容")
        self.assertEqual(result[0]["url"], post()["url"])

    def test_max_five_and_different_authors_never_fill_quota(self):
        rows = [post(str(123456+i), f"author{i}") for i in range(7)]
        rows[1]["handle"] = rows[0]["handle"]
        selected = select_posts(rows, {"items": [verdict(r["id"]) for r in rows]})
        self.assertEqual(len(selected), 5)
        self.assertEqual(len({p["handle"] for p in selected}), 5)
        self.assertEqual(len(select_posts([post()], {"items": [verdict()]})), 1)

    def test_x_source_validation_dedup_old_posts_and_ads(self):
        text = "This study explains the specific launch event, its background and the documented mechanism. " * 4
        source = {"data": [{"id": "123456", "author_id": "1", "created_at": "2026-09-08T00:00:00Z", "text": text},
                           {"id": "123457", "author_id": "1", "created_at": "2026-09-08T00:00:00Z", "text": text}],
                  "includes": {"users": [{"id": "1", "username": "writer", "public_metrics": {"followers_count": 90000}}]}}
        self.assertEqual(len(normalize_posts(source, now=1788864000)), 1)
        self.assertEqual(normalize_posts(source, now=1789864000), [])
        source["data"][0]["text"] += " join my vip"
        source["data"].pop()
        self.assertEqual(normalize_posts(source, now=1788864000), [])

    def test_search_operators_and_event_cache_binding(self):
        context = {"symbol": 'LUNA" OR from:evil', "contract": "0x12345"}
        self.assertNotIn("from:", search_query(context))
        self.assertIn("lang:zh", search_query(context))
        self.assertIn("lang%3Azh", manual_search_links(context)[0]['url'])
        self.assertNotEqual(research_key(context), research_key({**context, "chain": "56"}))
        self.assertNotEqual(research_key(context), research_key({**context, "evidence": "new-event"}))

    def test_ai_explanation_requires_multiple_bounded_fields(self):
        self.assertEqual(normalized_ai_explanation({'analysis':{'summary':'只有一项'}}), {})
        result = normalized_ai_explanation({'analysis':{'summary':'事件概要','asset':'项目背景',
            'confusionPoint':'用户容易把质押凭证误认为协议治理币。',
            'plainMechanism':'用户质押原生币 → 协议返回质押凭证 → 凭证随质押收益增加价值',
            'conceptDistinctions':'原生币：被质押的底层资产。\n质押凭证：代表赎回权，不是治理币。',
            'underlyingEvent':'产品发布触发传播','whyHot':'官方消息叠加成交放大',
            'narrative':'公司资产映射叙事','opportunity':'关注持续采用和流动性扩张'}})
        self.assertEqual(result['sourceType'], 'local-codex-analysis')
        self.assertEqual(result['asset'], '项目背景')
        self.assertEqual(result['underlyingEvent'], '产品发布触发传播')
        self.assertEqual(result['narrative'], '公司资产映射叙事')
        self.assertIn('误认为', result['confusionPoint'])
        self.assertIn('协议返回质押凭证', result['plainMechanism'])
        self.assertIn('不是治理币', result['conceptDistinctions'])

    def test_leaderboard_input_is_never_presented_as_the_underlying_event(self):
        result = normalized_ai_explanation({'analysis':{
            'summary':'热度上升','asset':'测试代币',
            'underlyingEvent':'输入事件将其标记为热门榜排名第一。'}})
        self.assertIn('公开证据尚未定位', result['underlyingEvent'])
        self.assertIn('不作为起因', result['underlyingEvent'])

    def test_prompt_requires_plain_mechanism_and_concept_comparison(self):
        instructions = discovery_prompt({'symbol':'TEST'})[0]['content']
        self.assertIn('plainMechanism', instructions)
        self.assertIn('一句话判断→它是什么→背后事件起因→为什么现在火→核心叙事', instructions)
        self.assertIn('confusionPoint', instructions)
        self.assertIn('当前标的 = 一句话定义', instructions)
        self.assertIn('conceptDistinctions', instructions)
        self.assertIn('不是什么', instructions)
        self.assertNotIn('LST/LRT', instructions)
        self.assertNotIn('4Stock', instructions)
        self.assertNotIn('BNC4', instructions)

    def test_prompt_requires_buyback_funding_and_execution_status(self):
        instructions = discovery_prompt({'symbol':'TEST','chain':'bsc','contract':'0x'+'12'*20})[0]['content']
        self.assertIn('币名+CA', instructions)
        self.assertIn('谁出资', instructions)
        self.assertIn('税费/手续费/产品收入', instructions)
        self.assertIn('官方名单或交易哈希', instructions)

    def test_generic_buyback_summary_surfaces_known_fee_funding_mechanism(self):
        result = normalized_ai_explanation({'analysis':{
            'summary':'STONKBALL 借回购销毁活动传播，但尚未证明已经获得回购。',
            'asset':'STONKBALL 是 BSC 上的社区 Meme 代币。',
            'underlyingEvent':'平台把 BNC4 交易手续费和 LP 费用收入用于回购符合资格的日成交冠军，并将买回代币销毁。',
            'plainMechanism':'每日产品收入进入回购预算，再买入符合规则的代币并销毁。',
        }})
        self.assertIn('交易手续费', result['summary'])
        self.assertIn('回购', result['summary'])
        self.assertIn('尚未证明', result['summary'])

    def test_reader_analysis_order_follows_the_explanation_logic(self):
        self.assertEqual([label for label, _keys in ANALYSIS_FIELDS], [
            '一句话判断', '它到底是什么', '背后事件起因', '为什么现在火', '核心叙事',
            '潜在机会', '后续验证信号', '具体机制怎么运转（大白话）', '最容易搞混的地方',
            '容易混淆的概念', '失效条件', '主要风险', '证据与推断边界', '仍需核对',
        ])

    def test_history_identity_normalizes_chain_aliases_and_evm_case(self):
        address = '0x' + 'Ab' * 20
        self.assertEqual(context_identity({'chain':'bsc','contract':address}),
                         context_identity({'chain':'56','contract':address.lower()}))

    def test_reader_center_and_external_link_safety(self):
        self.assertEqual(centered_geometry((0, 0, 1920, 1040)), "1040x780+440+130")
        self.assertEqual(centered_geometry((-1920, 0, 0, 1040)), "1040x780-1480+130")
        for url in ("javascript:alert(1)", "https://x.com.evil/a/status/12345", "https://x.com/a/status/12345?redirect=evil"):
            self.assertEqual(safe_post_url(url), "")
        valid='https://x.com/search?q=%22TEST%22&f=live'
        self.assertEqual(safe_search_url(valid),valid)
        for url in ('https://evil.test/search?q=TEST&f=live','https://x.com/search?q=TEST&f=top',
                    'https://x.com/search?q=TEST&f=live&redirect=evil'):
            self.assertEqual(safe_search_url(url),'')

    def test_top_three_cover_available_explanation_angles(self):
        rows = [post(str(123456+i), f"author{i}") for i in range(5)]
        reviews = [verdict(r["id"]) for r in rows]
        reviews[3]["focus"] = "标的是什么"
        reviews[4]["focus"] = "可能影响"
        chosen = select_posts(rows, {"items": reviews})
        self.assertEqual({r["focus"] for r in chosen[:3]}, {"标的是什么", "事件来龙去脉", "可能影响"})


class ServerBindingTests(unittest.TestCase):
    def test_normalization_rebuilds_key_and_preserves_context_without_trusting_url(self):
        import server
        payload = {"kind": "News Trade · 潜在机会", "explanationKey": "evil", "explanationPort": 80,
                   "explanationContext": {"symbol": "TEST", "eventAt": 1, "catalyst": "发布新机制"}}
        normalized = server.normalize_desktop_alert(payload)
        self.assertEqual(normalized["explanationKey"], research_key(normalized["explanationContext"]))
        self.assertNotEqual(normalized["explanationPort"], 80)
        other = server.normalize_desktop_alert({**payload, "kind": "价格监控"})
        self.assertFalse(other["explanationKey"])

    def test_prefetch_exception_does_not_escape_delivery(self):
        import server
        from unittest.mock import patch
        with patch.object(server, "news_explanation_service", side_effect=RuntimeError("injected")):
            server.prefetch_popup_explanations({"kind": "News Trade", "explanationContext": {"symbol": "TEST"}})

    def test_wallet_four_hour_popup_has_research_identity_and_button_key(self):
        import server
        event = server.rank_monitor_event("hot", {"sourceId": "binance-wallet-hot", "period": "4h",
            "periodLabel": "4 小时", "symbol": "小股东", "sourceTitle": "币安钱包热门榜",
            "url": "https://web3.binance.com/en/token/bsc/0x" + "12"*20}, "new")
        normalized = server.normalize_desktop_alert(event)
        self.assertRegex(normalized["explanationKey"], r"^[a-f0-9]{40}$")
        self.assertEqual(normalized["explanationContext"]["contract"], "0x" + "12"*20)
        self.assertEqual(normalized["explanationContext"]["symbol"], "小股东")

    def test_newsflash_explanation_button_only_marks_meme_or_event_news(self):
        import server
        meme = server.newsflash_explanation_profile({
            "id": "meme-1", "title": "社区热议 $PEPE 同名 Meme 币", "content": "合约地址：0x" + "12" * 20,
            "source": "方程式新闻", "add_time": 1_788_890_400,
        })
        event = server.newsflash_explanation_profile({
            "id": "event-1", "title": "某项目宣布上线新的回购机制", "content": "官方将在本周启动回购",
            "source": "BlockBeats", "add_time": 1_788_890_400,
        })
        routine = server.newsflash_explanation_profile({
            "id": "quote-1", "title": "BTC 现报 65000 美元", "content": "24 小时成交额为 20 亿美元",
            "source": "行情播报", "add_time": 1_788_890_400,
        })
        self.assertEqual(meme["explanationReason"], "Meme 叙事")
        self.assertEqual(meme["explanationContext"]["symbol"], "PEPE")
        self.assertEqual(meme["explanationContext"]["contract"], "0x" + "12" * 20)
        self.assertEqual(event["explanationReason"], "新闻催化")
        self.assertTrue(event["explanationContext"]["symbol"])
        self.assertEqual(routine, {})
        enriched = server.enrich_newsflash_explanation_item({"id": "meme-1", "title": "PEPE Meme 热议"})
        self.assertRegex(enriched["explanationKey"], r"^[a-f0-9]{40}$")
        self.assertNotIn("explanationContext", enriched)

    def test_newsflash_popup_keeps_meme_context_and_uses_parenthetical_ticker(self):
        import server
        item = {
            "id": "215297",
            "title": "NO MANUAL（ANT人生）于BINGAN平台成功发射",
            "content": "MEME 文化项目 NO MANUAL（ANT 人生）完成公平发射",
            "source": "BlockBeats 律动",
            "add_time": 1_788_947_445,
            "url": "https://x.com/NoManualSOL",
        }

        profile = server.newsflash_explanation_profile(item)
        event = server.parse_site_newsflash_events({"items": [item]})[0]
        normalized = server.normalize_desktop_alert(event)

        self.assertEqual(profile["explanationContext"]["symbol"], "ANT")
        self.assertEqual(event["sourceType"], "newsflash")
        self.assertEqual(normalized["explanationContext"]["symbol"], "ANT")
        self.assertRegex(normalized["explanationKey"], r"^[a-f0-9]{40}$")
        self.assertEqual(
            normalized["explanationOpenEndpoint"],
            f"http://127.0.0.1:{normalized['explanationPort']}/api/newsflash/explanations/open",
        )

    def test_newsflash_popup_does_not_prefetch_until_clicked(self):
        import server
        from unittest.mock import patch
        context = {"symbol": "ANT", "title": "ANT Meme"}
        with patch.object(server, "news_explanation_service") as service:
            server.prefetch_popup_explanations({
                "kind": "聚合快讯",
                "sourceType": "newsflash",
                "explanationContext": context,
            })
        registered = service.return_value.register_context.call_args.args[0]
        self.assertEqual(registered['symbol'], 'ANT')
        self.assertEqual(registered['title'], 'ANT Meme')
        service.return_value.prefetch_context.assert_not_called()

    def test_newsflash_open_prefetches_on_click_then_launches_reader(self):
        import server
        from unittest.mock import patch
        profile = server.newsflash_explanation_profile({
            "id": "event-2", "title": "ABC 宣布上线主网", "content": "社区正在热议本次发布",
            "source": "方程式新闻", "add_time": 1_788_890_400,
        })
        service = Mock()
        service.prefetch_context.return_value = profile["explanationKey"]
        service.get.return_value = {"status": "pending"}
        with patch.object(server, "cached_newsflash_explanation_profile", return_value=profile), \
             patch.object(server, "news_explanation_service", return_value=service), \
             patch.object(server, "spawn_explanation_reader") as spawn:
            result = server.open_cached_newsflash_explanation(profile["explanationKey"])
        self.assertEqual(result["status"], "pending")
        service.prefetch_context.assert_called_once_with(profile["explanationContext"])
        spawn.assert_called_once_with(profile["explanationKey"], profile["explanationTitle"])


class ServiceTests(unittest.TestCase):
    def test_restart_migrates_an_evidenced_buyback_summary_without_another_search(self):
        with tempfile.TemporaryDirectory() as directory:
            search=Mock(side_effect=AssertionError('evidence already exists in the cached analysis'))
            service=ExplanationService(Path(directory)/'cache.sqlite',lambda:'fake',Mock(),searcher=search)
            context={'symbol':'STONKBALL','chain':'bsc','contract':'0x'+'12'*20,'eventAt':1}
            key=research_key(context);conn=service._db()
            old={'id':key,'title':'STONKBALL','status':'analysis','posts':[],
                 'aiExplanation':{
                     'summary':'STONKBALL 借回购销毁活动传播，但不等于已经获得回购。',
                     'asset':'STONKBALL 是 BSC 上的社区 Meme 代币。',
                     'underlyingEvent':'平台把 BNC4 交易手续费和 LP 费用收入用于回购符合资格的日成交冠军，并将买回代币销毁。',
                 },'selectionPolicy':11,'updatedAt':int(time.time()*1000)}
            with conn:
                conn.execute('INSERT INTO explanations VALUES(?,?,?)',(key,time.time(),json.dumps(old)))
                conn.execute('INSERT INTO explanation_inputs VALUES(?,?,?,?,?)',
                             (key,json.dumps(context),'',time.time(),time.time()))
            conn.close()
            current=service.get(key)
            self.assertEqual(current['selectionPolicy'],12)
            self.assertIn('交易手续费',current['aiExplanation']['summary'])
            self.assertIn('不等于已经获得回购',current['aiExplanation']['summary'])
            search.assert_not_called()

    def test_restart_resumes_an_outdated_policy_instead_of_showing_old_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            started, release = threading.Event(), threading.Event()
            def search(_messages):
                started.set(); release.wait(2)
                return {'posts':[], 'links':[], 'analysis':{'summary':'新结论', 'asset':'新标的说明'}}
            service=ExplanationService(Path(directory)/'cache.sqlite',lambda:'fake',Mock(),
                                       searcher=search,combined=True,accept_search_results=True)
            context={'symbol':'TEST','chain':'bsc','contract':'0x'+'12'*20,'eventAt':1}
            key=research_key(context);conn=service._db()
            old={'id':key,'title':'TEST','status':'analysis','posts':[],
                 'aiExplanation':{'summary':'旧结论'},'selectionPolicy':11,'updatedAt':int(time.time()*1000)}
            with conn:
                conn.execute('INSERT INTO explanations VALUES(?,?,?)',(key,time.time(),json.dumps(old)))
                conn.execute('INSERT INTO explanation_inputs VALUES(?,?,?,?,?)',
                             (key,json.dumps(context),'',time.time(),time.time()))
            conn.close()
            current=service.get(key)
            self.assertEqual(current['status'],'pending')
            self.assertNotEqual(current.get('aiExplanation',{}).get('summary'),'旧结论')
            self.assertTrue(started.wait(1))
            release.set();service.jobs.join()
            self.assertEqual(service.get(key)['selectionPolicy'],12)
            self.assertEqual(service.get(key)['aiExplanation']['summary'],'新结论')

    def test_old_empty_policy_is_not_reused_after_related_posts_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ExplanationService(Path(directory)/'cache.sqlite',lambda:'fake',Mock(return_value={'items':[verdict()]}))
            context={'symbol':'TEST','title':'TEST','eventAt':1};key=research_key(context)
            conn=service._db()
            with conn:
                conn.execute('INSERT INTO explanations VALUES(?,?,?)',
                             (key,time.time(),json.dumps({'id':key,'status':'empty','posts':[]})))
                conn.execute('INSERT INTO explanation_inputs VALUES(?,?,?,?,?)',
                             (key,json.dumps(context),json.dumps([post('654321')]),time.time(),time.time()))
            conn.close()
            service._search=Mock(return_value=[post()])
            service.prefetch_context(context);service.jobs.join()
            self.assertEqual(service.get(key)['status'],'ready')
            self.assertEqual(service.get(key)['selectionPolicy'],12)
            service._search.assert_called_once()

    def test_failed_review_retains_inputs_and_retries_after_restart_without_researching(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'cache.sqlite'
            service=ExplanationService(path,lambda:'fake',Mock(side_effect=TimeoutError('PRIVATE_ERROR')))
            service._search=Mock(return_value=[post()])
            context={'symbol':'TEST','title':'TEST','eventAt':1}
            key=service.prefetch_context(context);service.jobs.join()
            failed=service.get(key)
            self.assertEqual(failed['failureStage'],'review')
            self.assertEqual(failed['searchedCount'],1)
            self.assertNotIn('PRIVATE_ERROR',json.dumps(failed))
            restarted=ExplanationService(path,lambda:'fake',Mock(return_value={'items':[verdict()]}))
            restarted._search=Mock(side_effect=AssertionError('reuse acquired originals'))
            self.assertEqual(restarted.retry(key)['status'],'unavailable') # cooldown retained across restart
            with patch('news_trade_explanations.time.time',return_value=time.time()+31):
                self.assertEqual(restarted.retry(key)['status'],'pending')
                restarted.jobs.join()
            self.assertEqual(restarted.get(key)['status'],'ready')
            self.assertEqual(len(restarted.get(key)['posts']),1)
            restarted._search.assert_not_called()

    def test_legacy_retry_uses_exact_saved_popup_identity_without_replaying_alert(self):
        from alert_delivery import AlertDeliveryStore
        with tempfile.TemporaryDirectory() as directory:
            context={'symbol':'TEST','title':'TEST','eventAt':1};key=research_key(context)
            store=AlertDeliveryStore(Path(directory)/'alerts.sqlite')
            admitted=store.admit({'explanationKey':key,'explanationContext':context},['original'],1)
            before=store.get(admitted['deliveryId'])
            service=ExplanationService(Path(directory)/'research.sqlite',lambda:'fake',Mock(return_value={'items':[verdict()]}),
                context_lookup=store.explanation_context)
            service._search=Mock(return_value=[post()])
            self.assertEqual(service.retry(key)['status'],'pending');service.jobs.join()
            self.assertEqual(service.get(key)['status'],'ready')
            self.assertEqual(store.get(admitted['deliveryId']),before)
            with self.assertRaises(ValueError):service.retry('b'*40)

    def test_queued_context_survives_restart_before_worker_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'research.sqlite'
            service=ExplanationService(path,lambda:'fake',Mock())
            service.worker=Mock();service.worker.is_alive.return_value=True
            context={'symbol':'QUEUED','eventAt':1}
            key=service.prefetch_context(context)
            restarted=ExplanationService(path,lambda:'fake',Mock())
            restored=restarted.get(key)
            self.assertEqual(restored['title'],'QUEUED')
            self.assertEqual(restored['status'],'pending')
            restarted.jobs.join()
            self.assertEqual(restarted.get(key)['status'],'unavailable')
            self.assertTrue(restarted.get(key)['retryable'])

    def test_nonblocking_singleflight_persistence_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = threading.Event()
            review = Mock(return_value={"items": [verdict()]})
            service = ExplanationService(Path(directory)/"cache.sqlite", lambda: "fake", review)
            def search(_, _known_posts=()):
                gate.wait(2)
                return [post()]
            service._search = Mock(side_effect=search)
            context = {"symbol": "TEST", "eventAt": 1}
            key = service.prefetch_context(context)
            self.assertEqual(service.get(key)["status"], "pending")
            self.assertEqual(key, service.prefetch_context(context))
            gate.set()
            service.jobs.join()
            self.assertEqual(service._search.call_count, 1)
            self.assertEqual(service.get(key)["status"], "ready")
            other = ExplanationService(Path(directory)/"cache.sqlite", lambda: "fake", review)
            self.assertEqual(other.get(key)["posts"][0]["id"], "123456")
            self.assertIsNone(other.get("../../.env"))
            service._search = Mock(side_effect=RuntimeError("SECRET_MUST_NOT_LEAK"))
            bad_key = service.prefetch_context({**context, "eventAt": 2})
            service.jobs.join()
            self.assertEqual(service.get(bad_key)["status"], "unavailable")
            self.assertNotIn("SECRET", json.dumps(service.get(bad_key)))

    def test_permission_failure_cooldown_and_no_redirects(self):
        http = Mock(return_value=Mock(status_code=403))
        service = ExplanationService("unused", lambda: "fake", Mock(), http=http)
        with self.assertRaisesRegex(ValueError, "直连接口已停用"):
            service._search({"symbol": "TEST"})
        http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
