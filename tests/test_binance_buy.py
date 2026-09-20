import copy
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import binance_buy as b

OWNER = "0x" + "12"*20
TOKEN = "0x" + "34"*20


def fixture(intent=None, stable=False):
    if intent is None:
        source = {"chainId":8453, "address":TOKEN if stable else b.ZERO, "decimals":6 if stable else 18,
                  "symbol":"USDC" if stable else "ETH", "owner":OWNER, "priceUsd":"1" if stable else "2000",
                  "balanceUsd":"1000", "balance":"100000000000000000000", "nativeBalance":"1000000000000000000", "chainLabel":"Base"}
        target = {"chainId":8453,"address":"0x"+"ab"*20,"symbol":"TEST","decimals":6}
        intent = {"source":source,"target":target,"sender":OWNER,"recipient":OWNER,"amount":"10000000" if stable else "5000000000000000",
                  "amountUsd":"10","amountUsdt":"10","slippageBps":500}
    intent = copy.deepcopy(intent)
    source, target = intent["source"], intent["target"]
    native = source["address"] == b.ZERO
    raw = {"executionMode":"SWAP","rfq":None,"routerResult":{
        "binanceChainId":str(source["chainId"]),"vendorName":"LiquidMesh","fromTokenAmount":intent["amount"],
        "toTokenAmount":"10000001","priceImpactPercent":"0.1","tradeFee":"0.01",
        "fromToken":{"tokenContractAddress":b.NATIVE if native else source["address"],"decimal":str(source["decimals"]),
                      "tokenUnitPrice":source["priceUsd"],"isHoneyPot":False,"taxRate":"0"},
        "toToken":{"tokenContractAddress":target["address"],"decimal":str(target["decimals"]),"tokenUnitPrice":"1","taxRate":"0","isHoneyPot":False},
        "dexRouterList":[{"dexName":"Uniswap V4"},{"dexName":"PancakeSwap V2"}]},
        "tx":{"from":intent["sender"],"to":b.ROUTER,"value":intent["amount"] if native else "0",
              "gas":"200000","gasPrice":"1000000","minReceiveAmount":"9500000","slippagePercent":"5","signatureData":None}}
    words = [1,0,0,int(source["address"],16),int(intent["amount"]),int(target["address"],16),9500000,0,10000001,320,4]
    raw["tx"]["data"] = b.SELECTOR + "".join(format(x,"064x") for x in words) + "deadbeef".ljust(64,"0")
    quote = b.normalize(raw,intent,[] if native else [b.approval(intent,int(intent["amount"]))])
    return quote,intent


def simulation(intent):
    return {"status":"SUCCESS","failReason":"","balanceChanges":[
        {"owner":intent["sender"],"contractAddress":b.NATIVE if intent["source"]["address"] == b.ZERO else intent["source"]["address"],"change":"-"+intent["amount"]},
        {"owner":intent["recipient"],"contractAddress":intent["target"]["address"],"change":"10000001"}],"allowanceChanges":[]}


