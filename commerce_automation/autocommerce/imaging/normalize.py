"""이미지 규격화 (로컬).

마켓별 요구사항이 서로 다르지만 교집합은 대체로 이렇다.
  - 정사각형, 한 변 1000px 이상 (네이버 대표이미지 권장 1000x1000)
  - 흰 배경, 상품이 화면의 대부분을 차지
  - 텍스트/로고/워터마크 없음 (대표이미지에 문구 넣으면 노출 제한)
  - JPEG, 과도한 용량 금지

여기서는 '변형'이 아니라 '규격화'만 한다. 비율 유지 축소 + 흰 여백 패딩 +
EXIF 제거 + 색공간 sRGB 통일. 배경 제거/재생성 같은 생성형 변환은
replicate_client.py 가 담당한다.

Pillow 가 없으면 원본을 그대로 통과시키고 경고를 남긴다 —
이미지 한 단계 때문에 전체 파이프라인이 멈추면 안 되기 때문.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..utils.logging import get

log = get("imaging.normalize")

try:  # Pillow 는 선택 의존성
    from PIL import Image, ImageOps  # type: ignore

    PILLOW = True
except ImportError:  # pragma: no cover
    Image = ImageOps = None  # type: ignore
    PILLOW = False


@dataclass
class NormalizeSpec:
    size: int = 1000
    background: str = "#FFFFFF"
    fmt: str = "jpeg"
    quality: int = 88
    padding_ratio: float = 0.04   # 캔버스 대비 여백 비율

    @classmethod
    def from_config(cls, cfg: dict) -> "NormalizeSpec":
        return cls(
            size=int(cfg.get("size", 1000)),
            background=str(cfg.get("background", "#FFFFFF")),
            fmt=str(cfg.get("format", "jpeg")).lower(),
            quality=int(cfg.get("quality", 88)),
            padding_ratio=float(cfg.get("padding_ratio", 0.04)),
        )


def output_name(source_path: str | Path, spec: NormalizeSpec, suffix: str = "") -> str:
    """입력이 같으면 같은 파일명 -> 재실행 시 불필요한 재작업을 피한다."""
    key = f"{Path(source_path).name}:{spec.size}:{spec.fmt}:{spec.quality}:{suffix}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:12]
    ext = "jpg" if spec.fmt in ("jpeg", "jpg") else spec.fmt
    return f"{Path(source_path).stem}_{digest}.{ext}"


def normalize_file(src: str | Path, dest: str | Path,
                   spec: NormalizeSpec) -> tuple[str, int, int, list[str]]:
    """(결과 경로, 너비, 높이, 경고목록)"""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if not PILLOW:
        warnings.append(
            "Pillow 미설치 — 이미지 규격화를 건너뛰고 원본을 사용합니다. "
            "`pip install Pillow` 후 다시 실행하세요."
        )
        if src != dest:
            dest.write_bytes(src.read_bytes())
        return str(dest), 0, 0, warnings

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)           # 회전 EXIF 반영 후 제거
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.mode else "RGB")

        canvas_size = spec.size
        inner = int(canvas_size * (1 - 2 * spec.padding_ratio))
        if max(im.size) < inner * 0.6:
            warnings.append(
                f"원본 해상도가 낮습니다 ({im.width}x{im.height}) — "
                f"{canvas_size}px 캔버스에서 흐릿할 수 있습니다."
            )

        im.thumbnail((inner, inner), Image.LANCZOS)

        canvas = Image.new("RGB", (canvas_size, canvas_size), spec.background)
        offset = ((canvas_size - im.width) // 2, (canvas_size - im.height) // 2)
        if im.mode == "RGBA":
            canvas.paste(im, offset, im)           # 알파 채널을 흰 배경에 합성
        else:
            canvas.paste(im, offset)

        save_kw = {"quality": spec.quality, "optimize": True} \
            if spec.fmt in ("jpeg", "jpg") else {}
        canvas.save(dest, format=spec.fmt.upper().replace("JPG", "JPEG"), **save_kw)
        return str(dest), canvas_size, canvas_size, warnings
