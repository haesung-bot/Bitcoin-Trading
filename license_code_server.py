"""
license_code_server.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
운영자가 직접 발급하고 직접 차단하는 "코드 기반" 라이선스 서버.

거래소 API 연동 없이, 아래 흐름으로 동작합니다:
  1. 사용자가 레퍼럴 링크로 가입 → 운영자가 거래소 어필리에이트 대시보드에서
     그 사용자가 실제로 들어왔는지 육안으로 확인
  2. 운영자가 /admin/generate 로 활성화 코드 발급 → 사용자에게 코드 전달
  3. 사용자는 프로그램(exe) 최초 실행 시 이 코드를 입력
  4. 프로그램은 24시간마다 서버에 "이 코드 아직 살아있어?"를 재확인
  5. 운영자가 주기적으로(예: 매주) 거래소 대시보드에서 거래내역을 확인하다가
     "N일 이상 거래가 없다" 싶으면 /admin/block 으로 그 코드를 차단
     → 사용자는 다음 재검증 시점(늦어도 24시간 이내)에 프로그램이 멈춤

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행 방법:
    pip install flask --break-system-packages
    python license_code_server.py

실제 배포하려면 본인 소유 서버(VPS, Render, Railway 등)에 올려서
24시간 띄워둬야 하고, license_client_code.py의 LICENSE_SERVER_URL을
그 서버의 공인 주소로 바꿔야 합니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from flask import Flask, request, jsonify
import hmac
import hashlib
import time
import os
import sqlite3
import secrets

app = Flask(__name__)

# ===================== 서버 설정 =====================
SERVER_SECRET = os.environ.get("LICENSE_SERVER_SECRET", "CHANGE_THIS_TO_A_RANDOM_LONG_SECRET")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "CHANGE_THIS_ADMIN_SECRET")

TOKEN_VALID_SECONDS = 24 * 60 * 60          # 활성화 토큰 유효 기간 (24시간마다 재검증)
REVIEW_WARNING_DAYS = 7                      # 이 기간 이상 미점검이면 관리자 목록에서 경고 표시

# Render 영구 디스크 마운트 경로를 환경변수(DATA_DIR)로 지정할 수 있게 함.
# 1) DATA_DIR 환경변수가 있으면 그 경로를 최우선 사용 (Render 대시보드에서 디스크 만들 때 설정한 Mount Path와 반드시 일치해야 함)
# 2) 없으면 관례적으로 많이 쓰이는 /data가 존재하는지 확인
# 3) 그것도 없으면(로컬 테스트 등) 기존처럼 코드 파일과 같은 폴더 사용 — 이 경우 Render에서는 재배포 시 사라짐
_data_dir = os.environ.get("DATA_DIR", "").strip()
if _data_dir and os.path.isdir(_data_dir):
    DB_PATH = os.path.join(_data_dir, "license_codes.db")
elif os.path.exists("/data"):
    DB_PATH = "/data/license_codes.db"
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "license_codes.db")

print(f"ℹ️ DB 저장 경로: {DB_PATH}")
if not (os.path.exists("/data") or (_data_dir and os.path.isdir(_data_dir))):
    print("⚠️ 영구 디스크가 감지되지 않았습니다! 이 상태로 재배포하면 코드/유저 기록이 초기화됩니다. "
          "Render에서 Persistent Disk를 추가하고 DATA_DIR 환경변수를 그 Mount Path와 동일하게 설정하세요.")


# ===================== DB =====================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            code TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'active',   -- 'active' / 'blocked'
            note TEXT,                                -- 사용자 식별용 메모 (예: 텔레그램ID, 이메일)
            issued_at INTEGER,
            last_operator_check INTEGER,              -- 운영자가 마지막으로 거래내역을 확인한 시각
            blocked_at INTEGER,
            blocked_reason TEXT,
            bound_device_id TEXT,                     -- 이 코드를 처음 사용한 기기의 고유값 (중복사용 방지)
            expires_at INTEGER                        -- 이 시각이 지나면 자동 만료 (NULL이면 무기한)
        )
    """)
    # 이전 버전 DB에는 없던 컬럼들을 마이그레이션
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(codes)").fetchall()]
    if "bound_device_id" not in existing_cols:
        conn.execute("ALTER TABLE codes ADD COLUMN bound_device_id TEXT")
    if "expires_at" not in existing_cols:
        conn.execute("ALTER TABLE codes ADD COLUMN expires_at INTEGER")

    # 텔레그램 체험 코드 중복 발급 방지용 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trial_users (
            telegram_user_id TEXT PRIMARY KEY,
            code TEXT,
            issued_at INTEGER
        )
    """)
    conn.commit()
    conn.close()


def _row_to_dict(row):
    if row is None:
        return None
    keys = ["code", "status", "note", "issued_at", "last_operator_check",
            "blocked_at", "blocked_reason", "bound_device_id", "expires_at"]
    return dict(zip(keys, row))


def get_code(code):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def generate_new_code(note="", duration_days=None, duration_minutes=None):
    """duration_days 또는 duration_minutes 중 하나를 주면 그만큼만 유효한 기간제 코드를 만든다.
    (duration_minutes는 테스트용 — 예: 10분짜리로 빠르게 만료 테스트 가능)
    둘 다 안 주면 기존처럼 무기한."""
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))  # 예: A1B2-C3D4-E5F6
    now = int(time.time())

    if duration_minutes:
        expires_at = now + int(duration_minutes) * 60
    elif duration_days:
        expires_at = now + int(duration_days) * 86400
    else:
        expires_at = None

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO codes (code, status, note, issued_at, last_operator_check, expires_at) "
        "VALUES (?, 'active', ?, ?, ?, ?)",
        (code, note, now, now, expires_at)
    )
    conn.commit()
    conn.close()
    return code, expires_at


def set_status(code, status, reason=""):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    if status == "blocked":
        conn.execute(
            "UPDATE codes SET status=?, blocked_at=?, blocked_reason=? WHERE code=?",
            (status, now, reason, code)
        )
    else:
        conn.execute(
            "UPDATE codes SET status=?, blocked_at=NULL, blocked_reason=NULL WHERE code=?",
            (status, code)
        )
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def mark_checked(code):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE codes SET last_operator_check=? WHERE code=?", (now, code))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def bind_device(code, device_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE codes SET bound_device_id=? WHERE code=?", (device_id, code))
    conn.commit()
    conn.close()


def reset_device_binding(code):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE codes SET bound_device_id=NULL WHERE code=?", (code,))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def is_expired(record):
    exp = record.get("expires_at")
    return exp is not None and int(time.time()) > int(exp)


def _readable(ts):
    """유닉스 타임스탬프(초)를 'YYYY-MM-DD HH:MM:SS' 형태로 바꾼다. 값이 없으면 None."""
    if not ts:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def list_all_codes():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM codes ORDER BY issued_at DESC").fetchall()
    conn.close()
    now = int(time.time())
    result = []
    for row in rows:
        d = _row_to_dict(row)
        days_since_check = (now - (d["last_operator_check"] or now)) / 86400
        d["days_since_check"] = round(days_since_check, 1)
        d["needs_review"] = d["status"] == "active" and days_since_check >= REVIEW_WARNING_DAYS
        d["expired"] = is_expired(d)
        if d.get("expires_at"):
            d["days_remaining"] = round((d["expires_at"] - now) / 86400, 1)
        else:
            d["days_remaining"] = None  # 무기한

        # ⚠️ 아래 필드들은 전부 '초 단위 유닉스 타임스탬프'라서 언뜻 보면 큰 숫자라 다른 값(유저ID 등)과
        # 헷갈리기 쉽습니다. 그래서 사람이 읽기 쉬운 날짜 형태를 같이 넣어드립니다.
        d["issued_at_readable"] = _readable(d.get("issued_at"))
        d["last_operator_check_readable"] = _readable(d.get("last_operator_check"))
        d["blocked_at_readable"] = _readable(d.get("blocked_at"))
        d["expires_at_readable"] = _readable(d.get("expires_at"))  # 무기한이면 None으로 표시됨

        result.append(d)
    return result


# ===================== 토큰 (기기ID 포함) =====================
def issue_token(code, device_id):
    expires_at = int(time.time()) + TOKEN_VALID_SECONDS
    payload = f"{code}:{device_id}:{expires_at}"
    signature = hmac.new(SERVER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def validate_token(token, device_id):
    try:
        code, token_device_id, expires_at, signature = token.rsplit(":", 3)
        payload = f"{code}:{token_device_id}:{expires_at}"
        expected_sig = hmac.new(SERVER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return False, "서명 불일치 (위조된 토큰)"
        if int(time.time()) > int(expires_at):
            return False, "토큰 만료 (재검증 필요)"
        if token_device_id != device_id:
            return False, "다른 기기에서 발급된 토큰입니다 (기기 불일치)"
        return True, {"code": code, "device_id": token_device_id}
    except Exception as e:
        return False, f"토큰 형식 오류: {e}"


def _check_admin_auth():
    return request.headers.get("X-Admin-Secret", "") == ADMIN_SECRET


# ===================== 사용자(클라이언트) API =====================
@app.route("/api/verify", methods=["POST"])
def api_verify():
    """클라이언트(봇)가 코드+기기ID를 보내 활성 상태를 확인하고 토큰을 발급받는다.
    코드가 처음 사용되는 거면 그 기기에 자동으로 묶이고(바인딩),
    이미 다른 기기에 묶여있는 코드면 거부한다 (중복사용 방지)."""
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip().upper()
    device_id = data.get("device_id", "").strip()

    if not code:
        return jsonify({"ok": False, "error": "코드를 입력해주세요."}), 400
    if not device_id:
        return jsonify({"ok": False, "error": "기기 정보를 확인할 수 없습니다 (device_id 누락)."}), 400

    record = get_code(code)
    if record is None:
        return jsonify({"ok": False, "error": "존재하지 않는 코드입니다."}), 404

    if record["status"] == "blocked":
        reason = record.get("blocked_reason") or "장기간 거래 활동이 없어 이용이 제한되었습니다."
        return jsonify({"ok": False, "error": f"이 코드는 차단되었습니다: {reason}"}), 403

    if is_expired(record):
        return jsonify({"ok": False, "error": "이 코드의 이용 기간이 만료되었습니다."}), 403

    bound = record.get("bound_device_id")
    if bound is None:
        # 이 코드가 처음 사용됨 — 지금 이 기기에 자동으로 묶는다
        bind_device(code, device_id)
    elif bound != device_id:
        return jsonify({
            "ok": False,
            "error": "이 코드는 이미 다른 기기에서 사용 중입니다. 기기를 바꾸셨다면 운영자에게 '기기 초기화'를 요청해주세요."
        }), 403

    token = issue_token(code, device_id)
    return jsonify({"ok": True, "token": token})


@app.route("/api/check_token", methods=["POST"])
def api_check_token():
    """캐시된 토큰이 여전히 유효한지 확인. 코드가 그 사이 차단됐거나
    기기 바인딩이 초기화/변경됐으면 즉시 무효 처리."""
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token", "")
    device_id = data.get("device_id", "")

    valid, info = validate_token(token, device_id)
    if not valid:
        return jsonify({"ok": False, "error": info}), 403

    # 서명/만료/기기일치가 유효해도, 그 사이 관리자가 차단했거나
    # 기기 바인딩을 초기화했거나 기간이 만료됐을 수 있으므로 현재 DB 상태를 다시 확인
    record = get_code(info["code"])
    if record is None or record["status"] == "blocked":
        return jsonify({"ok": False, "error": "코드가 차단되었거나 존재하지 않습니다."}), 403
    if is_expired(record):
        return jsonify({"ok": False, "error": "이 코드의 이용 기간이 만료되었습니다."}), 403
    if record.get("bound_device_id") != device_id:
        return jsonify({"ok": False, "error": "기기 바인딩이 변경되었습니다. 다시 인증해주세요."}), 403

    return jsonify({"ok": True, "info": info})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": int(time.time())})


# ===================== 운영자(관리자) API =====================
@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    """코드 발급.
    - duration_days를 주면 그 일수만큼만 유효한 기간제 코드
    - duration_minutes를 주면 그 분(分)만큼만 유효 (테스트용, 예: 10분)
    - 둘 다 안 주면(또는 0/null) 무기한 코드
    예: {"note": "텔레그램 @user", "duration_days": 30}
    예: {"note": "테스트", "duration_minutes": 10}"""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    note = data.get("note", "")
    duration_days = data.get("duration_days")
    duration_minutes = data.get("duration_minutes")

    try:
        duration_days = int(duration_days) if duration_days else None
        if duration_days is not None and duration_days <= 0:
            raise ValueError
        duration_minutes = int(duration_minutes) if duration_minutes else None
        if duration_minutes is not None and duration_minutes <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "duration_days/duration_minutes는 0보다 큰 정수여야 합니다."}), 400

    code, expires_at = generate_new_code(note, duration_days, duration_minutes)
    return jsonify({
        "ok": True,
        "code": code,
        "duration_days": duration_days,
        "duration_minutes": duration_minutes,
        "expires_at": expires_at  # null이면 무기한
    })


@app.route("/admin/block", methods=["POST"])
def admin_block():
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip().upper()
    reason = data.get("reason", "장기간 거래 활동 없음")
    if set_status(code, "blocked", reason):
        return jsonify({"ok": True, "message": f"{code} 차단 완료"})
    return jsonify({"ok": False, "error": "해당 코드를 찾을 수 없습니다."}), 404


@app.route("/admin/unblock", methods=["POST"])
def admin_unblock():
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip().upper()
    if set_status(code, "active"):
        return jsonify({"ok": True, "message": f"{code} 차단 해제 완료"})
    return jsonify({"ok": False, "error": "해당 코드를 찾을 수 없습니다."}), 404


@app.route("/admin/reset_device", methods=["POST"])
def admin_reset_device():
    """사용자가 정말로 PC를 바꾼 경우, 기기 바인딩을 초기화해서
    새 기기에서 다시 인증받을 수 있게 해준다."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip().upper()
    if reset_device_binding(code):
        return jsonify({"ok": True, "message": f"{code} 기기 바인딩 초기화 완료 — 다음 접속 기기로 새로 묶입니다."})
    return jsonify({"ok": False, "error": "해당 코드를 찾을 수 없습니다."}), 404


