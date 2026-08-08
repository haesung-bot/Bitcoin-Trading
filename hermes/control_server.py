"""헤르메스 제어용 로컬 HTTP API.

표준 라이브러리만 사용하므로 추가 의존성이 없다 (봇이 PyInstaller로 exe 빌드되는
구조라 의존성을 늘리지 않는 편이 안전하다).

보안 관련 기본값:
  * 127.0.0.1(로컬호스트)에만 바인딩한다. 이 API는 실거래 계좌를 움직일 수 있으므로
    기본적으로 같은 PC에서만 접근 가능해야 한다.
  * 모든 제어 엔드포인트는 Bearer 토큰을 요구하며, 비교는 타이밍 공격에 안전한
    hmac.compare_digest로 한다.
  * 외부 IP에 바인딩하려면 allow_remote=True를 명시해야 한다. 그 경우에도
    토큰은 반드시 있어야 한다.

엔드포인트:
    GET  /health          인증 불필요. 서버가 살아있는지만 확인.
    GET  /status          상태·포지션·잔고
    GET  /params          설정 항목과 현재 값
    GET  /log?limit=20    제어 명령 이력
    POST /command         {"command": "set", "args": ["leverage", "5"]}
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from .agent import HermesAgent
from .commands import CommandError, dispatch, help_text

# 요청 본문 상한. 제어 명령은 아주 작으므로 넉넉히 잡아도 64KB면 충분하다.
MAX_BODY_BYTES = 64 * 1024


class ControlServer:
    """에이전트를 감싸는 HTTP 제어 서버."""

    def __init__(
        self,
        agent: HermesAgent,
        host: str = "127.0.0.1",
        port: int = 8787,
        token: Optional[str] = None,
        allow_remote: bool = False,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
            raise ValueError(
                f"'{host}'는 로컬호스트가 아닙니다. 이 API는 실거래를 제어하므로 "
                "외부 노출은 기본적으로 막혀 있습니다. 정말 필요하면 allow_remote=True를 "
                "명시하고, 반드시 방화벽과 강력한 토큰을 함께 쓰세요."
            )

        self.agent = agent
        self.host = host
        self.port = port
        self._logger = logger
        # 토큰 우선순위: 인자 > 환경변수 > 자동 생성
        self.token = token or os.environ.get("HERMES_TOKEN") or secrets.token_urlsafe(24)
        self._generated_token = not (token or os.environ.get("HERMES_TOKEN"))

        # HTTP 헤더는 latin-1로만 인코딩되므로 한글 등이 섞인 토큰은 대부분의
        # 클라이언트가 아예 보내지 못한다. 나중에 '인증이 안 된다'로 헤매지 않도록
        # 여기서 미리 막는다.
        if not self.token.isascii():
            raise ValueError(
                "토큰에는 ASCII 문자만 쓸 수 있습니다 (HTTP 헤더 제약). "
                "영문/숫자/기호 조합으로 설정해주세요."
            )
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(f"[hermes-api] {message}")

    def start(self) -> str:
        """서버를 백그라운드 스레드로 띄우고 접속 URL을 돌려준다."""
        if self._httpd is not None:
            raise RuntimeError("제어 서버가 이미 실행 중입니다.")

        handler = _make_handler(self.agent, self.token, self._log)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        # 실제 바인딩된 포트 (port=0으로 임의 포트를 요청한 경우 대비)
        self.port = self._httpd.server_address[1]

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="hermes-control-server",
            daemon=True,
        )
        self._thread.start()

        url = f"http://{self.host}:{self.port}"
        self._log(f"제어 API 시작: {url}")
        if self._generated_token:
            # 자동 생성된 토큰은 여기서 한 번 알려주지 않으면 쓸 방법이 없다.
            self._log(f"접속 토큰(자동 생성): {self.token}")
            self._log("고정 토큰을 쓰려면 HERMES_TOKEN 환경변수를 설정하세요.")
        return url

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None
        self._log("제어 API를 정지했습니다.")

    def __enter__(self) -> "ControlServer":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()


def _make_handler(
    agent: HermesAgent,
    token: str,
    log: Callable[[str], None],
) -> type[BaseHTTPRequestHandler]:
    """에이전트/토큰을 클로저로 묶은 요청 핸들러 클래스를 만든다."""

    token_bytes = token.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "Hermes/1.0"

        # ---------- 응답 도구 ----------

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # 제어 응답은 절대 캐시되면 안 된다.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            # 바이트로 비교한다. compare_digest는 ASCII 밖의 str을 받으면
            # TypeError를 내기 때문에, 이상한 토큰이 들어와도 죽지 않게 한다.
            supplied = header[len(prefix):].strip().encode("utf-8", "surrogateescape")
            return hmac.compare_digest(supplied, token_bytes)

        def _require_auth(self) -> bool:
            if self._authorized():
                return True
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "인증 실패 — Authorization: Bearer <토큰> 헤더가 필요합니다."},
            )
            return False

        # ---------- 라우팅 ----------

        def do_GET(self) -> None:  # noqa: N802 (http.server가 요구하는 이름)
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/health":
                # 인증 없이도 살아있는지는 알 수 있게 한다. 상태값은 노출하지 않는다.
                self._send_json(HTTPStatus.OK, {"ok": True, "service": "hermes"})
                return

            if not self._require_auth():
                return

            if path == "/status":
                self._run(lambda: dispatch(agent, "status"))
            elif path == "/params":
                self._run(lambda: dispatch(agent, "params"))
            elif path == "/log":
                limit = parse_qs(parsed.query).get("limit", ["20"])[0]
                self._run(lambda: dispatch(agent, "log", [limit]))
            elif path == "/":
                self._send_json(HTTPStatus.OK, {"ok": True, "help": help_text()})
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"없는 경로입니다: {path}"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if not self._require_auth():
                return

            if path != "/command":
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"없는 경로입니다: {path}"})
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Content-Length가 올바르지 않습니다."})
                return

            if length > MAX_BODY_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"ok": False, "error": "요청 본문이 너무 큽니다."},
                )
                return

            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"JSON 파싱 실패: {exc}"})
                return

            if not isinstance(payload, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "본문은 JSON 객체여야 합니다."})
                return

            name = payload.get("command")
            if not isinstance(name, str) or not name:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "'command' 필드가 필요합니다.", "help": help_text()},
                )
                return

            args = payload.get("args", [])
            if not isinstance(args, list):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "'args'는 배열이어야 합니다."})
                return

            log(f"{self.client_address[0]} → {name} {args}")
            self._run(lambda: dispatch(agent, name, [str(a) for a in args]))

        # ---------- 실행 래퍼 ----------

        def _run(self, action: Callable[[], dict[str, Any]]) -> None:
            """명령을 실행하고 예외를 HTTP 상태 코드로 변환한다."""
            try:
                result = action()
            except CommandError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # 서버가 죽지 않도록 최후 방어
                log(f"명령 처리 중 예외: {exc}")
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": f"내부 오류: {exc}"},
                )
                return

            # 명령 자체가 거부된 경우(ok=False)도 요청은 올바르게 처리된 것이므로 200으로 돌려주고,
            # 성공 여부는 본문의 ok 필드로 판단하게 한다.
            self._send_json(HTTPStatus.OK, result)

        # ---------- 로깅 ----------

        def log_message(self, format: str, *args: Any) -> None:
            # 기본 구현은 stderr로 직접 찍는다. exe로 패키징된 GUI 앱에서는
            # stderr가 없을 수 있으므로 주입된 로거로 돌린다.
            log(format % args)

    return Handler