class BinanceBuyTests(unittest.TestCase):
    def test_auto_slippage_20_cap_and_original_lower_limits_preserved(self):
        q,i=fixture();i.update(autoSlippage=True,slippageBps=2000)
        client=Mock();client.request.side_effect=[q['raw'],simulation(i)]
        adapter=b.BinanceBuyAdapter(client,Mock());adapter.verify_router=Mock()
        result=adapter.build(i,{})
        params=client.request.call_args_list[0].args[2]
        self.assertEqual(params['autoSlippage'],'true')
        self.assertEqual(params['maxAutoSlippagePercent'],'20')
        self.assertNotIn('slippagePercent', params, 'Live API rejects simultaneous auto + fixed slippage')
        self.assertGreater(result['_simulatedAt'], time.time()-5)
        self.assertIn('maxAmount',result['fees']['gas'])
        q['raw']['tx']['slippagePercent']='20.01'
        with self.assertRaises(ValueError): b.validate_raw(q['raw'],i)
        q['raw']['tx']['slippagePercent']='6'
        i['slippageBps']=500
        with self.assertRaises(ValueError): b.validate_raw(q['raw'],i)

    def test_native_and_exact_stable_approval(self):
        for stable in (False,True):
            q,i = fixture(stable=stable)
            self.assertIs(b.validate_quote(q,i),q)
            self.assertTrue(b.validate_simulation(simulation(i),i,"9500000"))

    def test_execution_summary_distinguishes_spender_target_and_recipient(self):
        q, i = fixture(stable=True)
        summary = q["execution"]
        self.assertEqual(summary["approval"]["spender"], b.ROUTER)
        self.assertEqual(summary["approval"]["amount"], i["amount"])
        self.assertTrue(summary["approval"]["exactAmount"])
        self.assertEqual(summary["targetAddress"], i["target"]["address"])
        self.assertEqual(summary["recipient"], i["recipient"])
        self.assertNotEqual(summary["approval"]["spender"], summary["targetAddress"])
        self.assertEqual(summary["dexes"], ["Uniswap V4", "PancakeSwap V2"])

    def test_quote_and_calldata_tampering_fail_closed(self):
        edits = [lambda q:q['raw'].update(executionMode='RFQ'),lambda q:q['raw'].update(rfq={'x':1}),
            lambda q:q['raw']['tx'].update(signatureData=['sign']), lambda q:q['raw']['tx'].update(to=TOKEN),
            lambda q:q['raw']['tx'].update({'from':TOKEN}),lambda q:q['raw']['tx'].update(slippagePercent='500'),
            lambda q:q['raw']['tx'].update(slippagePercent='NaN'),lambda q:q['raw']['tx'].update(minReceiveAmount='1'),
            lambda q:q['raw']['tx'].update(value='1'),lambda q:q['raw']['routerResult'].update(binanceChainId='56'),
            lambda q:q['raw']['routerResult']['toToken'].update(tokenContractAddress=TOKEN),
            lambda q:q['raw']['routerResult'].update(priceImpactPercent='4'),
            lambda q:q['raw']['routerResult']['toToken'].update(isHoneyPot=True),
            lambda q:q['raw']['routerResult'].update(feeAmount='1'),
            lambda q:q['raw']['tx'].update(data=q['raw']['tx']['data'][:394]+format(1,'064x')+q['raw']['tx']['data'][458:]),
            lambda q:q['steps'][-1]['items'][0]['data'].update(data='0xdeadbeef'),
            lambda q:q['steps'].append(copy.deepcopy(q['steps'][0])),lambda q:q['details']['currencyOut'].update(minimumAmount='1'),
            lambda q:q['execution']['approval'].update(spender=TOKEN)]
        for edit in edits:
            q,i=fixture();edit(q)
            with self.subTest(edit=edit),self.assertRaises(ValueError):b.validate_quote(q,i)
        q,i=fixture(stable=True)
        q['steps'][0]['items'][0]['data']['data']='0x095ea7b3'+b.ROUTER[2:].rjust(64,'0')+'f'*64
        with self.assertRaises(ValueError):b.validate_quote(q,i)

    def test_simulation_requires_exact_own_funds_and_real_minimum(self):
        _,i=fixture()
        edits=[lambda s:s.update(status='FAILED'),lambda s:s.update(failReason='revert'),
               lambda s:s.pop('allowanceChanges'),lambda s:s['balanceChanges'][1].update(owner=TOKEN),
               lambda s:s['balanceChanges'][1].update(change='1'),lambda s:s['balanceChanges'][0].update(change='-1'),
               lambda s:s['balanceChanges'].append({'owner':OWNER,'contractAddress':TOKEN,'change':'-1'}),
               lambda s:s['allowanceChanges'].append({'owner':OWNER,'tokenAddress':TOKEN,'spender':b.ROUTER,'preAmount':'0','postAmount':'999'})]
        for edit in edits:
            sim=simulation(i);edit(sim)
            with self.subTest(edit=edit),self.assertRaises(ValueError):b.validate_simulation(sim,i,'9500000')

    def test_flash_parameters_and_simulation_no_broadcast(self):
        q,i=fixture();client=Mock();client.request.side_effect=[q['raw'],simulation(i)]
        adapter=b.BinanceBuyAdapter(client,Mock());adapter.verify_router=Mock()
        result=adapter.build(i,{})
        params=client.request.call_args_list[0].args[2]
        self.assertEqual(params['slippagePercent'],'5')
        self.assertEqual(params['autoSlippage'],'false')
        self.assertNotIn('maxAutoSlippagePercent', params)
        self.assertEqual(params['approveTransaction'],'false')
        self.assertEqual(params['gasLevel'],'fast')
        self.assertEqual(params['priceImpactProtectionPercent'],'3')
        self.assertEqual(params['fromTokenAddress'],b.NATIVE)
        self.assertEqual(result['provider'],b.PROVIDER)
        self.assertGreaterEqual(result['_expires'] - time.time(), b.QUOTE_TTL_SECONDS - 1)
        self.assertEqual(client.request.call_args_list[1].args[:2],('POST','/api/v1/dex/pre-transaction/simulate'))

    def test_independent_chain_simulation_uses_exact_swap_and_never_broadcasts(self):
        quote, intent = fixture()
        rpc = Mock(return_value="0x")
        adapter = b.BinanceBuyAdapter(Mock(), rpc)
        self.assertTrue(adapter.chain_simulate(quote, intent, {"id": 8453}))
        method, params = rpc.call_args.args[1:]
        self.assertEqual(method, "eth_call")
        self.assertEqual(params[0]["to"], b.ROUTER)
        self.assertEqual(params[0]["data"], quote["steps"][-1]["items"][0]["data"]["data"])
        self.assertEqual(params[1], "pending")
        rpc.return_value = None
        with self.assertRaisesRegex(ValueError, "链上试运行"):
            adapter.chain_simulate(quote, intent, {"id": 8453})

    def test_allowance_reset_and_unsupported_chain(self):
        q,i=fixture(stable=True);client=Mock();client.request.return_value=q['raw']
        adapter=b.BinanceBuyAdapter(client,Mock(return_value='0x1'));adapter.verify_router=Mock()
        result=adapter.build(i,{})
        self.assertEqual(len(result['steps']),3)
        self.assertEqual(int(result['steps'][0]['items'][0]['data']['data'][-64:],16),0)
        self.assertEqual(int(result['steps'][1]['items'][0]['data']['data'][-64:],16),int(i['amount']))
        self.assertEqual(client.request.call_count,1,'No pre-approval simulation can pass a missing allowance')
        i['source']['chainId']=792703809
        with self.assertRaises(ValueError):adapter.build(i,{})

    def test_session_mode_never_reuses_an_existing_allowance(self):
        q,i=fixture(stable=True);i['forceExactApproval']=True
        client=Mock();client.request.return_value=q['raw']
        adapter=b.BinanceBuyAdapter(client,Mock(return_value=hex(int(i['amount'])*2)));adapter.verify_router=Mock()
        result=adapter.build(i,{})
        self.assertEqual([int(step['items'][0]['data']['data'][-64:],16) for step in result['steps'][:-1]],
                         [0,int(i['amount'])])
        self.assertEqual(result['steps'][-1]['id'],'swap')

    def test_router_upgrade_rejected(self):
        rpc=Mock(return_value='0x'+'0'*64)
        adapter=b.BinanceBuyAdapter(Mock(),rpc)
        with self.assertRaises(ValueError):adapter.verify_router({})
        rpc.side_effect=['0x'+b.FACET[2:].rjust(64,'0'),'0x1234']
        with self.assertRaises(ValueError):adapter.verify_router({})

    def test_receipt_is_not_success_until_target_received(self):
        q,i=fixture();tx=q['steps'][-1]['items'][0]['data'];h='0x'+'ab'*32
        transaction={'from':OWNER,'to':tx['to'],'input':tx['data'],'value':hex(int(tx['value'])), 'blockHash':'0x'+'cd'*32}
        receipt={'transactionHash':h,'blockHash':transaction['blockHash'],'status':'0x1','logs':[{'address':i['target']['address'],'topics':[b.TRANSFER,'0x'+'0'*64,'0x'+OWNER[2:].rjust(64,'0')],'data':hex(10000001)}]}
        record={'quote':q,'intent':i,'txHashes':[h]}
        rpc=Mock(side_effect=[transaction,receipt]);adapter=b.BinanceBuyAdapter(Mock(),rpc)
        self.assertEqual(adapter.status(record,{})['executionStatus'],'success')
        rpc.side_effect=[transaction,{**receipt,'logs':[]}]
        self.assertEqual(adapter.status(record,{})['executionStatus'],'settlement-unverified')
        rpc.side_effect=[{**transaction,'to':TOKEN,'input':'0x095ea7b3'}]
        self.assertEqual(adapter.status(record,{})['executionStatus'],'waiting')
        rpc.side_effect=[transaction,{**receipt,'status':'0x0'}]
        self.assertEqual(adapter.status(record,{})['executionStatus'],'failure')
        rpc.side_effect=[transaction,{**receipt,'blockHash':'0x'+'ef'*32}]
        self.assertEqual(adapter.status(record,{})['executionStatus'],'settlement-unverified')
        q,i=fixture(stable=True);approve=q['steps'][0]['items'][0]['data']
        rpc.side_effect=[{'from':OWNER,'to':approve['to'],'input':approve['data'],'value':'0x0'}]
        self.assertEqual(adapter.status({'quote':q,'intent':i,'txHashes':[h]}, {})['executionStatus'],'approval-only')


if __name__=='__main__':unittest.main()