@app.route("/admin/mark_checked", methods=["POST"])
def admin_mark_checked():
    """운영자가 거래소 대시보드에서 거래내역을 확인했다는 걸 기록 (차단 여부와 별개).
    이걸 눌러두면 '점검 필요' 목록에서 당분간 빠진다."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip().upper()
    if mark_checked(code):
        return jsonify({"ok": True, "message": f"{code} 점검 기록 갱신 완료"})
    return jsonify({"ok": False, "error": "해당 코드를 찾을 수 없습니다."}), 404


@app.route("/admin/list", methods=["GET"])
def admin_list():
    """전체 코드 목록. needs_review=true인 항목이 'N일 이상 안 본 코드'입니다."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    return jsonify({
        "ok": True,
        "codes": list_all_codes(),
        "review_warning_days": REVIEW_WARNING_DAYS,
        "note": (
            "issued_at/last_operator_check/blocked_at/expires_at는 초 단위 유닉스 타임스탬프입니다 "
            "(예: expires_at=1784312256는 텔레그램 유저ID가 아니라 '만료 시각'입니다). "
            "각 필드 옆의 '_readable' 값이 사람이 읽기 쉬운 날짜입니다. "
            "이 목록은 '코드' 정보만 담고 있으며, 어떤 텔레그램 유저가 받았는지는 "
            "/admin/list_trial_users 에서 확인하세요."
        )
    })


