# -*- coding: utf-8 -*-
"""보유 재료와 레시피를 대조해 만들 수 있는 요리를 찾아내는 엔진."""

import json
import os

from ingredients import (
    PANTRY_STAPLES,
    normalize,
    parse_input,
    suggest_substitutes,
)

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recipes.json")

# 부족한 재료가 이 개수를 넘으면 결과에서 제외한다.
DEFAULT_MAX_MISSING = 2


def load_recipes(path=DATA_PATH):
    """레시피 JSON을 읽어 표준화된 재료 이름을 미리 붙여둔다."""
    with open(path, encoding="utf-8") as f:
        recipes = json.load(f)

    for recipe in recipes:
        recipe["main_names"] = [normalize(item["name"]) for item in recipe.get("main", [])]
        recipe["optional_names"] = [normalize(item["name"]) for item in recipe.get("optional", [])]
        recipe["seasoning_names"] = [normalize(s) for s in recipe.get("seasoning", [])]
    return recipes


def _score(recipe, pantry, use_staples, user_pantry):
    """레시피 하나에 대한 매칭 결과를 계산한다.

    필수 재료(main)만 점수에 반영하고, 선택 재료(optional)는
    같은 점수일 때 순위를 가르는 보너스로만 쓴다.

    pantry      : 기본 양념까지 포함한 전체 보유 재료
    user_pantry : 사용자가 직접 입력한 재료만
    """
    have_main = [n for n in recipe["main_names"] if n in pantry]
    have_user_main = [n for n in recipe["main_names"] if n in user_pantry]
    missing_main = [n for n in recipe["main_names"] if n not in pantry]
    have_optional = [n for n in recipe["optional_names"] if n in pantry]

    total_main = len(recipe["main_names"]) or 1
    match_ratio = len(have_main) / total_main

    if use_staples:
        # 기본 양념은 있다고 가정하므로 부족 양념을 따로 세지 않는다.
        missing_seasoning = []
    else:
        missing_seasoning = [
            n for n in recipe["seasoning_names"]
            if n not in pantry and n in PANTRY_STAPLES
        ]

    # 선택 재료 보너스는 최대 0.09로 제한해 필수 재료 1개(최소 0.1)를 넘지 못하게 한다.
    optional_bonus = min(len(have_optional) * 0.03, 0.09)

    substitutes = {}
    for name in missing_main:
        options = suggest_substitutes(name, pantry)
        if options:
            substitutes[name] = options

    return {
        "recipe": recipe,
        "have": have_main,
        "have_user": have_user_main,
        "missing": missing_main,
        "have_optional": have_optional,
        "missing_seasoning": missing_seasoning,
        "match_ratio": match_ratio,
        "score": match_ratio + optional_bonus,
        "substitutes": substitutes,
    }


def _to_result(scored):
    """정렬이 끝난 내부 결과를 API 응답용 딕셔너리로 바꾼다."""
    recipe = scored["recipe"]
    missing_count = len(scored["missing"])

    if missing_count == 0:
        status = "ready"          # 지금 바로 만들 수 있음
    elif missing_count == 1:
        status = "almost"         # 1개만 더 사면 됨
    else:
        status = "partial"        # 2개 이상 부족

    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "category": recipe["category"],
        "time_minutes": recipe["time_minutes"],
        "difficulty": recipe["difficulty"],
        "servings": recipe["servings"],
        "tags": recipe.get("tags", []),
        "main": recipe.get("main", []),
        "optional": recipe.get("optional", []),
        "seasoning": recipe.get("seasoning", []),
        "steps": recipe.get("steps", []),
        "tip": recipe.get("tip", ""),
        "status": status,
        "match_percent": round(scored["match_ratio"] * 100),
        "have": scored["have"],
        "missing": scored["missing"],
        "have_optional": scored["have_optional"],
        "missing_seasoning": scored["missing_seasoning"],
        "substitutes": scored["substitutes"],
    }


def search(
    pantry_text,
    recipes,
    max_missing=DEFAULT_MAX_MISSING,
    use_staples=True,
    category=None,
    max_time=None,
    limit=30,
):
    """보유 재료로 만들 수 있는 요리를 찾아 정렬된 목록으로 돌려준다.

    pantry_text : 사용자가 입력한 재료 문자열 또는 재료 이름 리스트
    max_missing : 허용할 부족 재료 개수 (0이면 바로 가능한 요리만)
    use_staples : 기본 양념(소금·간장 등)을 보유한 것으로 칠지 여부
    category    : 특정 카테고리로 좁히기 ("국/찌개" 등)
    max_time    : 조리 시간 상한(분)
    """
    user_pantry = parse_input(pantry_text)

    # 입력이 아예 없으면 양념만 가지고 추천하지 않는다.
    if not user_pantry:
        return {"pantry": [], "total": 0, "results": []}

    pantry = list(user_pantry)
    if use_staples:
        pantry = pantry + [s for s in PANTRY_STAPLES if s not in pantry]

    scored_list = []
    for recipe in recipes:
        if category and recipe["category"] != category:
            continue
        if max_time is not None and recipe["time_minutes"] > max_time:
            continue

        scored = _score(recipe, pantry, use_staples, user_pantry)

        # 사용자가 직접 넣은 재료가 하나도 안 쓰이는 요리는 "추천"이 아니라 소음이다.
        # (기본 양념인 마늘 하나만 겹쳐서 추천되는 일을 막는다)
        if not scored["have_user"]:
            continue
        if len(scored["missing"]) > max_missing:
            continue

        scored_list.append(scored)

    # 부족 재료가 적은 순 → 점수 높은 순 → 조리 시간 짧은 순
    scored_list.sort(
        key=lambda s: (
            len(s["missing"]),
            -s["score"],
            s["recipe"]["time_minutes"],
            s["recipe"]["name"],
        )
    )

    results = [_to_result(s) for s in scored_list[:limit]]
    return {
        "pantry": pantry,
        "total": len(scored_list),
        "results": results,
    }


def shopping_list(results):
    """검색 결과 전체에서 부족한 재료를 모아 빈도순으로 돌려준다.

    한 가지만 더 사면 만들 수 있는 요리가 늘어나는지 한눈에 보여준다.
    """
    counter = {}
    for item in results:
        for name in item["missing"]:
            entry = counter.setdefault(name, {"name": name, "count": 0, "recipes": []})
            entry["count"] += 1
            entry["recipes"].append(item["name"])

    return sorted(counter.values(), key=lambda e: (-e["count"], e["name"]))
