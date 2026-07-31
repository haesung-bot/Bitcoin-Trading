"""
security.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
요청 서명 검증과 관리자 인증.

게임이 "이 유저가 3200점 받았어요"라고 서버에 알려줄 때,
아무나 그 요청을 흉내낼 수 있으면 포인트는 무한정 찍혀 나갑니다.
그래서 게임마다 발급된 secret 으로 HMAC-SHA256 서명을 붙이게 하고,
서버는 같은 방식으로 다시 계산해서 일치하는지 봅니다.

서명 대상 문자열(정규화된 형태):
    game_id|user_id|score|duration_ms|nonce|timestamp

★ 중요 ★
secret 은 "서버 대 서버" 비밀입니다.
게임 클라이언트(앱/웹) 안에 secret 을 박아두면 뜯어볼 수 있으므로,
반드시 여러분이 운영하는 게임 백엔드에서 서명해서 보내야 합니다.
백엔드가 없는 순수 클라이언트 게임이라면 README 의
"클라이언트 전용 게임" 절을 참고하세요.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import hashlib
import hmac
import secrets
import time

from . import config


def new_secret() -> str:
    """게임에 발급할 새 비밀키."""
    return secrets.token_hex(32)


def canonical_payload(game_id: str, user_id: str, score: int,
                      duration_ms: int, nonce: str, timestamp: int) -> str:
    """서명 대상 문자열을 한 가지 형태로 고정한다."""
    return f"{game_id}|{user_id}|{int(score)}|{int(duration_ms)}|{nonce}|{int(timestamp)}"


def sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(secret: str, payload: str, signature: str) -> bool:
    """타이밍 공격을 피하려고 compare_digest 를 쓴다."""
    if not signature:
        return False
    expected = sign(secret, payload)
    return hmac.compare_digest(expected, signature.strip().lower())


def timestamp_is_fresh(timestamp: int, now: int = None) -> bool:
    """너무 오래된(또는 미래의) 요청을 거른다. 재전송 공격 방어의 한 축."""
    now = now if now is not None else int(time.time())
    return abs(now - int(timestamp)) <= config.SIGNATURE_TOLERANCE_SECONDS


def is_admin(req) -> bool:
    """
    관리자 인증. 헤더 X-Admin-Secret 또는 쿼리스트링 admin_secret 을 본다.
    운영에서는 반드시 HTTPS 위에서만 쓰세요.
    """
    provided = req.headers.get("X-Admin-Secret", "") or req.args.get("admin_secret", "")
    if not provided:
        return False
    return hmac.compare_digest(provided, config.ADMIN_SECRET)


def new_nonce() -> str:
    return secrets.token_hex(16)


# ===================== 유저 토큰 =====================
# 잔액 조회나 전환 요청 같은 "그 유저 본인만 해야 하는" API 를
# 아무나 user_id 만 바꿔 호출하지 못하게 막는 장치.
#
#   게임 백엔드가 POST /auth/session 으로 토큰을 발급받아 앱에 내려주고,
#   앱은 이후 요청에 X-User-Token 헤더로 붙인다.
#
# 형식: "<만료시각>.<서명>"

USER_TOKEN_TTL_SECONDS = 24 * 60 * 60


def issue_user_token(user_id: str, ttl: int = USER_TOKEN_TTL_SECONDS) -> str:
    exp = int(time.time()) + int(ttl)
    mac = sign(config.SERVER_SECRET, f"usertoken|{user_id}|{exp}")
    return f"{exp}.{mac}"


def verify_user_token(user_id: str, token: str) -> bool:
    if not token or "." not in token:
        return False
    exp_str, mac = token.split(".", 1)
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    expected = sign(config.SERVER_SECRET, f"usertoken|{user_id}|{exp}")
    return hmac.compare_digest(expected, mac.strip().lower())


def auth_payload(game_id: str, user_id: str, timestamp: int) -> str:
    """POST /auth/session 서명 대상 문자열."""
    return f"auth|{game_id}|{user_id}|{int(timestamp)}"
