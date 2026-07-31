"""
config.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Xcoin 포인트 시스템의 모든 설정값.

값은 전부 환경변수로 덮어쓸 수 있습니다.
운영 배포 시 최소한 아래 3개는 반드시 바꿔야 합니다:
  XCOIN_ADMIN_SECRET   : 관리자 API 열쇠
  XCOIN_SERVER_SECRET  : 서버 내부 서명용 비밀키
  XCOIN_DATA_DIR       : DB를 저장할 영구 디스크 경로
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


# ===================== 기본 정보 =====================
COIN_NAME = _env("XCOIN_NAME", "Xcoin")
COIN_SYMBOL = _env("XCOIN_SYMBOL", "XCN")
COIN_DECIMALS = _env_int("XCOIN_DECIMALS", 18)

# ===================== 보안 =====================
ADMIN_SECRET = _env("XCOIN_ADMIN_SECRET", "CHANGE_THIS_ADMIN_SECRET")
SERVER_SECRET = _env("XCOIN_SERVER_SECRET", "CHANGE_THIS_SERVER_SECRET")

# 게임에서 보낸 요청의 타임스탬프 허용 오차(초). 이 범위를 벗어나면 거부.
SIGNATURE_TOLERANCE_SECONDS = _env_int("XCOIN_SIGNATURE_TOLERANCE", 300)

# 지갑 연동용 서명 챌린지(nonce)의 유효 기간(초)
WALLET_NONCE_TTL_SECONDS = _env_int("XCOIN_WALLET_NONCE_TTL", 600)

# True면 유저 본인용 API(잔액/전환/지갑)에 X-User-Token 헤더를 요구한다.
# 끄면 user_id 만 알면 남의 잔액을 조회할 수 있으므로 운영에서는 반드시 켜둘 것.
REQUIRE_USER_TOKEN = _env_bool("XCOIN_REQUIRE_USER_TOKEN", True)

# ===================== 저장 경로 =====================
# Render/Railway 등에서 영구 디스크를 쓸 때 DATA_DIR(또는 XCOIN_DATA_DIR)를 마운트 경로로 지정.
_data_dir = _env("XCOIN_DATA_DIR") or _env("DATA_DIR")
if _data_dir and os.path.isdir(_data_dir):
    DB_PATH = os.path.join(_data_dir, "xcoin.db")
elif os.path.isdir("/data"):
    DB_PATH = "/data/xcoin.db"
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xcoin.db")

# ===================== 포인트 정책 =====================
# "하루"의 기준 시간대. 9 = 한국시간(KST, UTC+9) 자정에 일일 한도가 초기화됨.
DAY_UTC_OFFSET_HOURS = _env_int("XCOIN_DAY_UTC_OFFSET", 9)

# 한 유저가 하루에 모든 게임을 합쳐 최대로 받을 수 있는 포인트
DAILY_POINT_CAP_PER_USER = _env_int("XCOIN_DAILY_POINT_CAP", 5000)

# 게임 한 판(세션)에서 인정하는 최대 점수. 이보다 크면 조작으로 간주하고 거부.
MAX_SCORE_PER_SESSION = _env_int("XCOIN_MAX_SCORE_PER_SESSION", 100000)

# 게임 한 판의 최소 플레이 시간(초). 이보다 짧으면 자동화/치트로 간주.
MIN_SESSION_SECONDS = _env_int("XCOIN_MIN_SESSION_SECONDS", 5)

# "초당 몇 점까지 정상으로 볼 것인가". 점수/플레이시간이 이 값을 넘으면 거부.
MAX_SCORE_PER_SECOND = _env_int("XCOIN_MAX_SCORE_PER_SECOND", 500)

# 같은 유저가 1분 안에 제출할 수 있는 최대 세션 수
MAX_SUBMITS_PER_MINUTE = _env_int("XCOIN_MAX_SUBMITS_PER_MINUTE", 10)

# ===================== 포인트 → Xcoin 전환 정책 =====================
# 몇 포인트가 1 Xcoin인지. 기본 1000포인트 = 1 XCN
POINTS_PER_XCOIN = _env_int("XCOIN_POINTS_PER_COIN", 1000)

# 한 번에 전환할 수 있는 최소 포인트 (가스비 낭비 방지)
MIN_CONVERT_POINTS = _env_int("XCOIN_MIN_CONVERT_POINTS", 1000)

# 전환 수수료 (basis point, 100 = 1%)
CONVERT_FEE_BPS = _env_int("XCOIN_CONVERT_FEE_BPS", 0)

# 유저 1명이 하루에 전환할 수 있는 최대 포인트
DAILY_CONVERT_CAP_POINTS = _env_int("XCOIN_DAILY_CONVERT_CAP", 50000)

# True면 전환 요청이 자동 승인되어 바로 지급 대기열로 들어감.
# False면 관리자가 /admin/conversions/approve 로 직접 승인해야 지급됨.
AUTO_APPROVE_CONVERSION = _env_bool("XCOIN_AUTO_APPROVE", True)

# ===================== 온체인(블록체인) 설정 =====================
# 이 값들이 비어 있으면 서버는 "시뮬레이션 모드"로 동작합니다.
# 시뮬레이션 모드에서도 전환/장부 기능은 모두 정상 동작하고,
# 실제 전송만 가짜 트랜잭션 해시로 기록됩니다. (개발/테스트용)
CHAIN_RPC_URL = _env("XCOIN_RPC_URL")
CHAIN_ID = _env_int("XCOIN_CHAIN_ID", 137)  # 기본값: Polygon 메인넷
TOKEN_CONTRACT_ADDRESS = _env("XCOIN_CONTRACT_ADDRESS")

# 유저에게 코인을 보내주는 지갑(재무 지갑)의 개인키.
# ★ 절대 코드나 깃허브에 넣지 말고 환경변수로만 주입할 것 ★
TREASURY_PRIVATE_KEY = _env("XCOIN_TREASURY_PRIVATE_KEY")

# 지급 방식: "transfer"(재무 지갑이 보유분에서 전송) 또는 "mint"(그 자리에서 새로 발행)
PAYOUT_MODE = _env("XCOIN_PAYOUT_MODE", "transfer").lower()

# 지급 워커가 몇 초마다 대기열을 확인할지
PAYOUT_INTERVAL_SECONDS = _env_int("XCOIN_PAYOUT_INTERVAL", 30)

# 한 번 돌 때 처리할 최대 건수
PAYOUT_BATCH_SIZE = _env_int("XCOIN_PAYOUT_BATCH", 20)

# 전송 실패 시 최대 재시도 횟수
PAYOUT_MAX_RETRY = _env_int("XCOIN_PAYOUT_MAX_RETRY", 5)


def is_simulation() -> bool:
    """온체인 설정이 하나라도 비어 있으면 시뮬레이션 모드."""
    return not (CHAIN_RPC_URL and TOKEN_CONTRACT_ADDRESS and TREASURY_PRIVATE_KEY)


def wei_per_point() -> int:
    """포인트 1점이 몇 wei(최소 단위)의 Xcoin인지."""
    return (10 ** COIN_DECIMALS) // POINTS_PER_XCOIN


def warn_if_insecure() -> list:
    """운영에 부적합한 설정을 찾아서 경고 문자열 리스트로 돌려준다."""
    warnings = []
    if ADMIN_SECRET == "CHANGE_THIS_ADMIN_SECRET":
        warnings.append("XCOIN_ADMIN_SECRET이 기본값입니다. 반드시 바꾸세요.")
    if SERVER_SECRET == "CHANGE_THIS_SERVER_SECRET":
        warnings.append("XCOIN_SERVER_SECRET이 기본값입니다. 반드시 바꾸세요.")
    if not (_data_dir or os.path.isdir("/data")):
        warnings.append(
            "영구 디스크가 감지되지 않았습니다. 재배포 시 포인트/전환 기록이 사라질 수 있습니다."
        )
    if is_simulation():
        warnings.append(
            "온체인 설정이 없어 시뮬레이션 모드로 동작합니다. "
            "실제 코인은 전송되지 않습니다."
        )
    if not REQUIRE_USER_TOKEN:
        warnings.append(
            "XCOIN_REQUIRE_USER_TOKEN 이 꺼져 있습니다. user_id 만 알면 남의 잔액을 "
            "조회하거나 전환을 요청할 수 있습니다."
        )
    if PAYOUT_MODE not in ("transfer", "mint"):
        warnings.append(f"XCOIN_PAYOUT_MODE 값이 이상합니다: {PAYOUT_MODE} (transfer 또는 mint)")
    return warnings