# ===================== 텔레그램 체험코드 중복발급 방지 =====================
def check_trial_issued(telegram_user_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT code, issued_at FROM trial_users WHERE telegram_user_id=?",
        (str(telegram_user_id),)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"code": row[0], "issued_at": row[1]}


def mark_trial_issued(telegram_user_id, code):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO trial_users (telegram_user_id, code, issued_at) VALUES (?, ?, ?)",
            (str(telegram_user_id), code, int(time.time()))
        )
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False  # 이미 기록된 유저(동시요청 등으로 중복 시도된 경우) — 중복 저장 방지
    conn.close()
    return success


@app.route("/admin/check_trial_user", methods=["POST"])
def api_check_trial_user():
    """텔레그램 봇이 코드 발급 전에 '이 유저 이미 받았는지' 확인할 때 호출.
    관리자 비밀번호로 보호되어 있어 봇 쪽에도 ADMIN_SECRET이 필요합니다."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    telegram_user_id = data.get("telegram_user_id", "")
    if not telegram_user_id:
        return jsonify({"ok": False, "error": "telegram_user_id가 필요합니다."}), 400

    existing = check_trial_issued(telegram_user_id)
    if existing:
        return jsonify({"ok": True, "already_issued": True, "detail": existing})
    return jsonify({"ok": True, "already_issued": False})


@app.route("/admin/mark_trial_issued", methods=["POST"])
def api_mark_trial_issued():
    """텔레그램 봇이 코드를 발급한 직후, '이 유저는 받았다'고 기록할 때 호출."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    telegram_user_id = data.get("telegram_user_id", "")
    code = data.get("code", "")
    if not telegram_user_id or not code:
        return jsonify({"ok": False, "error": "telegram_user_id와 code가 모두 필요합니다."}), 400

    success = mark_trial_issued(telegram_user_id, code)
    if success:
        return jsonify({"ok": True, "message": "기록 완료"})
    return jsonify({"ok": False, "error": "이미 기록된 유저입니다 (중복 방지됨)"}), 409


