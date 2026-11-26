"""RootData 机构投资榜：签名请求 / 解析 / 快照 → 榜单行。

这个模块有三处「一旦写错就会静默出错」的地方，测试就钉这三处：
1. **签名口径**：body 用 RSA 加密、再对 ``md5("content=" + content)`` 做 RSA-SHA1
   签名。漏掉 md5 这一层，服务端回的是 ``result:111 签名错误``（HTTP 仍 200），
   写错也看不出来。
2. **发币状态**：机构页 SSR 里**没有**这个字段，只能靠接口的 ``lssuingStatus``。
   行里必须明确写 ``tokenState=noToken``，不许由前端猜。
3. **失败不许伪装成空榜**：没有快照 / 接口挂了要带 ``status`` 和错误原因，
   绝不能渲染成「0 个未发币项目」这种看起来正常的结果。
"""

import base64
import hashlib
import time
import unittest
from unittest import mock

import server
import rootdata_investor as ri


def snapshot_entry(items):
    now = int(time.time())
    return {
        "updatedAt": now,
        "investors": {
            "yzi-labs": {
                "id": 229,
                "label": "YZi Labs",
                "slug": "YZi Labs",
                "updatedAt": now,
                "total": len(items),
                "items": items,
                "error": "",
            }
        },
    }


