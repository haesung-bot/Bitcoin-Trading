"""
ict_fvg/config.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ICT High-Probability FVG 전략의 모든 설정값을 한곳에 모아둔 파일.

여기 값만 바꾸면 전략/백테스트 동작이 전부 바뀝니다.
숫자를 바꿔가며 최적화할 때도 이 파일만 건드리면 됩니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from dataclasses import dataclass, asdict


# 타임프레임 문자열 → 밀리초. CCXT가 주는 타임스탬프가 ms 단위라 ms로 통일한다.
TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def timeframe_ms(timeframe: str) -> int:
    """타임프레임 문자열을 밀리초로 바꾼다. 모르는 값이면 바로 에러를 낸다."""
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(
            f"지원하지 않는 타임프레임입니다: {timeframe!r} "
            f"(사용 가능: {', '.join(TIMEFRAME_MS)})"
        )
    return TIMEFRAME_MS[timeframe]


@dataclass
class StrategyConfig:
    # ───────────────────────── 1. 시장 / 타임프레임 ─────────────────────────
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    market_type: str = "future"      # 'future'(USDT 무기한) 또는 'spot'

    htf: str = "1h"                  # 상위 타임프레임 — 스윙·구역 판단용 (1h 또는 1d)
    ltf: str = "15m"                 # 하위 타임프레임 — 진입 타점용 (5m 또는 15m)

    # ───────────────────────── 2. 스윙(파동) 판정 ─────────────────────────
    # 프랙탈 방식: 좌우 N개 봉보다 높으면(낮으면) 스윙 고점(저점).
    # ⚠️ 스윙은 오른쪽 N개 봉이 마감돼야 "확정"된다. 백테스트에서는 확정 시점부터만 쓴다.
    htf_swing_left: int = 3
    htf_swing_right: int = 3
    ltf_swing_left: int = 2
    ltf_swing_right: int = 2

    # ───────────────────────── 3. FVG 설정 ─────────────────────────
    # ATR 대비 이 비율보다 작은 FVG는 노이즈로 보고 버린다. 0으로 두면 전부 사용.
    min_fvg_atr: float = 0.15
    # FVG가 생성된 뒤 이 봉 수 안에 CE를 못 건드리면 폐기 (오래된 갭은 신뢰도가 떨어짐)
    fvg_max_age_bars: int = 80
    # FVG 무효화 기준: 'close'면 종가가 갭 반대편을 넘었을 때, 'wick'이면 꼬리만 넘어도 무효
    fvg_invalidate_on: str = "close"

    # ───────────────────────── 4. 필터링 규칙 ─────────────────────────
    # 외부 유동성(직전 스윙 고/저) 사냥을 요구할지 여부.
    # False로 두면 레인지 내부 FVG까지 전부 잡히므로 승률이 크게 떨어진다.
    require_sweep: bool = True
    # 사냥 이후 구조 전환(MSS)까지 확인할지 여부. ICT 원형에 가장 가까운 설정은 True.
    require_mss: bool = True
    # 사냥/MSS 이후 이 봉 수가 지나면 셋업 폐기
    setup_valid_bars: int = 40
    # MSS 직전 몇 봉까지의 FVG를 인정할지 (구조를 깨는 변동 구간에서 생긴 갭을 잡기 위함)
    fvg_pre_mss_bars: int = 3

    # HTF 딜링 레인지가 HTF ATR의 이 배수보다 좁으면 '판단 불가'로 본다.
    # 레인지가 너무 좁으면 0.5 레벨이 사실상 현재가와 같아서 필터 구실을 못 한다.
    min_range_atr: float = 1.0

    # 프리미엄/디스카운트 판정 기준.
    #  'ce'   → FVG의 CE(0.5 레벨)가 해당 구역에 있으면 통과 (기본, 덜 엄격)
    #  'full' → FVG 전체가 해당 구역 안에 들어와야 통과 (엄격)
    zone_mode: str = "ce"

    # ───────────────────────── 5. 진입 / 손절 / 익절 ─────────────────────────
    min_rr: float = 2.0              # 이 손익비 미만이면 주문 자체를 넣지 않는다
    # 손절 기준:
    #  'fvg'   → FVG 반대편 끝
    #  'swing' → 직전 LTF 스윙 저점(롱)/고점(숏) 바깥
    #  'wider' → 둘 중 더 먼 쪽 (가장 보수적, 기본값)
    sl_mode: str = "wider"
    sl_buffer_atr: float = 0.10      # 손절선에 ATR 기준 여유를 더 준다 (스탑 헌팅 방지)
    # 익절 목표:
    #  'nearest'    → 진입가 위쪽에서 가장 가까운 확정 HTF 스윙 고점 (기본)
    #  'range_high' → 현재 딜링 레인지의 최고점
    tp_target: str = "nearest"

    # ───────────────────────── 6. 자금 관리 ─────────────────────────
    initial_capital: float = 10_000.0
    risk_pct: float = 1.0            # 1회 거래당 감수할 자본 비율(%) — 손절까지의 거리로 수량을 역산
    max_leverage: float = 10.0       # 명목가치가 자본의 이 배수를 넘지 않게 수량을 제한
    allow_long: bool = True
    allow_short: bool = True

    # ───────────────────────── 7. 체결 비용 ─────────────────────────
    maker_fee: float = 0.0002        # 지정가 체결 수수료 (진입, 익절)
    taker_fee: float = 0.0005        # 시장가 체결 수수료 (손절)
    slippage_bps: float = 1.0        # 손절 체결 시 불리하게 밀리는 정도 (1bp = 0.01%)

    # ───────────────────────── 8. 기타 ─────────────────────────
    atr_period: int = 14
    warmup_bars: int = 200           # 지표가 안정될 때까지 거래하지 않는 구간

    def validate(self) -> "StrategyConfig":
        """설정값이 서로 모순되지 않는지 확인한다. 잘못된 값은 백테스트 결과를
        조용히 망가뜨리기 때문에, 실행 전에 여기서 먼저 걸러낸다."""
        timeframe_ms(self.htf)
        timeframe_ms(self.ltf)

        if timeframe_ms(self.htf) <= timeframe_ms(self.ltf):
            raise ValueError(
                f"HTF({self.htf})는 LTF({self.ltf})보다 커야 합니다. 값이 뒤바뀌지 않았는지 확인하세요."
            )
        if self.zone_mode not in ("ce", "full"):
            raise ValueError(f"zone_mode는 'ce' 또는 'full'이어야 합니다: {self.zone_mode!r}")
        if self.sl_mode not in ("fvg", "swing", "wider"):
            raise ValueError(f"sl_mode는 'fvg'/'swing'/'wider' 중 하나여야 합니다: {self.sl_mode!r}")
        if self.tp_target not in ("nearest", "range_high"):
            raise ValueError(f"tp_target은 'nearest' 또는 'range_high'여야 합니다: {self.tp_target!r}")
        if self.fvg_invalidate_on not in ("close", "wick"):
            raise ValueError(f"fvg_invalidate_on은 'close' 또는 'wick'이어야 합니다: {self.fvg_invalidate_on!r}")
        if self.min_rr <= 0:
            raise ValueError("min_rr은 0보다 커야 합니다.")
        if not 0 < self.risk_pct <= 100:
            raise ValueError("risk_pct는 0 초과 100 이하로 설정하세요.")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital은 0보다 커야 합니다.")
        if min(self.htf_swing_left, self.htf_swing_right,
               self.ltf_swing_left, self.ltf_swing_right) < 1:
            raise ValueError("스윙 좌/우 봉 수는 1 이상이어야 합니다.")
        if not self.allow_long and not self.allow_short:
            raise ValueError("allow_long과 allow_short를 모두 끄면 거래가 발생하지 않습니다.")
        return self

    def to_dict(self) -> dict:
        return asdict(self)
