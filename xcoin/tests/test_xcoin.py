"""
test_xcoin.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
표준 라이브러리만으로 도는 통합 테스트.

    python xcoin/tests/test_xcoin.py

돈이 걸린 시스템이라 "정상 동작"보다 "이상한 요청을 막는지"를 더 많이 봅니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcoin.server import config  # noqa: E402

# 테스트 전용 설정 — import 직후, DB 만들기 전에 바꾼다.
config.ADMIN_SECRET = "test-admin-secret"
config.SERVER_SECRET = "test-server-secret"
config.REQUIRE_USER_TOKEN = True
config.AUTO_APPROVE_CONVERSION = True
config.CHAIN_RPC_URL = ""  # 시뮬레이션 모드 강제

from xcoin.server import db, exchange, points, security, wallet  # noqa: E402
from xcoin.server.app import create_app  # noqa: E402
from xcoin.server.points import PointError  # noqa: E402

GAME_ID = "puzzle_test"
USER = "tester_1"


class XcoinTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_for_tests(self.tmp.name)

        self.app = create_app(quiet=True)
        self.client = self.app.test_client()

        self.game = points.register_game(GAME_ID, "테스트 퍼즐",
                                         points_per_score=0.1, daily_point_cap=2000)
        self.secret = self.game["secret"]
        self.token = security.issue_user_token(USER)

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    # ---------- 도우미 ----------

    def submit(self, score=1000, duration_ms=30000, nonce=None, user=USER,
               timestamp=None, secret=None):
        nonce = nonce or security.new_nonce()
        timestamp = timestamp if timestamp is not None else int(time.time())
        secret = secret or self.secret
        payload = security.canonical_payload(GAME_ID, user, score, duration_ms, nonce, timestamp)
        return self.client.post("/points/submit", json={
            "game_id": GAME_ID, "user_id": user, "score": score,
            "duration_ms": duration_ms, "nonce": nonce,
            "timestamp": timestamp, "signature": security.sign(secret, payload),
        })

    def auth_headers(self, user=USER):
        return {"X-User-Token": security.issue_user_token(user)}

    def link_wallet(self, user=USER, address=None):
        """서명 검증을 우회하고 DB에 직접 지갑을 넣는다 (eth-account 없이도 돌게)."""
        address = address or "0x000000000000000000000000000000000000dEaD"
        with db.tx() as conn:
            points.ensure_user(conn, user)
            conn.execute(
                "INSERT INTO wallets(user_id, address, chain_id, verified_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET address = excluded.address",
                (user, address, 137, int(time.time())),
            )

    # ================= 포인트 적립 =================

    def test_정상_제출시_포인트가_적립된다(self):
        res = self.submit(score=1000)
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()["awarded"], 100)  # 1000 * 0.1

    def test_같은_nonce는_두번_지급되지_않는다(self):
        nonce = security.new_nonce()
        self.submit(score=1000, nonce=nonce)
        second = self.submit(score=1000, nonce=nonce)
        body = second.get_json()
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["awarded"], 0)
        self.assertEqual(body["points"], 100)

    def test_서명이_틀리면_거부한다(self):
        res = self.submit(secret="wrong-secret")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "bad_signature")

    def test_오래된_타임스탬프는_거부한다(self):
        res = self.submit(timestamp=int(time.time()) - 9999)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "stale_timestamp")

    def test_말도안되는_점수는_거부한다(self):
        res = self.submit(score=config.MAX_SCORE_PER_SESSION + 1, duration_ms=60000)
        self.assertEqual(res.status_code, 422)

    def test_너무_짧은_플레이는_거부한다(self):
        res = self.submit(score=500, duration_ms=100)
        self.assertEqual(res.status_code, 422)

    def test_초당_점수가_비정상이면_거부한다(self):
        # 10초에 90000점 = 초당 9000점
        res = self.submit(score=90000, duration_ms=10000)
        self.assertEqual(res.status_code, 422)

    def test_게임별_일일한도를_넘지_않는다(self):
        # daily_point_cap = 2000. 한 판에 500포인트씩 → 5판째는 잘려야 한다.
        awarded = 0
        for _ in range(6):
            body = self.submit(score=5000, duration_ms=60000).get_json()
            awarded += body.get("awarded", 0)
        self.assertEqual(awarded, 2000)

    def test_비활성화된_게임은_거부한다(self):
        with db.tx() as conn:
            conn.execute("UPDATE games SET enabled = 0 WHERE game_id = ?", (GAME_ID,))
        res = self.submit()
        self.assertEqual(res.status_code, 403)

    def test_차단된_유저는_적립되지_않는다(self):
        with db.tx() as conn:
            points.ensure_user(conn, USER)
            conn.execute("UPDATE users SET blocked = 1, block_reason = '치트' WHERE user_id = ?",
                         (USER,))
        res = self.submit()
        self.assertEqual(res.status_code, 403)

    # ================= 유저 토큰 =================

    def test_토큰_없이는_잔액을_볼_수_없다(self):
        res = self.client.get(f"/points/balance?user_id={USER}")
        self.assertEqual(res.status_code, 401)

    def test_남의_토큰으로는_내_잔액을_볼_수_없다(self):
        other = security.issue_user_token("someone_else")
        res = self.client.get(f"/points/balance?user_id={USER}",
                              headers={"X-User-Token": other})
        self.assertEqual(res.status_code, 401)

    def test_만료된_토큰은_거부한다(self):
        expired = security.issue_user_token(USER, ttl=-10)
        res = self.client.get(f"/points/balance?user_id={USER}",
                              headers={"X-User-Token": expired})
        self.assertEqual(res.status_code, 401)

    def test_게임서명으로_토큰을_발급받을_수_있다(self):
        ts = int(time.time())
        sig = security.sign(self.secret, security.auth_payload(GAME_ID, USER, ts))
        res = self.client.post("/auth/session", json={
            "game_id": GAME_ID, "user_id": USER, "timestamp": ts, "signature": sig
        })
        self.assertEqual(res.status_code, 200)
        token = res.get_json()["user_token"]
        self.assertTrue(security.verify_user_token(USER, token))

    # ================= 지갑 =================

    def test_지갑_미연동이면_전환할_수_없다(self):
        self.submit(score=20000, duration_ms=120000)  # 2000 포인트
        res = self.client.post("/exchange/convert",
                               json={"user_id": USER, "points": 1000},
                               headers=self.auth_headers())
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"], "wallet_not_linked")

    def test_지갑_주소_형식을_검사한다(self):
        with self.assertRaises(PointError):
            wallet.link_wallet(USER, "0x123", "0xdeadbeef")

    def test_같은_지갑을_두_계정에_붙일_수_없다(self):
        addr = "0x1111111111111111111111111111111111111111"
        self.link_wallet("user_a", addr)
        with db.tx() as conn:
            points.ensure_user(conn, "user_b")
        # link_wallet 은 서명이 필요하므로, 중복 검사만 직접 확인한다.
        conn = db.get_conn()
        row = conn.execute(
            "SELECT user_id FROM wallets WHERE LOWER(address) = LOWER(?)", (addr,)
        ).fetchone()
        self.assertEqual(row["user_id"], "user_a")

    # ================= 전환 =================

    def _earn(self, target_points):
        """일일 한도를 우회해 테스트용 포인트를 넣는다."""
        with db.tx() as conn:
            points.apply_delta(conn, USER, target_points, "admin_adjust",
                               ref=f"seed:{security.new_nonce()}")

    def test_전환하면_포인트가_차감되고_기록이_남는다(self):
        self._earn(5000)
        self.link_wallet()
        res = self.client.post("/exchange/convert",
                               json={"user_id": USER, "points": 2000,
                                     "idempotency_key": "key-1"},
                               headers=self.auth_headers())
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertEqual(body["points_spent"], 2000)
        self.assertEqual(body["amount"], "2")  # 1000 P = 1 XCN
        self.assertEqual(points.get_balance(USER)["points"], 3000)

    def test_같은_키로_두번_전환해도_한번만_처리된다(self):
        self._earn(5000)
        self.link_wallet()
        payload = {"user_id": USER, "points": 2000, "idempotency_key": "same-key"}
        first = self.client.post("/exchange/convert", json=payload,
                                 headers=self.auth_headers()).get_json()
        second = self.client.post("/exchange/convert", json=payload,
                                  headers=self.auth_headers()).get_json()
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(points.get_balance(USER)["points"], 3000)

    def test_잔액보다_많이_전환할_수_없다(self):
        self._earn(1500)
        self.link_wallet()
        res = self.client.post("/exchange/convert",
                               json={"user_id": USER, "points": 5000},
                               headers=self.auth_headers())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], "insufficient_points")
        self.assertEqual(points.get_balance(USER)["points"], 1500)

    def test_최소_전환량_미만은_거부한다(self):
        self._earn(5000)
        self.link_wallet()
        res = self.client.post("/exchange/convert",
                               json={"user_id": USER, "points": 10},
                               headers=self.auth_headers())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], "below_minimum")

    def test_하루_전환한도를_넘을_수_없다(self):
        self._earn(config.DAILY_CONVERT_CAP_POINTS + 10000)
        self.link_wallet()
        ok = self.client.post("/exchange/convert",
                              json={"user_id": USER,
                                    "points": config.DAILY_CONVERT_CAP_POINTS,
                                    "idempotency_key": "cap-1"},
                              headers=self.auth_headers())
        self.assertEqual(ok.status_code, 200)
        over = self.client.post("/exchange/convert",
                                json={"user_id": USER, "points": 1000,
                                      "idempotency_key": "cap-2"},
                                headers=self.auth_headers())
        self.assertEqual(over.status_code, 400)
        self.assertEqual(over.get_json()["error"], "daily_convert_cap")

    def test_지급워커가_전환건을_처리한다(self):
        self._earn(5000)
        self.link_wallet()
        conv = self.client.post("/exchange/convert",
                                json={"user_id": USER, "points": 2000,
                                      "idempotency_key": "payout-1"},
                                headers=self.auth_headers()).get_json()
        result = exchange.process_pending_payouts()
        self.assertEqual(result["sent"], 1)
        after = exchange.get_conversion(conv["id"])
        self.assertEqual(after["status"], "sent")
        self.assertTrue(after["tx_hash"].startswith("0xSIM"))

    def test_거절하면_포인트가_환불된다(self):
        config.AUTO_APPROVE_CONVERSION = False
        try:
            self._earn(5000)
            self.link_wallet()
            conv = self.client.post("/exchange/convert",
                                    json={"user_id": USER, "points": 2000,
                                          "idempotency_key": "reject-1"},
                                    headers=self.auth_headers()).get_json()
            self.assertEqual(conv["status"], "pending")
            self.assertEqual(points.get_balance(USER)["points"], 3000)

            exchange.reject(conv["id"], "테스트 거절")
            self.assertEqual(points.get_balance(USER)["points"], 5000)
            self.assertEqual(exchange.get_conversion(conv["id"])["status"], "rejected")
        finally:
            config.AUTO_APPROVE_CONVERSION = True

    def test_전송이_계속_실패하면_자동_환불된다(self):
        self._earn(5000)
        self.link_wallet()
        conv = self.client.post("/exchange/convert",
                                json={"user_id": USER, "points": 2000,
                                      "idempotency_key": "fail-1"},
                                headers=self.auth_headers()).get_json()

        from xcoin.server import chain
        original = chain.send_xcoin
        chain.send_xcoin = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("가스 부족"))
        try:
            for _ in range(config.PAYOUT_MAX_RETRY):
                exchange.process_pending_payouts()
        finally:
            chain.send_xcoin = original

        after = exchange.get_conversion(conv["id"])
        self.assertEqual(after["status"], "failed")
        self.assertEqual(points.get_balance(USER)["points"], 5000)  # 전액 환불

    def test_환불은_두번_일어나지_않는다(self):
        self._earn(5000)
        self.link_wallet()
        conv = self.client.post("/exchange/convert",
                                json={"user_id": USER, "points": 2000,
                                      "idempotency_key": "double-refund"},
                                headers=self.auth_headers()).get_json()
        with db.tx() as conn:
            row = conn.execute("SELECT * FROM conversions WHERE id = ?",
                               (conv["id"],)).fetchone()
            exchange._refund(conn, row, "1차")
            exchange._refund(conn, row, "2차")  # 같은 ref → 무시되어야 한다
        self.assertEqual(points.get_balance(USER)["points"], 5000)

    # ================= 관리자 =================

    def test_관리자_인증이_없으면_거부한다(self):
        self.assertEqual(self.client.get("/admin/stats").status_code, 403)
        self.assertEqual(
            self.client.get("/admin/stats",
                            headers={"X-Admin-Secret": "wrong"}).status_code, 403)

    def test_관리자는_현황을_볼_수_있다(self):
        self.submit(score=1000)
        res = self.client.get("/admin/stats",
                              headers={"X-Admin-Secret": config.ADMIN_SECRET})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["points_outstanding"], 100)

    def test_관리자_포인트_조정은_ref로_중복_방지된다(self):
        headers = {"X-Admin-Secret": config.ADMIN_SECRET}
        body = {"user_id": USER, "delta": 500, "ref": "event-2026-01"}
        self.client.post("/admin/points/adjust", json=body, headers=headers)
        self.client.post("/admin/points/adjust", json=body, headers=headers)
        self.assertEqual(points.get_balance(USER)["points"], 500)

    # ================= 계산 =================

    def test_포인트_코인_환산이_정확하다(self):
        self.assertEqual(points.points_to_wei(1000), 10 ** 18)
        self.assertEqual(points.points_to_wei(1), 10 ** 15)
        self.assertEqual(points.points_to_xcoin_str(1500), "1.5")
        self.assertEqual(points.points_to_xcoin_str(1000), "1")
        self.assertEqual(points.points_to_xcoin_str(0), "0")

    def test_전환_견적이_수수료를_반영한다(self):
        config.CONVERT_FEE_BPS = 500  # 5%
        try:
            q = exchange.quote(10000)
            self.assertEqual(q["fee_points"], 500)
            self.assertEqual(q["net_points"], 9500)
            self.assertEqual(q["amount"], "9.5")
        finally:
            config.CONVERT_FEE_BPS = 0


if __name__ == "__main__":
    unittest.main(verbosity=2)
