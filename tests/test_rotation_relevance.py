import unittest
from copy import deepcopy
import server


class RotationRelevanceTests(unittest.TestCase):
    def test_direct_then_derivative_outrank_liquid_weak_peers_without_symbol_rules(self):
        for leader, direct, derivative in [('4STOCK', 'BNC4', 'STRATTON'), ('ALPHA', 'FIRST', 'COPY')]:
            peers = [
                {'symbol': 'WEAK', 'relationshipType': 'beneficiary', 'reason': f'BSC 生态情绪被 {leader} 点燃后，老牌 Meme 被动受益于流动性回流'},
                {'symbol': direct, 'relationshipType': 'theme', 'reason': f'{direct} 是第一个通过 {leader} 上链的股票'},
                {'symbol': derivative, 'relationshipType': 'theme', 'reason': f'{derivative} 与 {leader} 具体叙事如出一辙，属于同题材仿盘'},
                {'symbol': 'CROSS', 'relationshipType': 'cross-chain-type', 'reason': f'与 {leader} 属于跨链同产品类型，采用同样的股票配对发行机制'},
            ]
            snapshot = {'status': 'ok', 'leaders': [{'symbol': leader, 'confidence': 98, 'family': '发射平台', 'narratives': ['股票配对发行'], 'reason': '产品主线', 'peers': peers}]}
            original = deepcopy(snapshot)
            tickers = {s: {'symbol': s, 'priceValue': 1, 'changeValue': 30, 'turnoverValue': 500000, 'exchange': 'Test'} for s in [leader, direct, derivative, 'WEAK', 'CROSS']}
            tickers[leader]['changeValue'] = 70000
            tickers['WEAK']['turnoverValue'] = 10**12
            tickers[derivative]['turnoverValue'] = 1000
            result = server.rotation_map_payload(market={'sources': []}, tickers=tickers,
                leader_metrics={leader: {'impulseGainPct': 70000}}, ai_snapshot=snapshot)
            candidates = next(m['candidates'] for m in result['maps'] if m['leader']['symbol'] == leader)
            self.assertEqual([c['symbol'] for c in candidates[:2]], [direct, derivative])
            scores = {c['symbol']: c['relevanceScore'] for c in candidates}
            self.assertGreater(scores[derivative], scores['CROSS'])
            self.assertGreater(scores['CROSS'], scores['WEAK'])
            self.assertEqual(snapshot, original)

    def test_relation_type_alone_cannot_invent_direct_benefit(self):
        score, _ = server.rotation_relation_relevance('LEADER', 'beneficiary', 'LEADER 走强使生态情绪上涨、被动受益')
        self.assertLess(score, 60)
        score, _ = server.rotation_relation_relevance('LEADER', 'family', 'LEADER 家族爸爸，关系尚未证实', '爸爸')
        self.assertLess(score, 60)
        score, _ = server.rotation_relation_relevance('LEADER', 'family', 'LEADER 真实动物家族中的爸爸', '爸爸')
        self.assertGreater(score, 85)

    def test_repeated_symbol_keeps_strongest_persisted_relationship(self):
        snapshot = {'leaders': [{'symbol': 'LEADER', 'confidence': 90, 'narratives': ['发射'], 'peers': [
            {'symbol': 'PEER', 'relationshipType': 'ecosystem', 'reason': '生态情绪外溢'},
            {'symbol': 'PEER', 'relationshipType': 'beneficiary', 'reason': 'PEER 通过 LEADER 平台发行，是首个股票映射标的'},
        ]}]}
        tickers = {s: {'symbol': s, 'priceValue': 1, 'changeValue': 0, 'turnoverValue': 1000000} for s in ['LEADER', 'PEER']}
        result = server.rotation_map_payload(market={'sources': []}, tickers=tickers, leader_metrics={}, ai_snapshot=snapshot)
        peers = result['maps'][0]['candidates']
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]['relevanceScore'], 96)
        self.assertEqual(len(peers[0]['semanticReasons']), 2)


if __name__ == '__main__': unittest.main()
