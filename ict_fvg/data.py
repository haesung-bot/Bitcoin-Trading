"""
ict_fvg/data.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CCXT로 OHLCV(봉 데이터)를 받아오고 CSV로 캐싱하는 모듈.

거래소는 한 번에 500~1500개 정도만 주기 때문에, 원하는 기간을 다 모으려면
시간을 밀어가며 여러 번 요청해야 합니다. 그 반복을 여기서 처리합니다.

한 번 받은 데이터는 CSV로 저장해두고, 다음 실행 때는 네트워크를 타지 않습니다.
(파라미터를 바꿔가며 백테스트를 반복할 때 훨씬 빠릅니다)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import re
import time

import pandas as pd

from .config import timeframe_ms

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".ohlcv_cache")

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# 연속으로 이만큼 실패하면 '다시 시도해도 안 되는 오류'로 보고 포기한다.
MAX_CONSECUTIVE_FAILURES = 4


def _safe_name(text: str) -> str:
    """심볼(BTC/USDT:USDT 등)을 파일명으로 쓸 수 있게 바꾼다."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def _cache_path(cache_dir, exchange, symbol, timeframe, start, end):
    name = f"{_safe_name(exchange)}_{_safe_name(symbol)}_{timeframe}_{start}_{end}.csv"
    return os.path.join(cache_dir, name)


def _build_exchange(exchange_id: str, market_type: str, trust_env: bool = True):
    """CCXT 거래소 객체를 만든다. 선물/현물에 따라 옵션이 달라진다.

    trust_env=True면 표준 환경변수(HTTPS_PROXY, REQUESTS_CA_BUNDLE)를 따르게 한다.
    ⚠️ CCXT는 내부 세션의 trust_env를 기본으로 False로 꺼두기 때문에,
       이걸 켜주지 않으면 회사 프록시나 사설 CA 환경에서 SSL 오류가 난다.
    """
    import ccxt

    if not hasattr(ccxt, exchange_id):
        raise ValueError(f"CCXT에 '{exchange_id}' 거래소가 없습니다. 이름을 확인하세요.")

    options = {}
    if market_type == "future":
        # 거래소마다 무기한 선물을 부르는 이름이 다르다.
        options["defaultType"] = "swap" if exchange_id in ("gate", "gateio", "bybit", "okx", "bitget") else "future"
    elif market_type == "spot":
        options["defaultType"] = "spot"
    else:
        raise ValueError(f"market_type은 'future' 또는 'spot'이어야 합니다: {market_type!r}")

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "options": options})
    if trust_env and getattr(exchange, "session", None) is not None:
        exchange.session.trust_env = True
    return exchange


def fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str = None,
    market_type: str = "future",
    cache_dir: str = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
    limit: int = 1000,
    max_requests: int = 5000,
    trust_env: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """지정한 기간의 봉 데이터를 모아서 DataFrame으로 돌려준다.

    start / end 는 'YYYY-MM-DD' 또는 ISO8601 문자열.
    end를 생략하면 현재 시각까지 받는다.

    반환 DataFrame:
      index   : UTC 기준 봉 '시가' 시각 (DatetimeIndex, tz-aware)
      columns : open, high, low, close, volume
    """
    end = end or pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir, exchange_id, symbol, timeframe, _safe_name(start), _safe_name(end))

    if use_cache and os.path.exists(path):
        if verbose:
            print(f"💾 캐시 사용: {path}")
        return load_csv(path)

    exchange = _build_exchange(exchange_id, market_type, trust_env=trust_env)
    tf_ms = timeframe_ms(timeframe)

    since = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    if since >= until:
        raise ValueError(f"start({start})가 end({end})보다 뒤입니다.")

    rows = []
    cursor = since
    requests_made = 0
    consecutive_failures = 0

    if verbose:
        print(f"⬇️  {exchange_id} {symbol} {timeframe} 수집 시작 ({start} ~ {end})")

    while cursor < until and requests_made < max_requests:
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
        except Exception as exc:  # 네트워크/레이트리밋 일시 오류는 잠깐 쉬고 재시도
            requests_made += 1
            consecutive_failures += 1
            # ⚠️ 무한정 재시도하면 안 된다. 지역 차단(HTTP 451)이나 잘못된 심볼처럼
            #    다시 시도해도 절대 성공하지 않는 오류가 있는데, 그 경우
            #    max_requests까지 몇 시간을 매달리게 된다. 몇 번 연속 실패하면 포기한다.
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(
                    f"{exchange_id}에서 데이터를 받지 못했습니다 "
                    f"({consecutive_failures}회 연속 실패). 마지막 오류: {exc}\n"
                    f"거래소 접속이 막혀 있거나(지역 차단), 심볼({symbol}) 또는 "
                    f"market_type({market_type}) 설정이 잘못됐을 수 있습니다."
                ) from exc
            if verbose:
                print(f"⚠️ 요청 실패 ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}회): {exc}")
            time.sleep(2 ** consecutive_failures)  # 지수 백오프
            continue

        requests_made += 1
        consecutive_failures = 0
        if not batch:
            break

        rows.extend(batch)
        next_cursor = batch[-1][0] + tf_ms

        # 커서가 앞으로 안 가면 무한루프에 빠지므로 즉시 중단한다.
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if verbose and requests_made % 10 == 0:
            got = pd.to_datetime(batch[-1][0], unit="ms", utc=True)
            print(f"   ... {len(rows):,}개 수집 (현재 {got:%Y-%m-%d %H:%M})")

    if not rows:
        raise RuntimeError(
            f"데이터를 하나도 받지 못했습니다. 심볼({symbol})/기간/market_type을 확인하세요."
        )

    df = _rows_to_frame(rows, until)
    if verbose:
        print(f"✅ 총 {len(df):,}개 봉 수집 완료 ({df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d})")

    df.to_csv(path)
    if verbose:
        print(f"💾 캐시 저장: {path}")
    return df


def _rows_to_frame(rows, until_ms: int) -> pd.DataFrame:
    """CCXT의 [[ts, o, h, l, c, v], ...]를 DataFrame으로 변환하고 정리한다."""
    df = pd.DataFrame(rows, columns=["timestamp"] + OHLCV_COLUMNS)
    df = df[df["timestamp"] < until_ms]
    # 페이지 경계에서 같은 봉이 중복으로 올 수 있다.
    df = df.drop_duplicates(subset="timestamp", keep="last")
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df[OHLCV_COLUMNS] = df[OHLCV_COLUMNS].astype(float)
    return df


def load_csv(path: str) -> pd.DataFrame:
    """저장해둔 CSV(또는 직접 준비한 CSV)를 읽는다.
    첫 컬럼이 시각이고 open/high/low/close/volume 컬럼이 있으면 된다."""
    df = pd.read_csv(path, index_col=0, parse_dates=True)

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV에 필요한 컬럼이 없습니다: {missing} (파일: {path})")

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df[~df.index.duplicated(keep="last")].sort_index()
    df[OHLCV_COLUMNS] = df[OHLCV_COLUMNS].astype(float)
    return df


def validate_ohlcv(df: pd.DataFrame, name: str = "OHLCV") -> pd.DataFrame:
    """봉 데이터가 백테스트에 쓸 수 있는 상태인지 확인한다.

    깨진 봉(고가 < 저가 등)은 조용히 잘못된 신호를 만들기 때문에 미리 걸러낸다.
    """
    if df.empty:
        raise ValueError(f"{name} 데이터가 비어 있습니다.")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"{name}의 인덱스가 DatetimeIndex가 아닙니다.")
    if not df.index.is_monotonic_increasing:
        raise ValueError(f"{name}의 시간 순서가 뒤섞여 있습니다.")

    bad = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"]) | (df["high"] < df["close"])
        | (df["low"] > df["open"]) | (df["low"] > df["close"])
        | df[OHLCV_COLUMNS].isna().any(axis=1)
    )
    if bad.any():
        raise ValueError(
            f"{name}에 정합성이 깨진 봉이 {int(bad.sum())}개 있습니다. "
            f"첫 발생 시각: {df.index[bad.argmax()]}"
        )
    return df
