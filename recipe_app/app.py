# -*- coding: utf-8 -*-
"""냉장고 요리사 - 집에 있는 재료로 만들 수 있는 요리를 찾아주는 웹앱.

실행:
    pip install -r requirements.txt
    python app.py
    브라우저에서 http://127.0.0.1:5000 접속
"""

import os

from flask import Flask, jsonify, render_template, request

import matcher
from ingredients import known_ingredients

app = Flask(__name__)

# 레시피는 파일에서 한 번만 읽어 메모리에 올려둔다.
RECIPES = matcher.load_recipes()

CATEGORIES = sorted({r["category"] for r in RECIPES})


@app.route("/")
def index():
    return render_template(
        "index.html",
        categories=CATEGORIES,
        recipe_count=len(RECIPES),
    )


@app.route("/api/ingredients")
def api_ingredients():
    """자동완성용 재료 목록."""
    return jsonify({"ingredients": known_ingredients()})


@app.route("/api/search", methods=["POST"])
def api_search():
    """보유 재료로 만들 수 있는 요리를 검색한다."""
    payload = request.get_json(silent=True) or {}

    pantry_text = payload.get("ingredients", "")
    category = payload.get("category") or None
    use_staples = bool(payload.get("use_staples", True))

    try:
        max_missing = int(payload.get("max_missing", matcher.DEFAULT_MAX_MISSING))
    except (TypeError, ValueError):
        max_missing = matcher.DEFAULT_MAX_MISSING
    max_missing = max(0, min(max_missing, 3))

    max_time = payload.get("max_time")
    try:
        max_time = int(max_time) if max_time else None
    except (TypeError, ValueError):
        max_time = None

    found = matcher.search(
        pantry_text,
        RECIPES,
        max_missing=max_missing,
        use_staples=use_staples,
        category=category,
        max_time=max_time,
    )
    found["shopping_list"] = matcher.shopping_list(found["results"])[:8]
    return jsonify(found)


@app.route("/api/recipe/<recipe_id>")
def api_recipe(recipe_id):
    """레시피 단건 조회."""
    for recipe in RECIPES:
        if recipe["id"] == recipe_id:
            return jsonify({k: v for k, v in recipe.items() if not k.endswith("_names")})
    return jsonify({"error": "레시피를 찾을 수 없습니다."}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print("냉장고 요리사 실행 중 → http://127.0.0.1:%d" % port)
    app.run(host="127.0.0.1", port=port, debug=debug)
