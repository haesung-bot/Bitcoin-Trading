"""
wallet.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지갑 연동.

"이 지갑 주소가 정말 당신 것이냐"를 확인하는 표준적인 방법은
서버가 만든 일회성 문구에 지갑으로 서명하게 하고, 그 서명에서
주소를 역산해 비교하는 것입니다. (EIP-191 personal_sign)

  1. 앱: GET  /wallet/nonce?user_id=... → 서명할 문구를 받는다
  2. 앱: 메타마스크/카이카스 등으로 그 문구에 서명
  3. 앱: POST /wallet/link {user_id, address, signature}
  4. 서버: 서명에서 주소를 복구해 일치하면 연동 완료

개인키는 서버로 절대 오지 않습니다. 서명만 옵니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import re
import time

from . import config, db, security
from .points import PointError, ensure_user

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _checksum(address: str) -> str:
    """
    EIP-55 체크섬 형식으로 정규화. eth_account 가 있으면 그걸 쓰고,
    없으면 소문자로 통일한다(비교에는 지장 없음).
    """
    try:
        from eth_utils import to_checksum_address
        return to_checksum_address(address)
    except Exception:
        return address.lower()


def is_valid_address(address: str) -> bool:
    return bool(address) and bool(ADDRESS_RE.match(address.strip()))


def issue_nonce(user_id: str) -> dict:
    """지갑 서명용 일회성 문구를 발급한다."""
    if not user_id:
        raise PointError("bad_request", "user_id 가 필요합니다.")
    nonce = security.new_nonce()
    now = int(time.time())
    with db.tx() as conn:
        ensure_user(conn, user_id)
        conn.execute(
            "INSERT INTO wallet_nonces(user_id, nonce, issued_at) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET nonce = excluded.nonce, "
            "issued_at = excluded.issued_at",
            (user_id, nonce, now),
        )
    return {
        "user_id": user_id,
        "nonce": nonce,
        "message": build_message(user_id, nonce),
        "expires_in": config.WALLET_NONCE_TTL_SECONDS,
    }


def build_message(user_id: str, nonce: str) -> str:
    """
    지갑에 뜨는 서명 문구. 유저가 무엇에 서명하는지 읽고 알 수 있어야 하므로
    사람이 읽는 문장으로 만든다.
    """
    return (
        f"{config.COIN_NAME} 지갑 연동\n"
        f"\n"
        f"이 서명은 지갑 소유 확인용이며 어떤 자산도 이동하지 않습니다.\n"
        f"유저: {user_id}\n"
        f"nonce: {nonce}"
    )


def recover_address(message: str, signature: str) -> str:
    """서명에서 서명자 주소를 복구한다. eth_account 필요."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError as exc:
        raise PointError(
            "dependency_missing",
            "서버에 eth-account 가 설치되어 있지 않습니다. pip install eth-account",
            500,
        ) from exc

    try:
        encoded = encode_defunct(text=message)
        return Account.recover_message(encoded, signature=signature)
    except Exception as exc:
        raise PointError("bad_signature", f"서명을 해석할 수 없습니다: {exc}", 400) from exc


def link_wallet(user_id: str, address: str, signature: str) -> dict:
    """서명을 검증하고 지갑을 연동한다."""
    if not user_id:
        raise PointError("bad_request", "user_id 가 필요합니다.")
    address = (address or "").strip()
    if not is_valid_address(address):
        raise PointError("bad_address", "지갑 주소 형식이 올바르지 않습니다. (0x + 40자리)")

    conn = db.get_conn()
    row = conn.execute(
        "SELECT nonce, issued_at FROM wallet_nonces WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise PointError("no_nonce", "먼저 /wallet/nonce 로 서명 문구를 발급받으세요.")
    if int(time.time()) - int(row["issued_at"]) > config.WALLET_NONCE_TTL_SECONDS:
        raise PointError("nonce_expired", "서명 문구가 만료되었습니다. 다시 발급받으세요.")

    message = build_message(user_id, row["nonce"])
    recovered = recover_address(message, signature)
    if recovered.lower() != address.lower():
        raise PointError("address_mismatch", "서명한 지갑과 제출한 주소가 다릅니다.", 401)

    normalized = _checksum(address)
    now = int(time.time())
    with db.tx() as conn:
        ensure_user(conn, user_id)
        # 한 지갑이 여러 계정에 붙는 어뷰징을 막는다.
        other = conn.execute(
            "SELECT user_id FROM wallets WHERE LOWER(address) = LOWER(?) AND user_id != ?",
            (normalized, user_id),
        ).fetchone()
        if other:
            raise PointError("address_taken", "이미 다른 계정에 연동된 지갑입니다.", 409)

        conn.execute(
            "INSERT INTO wallets(user_id, address, chain_id, verified_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET address = excluded.address, "
            "chain_id = excluded.chain_id, verified_at = excluded.verified_at",
            (user_id, normalized, config.CHAIN_ID, now),
        )
        # nonce 는 한 번 쓰면 버린다.
        conn.execute("DELETE FROM wallet_nonces WHERE user_id = ?", (user_id,))

    return {"user_id": user_id, "address": normalized, "chain_id": config.CHAIN_ID,
            "verified_at": now}


def get_wallet(user_id: str) -> dict:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT address, chain_id, verified_at FROM wallets WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return {"user_id": user_id, "linked": False}
    return {
        "user_id": user_id,
        "linked": True,
        "address": row["address"],
        "chain_id": row["chain_id"],
        "verified_at": row["verified_at"],
    }


def unlink_wallet(user_id: str) -> dict:
    with db.tx() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM conversions WHERE user_id = ? "
            "AND status IN ('pending', 'approved', 'sending')",
            (user_id,),
        ).fetchone()
        if int(pending["c"]) > 0:
            raise PointError("pending_conversion",
                             "처리 중인 전환 요청이 있어 지갑을 해제할 수 없습니다.", 409)
        conn.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))
    return {"user_id": user_id, "linked": False}
