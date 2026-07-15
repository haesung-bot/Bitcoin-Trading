"""
license_client_code.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
배포용 봇(exe) 안에 포함되는 클라이언트 모듈.
사용자가 운영자에게 발급받은 "활성화 코드"를 입력하면,
license_code_server.py에 물어봐서 아직 살아있는 코드인지 확인한다.

사용 예 (봇 메인 파일 상단에 추가):

    from license_client_code import ensure_license_active

    if __name__ == "__main__":
        if not ensure_license_active():
            sys.exit(1)
        # ... 이후 정상적으로 봇 GUI 실행
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import os
import time
import threading
import platform
import uuid
import hashlib
import requests
import tkinter as tk
from tkinter import simpledialog, messagebox

# ⚠️ 배포 전 반드시 본인이 운영하는 서버 주소로 교체하세요.
LICENSE_SERVER_URL = "https://bitcoin-trading-1111.onrender.com"
LICENSE_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".bot_license_code_cache.json")

# 프로그램이 켜져 있는 동안 이 주기(초)마다 서버에 재검증을 요청한다.
# 테스트할 땐 30초처럼 짧게, 실제 운영할 땐 3600(1시간)~86400(24시간) 정도로 늘리세요.
PERIODIC_RECHECK_SECONDS = 3600


def get_device_id():
    """이 PC를 식별하는 고유값을 만든다 (하드웨어 정보 기반).
    같은 PC에서는 항상 같은 값이 나오고, 다른 PC에서는 다른 값이 나온다.
    이 값으로 '코드가 어느 기기에 묶여있는지'를 서버가 판단한다."""
    raw = f"{platform.node()}-{uuid.getnode()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _load_cache():
    try:
        if os.path.exists(LICENSE_CACHE_PATH):
            with open(LICENSE_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cache(data):
    try:
        with open(LICENSE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _ask_code_dialog(root, error_message=None):
    prompt = "발급받은 활성화 코드를 입력하세요.\n(예: A1B2-C3D4-E5F6)"
    if error_message:
        prompt = f"{error_message}\n\n{prompt}"
    code = simpledialog.askstring("활성화 코드 입력", prompt, parent=root)
    return code.strip().upper() if code else None


def _request_verification(code):
    try:
        resp = requests.post(
            f"{LICENSE_SERVER_URL}/api/verify",
            json={"code": code, "device_id": get_device_id()},
            timeout=30  # Render 콜드스타트 감안
        )
        data = resp.json()
        if data.get("ok"):
            return data.get("token"), None
        return None, data.get("error", "알 수 없는 오류")
    except requests.exceptions.RequestException as e:
        return None, f"라이선스 서버 연결 실패: {e}\n인터넷 연결 또는 서버 상태를 확인해주세요."


def _check_token_remote(token):
    """토큰 상태를 확인한다. 세 가지 결과를 구분해서 반환한다:
    - 'valid': 서버가 명확히 유효하다고 응답함
    - 'invalid': 서버가 명확히 무효/차단/만료라고 응답함 (진짜로 차단된 경우)
    - 'unreachable': 서버에 연결이 안 됨/타임아웃 (Render 무료 플랜이 잠들어 있는 경우 등 — 
       진짜 차단이 아니므로 이 경우엔 프로그램을 끄면 안 되고 다음 주기에 재시도해야 한다)
    반환값: (status, detail) 튜플. detail은 unreachable일 때 실제 에러 원인 문자열."""
    try:
        resp = requests.post(
            f"{LICENSE_SERVER_URL}/api/check_token",
            json={"token": token, "device_id": get_device_id()},
            timeout=30  # Render 무료 플랜의 콜드스타트(최대 30초 정도 걸림)를 감안해 넉넉하게
        )
        data = resp.json()
        if data.get("ok"):
            # 서버가 토큰을 새로 발급해줬으면(=기존 토큰의 24시간이 지나 갱신된 경우)
            # 캐시의 토큰을 새 값으로 교체해 둔다. 이렇게 해야 무기한 코드가
            # 24시간 뒤 '만료'로 오인돼 프로그램이 꺼지는 일이 없어진다.
            new_token = data.get("token")
            if new_token and new_token != token:
                cache = _load_cache()
                cache["token"] = new_token
                _save_cache(cache)
            return "valid", None
        return "invalid", None
    except requests.exceptions.RequestException as e:
        return "unreachable", str(e)  # 네트워크 문제 — 차단으로 오인하면 안 됨, 원인은 detail에 담아 전달


def ensure_license_active():
    """
    봇 시작 시 반드시 호출.
    - 캐시된 토큰이 유효하면 통과
    - 없거나 만료/차단됐으면 코드 입력을 요구
    - 서버 연결이 일시적으로 안 되는 경우, 몇 초 후 한 번 더 재시도해본다
      (Render 무료 플랜이 잠들어 있다가 깨어나는 경우를 감안)
    - 확인하는 동안 "라이선스 확인 중입니다..." 대기창을 띄워서,
      아무 반응 없는 것처럼 보여 사용자가 프로그램이 멈췄다고 오해하지 않게 한다.
    반환값: True(실행 가능) / False(실행 차단)
    """
    # 확인 과정 내내 쓸 창을 하나만 만들어서 재사용 (창이 여러 개 생기는 것 방지)
    root = tk.Tk()
    root.title("라이선스 확인")
    root.geometry("360x130")
    root.resizable(False, False)
    # 화면 가운데쯤에 뜨도록
    root.eval('tk::PlaceWindow . center')

    status_label = tk.Label(
        root, text="🔐 라이선스 확인 중입니다...\n잠시만 기다려주세요.",
        font=("맑은 고딕", 11), pady=20, justify="center"
    )
    status_label.pack(expand=True, fill="both")
    root.update()  # 창을 즉시 화면에 그려서 "멈춘 것처럼" 보이지 않게 함

    def set_status(text):
        status_label.config(text=text)
        root.update()

    result = _ensure_license_active_inner(root, set_status)
    root.destroy()
    return result


def _ensure_license_active_inner(root, set_status):
    cache = _load_cache()
    token = cache.get("token")

    if token:
        status, detail = _check_token_remote(token)
        if status == "valid":
            return True
        if status == "unreachable":
            # 서버가 잠들어 있을 수 있으니, 대기 중임을 화면에 표시하고 잠깐 기다렸다가 한 번 더 시도
            set_status("🔄 서버 응답을 기다리는 중입니다...\n(서버가 깨어나는 중일 수 있습니다, 최대 1분 정도)")
            time.sleep(5)
            status, detail = _check_token_remote(token)
            if status == "valid":
                return True
            if status == "unreachable":
                # 그래도 안 되면, 정말 오프라인인지 사용자에게 물어봄 — 실제 에러 원인도 같이 보여줌
                retry = messagebox.askretrycancel(
                    "서버 연결 실패",
                    "라이선스 서버에 연결할 수 없습니다.\n"
                    "(서버가 깨어나는 중일 수 있습니다 — 잠시 후 다시 시도해보세요)\n\n"
                    f"실제 에러 내용: {detail}\n\n"
                    "다시 시도하시겠습니까?",
                    parent=root
                )
                if retry:
                    set_status("🔐 라이선스 확인 중입니다...\n잠시만 기다려주세요.")
                    return _ensure_license_active_inner(root, set_status)
                return False
        # status == 'invalid' → 아래로 내려가서 코드 재입력 요구

    error_message = None
    while True:
        set_status("✏️ 활성화 코드 입력을 기다리는 중입니다...")
        code = _ask_code_dialog(root, error_message)
        if not code:
            return False  # 사용자가 취소함

        set_status("🔄 코드를 확인하는 중입니다...\n잠시만 기다려주세요.")
        new_token, error = _request_verification(code)
        if new_token:
            _save_cache({"token": new_token, "code": code, "issued_at": int(time.time())})
            return True

        # 실패 시 다시 입력 기회를 주되, 메시지를 보여줌
        retry = messagebox.askretrycancel("인증 실패", f"{error}\n\n다시 시도하시겠습니까?", parent=root)
        if not retry:
            return False
        error_message = None  # 다음 다이얼로그는 깨끗하게


def start_periodic_recheck(root=None, on_blocked=None):
    """
    프로그램이 켜져 있는 동안 백그라운드에서 주기적으로(PERIODIC_RECHECK_SECONDS마다)
    서버에 재검증을 요청한다.

    ⚠️ 중요: 서버가 명확히 "차단/만료됐다"고 응답한 경우에만 프로그램을 종료한다.
    서버 연결이 일시적으로 안 되는 경우(Render 무료 플랜이 잠들어 있는 등)는
    진짜 차단이 아니므로 프로그램을 끄지 않고, 그냥 로그만 남기고 다음 주기에 재시도한다.

    사용 예 (봇 메인 파일에서, ensure_license_active() 통과 직후):

        root = tk.Tk()
        app = GateioProSuperTrendBot(root)
        start_periodic_recheck(root)   # ← 이 줄 추가
        root.mainloop()
    """
    def _default_on_blocked():
        messagebox.showerror(
            "라이선스 만료/차단",
            "라이선스가 더 이상 유효하지 않습니다.\n프로그램을 종료합니다."
        )
        os._exit(1)  # 매매 스레드가 돌고 있어도 즉시 강제 종료

    callback = on_blocked or _default_on_blocked

    def _loop():
        consecutive_unreachable = 0
        while True:
            time.sleep(PERIODIC_RECHECK_SECONDS)
            cache = _load_cache()
            token = cache.get("token")

            if not token:
                # 캐시가 아예 없는 비정상적인 상황 — 이것도 진짜 차단은 아니므로 종료하지 않고 건너뜀
                continue

            status, detail = _check_token_remote(token)

            if status == "valid":
                consecutive_unreachable = 0
                continue

            if status == "unreachable":
                # 서버 연결 문제 — 진짜 차단이 아니므로 절대 프로그램을 끄지 않는다.
                consecutive_unreachable += 1
                print(f"⚠️ 라이선스 서버 연결 실패 (재시도 예정, 연속 {consecutive_unreachable}회) "
                      f"— 원인: {detail} — 프로그램은 계속 실행됩니다.")
                continue

            # status == 'invalid' → 서버가 명확히 차단/만료라고 응답한 경우에만 진짜로 종료
            if root is not None:
                root.after(0, callback)
            else:
                callback()
            return

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    ok = ensure_license_active()
    print("라이선스 활성화 상태:", ok)