@app.route("/admin/list_trial_users", methods=["GET"])
def api_list_trial_users():
    """지금까지 체험 코드를 받은 텔레그램 유저 전체 목록 조회.
    브라우저 주소창이나 PowerShell로 직접 확인할 수 있습니다."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT telegram_user_id, code, issued_at FROM trial_users ORDER BY issued_at DESC"
    ).fetchall()
    conn.close()
    result = [
        {"telegram_user_id": r[0], "code": r[1], "issued_at": r[2],
         "issued_at_readable": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[2]))}
        for r in rows
    ]
    return jsonify({"ok": True, "count": len(result), "trial_users": result})


@app.route("/admin/reset_trial_user", methods=["POST"])
def admin_reset_trial_user():
    """테스트/재발급 목적으로 특정 텔레그램 유저의 '체험코드 받음' 기록을 지운다.
    이후 그 유저가 /start를 다시 누르면 새 7일 코드를 자동으로 받을 수 있게 된다."""
    if not _check_admin_auth():
        return jsonify({"ok": False, "error": "관리자 인증 실패"}), 401
    data = request.get_json(force=True, silent=True) or {}
    tgt_id = data.get("telegram_user_id", "")
    if not tgt_id:
        return jsonify({"ok": False, "error": "telegram_user_id가 필요합니다."}), 400
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM trial_users WHERE telegram_user_id=?", (str(tgt_id),))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    if changed > 0:
        return jsonify({"ok": True, "message": f"유저 {tgt_id} 리셋 완료 — 다시 /start 하면 새 코드를 받습니다."})
    return jsonify({"ok": False, "error": "해당 유저의 발급 기록을 찾을 수 없습니다."}), 404


if __name__ == "__main__":
    init_db()
    print("⚠️  실행 전 확인: SERVER_SECRET / ADMIN_SECRET을 랜덤 값으로 교체했는지 확인하세요.")
    port = int(os.environ.get("PORT", 5000))  # Render/Railway 등은 PORT 환경변수로 포트를 지정함
    app.run(host="0.0.0.0", port=port, debug=False)