import unittest

from autocommerce.crawlers.abcmart import (
    AbcMartCrawler, parse_price, select, select_one,
)
from autocommerce.crawlers.base import BaseCrawler, CrawlReport
from autocommerce.models import RawProduct

JSONLD_PAGE = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"아디다스 삼바 OG",
 "sku":"IE3438","brand":{"name":"아디다스"},
 "image":["https://cdn.test/a.jpg","https://cdn.test/b.jpg"],
 "offers":{"@type":"Offer","price":"139000","highPrice":"159000",
           "priceCurrency":"KRW"}}
</script></head><body>
<ul><li class="bc">신발</li><li class="bc">스니커즈</li></ul>
<div class="opt">250</div><div class="opt">260</div>
<img class="thumb" src="/img/1.jpg"/>
</body></html>"""

NO_LD_PAGE = """<html><body>
<h1 class="prd-name">뉴발란스 530 스틸그레이</h1>
<span class="price">109,000원</span>
<img class="thumb" src="/img/nb.jpg"/>
<li class="bc">신발</li>
</body></html>"""


def crawler(selectors=None):
    return AbcMartCrawler({"selectors": selectors or {}}, None, None)


class TestMiniSelector(unittest.TestCase):
    def test_class_text(self):
        self.assertEqual(select(JSONLD_PAGE, "li.bc"), ["신발", "스니커즈"])

    def test_attribute_on_self_closing_tag(self):
        self.assertEqual(select(JSONLD_PAGE, "img.thumb", attr="src"), ["/img/1.jpg"])

    def test_missing_selector_returns_empty(self):
        self.assertEqual(select(JSONLD_PAGE, "div.nope"), [])
        self.assertIsNone(select_one(JSONLD_PAGE, ""))


class TestPriceParsing(unittest.TestCase):
    def test_comma_and_suffix(self):
        self.assertEqual(parse_price("109,000원"), 109000)
        self.assertEqual(parse_price("판매가 89,000"), 89000)

    def test_garbage_is_zero(self):
        self.assertEqual(parse_price("품절"), 0)
        self.assertEqual(parse_price(None), 0)


class TestJsonLdPreferred(unittest.TestCase):
    """JSON-LD 는 CSS 셀렉터보다 사이트 개편에 강해서 1순위여야 한다."""

    def test_parses_from_structured_data(self):
        p = crawler({"breadcrumb": "li.bc", "option": "div.opt"}).parse(
            "https://s.test/p?prdtNo=Z1", JSONLD_PAGE)
        self.assertEqual(p.source_product_id, "IE3438")   # URL 이 아니라 sku
        self.assertEqual(p.brand, "아디다스")
        self.assertEqual(p.cost_price, 139000)
        self.assertEqual(p.list_price, 159000)
        self.assertEqual(p.image_urls, ["https://cdn.test/a.jpg",
                                        "https://cdn.test/b.jpg"])
        self.assertEqual([o.value for o in p.options], ["250", "260"])

    def test_falls_back_to_selectors(self):
        p = crawler({"name": "h1.prd-name", "price": "span.price",
                     "image": "img.thumb", "breadcrumb": "li.bc"}).parse(
            "https://s.test/p?prdtNo=MR530", NO_LD_PAGE)
        self.assertEqual(p.name, "뉴발란스 530 스틸그레이")
        self.assertEqual(p.cost_price, 109000)
        self.assertEqual(p.source_product_id, "MR530")   # URL 파라미터에서 추출
        # 상대경로 이미지는 목록 base_url 이 아니라 상세페이지 URL 기준으로 절대화된다
        self.assertEqual(p.image_urls, ["https://s.test/img/nb.jpg"])


class _Stub(BaseCrawler):
    source = "stub"

    def __init__(self, products):
        self.products = products
        self.cfg, self.http, self.robots = {}, None, None
        self.report = CrawlReport(source=self.source)

    def discover(self, **params):
        return [p.source_url for p in self.products]

    def fetch(self, url):
        return url

    def parse(self, url, body):
        return next(p for p in self.products if p.source_url == url)


def make(pid, cost, images=("i",), name="상품"):
    return RawProduct(source="stub", source_product_id=pid,
                      source_url=f"https://x.test/{pid}", name=name, brand="B",
                      cost_price=cost, image_urls=list(images))


class TestValidationGate(unittest.TestCase):
    def test_bad_rows_dropped_batch_survives(self):
        """한 건이 깨져도 나머지는 계속 수집되어야 한다."""
        c = _Stub([make("ok1", 50000), make("zero", 0),
                   make("noimg", 50000, images=()), make("noname", 50000, name=" "),
                   make("ok2", 60000)])
        got = list(c.crawl())
        self.assertEqual([p.source_product_id for p in got], ["ok1", "ok2"])
        self.assertEqual(c.report.parsed, 2)
        self.assertEqual(len(c.report.failed), 3)

    def test_limit_applies(self):
        c = _Stub([make(f"p{i}", 10000) for i in range(10)])
        self.assertEqual(len(list(c.crawl(limit=3))), 3)


class TestParseFailureIsolation(unittest.TestCase):
    def test_exception_in_parse_does_not_kill_batch(self):
        class Boom(_Stub):
            def parse(self, url, body):
                if url.endswith("bad"):
                    raise ValueError("DOM 구조 변경")
                return super().parse(url, body)

        c = Boom([make("ok", 50000), make("bad", 50000)])
        got = list(c.crawl())
        self.assertEqual(len(got), 1)
        self.assertIn("DOM 구조 변경", c.report.failed[0][1])


if __name__ == "__main__":
    unittest.main()
