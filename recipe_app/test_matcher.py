# -*- coding: utf-8 -*-
"""매칭 엔진 테스트.

실행: python test_matcher.py
(표준 라이브러리만 사용하므로 Flask 없이도 돌아갑니다)
"""

import unittest

import matcher
from ingredients import PANTRY_STAPLES, known_ingredients, normalize, parse_input

RECIPES = matcher.load_recipes()


class TestNormalize(unittest.TestCase):
    def test_alias_to_canonical(self):
        self.assertEqual(normalize("달걀"), "계란")
        self.assertEqual(normalize("삼겹살"), "돼지고기")
        self.assertEqual(normalize("스팸"), "햄")
        self.assertEqual(normalize("오뎅"), "어묵")

    def test_strips_amount_and_space(self):
        self.assertEqual(normalize("계란 2개"), "계란")
        self.assertEqual(normalize("돼지고기 300g"), "돼지고기")
        self.assertEqual(normalize("밥 1공기"), "밥")
        self.assertEqual(normalize(" 대 파 "), "대파")

    def test_strips_parentheses(self):
        self.assertEqual(normalize("미역(마른미역)"), "미역")
        self.assertEqual(normalize("소고기(불고기용)"), "소고기")

    def test_partial_match(self):
        self.assertEqual(normalize("냉동새우"), "새우")
        self.assertEqual(normalize("볶음용돼지고기"), "돼지고기")

    def test_unknown_ingredient_kept(self):
        self.assertEqual(normalize("트러플오일"), "트러플오일")

    def test_empty_input(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(None), "")
        self.assertEqual(normalize("500g"), "")