class SignedRequestTests(unittest.TestCase):
    def test_body_carries_one_rsa_block_plus_a_signature(self):
        payload = {"id": 229, "page": 1, "limit": 500, "lssuingStatus": 0, "yyStatus": 1}
        body = ri.signed_body(payload)
        self.assertEqual(set(body), {"content", "sign"})
        # 加密用的是**服务端公钥**（私钥不在我们手里，所以没法在本地解回来），
        # 只能验它是一整个 1024 位 RSA 块；明文大小由下面那条签名校验兜住。
        self.assertEqual(len(base64.b64decode(body["content"])), 128)
        self.assertEqual(len(base64.b64decode(body["sign"])), 128)

    def test_signature_verifies_against_the_embedded_keypair(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        body = ri.signed_body({"id": 229})
        # 签名用的是 chunk 里那对「私钥 Z」——公钥可以由它导出，所以这一半能自证。
        private_key = serialization.load_der_private_key(
            base64.b64decode(ri._RSA_PRIVATE_B64), password=None
        )
        public_key = private_key.public_key()
        digest = hashlib.md5(f"content={body['content']}".encode()).hexdigest()
        public_key.verify(
            base64.b64decode(body["sign"]), digest.encode(), padding.PKCS1v15(), hashes.SHA1()
        )

    def test_signature_is_over_the_md5_digest_not_the_raw_string(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        body = ri.signed_body({"id": 229})
        public_key = serialization.load_der_private_key(
            base64.b64decode(ri._RSA_PRIVATE_B64), password=None
        ).public_key()
        raw = f"content={body['content']}".encode()
        with self.assertRaises(Exception):
            public_key.verify(base64.b64decode(body["sign"]), raw, padding.PKCS1v15(), hashes.SHA1())


class ParsingTests(unittest.TestCase):
    def test_i18n_prefers_chinese_and_tolerates_plain_strings(self):
        self.assertEqual(ri._i18n('{"en_value":"Hello","cn_value":"你好"}'), "你好")
        self.assertEqual(ri._i18n('{"tw_value":"你好","en_value":"Hello"}'), "你好")
        self.assertEqual(ri._i18n("纯文本项目名"), "纯文本项目名")
        self.assertEqual(ri._i18n(None), "")

    def test_detail_url_matches_the_frontend_rule(self):
        # 前端：slug = name.replace(/\//g,"-").replace(/\./g,"")，再 base64(itemId) 后 URL 编码。
        self.assertEqual(
            ri.detail_url(2797, "A/B.C"),
            "https://cn.rootdata.com/projects/detail/A-BC?k=Mjc5Nw%3D%3D",
        )
        self.assertEqual(
            ri.detail_url(229, "YZi Labs"),
            "https://cn.rootdata.com/projects/detail/YZi%20Labs?k=MjI5",
        )
        self.assertEqual(ri.detail_url(0, "x"), "")

    def test_thumb_only_rewrites_real_urls(self):
        self.assertEqual(
            ri._thumb("https://public.rootdata.com/images/b6/1.jpg"),
            "https://public.rootdata.com/images/b6/1_128x128.webp",
        )
        self.assertEqual(ri._thumb(""), "")
        self.assertEqual(ri._thumb("not-a-url"), "")

    def test_normalize_item_keeps_operate_status_and_marks_not_acquired(self):
        item = ri._normalize_item({
            "itemId": 5459,
            "itemName": '{"cn_value":"42","en_value":"42"}',
            "briefIntd": '{"cn_value":"链上预测协议"}',
            "imgUrl": "https://public.rootdata.com/images/b/1.png",
            "operateStatus": 1,
        })
        self.assertEqual(item["name"], "42")
        self.assertEqual(item["brief"], "链上预测协议")
        self.assertEqual(item["operateLabel"], "运营中")
        self.assertFalse(item["acquired"])
        self.assertTrue(item["url"].startswith("https://cn.rootdata.com/projects/detail/42?k="))
        self.assertEqual(item["icon"], "https://public.rootdata.com/images/b/1_128x128.webp")


class BoardRowTests(unittest.TestCase):
    def test_rows_come_from_the_snapshot_and_say_not_issued(self):
        items = [
            {"itemId": 5459, "name": "42", "brief": "链上预测协议", "icon": "",
             "iconCandidates": [], "operateStatus": 1, "operateLabel": "运营中",
             "url": "https://cn.rootdata.com/projects/detail/42?k=NTQ1OQ%3D%3D", "acquired": False},
            {"itemId": 1, "name": "Old", "brief": "被收购的", "icon": "https://x/i.png",
             "iconCandidates": ["https://x/i.png"], "operateStatus": 1, "operateLabel": "运营中",
             "url": "https://cn.rootdata.com/projects/detail/Old?k=MQ%3D%3D", "acquired": True},
        ]
        with mock.patch.object(ri, "read_snapshot", return_value=snapshot_entry(items)):
            board = ri.board_rows("yzi-labs", limit=10)
        self.assertEqual(board["status"], "ok")
        self.assertEqual(board["total"], 2)
        self.assertEqual([row["symbol"] for row in board["rows"]], ["42", "Old"])
        first = board["rows"][0]
        self.assertEqual(first["turnover"], "未发行")
        self.assertEqual(first["tokenState"], "noToken")
        self.assertEqual(first["note"], "运营中")
        self.assertEqual(first["rank"], 1)
        self.assertIn("已被收购", board["rows"][1]["note"])

    def test_rows_are_sent_whole_so_the_card_can_scroll(self):
        # 「只露 10 行」是前端窗口的事；服务端截断就滑不到后面的人了。
        items = [
            {"itemId": i, "name": f"P{i}", "brief": "", "icon": "", "iconCandidates": [],
             "operateStatus": 1, "operateLabel": "运营中", "url": "", "acquired": False}
            for i in range(1, 26)
        ]
        with mock.patch.object(ri, "read_snapshot", return_value=snapshot_entry(items)):
            board = ri.board_rows("yzi-labs")
        self.assertEqual(board["total"], 25)
        self.assertEqual(len(board["rows"]), 25, "默认必须给全量，不许截到 10")
        self.assertGreaterEqual(ri.ROW_LIMIT, 25, "上限不能比实际条数小")

    def test_missing_snapshot_is_unavailable_not_empty(self):
        with mock.patch.object(ri, "read_snapshot", return_value={}):
            board = ri.board_rows("yzi-labs")
        self.assertEqual(board["rows"], [])
        self.assertEqual(board["status"], "unavailable")

    def test_unknown_investor_is_rejected(self):
        board = ri.board_rows("nobody")
        self.assertEqual(board["rows"], [])
        self.assertEqual(board["status"], "unavailable")


class ServerAttachmentTests(unittest.TestCase):
    """``attach_rootdata_investors`` 是 app.js 下拉框唯一的数据来源。"""

    def test_options_and_boards_are_attached(self):
        source = {"id": "rootdata-projects", "rows": [{"symbol": "AAA"}]}
        board = {
            "investor": "yzi-labs",
            "label": "YZi Labs",
            "rows": [{"symbol": "BitGo"}],
            "total": 82,
            "status": "ok",
        }
        with mock.patch.object(server.ROOTDATA_INVESTOR, "board_rows", return_value=board), \
                mock.patch.object(server.ROOTDATA_INVESTOR, "refresh_async") as refresh:
            out = server.attach_rootdata_investors(source)
        self.assertEqual(out["investor"], "")
        self.assertEqual(out["investorOptions"][0]["value"], "")
        self.assertIn("未发币 82", out["investorOptions"][1]["label"])
        self.assertEqual(out["investorBoards"][0]["rows"][0]["symbol"], "BitGo")
        # 快照新鲜时不该去撞接口。
        refresh.assert_not_called()

    def test_stale_board_triggers_a_background_refresh(self):
        board = {"investor": "yzi-labs", "label": "YZi Labs", "rows": [{"symbol": "X"}],
                 "total": 82, "status": "stale"}
        with mock.patch.object(server.ROOTDATA_INVESTOR, "board_rows", return_value=board), \
                mock.patch.object(server.ROOTDATA_INVESTOR, "refresh_async") as refresh:
            out = server.attach_rootdata_investors({"id": "rootdata-projects", "rows": []})
        refresh.assert_called_once_with("yzi-labs")
        # 旧行照发，别把卡片清空。
        self.assertEqual(out["investorBoards"][0]["rows"][0]["symbol"], "X")


if __name__ == "__main__":
    unittest.main()
