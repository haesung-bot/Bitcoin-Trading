# -*- coding: utf-8 -*-
"""브라우저 없이 터미널에서 바로 쓰는 버전.

사용법:
    python cli.py 김치 계란 밥
    python cli.py                 (실행 후 재료를 물어봄)
"""

import sys

import matcher

BADGE = {"ready": "[바로 가능]", "almost": "[1개만 더]", "partial": "[조금 부족]"}


def show(item, index):
    print("\n%d. %s  %s" % (index, item["name"], BADGE[item["status"]]))
    print("   %s · %d분 · %s · %s"
          % (item["category"], item["time_minutes"], item["difficulty"], item["servings"]))
    print("   있는 재료: %s" % ", ".join(item["have"]))
    if item["missing"]:
        print("   부족한 재료: %s" % ", ".join(item["missing"]))
    for missing_name, options in item["substitutes"].items():
        print("   └ %s 대신 %s 사용 가능" % (missing_name, "/".join(options)))


def show_detail(item):
    print("\n" + "=" * 50)
    print("%s (%s · %d분)" % (item["name"], item["difficulty"], item["time_minutes"]))
    print("=" * 50)
    print("\n[필요한 재료]")
    for ing in item["main"]:
        mark = "O" if ing["name"] in item["have"] else "X"
        print("  %s %s %s" % (mark, ing["name"], ing["amount"]))
    if item["optional"]:
        print("\n[있으면 더 좋아요]")
        for ing in item["optional"]:
            print("  - %s %s" % (ing["name"], ing["amount"]))
    if item["seasoning"]:
        print("\n[양념]\n  %s" % ", ".join(item["seasoning"]))
    print("\n[만드는 법]")
    for step_no, step in enumerate(item["steps"], 1):
        print("  %d) %s" % (step_no, step))
    if item["tip"]:
        print("\n[TIP] %s" % item["tip"])
    print()


def main():
    recipes = matcher.load_recipes()

    if len(sys.argv) > 1:
        pantry_text = " ".join(sys.argv[1:])
    else:
        print("냉장고 요리사 (레시피 %d개)" % len(recipes))
        pantry_text = input("집에 있는 재료를 입력하세요 (쉼표로 구분): ")

    found = matcher.search(pantry_text, recipes, limit=15)
    if not found["results"]:
        print("\n조건에 맞는 요리를 찾지 못했습니다. 재료를 더 입력해 보세요.")
        return

    ready = sum(1 for r in found["results"] if r["status"] == "ready")
    print("\n총 %d개를 찾았고, 그중 %d개는 지금 바로 만들 수 있습니다."
          % (found["total"], ready))

    for index, item in enumerate(found["results"], 1):
        show(item, index)

    buy = matcher.shopping_list(found["results"])
    useful = [b for b in buy if b["count"] >= 2][:5]
    if useful:
        print("\n[이것만 사면 만들 수 있는 요리가 늘어나요]")
        for entry in useful:
            print("  %s - %d개 요리에 필요" % (entry["name"], entry["count"]))

    try:
        choice = input("\n자세히 볼 번호 (엔터로 종료): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if choice.isdigit() and 1 <= int(choice) <= len(found["results"]):
        show_detail(found["results"][int(choice) - 1])


if __name__ == "__main__":
    main()