class TestParseInput(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(parse_input("김치, 밥, 계란"), ["김치", "밥", "계란"])

    def test_mixed_separators(self):
        got = parse_input("김치, 밥 1공기\n달걀 2개/대파")
        self.assertEqual(got, ["김치", "밥", "계란", "대파"])

    def test_space_only(self):
        self.assertEqual(parse_input("김치 밥 계란"), ["김치", "밥", "계란"])

    def test_dedupes_preserving_order(self):
        self.assertEqual(parse_input("계란, 달걀, 계란 3개"), ["계란"])

    def test_accepts_list(self):
        self.assertEqual(parse_input(["달걀", "밥"]), ["계란", "밥"])

    def test_empty(self):
        self.assertEqual(parse_input(""), [])
        self.assertEqual(parse_input(None), [])


class TestSearch(unittest.TestCase):
    def test_kimchi_fried_rice_is_ready(self):
        found = matcher.search("김치, 밥, 대파", RECIPES)
        ready = [r for r in found["results"] if r["status"] == "ready"]
        names = [r["name"] for r in ready]
        self.assertIn("김치볶음밥", names)

    def test_ready_recipes_have_no_missing(self):
        found = matcher.search("계란, 밥, 대파, 김치, 두부", RECIPES)
        for item in found["results"]:
            if item["status"] == "ready":
                self.assertEqual(item["missing"], [])
                self.assertEqual(item["match_percent"], 100)

    def test_max_missing_zero_returns_only_ready(self):
        found = matcher.search("계란, 밥, 대파", RECIPES, max_missing=0)
        self.assertTrue(found["results"])
        for item in found["results"]:
            self.assertEqual(item["status"], "ready")

    def test_results_sorted_by_missing_count(self):
        found = matcher.search("계란, 밥, 대파, 양파, 감자", RECIPES)
        missing_counts = [len(r["missing"]) for r in found["results"]]
        self.assertEqual(missing_counts, sorted(missing_counts))

    def test_empty_pantry_returns_nothing(self):
        found = matcher.search("", RECIPES)
        self.assertEqual(found["total"], 0)
        self.assertEqual(found["results"], [])

    def test_unrelated_ingredient_returns_nothing(self):
        found = matcher.search("트러플, 캐비어", RECIPES)
        self.assertEqual(found["results"], [])

    def test_category_filter(self):
        found = matcher.search("계란, 밥, 김치, 두부, 대파", RECIPES, category="국/찌개")
        self.assertTrue(found["results"])
        for item in found["results"]:
            self.assertEqual(item["category"], "국/찌개")

    def test_max_time_filter(self):
        found = matcher.search("계란, 밥, 대파, 김치", RECIPES, max_time=15)
        self.assertTrue(found["results"])
        for item in found["results"]:
            self.assertLessEqual(item["time_minutes"], 15)

    def test_staples_are_added_to_pantry(self):
        found = matcher.search("김치", RECIPES, use_staples=True)
        for staple in PANTRY_STAPLES:
            self.assertIn(staple, found["pantry"])

    def test_alias_input_matches(self):
        with_alias = matcher.search("달걀, 밥, 파", RECIPES)
        with_canonical = matcher.search("계란, 밥, 대파", RECIPES)
        self.assertEqual(
            [r["id"] for r in with_alias["results"]],
            [r["id"] for r in with_canonical["results"]],
        )

    def test_limit_is_respected(self):
        found = matcher.search("계란, 밥, 대파, 양파, 감자, 김치, 두부", RECIPES, limit=5)
        self.assertLessEqual(len(found["results"]), 5)

    def test_substitute_suggested(self):
        # 크림파스타의 생크림이 없고 우유가 있으면 대체 안내가 떠야 한다.
        found = matcher.search("스파게티면, 양파, 베이컨, 우유", RECIPES)
        cream = [r for r in found["results"] if r["id"] == "cream-pasta"]
        self.assertTrue(cream)
        self.assertIn("우유", cream[0]["substitutes"].get("생크림", []))


class TestShoppingList(unittest.TestCase):
    def test_counts_missing_ingredients(self):
        found = matcher.search("밥, 계란, 김치", RECIPES)
        buy = matcher.shopping_list(found["results"])
        self.assertTrue(buy)
        counts = [entry["count"] for entry in buy]
        self.assertEqual(counts, sorted(counts, reverse=True))
        for entry in buy:
            self.assertEqual(entry["count"], len(entry["recipes"]))

    def test_ready_only_search_has_empty_shopping_list(self):
        found = matcher.search("계란, 밥, 대파", RECIPES, max_missing=0)
        self.assertEqual(matcher.shopping_list(found["results"]), [])


class TestRecipeData(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [r["id"] for r in RECIPES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_required_fields_present(self):
        required = ["id", "name", "category", "time_minutes", "difficulty",
                    "servings", "main", "steps"]
        for recipe in RECIPES:
            for field in required:
                self.assertIn(field, recipe, "%s 에 %s 없음" % (recipe.get("id"), field))
            self.assertTrue(recipe["main"], "%s 필수 재료 없음" % recipe["id"])
            self.assertTrue(recipe["steps"], "%s 조리법 없음" % recipe["id"])

    def test_main_ingredients_have_name_and_amount(self):
        for recipe in RECIPES:
            for item in recipe["main"] + recipe.get("optional", []):
                self.assertIn("name", item)
                self.assertIn("amount", item)

    def test_normalized_names_are_stable(self):
        # 레시피에 적힌 재료명이 정규화 후에도 사라지지 않아야 한다.
        for recipe in RECIPES:
            for name in recipe["main_names"]:
                self.assertTrue(name, "%s 재료명 정규화 결과가 빔" % recipe["id"])

    def test_every_recipe_is_findable(self):
        # 자기 필수 재료를 모두 가지고 있으면 반드시 '바로 가능'으로 나와야 한다.
        for recipe in RECIPES:
            found = matcher.search(recipe["main_names"], RECIPES, max_missing=0)
            names = [r["id"] for r in found["results"]]
            self.assertIn(recipe["id"], names, "%s 를 찾지 못함" % recipe["id"])

    def test_known_ingredient_list_not_empty(self):
        self.assertGreater(len(known_ingredients()), 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
