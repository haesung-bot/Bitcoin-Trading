import unittest

from autocommerce.models import RawProduct
from autocommerce.pricing.seo import SeoConfig, SeoEngine, is_size_token, normalize


def product(name, **kw):
    base = dict(source="fixture", source_product_id="X", source_url="u",
                brand="나이키", cost_price=10000,
                category_path=["신발", "운동화"])
    base.update(kw)
    return RawProduct(name=name, **base)


ENGINE = SeoEngine(SeoConfig(
    keyword_sets={"운동화": ["러닝화", "데일리운동화"]},
    banned_terms=["최저가", "정품", "무료배송"],
))


class TestSizeToken(unittest.TestCase):
    def test_shoe_sizes_detected(self):
        for t in ("250", "270", "330", "270mm", "280사이즈", "XL", "M"):
            self.assertTrue(is_size_token(t), t)

    def test_model_numbers_survive(self):
        """에어맥스 90, 뉴발란스 993 같은 모델 숫자를 사이즈로 오인하면 안 된다."""
        for t in ("90", "993", "1687", "530", "97"):
            self.assertFalse(is_size_token(t), t)


class TestTitle(unittest.TestCase):
    def test_banned_terms_removed(self):
        seo = ENGINE.build(product("나이키 최저가 에어포스 정품 운동화"))
        self.assertNotIn("최저가", seo.title)
        self.assertNotIn("정품", seo.title)
        self.assertIn("최저가", seo.dropped_terms)

    def test_promo_brackets_stripped(self):
        seo = ENGINE.build(product("[단독][특가] 나이키 에어포스1 운동화"))
        self.assertNotIn("[", seo.title)
        self.assertNotIn("단독", seo.title)

    def test_duplicate_words_collapsed(self):
        seo = ENGINE.build(product("나이키 나이키 에어포스 에어포스 운동화"))
        self.assertEqual(seo.title.count("나이키"), 1)
        self.assertEqual(seo.title.count("에어포스"), 1)

    def test_title_length_capped(self):
        cfg = SeoConfig(max_title_len=20, banned_terms=[])
        seo = SeoEngine(cfg).build(product("나이키 " + " ".join(f"단어{i}" for i in range(30))))
        self.assertLessEqual(len(seo.title), 20)

    def test_model_number_kept_in_title(self):
        seo = ENGINE.build(product("나이키 에어맥스 90 운동화 270"))
        self.assertIn("90", seo.title)
        self.assertNotIn("270", seo.title)


class TestTagsAndKeywords(unittest.TestCase):
    def test_tags_capped_and_deduped(self):
        seo = SeoEngine(SeoConfig(max_tags=3, keyword_sets={"운동화": ["a", "b", "c", "d"]})) \
            .build(product("나이키 운동화"))
        self.assertLessEqual(len(seo.tags), 3)
        self.assertEqual(len(seo.tags), len(set(seo.tags)))

    def test_keywords_combine_brand_and_category(self):
        seo = ENGINE.build(product("나이키 에어포스 운동화"))
        self.assertTrue(any("나이키" in k and "운동화" in k for k in seo.search_keywords))


class TestNormalize(unittest.TestCase):
    def test_fullwidth_normalized(self):
        self.assertEqual(normalize("ＮＩＫＥ"), "NIKE")

    def test_emoji_and_specials_removed(self):
        self.assertNotIn("★", normalize("나이키 ★신상★ 운동화"))


if __name__ == "__main__":
    unittest.main()
