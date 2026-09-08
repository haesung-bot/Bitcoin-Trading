"""파이프라인 통합 테스트 — 픽스처 소스로 4단계를 전부 통과시킨다."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from autocommerce import config as configmod
from autocommerce.pipeline import Pipeline

FIXTURE = [
    {
        "source_product_id": "OK1",
        "source_url": "https://shop.test/p?prdtNo=OK1",
        "name": "[특가] 아디다스 삼바 OG IE3438 운동화 최저가 270",
        "brand": "아디다스",
        "cost_price": 89000,
        "category_path": ["신발", "운동화"],
        "image_urls": [],
        "options": [{"name": "사이즈", "value": "260", "stock": 5},
                    {"name": "사이즈", "value": "270", "stock": 2}],
        "attributes": {"색상": "화이트", "제조사": "아디다스코리아", "원산지": "베트남"},
    },
    {
        "source_product_id": "BAD1",
        "source_url": "https://shop.test/p?prdtNo=BAD1",
        "name": "가격 파싱 실패",
        "brand": "테스트",
        "cost_price": 0,
        "image_urls": [],
        "options": [],
    },
]

SETTINGS = {
    "runtime": {"dry_run": True, "log_level": "CRITICAL"},
    "crawler": {"sources": {"fixture": {"enabled": True}}},
    "pricing": {
        "default_policy": "standard",
        "policies": {"standard": {
            "target_margin": 0.18, "vat_mode": "general",
            "outbound_shipping": 3000, "packaging": 500,
            "fees": {"naver": {"c": 0.0774}, "coupang": {"c": 0.128}},
            "rounding": {"mode": "charm", "unit": 1000, "ending": 900},
            "guards": {"min_price": 9900, "max_price": 900000,
                       "min_net_profit": 2000, "max_price_multiple": 3.0},
        }},
    },
    "seo": {"max_title_len": 100, "max_tags": 10,
            "banned_terms": ["최저가", "특가"],
            "keyword_sets": {"운동화": ["러닝화"]}},
    "imaging": {"enabled": False},   # 네트워크 없이 돌리기 위해 통과 모드
    "categories": {"map": {"운동화": {"naver": "50002380", "coupang": "63903"}}},
    "markets": {"naver": {"enabled": True}, "coupang": {"enabled": True}},
}


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        (root / "img").mkdir()
        # 픽스처 이미지 2장을 로컬 파일로 만들고 경로를 넣는다
        for name in ("m.png", "s.png"):
            (root / "img" / name).write_bytes(_tiny_png())
        data = json.loads(json.dumps(FIXTURE))
        data[0]["image_urls"] = [str(root / "img" / "m.png"),
                                 str(root / "img" / "s.png")]
        fixture_path = root / "fixture.json"
        fixture_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        cfg = json.loads(json.dumps(SETTINGS))
        cfg["runtime"]["workdir"] = str(root / "var")
        cfg["runtime"]["db_path"] = str(root / "var" / "t.db")
        cfg["crawler"]["sources"]["fixture"]["path"] = str(fixture_path)
        self.settings = configmod.Settings(raw=cfg)
        self.pipe = Pipeline(self.settings)

    def tearDown(self):
        self.pipe.close()
        self.dir.cleanup()


class TestEndToEnd(PipelineCase):
    def test_dry_run_registers_both_markets(self):
        stats = self.pipe.run("fixture", ["naver", "coupang"])
        self.assertEqual(stats.crawled, 1)      # BAD1 은 검증에서 탈락
        self.assertEqual(stats.listed, 2)       # 1상품 x 2마켓
        self.assertEqual(stats.failed, 0)
        self.assertEqual(stats.held, 0)

    def test_payload_reflects_pricing_and_seo(self):
        self.pipe.run("fixture", ["naver"])
        row = self.pipe.store.recent_listings(1)[0]
        payload = json.loads(row["request_json"])
        origin = payload["originProduct"]

        # 마진: 원가 89000 + 3500 = 92500, 수수료 7.74%, 목표 18%
        expected = 92500 / (1 - 0.0774 - 1.1 * 0.18)
        self.assertGreaterEqual(origin["salePrice"], expected)
        self.assertEqual(origin["salePrice"] % 1000, 900)

        # SEO: 금칙어 제거 + 사이즈 토큰 제거
        self.assertNotIn("최저가", origin["name"])
        self.assertNotIn("특가", origin["name"])
        self.assertNotIn("270", origin["name"])
        self.assertIn("아디다스", origin["name"])

        self.assertEqual(origin["leafCategoryId"], "50002380")

    def test_second_run_reuses_state(self):
        first = self.pipe.run("fixture", ["naver"])
        second = self.pipe.run("fixture", ["naver"])
        self.assertEqual(first.crawled, second.crawled)
        self.assertEqual(self.pipe.store.counts_by_stage().get("failed", 0), 0)

    def test_options_carried_to_both_markets(self):
        self.pipe.run("fixture", ["naver", "coupang"])
        by_market = {r["market"]: json.loads(r["request_json"])
                     for r in self.pipe.store.recent_listings(2)}
        naver_opts = by_market["naver"]["originProduct"]["detailAttribute"][
            "optionInfo"]["optionCombinations"]
        self.assertEqual([o["optionName1"] for o in naver_opts], ["260", "270"])
        self.assertEqual(len(by_market["coupang"]["items"]), 2)


class TestHoldGate(PipelineCase):
    def test_negative_margin_product_is_held_not_listed(self):
        """수수료+배송비로 역마진이 나는 저가 상품은 등록을 막아야 한다."""
        self.settings.raw["pricing"]["policies"]["standard"]["guards"][
            "min_net_profit"] = 100000
        pipe = Pipeline(self.settings)
        try:
            stats = pipe.run("fixture", ["naver"], force=True)
            self.assertEqual(stats.listed, 0)
            self.assertEqual(stats.held, 1)
            self.assertIn("보류", pipe.store.recent_listings(1)[0]["error"])
        finally:
            pipe.close()

    def test_allow_warnings_overrides_hold(self):
        self.settings.raw["pricing"]["policies"]["standard"]["guards"][
            "min_net_profit"] = 100000
        pipe = Pipeline(self.settings)
        try:
            stats = pipe.run("fixture", ["naver"], force=True, allow_warnings=True)
            self.assertEqual(stats.listed, 1)
            self.assertEqual(stats.held, 0)
        finally:
            pipe.close()


class TestDryRunSafety(PipelineCase):
    def test_dry_run_never_marks_listed_stage(self):
        self.pipe.run("fixture", ["naver"])
        self.assertNotIn("listed", self.pipe.store.counts_by_stage())

    def test_dry_run_flag_recorded_on_every_listing(self):
        self.pipe.run("fixture", ["naver", "coupang"])
        self.assertTrue(all(r["dry_run"] for r in self.pipe.store.recent_listings(10)))


def _tiny_png() -> bytes:
    import struct
    import zlib

    w = h = 8
    raw = b"".join(b"\x00" + bytes((220, 220, 225)) * w for _ in range(h))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    unittest.main()
