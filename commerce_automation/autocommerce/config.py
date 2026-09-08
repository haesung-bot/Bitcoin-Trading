"""설정 로더.

우선순위: 환경변수 > settings.yaml > 코드 기본값

비밀값(API 키/시크릿)은 YAML 에 절대 넣지 않는다. YAML 에는 `${ENV_NAME}` 참조만
쓰고 실제 값은 환경변수(.env)로 주입한다. 이렇게 해야 설정 파일을 git 에 커밋해도
안전하다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


class ConfigError(RuntimeError):
    pass


def _expand(value: Any) -> Any:
    """`${VAR}` / `${VAR:-default}` 를 환경변수로 치환."""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            got = os.environ.get(name)
            if got is None:
                if default is None:
                    return ""  # 비어 있으면 사용 시점에 검증
                return default
            return got
        return ENV_REF.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_dotenv(path: str | Path = ".env") -> None:
    """의존성 없이 .env 를 읽어 환경변수로 올린다(기존 환경변수 우선)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    # ---- 조회 헬퍼 -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        val = self.get(dotted)
        if val in (None, "", []):
            raise ConfigError(
                f"설정값 '{dotted}' 이(가) 비어 있습니다. "
                f"settings.yaml 또는 환경변수를 확인하세요."
            )
        return val

    def section(self, name: str) -> dict[str, Any]:
        val = self.get(name, {})
        return val if isinstance(val, dict) else {}

    # ---- 자주 쓰는 값 ---------------------------------------------
    @property
    def dry_run(self) -> bool:
        return bool(self.get("runtime.dry_run", True))

    @property
    def workdir(self) -> Path:
        return Path(self.get("runtime.workdir", "./var")).expanduser()

    @property
    def db_path(self) -> Path:
        return Path(self.get("runtime.db_path", str(self.workdir / "autocommerce.db")))

    def credentials_ready(self, market: str) -> tuple[bool, list[str]]:
        """마켓 자격증명이 다 채워졌는지 확인. (준비여부, 빈 키 목록)"""
        cfg = self.section(f"markets.{market}")
        missing = [
            k for k in ("access_key", "secret_key", "vendor_id",
                        "client_id", "client_secret")
            if k in cfg and not cfg.get(k)
        ]
        return (not missing), missing


DEFAULTS: dict[str, Any] = {
    "runtime": {
        "dry_run": True,
        "workdir": "./var",
        "log_level": "INFO",
        "concurrency": 4,
    },
    "crawler": {
        "user_agent": "autocommerce/0.1",
        "respect_robots": True,
        "rate_per_sec": 0.5,
        "timeout": 20,
    },
    "pricing": {"default_policy": "standard"},
    "seo": {"max_title_len": 100, "max_tags": 10},
    "imaging": {"enabled": True, "provider": "local", "size": 1000},
    "markets": {},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path | None = None, *, dotenv: str | Path = ".env") -> Settings:
    load_dotenv(dotenv)
    data = dict(DEFAULTS)
    resolved: Path | None = None
    if path:
        resolved = Path(path)
        if not resolved.exists():
            raise ConfigError(f"설정 파일이 없습니다: {resolved}")
        loaded = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, loaded)
    return Settings(raw=_expand(data), path=resolved)
