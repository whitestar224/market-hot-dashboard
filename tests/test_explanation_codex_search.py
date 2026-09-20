import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
from news_trade_explanations import (canonical_post_url, codex_discovered_posts, SearchUnavailable,
    ExplanationService, discovered_links, fast_discovery_prompt, provisional_explanation)


class CodexSearchTests(unittest.TestCase):
    def test_live_service_accepts_codex_results_and_has_no_x_token_path(self):
        original=server.NEWS_EXPLANATIONS
        try:
            server.NEWS_EXPLANATIONS=None
            service=server.news_explanation_service()
            self.assertTrue(service.accept_search_results)
            self.assertEqual(service.token(),'')
        finally:
            server.NEWS_EXPLANATIONS=original

    def test_explanation_review_does_not_queue_behind_other_analysis(self):
        with server.CODEX_CLI_FAST_ANALYSIS_LOCK:
            with server.codex_cli_analysis_slot(time.monotonic()+1,lane='explanations-review'):
                self.assertFalse(server.CODEX_CLI_EXPLANATION_REVIEW_LOCK.acquire(blocking=False))

    def test_search_timeout_never_calls_paid_fallback_or_leaks_private_output(self):
        from subprocess import TimeoutExpired
        service=ExplanationService('unused',lambda:'fake',Mock(),
            searcher=Mock(side_effect=TimeoutExpired('private-command',85,output='PRIVATE_SECRET')))
        service.http=Mock(side_effect=AssertionError('no direct X'))
        with self.assertRaises(SearchUnavailable) as error:
            service._search({'symbol':'TEST'})
        service.http.assert_not_called()
        self.assertIn('Codex 搜索超时',str(error.exception))
        self.assertNotIn('PRIVATE',str(error.exception))
        self.assertNotIn('private-command',str(error.exception))

    def test_only_search_lane_enables_web_without_files_shell_or_secrets(self):
        def run(cmd, **kwargs):
            self.assertEqual(cmd[1:3], ['--search', 'exec'])
            self.assertIn('Use only the native web search tool', kwargs['input'])
            self.assertNotIn('Do not use tools, browse', kwargs['input'])
            self.assertIn('read-only', cmd)
            self.assertIn('shell_tool', cmd)
            Path(cmd[cmd.index('--output-last-message')+1]).write_text(json.dumps({'content':json.dumps({
                'posts':[], 'links':[], 'analysis':{'summary':'简要判断','risk':'风险说明'}
            }, ensure_ascii=False)}), encoding='utf-8')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch.object(server,'codex_cli_executable',return_value='codex.exe'), \
             patch.object(server,'codex_cli_fallback_available',return_value=True), \
             patch.object(server.subprocess,'run',side_effect=run):
            result = server.news_explanation_search([])
            self.assertEqual(result['analysis']['summary'], '简要判断')

    def response(self, pid=None):
        pid = pid or str((int(time.time()*1000)-1288834974657)<<22)
        url = f'https://x.com/researcher/status/{pid}'
        data = {'url':url,'openedUrl':url,'contentKind':'source-excerpt',
                'language':'zh','text':'这是关于该标的与本次事件的中文研究说明，并列出了可以核对的公开依据。'}
        return url, Mock(status_code=200,content=b'valid',json=lambda:data), data

    def test_local_excerpts_are_labeled_not_claimed_as_api_verified(self):
        url,response,payload=self.response()
        http=Mock(return_value=response)
        rows=codex_discovered_posts({'posts':[payload],'followers':999999})
        self.assertEqual(len(rows),1)
        self.assertIn('中文研究说明', rows[0]['text'])
        self.assertNotIn('footer', rows[0]['text'])
        self.assertEqual(rows[0]['followers'],0)
        http.assert_not_called()
        self.assertEqual(rows[0]['sourceType'],'codex-search-excerpt')
        self.assertNotIn('sourceVerified',rows[0])

    def test_wrong_id_author_missing_original_and_redirect_never_become_posts(self):
        for bad in ('id','author','summary','missing'):
            url,response,payload=self.response()
            if bad=='id': payload['url']='https://x.com/researcher/status/12345'
            if bad=='author': payload['openedUrl']='https://x.com/another/status/'+url.rsplit('/',1)[1]
            if bad=='summary': payload['contentKind']='summary'
            if bad=='missing': payload.pop('openedUrl')
            with self.subTest(bad=bad):
                self.assertEqual(codex_discovered_posts({'posts':[payload]}),[])

    def test_english_search_result_is_not_displayed_as_a_chinese_tweet(self):
        url,_,payload=self.response()
        payload.update(language='en', text='English explanation of the event and its possible impact.')
        self.assertEqual(codex_discovered_posts({'posts':[payload]}), [])

    def test_no_links_returns_empty_without_network_and_cli_can_replace_x_search_token(self):
        http=Mock()
        self.assertEqual(codex_discovered_posts({'posts':[]}),[])
        http.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            searcher=Mock(return_value={'posts':[]})
            service=ExplanationService(Path(folder)/'test.db',lambda:'',Mock(),searcher=searcher,http=http)
            self.assertEqual(service._search({'symbol':'CATE','chain':'sol','contract':'test'}),[])
            self.assertIn('合约',searcher.call_args.args[0][0]['content'])
            http.assert_not_called()

    def test_timeout_does_not_launch_another_whole_model_turn(self):
        searcher=Mock(side_effect=TimeoutError())
        http=Mock(side_effect=AssertionError('no direct APIs'))
        service=ExplanationService('unused',Mock(side_effect=AssertionError('no token read')),Mock(),searcher=searcher,http=http)
        with self.assertRaises(SearchUnavailable):
            service._search({'symbol':'TEST'})
        searcher.assert_called_once()
        http.assert_not_called()
        service.token.assert_not_called()

    def test_search_index_excerpt_is_retained_without_reopening_x(self):
        url,_,post=self.response()
        post.update(contentKind='search-excerpt',sourceUrl=url)
        post.pop('openedUrl')
        rows=codex_discovered_posts({'posts':[post]})
        self.assertEqual(rows[0]['sourceType'],'codex-indexed-excerpt')
        self.assertNotIn('sourceVerified',rows[0])
        post['sourceUrl']='https://example.org/not-the-post'
        self.assertEqual(codex_discovered_posts({'posts':[post]}),[])

    def test_unreadable_pages_keep_safe_links_but_no_invented_post_body(self):
        url,_,post=self.response()
        found={'posts':[], 'items':[], 'links':[{'url':url,'title':'项目背景'},
            {'url':'javascript:alert(1)'},{'url':url}]}
        service=ExplanationService('unused',Mock(),Mock(),searcher=Mock(return_value=found),combined=True)
        result=service._search({'symbol':'TEST'})
        self.assertEqual(list(result),[])
        self.assertEqual([r['url'] for r in result.links],[url])
        service.review.assert_not_called()

    def test_one_cli_call_keeps_found_post_and_reuses_repeated_event_after_restart(self):
        _,_,post=self.response()
        searcher=Mock(return_value={'posts':[post],'links':[]})
        review=Mock(side_effect=AssertionError('no second model'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite'
            service=ExplanationService(path,Mock(),review,searcher=searcher,combined=True,accept_search_results=True)
            context={'symbol':'TEST','chain':'56','contract':'0x1234','title':'same event','eventAt':1}
            key=service.prefetch_context(context);service.jobs.join()
            self.assertEqual(service.get(key)['status'],'ready')
            self.assertNotIn('_verdict',service.get(key)['posts'][0])
            restarted=ExplanationService(path,Mock(),review,searcher=searcher,combined=True,accept_search_results=True)
            repeat=restarted.prefetch_context({**context,'eventAt':2})
            self.assertEqual(restarted.get(repeat)['status'],'ready')
            self.assertTrue(restarted.get(repeat)['reused'])
            self.assertEqual(ExplanationService(path,Mock(),review).get(repeat)['status'],'ready')
            searcher.assert_called_once();review.assert_not_called()
            other=restarted.prefetch_context({**context,'contract':'0x9999'})
            restarted.jobs.join()
            self.assertEqual(searcher.call_count,2)

    def test_registered_popup_context_starts_search_only_when_reader_gets_it(self):
        found = {'posts':[], 'links':[], 'analysis':{
            'summary':'这是点击后生成的解释。', 'risk':'仍需核对流动性。'
        }}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lazy.sqlite'
            searcher = Mock(return_value=found)
            service = ExplanationService(path, Mock(), Mock(), searcher=searcher,
                combined=True, accept_search_results=True)
            context = {'symbol':'LAZY','chain':'56','contract':'0x'+'12'*20,'eventAt':1}
            key = service.register_context(context)
            searcher.assert_not_called()
            state = service.get(key)
            self.assertEqual(state['status'], 'pending')
            service.jobs.join()
            self.assertEqual(service.get(key)['status'], 'analysis')
            searcher.assert_called_once()

    def test_reader_has_immediate_monitor_explanation_while_codex_is_still_running(self):
        gate = threading.Event()
        def slow_search(_messages):
            gate.wait(2)
            return {'posts':[], 'links':[], 'analysis':{
                'summary':'Codex 已补充说明。', 'risk':'仍需核对风险。'
            }}
        with tempfile.TemporaryDirectory() as folder:
            service = ExplanationService(Path(folder)/'immediate.sqlite', Mock(), Mock(),
                searcher=slow_search, combined=True, accept_search_results=True)
            context = {'symbol':'NOW','name':'Now Token','chain':'bsc',
                       'contract':'0x'+'34'*20,'catalyst':'4小时热门榜新进',
                       'marketSnapshot':'排名 #3 / 成交 $100K','eventAt':1}
            key = service.register_context(context)
            state = service.get(key)
            self.assertEqual(state['status'], 'pending')
            self.assertEqual(state['aiExplanation']['sourceType'], 'local-monitor-context')
            self.assertIn(context['contract'], state['aiExplanation']['asset'])
            self.assertIn('排名 #3', state['aiExplanation']['whyHot'])
            gate.set(); service.jobs.join()
            self.assertEqual(service.get(key)['aiExplanation']['sourceType'], 'local-codex-analysis')

    def test_fast_prompt_is_compact_and_keeps_identity_search_requirements(self):
        prompt = fast_discovery_prompt(
            {'symbol':'FAST','chain':'bsc','contract':'0x'+'56'*20},
            preferred=['writer'], known_posts=[])
        system = prompt[0]['content']
        self.assertLess(len(system), 3000)
        self.assertIn('lang:zh', system)
        self.assertIn('CA合约', system)
        self.assertIn('最多保留3条', system)
        self.assertIn('最多执行2次搜索', system)
        event = json.loads(prompt[1]['content'])
        self.assertEqual(event['event']['symbol'], 'FAST')

    def test_provisional_explanation_is_labeled_as_monitor_data_not_ai(self):
        result = provisional_explanation({'symbol':'TEST','chain':'56','contract':'0x1234',
                                          'catalyst':'热门榜新进'})
        self.assertEqual(result['sourceType'], 'local-monitor-context')
        self.assertIn('0x1234', result['asset'])
        self.assertIn('榜单和价格变化是热度结果', result['underlyingEvent'])

    def test_same_contract_history_is_recovered_across_chain_aliases(self):
        _,_,post=self.response()
        full_analysis={'summary':'发生新的传播','asset':'同一链上代币',
            'underlyingEvent':'社区发布了新的产品说明','whyHot':'公开消息与交易活跃叠加',
            'narrative':'产品采用叙事','opportunity':'关注真实采用是否扩张',
            'validation':'观察官方后续与链上活跃','invalidation':'消息被否认或活跃衰退',
            'risk':'同名误配和流动性风险','evidence':'公开资料与已保存原帖',
            'uncertainty':'持续时间仍不确定'}
        searcher=Mock(side_effect=[{'posts':[post],'links':[],'analysis':full_analysis},
                                   {'posts':[],'links':[],'analysis':full_analysis}])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.sqlite'
            service=ExplanationService(path,Mock(),Mock(),searcher=searcher,
                combined=True,accept_search_results=True)
            address='0x'+'12'*20
            first={'symbol':'TEST','chain':'bsc','contract':address,'title':'第一次','eventAt':1}
            service.prefetch_context(first);service.jobs.join()
            second={'symbol':'TEST','chain':'56','contract':address.upper(),'title':'第二次','eventAt':2}
            key=service.prefetch_context(second);service.jobs.join()
            state=service.get(key)
            self.assertEqual(len(state['posts']),1)
            self.assertEqual(state['posts'][0]['sourceType'],'previously-saved-post')
            prompt=json.loads(searcher.call_args_list[1].args[0][1]['content'])
            self.assertEqual(len(prompt['previouslySavedPosts']),1)
            self.assertEqual(prompt['previouslySavedPosts'][0]['url'],post['url'])

    def test_found_short_old_or_promotional_excerpt_is_not_quality_filtered(self):
        pid=str((int((time.time()-400*86400)*1000)-1288834974657)<<22)
        url=f'https://x.com/trader/status/{pid}'
        found={'posts':[{'url':url,'openedUrl':url,'contentKind':'source-excerpt','language':'zh','text':'现在买入会员'}], 'links':[]}
        with tempfile.TemporaryDirectory() as folder:
            review=Mock(side_effect=AssertionError('quality review removed'))
            service=ExplanationService(Path(folder)/'relaxed.sqlite',Mock(),review,
                searcher=Mock(return_value=found),accept_search_results=True)
            key=service.prefetch_context({'symbol':'TEST','eventAt':1});service.jobs.join()
            state=service.get(key)
            self.assertEqual(state['status'],'ready')
            self.assertEqual(state['posts'][0]['text'],'现在买入会员')
            review.assert_not_called()

    def test_no_chinese_post_uses_analysis_from_the_same_cli_call(self):
        found={'posts':[], 'links':[], 'analysis':{
            'summary':'该事件正在传播。','asset':'这是一个新代币。','cause':'由上榜触发关注。',
            'process':'市场开始讨论。','result':'暂未形成明确结果。','impact':'短期关注度可能上升。',
            'uncertainty':'合约与后续进展仍需核对。'}}
        searcher=Mock(return_value=found)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'analysis.sqlite'
            service=ExplanationService(path,Mock(),Mock(),searcher=searcher,
                combined=True,accept_search_results=True)
            key=service.prefetch_context({'symbol':'TEST','eventAt':1});service.jobs.join()
            state=service.get(key)
            self.assertEqual(state['status'],'analysis')
            self.assertEqual(state['aiExplanation']['sourceType'],'local-codex-analysis')
            self.assertEqual(searcher.call_count,1)

    def test_link_only_result_survives_worker_and_reader_restart(self):
        url,_,_=self.response()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'links.sqlite';review=Mock()
            service=ExplanationService(path,Mock(),review,combined=True,
                searcher=Mock(return_value={'posts':[],'items':[],'links':[{
                    'url':url,'language':'zh','title':'中文相关推文'}]}))
            key=service.prefetch_context({'symbol':'TEST','eventAt':1});service.jobs.join()
            state=ExplanationService(path,Mock(),review).get(key)
            self.assertEqual(state['status'],'links')
            self.assertEqual(state['links'][0]['url'],url)
            self.assertEqual(state['posts'],[]);review.assert_not_called()

    def test_first_usable_batch_returns_without_extra_search(self):
        _,_,post=self.response();searcher=Mock(return_value={'posts':[post]})
        service=ExplanationService('unused',lambda:'configured-but-not-authorized',Mock(),searcher=searcher)
        self.assertEqual(len(service._search({'symbol':'TEST'})),1)
        searcher.assert_called_once()

    def test_review_uses_local_codex_without_paid_llm_fallback(self):
        with patch.object(server,'codex_cli_chat',return_value={'choices':[{'message':{'content':'{"items":[]}'}}]}) as cli, \
             patch.object(server,'deepseek_chat',side_effect=AssertionError('no paid review')):
            self.assertEqual(server.news_explanation_review([]),{'items':[]})
        self.assertEqual(cli.call_args.kwargs['lane'],'explanations-review')

    def test_explanation_search_uses_lightweight_dedicated_profile(self):
        with patch.object(server,'codex_cli_chat',return_value={
                'choices':[{'message':{'content':'{"posts":[],"links":[],"analysis":{"summary":"简要判断","risk":"主要风险"}}'}}]}) as cli:
            server.news_explanation_search([])
        self.assertEqual(cli.call_args.kwargs['lane'],'explanations-search')
        self.assertEqual(cli.call_args.kwargs['reasoning_effort_override'],'low')
        self.assertEqual(cli.call_args.kwargs['model_override'],'gpt-5.6-luna')
        self.assertEqual(cli.call_args.kwargs['timeout_seconds'],70)
        self.assertIsNotNone(cli.call_args.kwargs['deadline'])

    def test_explanation_search_timeout_returns_fast_no_web_analysis(self):
        timeout = TimeoutExpired('codex', 70)
        fallback = {'choices':[{'message':{'content':json.dumps({
            'posts':[], 'links':[], 'analysis':{
                'summary':'根据现有事件生成解释。', 'asset':'这是待核对的链上代币。',
                'underlyingEvent':'公开证据不足。', 'whyHot':'短时成交与榜单曝光。',
                'risk':'流动性与同名误配风险。', 'uncertainty':'尚无可核对原帖。'
            }
        }, ensure_ascii=False)}}]}
        with patch.object(server,'codex_cli_chat',side_effect=[timeout, fallback]) as cli:
            result = server.news_explanation_search([
                {'role':'user','content':'{"event":{"symbol":"TEST"}}'}
            ])
        self.assertEqual(cli.call_count, 2)
        self.assertTrue(cli.call_args_list[0].kwargs['web_search'])
        self.assertFalse(cli.call_args_list[1].kwargs['web_search'])
        self.assertEqual(cli.call_args_list[1].kwargs['lane'],'explanations-review')
        self.assertEqual(result['posts'], [])
        self.assertIn('summary', result['analysis'])

    def test_empty_search_result_uses_no_web_explanation_instead_of_empty_page(self):
        empty = {'choices':[{'message':{'content':'{"posts":[],"links":[],"analysis":{}}'}}]}
        fallback = {'choices':[{'message':{'content':'{"analysis":{"summary":"简要解释","risk":"风险说明"}}'}}]}
        with patch.object(server,'codex_cli_chat',side_effect=[empty, fallback]) as cli:
            result = server.news_explanation_search([{'role':'user','content':'{}'}])
        self.assertEqual(cli.call_count, 2)
        self.assertEqual(result['analysis']['summary'], '简要解释')
        self.assertEqual(result['posts'], [])


if __name__ == '__main__': unittest.main()
