"""SEO 상품명 / 태그 생성 엔진.

마켓 상품명은 '많이 넣을수록 좋다'가 아니다. 네이버·쿠팡 모두
  - 중복 단어 반복  - 홍보성 문구("최저가", "정품", "무료배송")
  - 특수문자 남발   - 카테고리 무관 키워드
를 어뷰징으로 보고 검색 노출을 깎는다. 그래서 이 엔진은 '더하기'보다
'거르기'를 먼저 한다.

조립 규칙:  {브랜드} {모델명} {상품유형} {핵심속성}
  - 토큰 단위로 정규화 -> 금칙어 제거 -> 중복 제거 -> 길이 컷
  - 잘라낸 단어는 dropped_terms 로 남겨서 왜 빠졌는지 추적 가능하게 한다
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from ..models import RawProduct, SeoContent
from ..utils.logging import get

log = get("seo")

# 상품명에서 걷어낼 잡음: 대괄호 프로모션, 이모지, 연속 특수문자
NOISE_PATTERNS = [
    re.compile(r"\[[^\]]{0,30}\]"),          # [단독][특가]
    re.compile(r"\([^)]{0,20}(행사|사은품|증정|쿠폰)[^)]{0,20}\)"),
    re.compile(r"[^\w\s가-힣\-\.\+/]"),      # 허용 문자 외 제거
    re.compile(r"\s{2,}"),
]

DEFAULT_BANNED = [
    "최저가", "정품", "특가", "무료배송", "이벤트", "세일", "인기", "베스트",
    "당일발송", "품절임박", "한정수량", "1위", "공식", "정품보장",
]

# 사이즈 표기는 상품명이 아니라 옵션으로 관리한다.
# 주의: "에어맥스 90", "993" 처럼 숫자가 모델명인 경우가 많아 단순 숫자 제거는 위험하다.
# 한국 신발 사이즈 관례(200~330mm, 5 단위)에 정확히 들어맞을 때만 사이즈로 본다.
_LETTER_SIZE = re.compile(r"^(XS|S|M|L|XL|XXL|2XL|3XL)$", re.I)
_SUFFIX_SIZE = re.compile(r"^(\d{2,3})\s*(mm|MM|호|사이즈)$")


def is_size_token(token: str) -> bool:
    if _LETTER_SIZE.match(token):
        return True
    if (m := _SUFFIX_SIZE.match(token)):
        return True
    if token.isdigit():
        n = int(token)
        return 200 <= n <= 330 and n % 5 == 0
    return False


@dataclass
class SeoConfig:
    max_title_len: int = 100
    max_tags: int = 10
    title_template: str = "{brand} {model} {category} {attrs}"
    banned_terms: list[str] = None
    keyword_sets: dict[str, list[str]] = None

    def __post_init__(self) -> None:
        self.banned_terms = list(self.banned_terms or DEFAULT_BANNED)
        self.keyword_sets = self.keyword_sets or {}

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "SeoConfig":
        known = {"max_title_len", "max_tags", "title_template",
                 "banned_terms", "keyword_sets"}
        return cls(**{k: v for k, v in (cfg or {}).items() if k in known})


def normalize(text: str) -> str:
    """전각/자모 정규화 + 잡음 제거."""
    out = unicodedata.normalize("NFKC", text or "")
    for pat in NOISE_PATTERNS:
        out = pat.sub(" ", out)
    return out.strip()


def tokenize(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t]


def dedupe(tokens: Iterable[str]) -> tuple[list[str], list[str]]:
    """대소문자·공백 무시 중복 제거. (남긴 것, 버린 것)"""
    seen: set[str] = set()
    kept, dropped = [], []
    for tok in tokens:
        key = tok.lower().replace("-", "").replace(".", "")
        if key in seen:
            dropped.append(tok)
            continue
        seen.add(key)
        kept.append(tok)
    return kept, dropped


class SeoEngine:
    def __init__(self, cfg: SeoConfig) -> None:
        self.cfg = cfg
        self._banned = {b.lower() for b in cfg.banned_terms}

    # --------------------------------------------------------------
    def build(self, product: RawProduct, *, market: str = "naver") -> SeoContent:
        warnings: list[str] = []
        dropped: list[str] = []

        brand = normalize(product.brand)
        category = self._leaf_category(product)
        model = self._model_tokens(product, brand, category)
        attrs = self._attr_tokens(product)

        parts = self.cfg.title_template.format(
            brand=brand, model=" ".join(model),
            category=category, attrs=" ".join(attrs),
        )
        tokens = tokenize(parts)

        clean = []
        for tok in tokens:
            if tok.lower() in self._banned:
                dropped.append(tok)
                continue
            if is_size_token(tok):
                dropped.append(tok)
                continue
            clean.append(tok)

        clean, dupes = dedupe(clean)
        dropped.extend(dupes)

        title = self._fit(clean, self.cfg.max_title_len)
        if not title:
            warnings.append("상품명이 비었습니다 — 원본 파싱을 확인하세요")
            title = normalize(product.name)[: self.cfg.max_title_len]
        if len(title) < 10:
            warnings.append(f"상품명이 너무 짧습니다({len(title)}자)")

        tags = self._tags(brand, category, model)
        keywords = self._keywords(brand, category, model, market)

        return SeoContent(
            title=title, tags=tags, search_keywords=keywords,
            dropped_terms=dropped, warnings=warnings,
        )

    # --------------------------------------------------------------
    def _leaf_category(self, product: RawProduct) -> str:
        if product.category_path:
            return normalize(product.category_path[-1])
        for key in self.cfg.keyword_sets:
            if key in product.name:
                return key
        return ""

    def _model_tokens(self, product: RawProduct, brand: str, category: str) -> list[str]:
        """원본 상품명에서 브랜드/카테고리를 뺀 나머지 = 모델명으로 본다."""
        drop = {brand.lower(), category.lower()}
        toks = [t for t in tokenize(product.name) if t.lower() not in drop]
        # 품번(영문+숫자 혼합)은 검색에 강하므로 앞으로 당긴다
        code = [t for t in toks if re.search(r"\d", t) and re.search(r"[A-Za-z]", t)]
        rest = [t for t in toks if t not in code]
        return (rest + code)[:8]

    def _attr_tokens(self, product: RawProduct) -> list[str]:
        wanted = ("색상", "소재", "성별", "용도")
        return [
            normalize(str(product.attributes[k]))
            for k in wanted
            if product.attributes.get(k)
        ]

    def _fit(self, tokens: list[str], limit: int) -> str:
        """길이 제한 안에서 앞에서부터 채운다(뒤 토큰일수록 덜 중요)."""
        out = ""
        for tok in tokens:
            candidate = f"{out} {tok}".strip()
            if len(candidate) > limit:
                break
            out = candidate
        return out

    def _tags(self, brand: str, category: str, model: list[str]) -> list[str]:
        base = [t for t in ([brand, category] + model[:3]) if t]
        base += self.cfg.keyword_sets.get(category, [])
        tags, _ = dedupe(base)
        return [t for t in tags if len(t) >= 2][: self.cfg.max_tags]

    def _keywords(self, brand: str, category: str, model: list[str],
                  market: str) -> list[str]:
        """검색 유입용 조합 키워드. 상품명에는 안 넣고 태그/검색어 필드에만 쓴다."""
        seeds = self.cfg.keyword_sets.get(category, [])
        combos = []
        if brand and category:
            combos.append(f"{brand}{category}")
            combos.append(f"{brand} {category}")
        for s in seeds:
            if brand:
                combos.append(f"{brand} {s}")
        if model:
            combos.append(f"{brand} {model[0]}".strip())
        combos, _ = dedupe(combos)
        return combos[: self.cfg.max_tags]
