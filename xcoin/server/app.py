"""
app.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Xcoin 포인트 시스템 HTTP API.

전체 흐름 한눈에 보기
─────────────────────────────────────────────
  [퍼즐게임 앱]         [게임 백엔드]          [이 서버]           [블록체인]
       │                     │                    │                    │
   한 판 종료 ──점수──▶  secret 으로 서명 ──▶ /points/submit           │
       │                     │              (치트검사·한도) 포인트 적립  │
       │                                          │                    │
   지갑 연결 ◀──── 서명문구 ──── /wallet/nonce ────┤                    │
       │──── 서명 ────────────▶ /wallet/link ─────▶ 지갑 확인·저장       │
       │                                          │                    │
   "코인으로 바꾸기" ─────────▶ /exchange/convert ─▶ 포인트 차감·대기열   │
                                                  │                    │
                                            지급 워커 ──── transfer ───▶ 유저 지갑
─────────────────────────────────────────────
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import time

from flask import Flask, jsonify, request, send_from_directory

from . import chain, config, db, exchange, points, security, wallet
from .admin import admin_bp
from .points import PointError

DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo")


def _require_user(user_id: str) -> None:
    """이 요청이 정말 그 유저 본인(또는 그 유저를 대신하는 게임 백엔드)인지 확인."""
    if not user_id:
        raise PointError("bad_request", "user_id 가 필요합니다.")
    if not config.REQUIRE_USER_TOKEN:
        return
    token = request.headers.get("X-User-Token", "") or request.args.get("user_token", "")
    if not security.verify_user_token(user_id, token):
        raise PointError("unauthorized",
                         "유저 토큰이 없거나 만료되었습니다. /auth/session 으로 발급받으세요.",
                         401)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def create_app(quiet: bool = False) -> Flask:
    app = Flask(__name__)
    db.init_db()

    if not quiet:
        for warning in config.warn_if_insecure():
            print(f"[xcoin] ⚠️  {warning}")
        print(f"[xcoin] DB: {config.DB_PATH}")
        print(f"[xcoin] 코인: {config.COIN_NAME}({config.COIN_SYMBOL}), "
              f"{config.POINTS_PER_XCOIN} 포인트 = 1 {config.COIN_SYMBOL}")

    app.register_blueprint(admin_bp)

    # ---------- 공통 오류 처리 ----------
    @app.errorhandler(PointError)
    def _handle_point_error(exc: PointError):
        return jsonify({"ok": False, "error": exc.code, "message": exc.message}), exc.http_status

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        app.logger.exception("처리되지 않은 오류")
        return jsonify({"ok": False, "error": "internal_error", "message": str(exc)}), 500

    @app.after_request
    def _cors(resp):
        # 게임이 웹/하이브리드일 수 있으므로 CORS 를 열어둔다.
        # 운영에서는 여러분 게임 도메인으로 좁히는 것을 권장합니다.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-User-Token, X-Admin-Secret"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.route("/<path:_any>", methods=["OPTIONS"])
    @app.route("/", methods=["OPTIONS"])
    def _preflight(_any=None):
        return "", 204

    # ---------- 기본 ----------
    @app.get("/health")
    def health():
        return jsonify({"ok": True, "time": int(time.time())})

    @app.get("/config")
    def public_config():
        """앱에서 화면에 표시할 정책값들. 비밀은 아무것도 포함하지 않는다."""
        return jsonify({
            "ok": True,
            "coin": {
                "name": config.COIN_NAME,
                "symbol": config.COIN_SYMBOL,
                "decimals": config.COIN_DECIMALS,
                "contract": config.TOKEN_CONTRACT_ADDRESS or None,
                "chain_id": config.CHAIN_ID,
                "mode": "simulation" if config.is_simulation() else "live",
            },
            "policy": {
                "points_per_xcoin": config.POINTS_PER_XCOIN,
                "min_convert_points": config.MIN_CONVERT_POINTS,
                "convert_fee_bps": config.CONVERT_FEE_BPS,
                "daily_point_cap": config.DAILY_POINT_CAP_PER_USER,
                "daily_convert_cap_points": config.DAILY_CONVERT_CAP_POINTS,
                "auto_approve": config.AUTO_APPROVE_CONVERSION,
            },
        })

    # ---------- 게임 백엔드용 세션 발급 ----------
    @app.post("/auth/session")
    def auth_session():
        """
        게임 백엔드가 게임 secret 으로 서명해서 호출하면
        그 유저용 단기 토큰(X-User-Token)을 발급한다.

        서명 대상: auth|{game_id}|{user_id}|{timestamp}
        """
        data = _body()
        game_id = str(data.get("game_id", ""))
        user_id = str(data.get("user_id", ""))
        timestamp = int(data.get("timestamp", 0))
        signature = str(data.get("signature", ""))

        if not game_id or not user_id:
            raise PointError("bad_request", "game_id 와 user_id 가 필요합니다.")

        game = points.get_game(db.get_conn(), game_id)
        if game is None:
            raise PointError("unknown_game", f"등록되지 않은 게임입니다: {game_id}", 404)
        payload = security.auth_payload(game_id, user_id, timestamp)
        if not security.verify_signature(game["secret"], payload, signature):
            raise PointError("bad_signature", "서명이 올바르지 않습니다.", 401)
        if not security.timestamp_is_fresh(timestamp):
            raise PointError("stale_timestamp", "요청 시각이 너무 오래되었습니다.", 401)

        with db.tx() as conn:
            points.ensure_user(conn, user_id)
            points.assert_not_blocked(conn, user_id)

        token = security.issue_user_token(user_id)
        return jsonify({
            "ok": True,
            "user_id": user_id,
            "user_token": token,
            "expires_in": security.USER_TOKEN_TTL_SECONDS,
        })

    # ---------- 포인트 ----------
    @app.post("/points/submit")
    def submit():
        """
        게임 한 판의 결과 제출. 서명이 필요하므로 게임 백엔드에서 호출하세요.

        body: {game_id, user_id, score, duration_ms, nonce, timestamp, signature}
        """
        data = _body()
        result = points.submit_play(
            game_id=str(data.get("game_id", "")),
            user_id=str(data.get("user_id", "")),
            score=int(data.get("score", 0)),
            duration_ms=int(data.get("duration_ms", 0)),
            nonce=str(data.get("nonce", "")),
            timestamp=int(data.get("timestamp", 0)),
            signature=str(data.get("signature", "")),
        )
        return jsonify({"ok": True, **result})

    @app.get("/points/balance")
    def balance():
        user_id = request.args.get("user_id", "")
        _require_user(user_id)
        return jsonify({"ok": True, **points.get_balance(user_id)})

    @app.get("/points/history")
    def point_history():
        user_id = request.args.get("user_id", "")
        _require_user(user_id)
        limit = min(int(request.args.get("limit", 50)), 200)
        return jsonify({"ok": True, "items": points.history(user_id, limit)})

    # ---------- 지갑 ----------
    @app.get("/wallet/nonce")
    def wallet_nonce():
        user_id = request.args.get("user_id", "")
        _require_user(user_id)
        return jsonify({"ok": True, **wallet.issue_nonce(user_id)})

    @app.post("/wallet/link")
    def wallet_link():
        data = _body()
        user_id = str(data.get("user_id", ""))
        _require_user(user_id)
        result = wallet.link_wallet(
            user_id=user_id,
            address=str(data.get("address", "")),
            signature=str(data.get("signature", "")),
        )
        return jsonify({"ok": True, **result})

    @app.get("/wallet")
    def wallet_get():
        user_id = request.args.get("user_id", "")
        _require_user(user_id)
        return jsonify({"ok": True, **wallet.get_wallet(user_id)})

    @app.post("/wallet/unlink")
    def wallet_unlink():
        user_id = str(_body().get("user_id", ""))
        _require_user(user_id)
        return jsonify({"ok": True, **wallet.unlink_wallet(user_id)})

    # ---------- 전환 ----------
    @app.get("/exchange/quote")
    def exchange_quote():
        amount = int(request.args.get("points", config.MIN_CONVERT_POINTS))
        return jsonify({"ok": True, **exchange.quote(amount)})

    @app.post("/exchange/convert")
    def exchange_convert():
        data = _body()
        user_id = str(data.get("user_id", ""))
        _require_user(user_id)
        result = exchange.request_conversion(
            user_id=user_id,
            points_amount=int(data.get("points", 0)),
            idempotency_key=str(data.get("idempotency_key", "")),
        )
        return jsonify({"ok": True, **result})

    @app.get("/exchange/conversions")
    def exchange_list():
        user_id = request.args.get("user_id", "")
        _require_user(user_id)
        limit = min(int(request.args.get("limit", 50)), 200)
        return jsonify({"ok": True, "items": exchange.list_conversions(user_id, limit)})

    @app.get("/exchange/conversion/<int:conv_id>")
    def exchange_one(conv_id: int):
        conv = exchange.get_conversion(conv_id)
        _require_user(conv["user_id"])
        return jsonify({"ok": True, **conv})

    # ---------- 데모 페이지 ----------
    @app.get("/demo")
    @app.get("/demo/")
    def demo_index():
        return send_from_directory(DEMO_DIR, "index.html")

    @app.get("/demo/<path:filename>")
    def demo_files(filename: str):
        return send_from_directory(DEMO_DIR, filename)

    return app


def main():
    app = create_app()
    exchange.start_worker()
    port = int(os.environ.get("PORT", "8080"))
    print(f"[xcoin] http://0.0.0.0:{port}  (데모: /demo)")
    print(f"[xcoin] 체인 상태: {chain.status()}")
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
