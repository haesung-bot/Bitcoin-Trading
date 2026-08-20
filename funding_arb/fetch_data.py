# -*- coding: utf-8 -*-
"""
상위 10개 거래소 BTC 무기한선물 펀딩비 / 가격 수집기
====================================================

수집 대상 (2026년 무기한선물 거래대금 상위권 기준):
  binance, okx, bitget, gate, mexc, htx, kucoin, bingx, hyperliquid, coinbase

제외:
  bybit    - CloudFront 지역 차단(HTTP 403). 로컬/타 리전에서는 어댑터 추가만 하면 동작
  deribit  - BTC-PERPETUAL 이 인버스(코인마진) 계약이라 선형 PnL 모델과 불일치

정규화 규약
-----------
* 8시간 정산 그리드(00:00 / 08:00 / 16:00 UTC)로 통일.
* 8h 정산 거래소 : 정산 시각을 가장 가까운 8h 경계로 스냅.
* 1h 정산 거래소 (hyperliquid, coinbase) : (T-8h, T] 구간의 시간당 펀딩비를 합산해
  8시간 실현 펀딩비로 환산.
* 가격 : 정산 시각 T 를 시작으로 하는 4H(또는 8H) 캔들의 시가 = T 시점 가격.
  binance / bingx / coinbase 는 펀딩 응답에 포함된 markPrice 사용.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List

import requests

H1 = 3_600_000
H8 = 28_800_000
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) funding-arb-research/1.0"}
# MEXC(Akamai)는 브라우저 UA 를 봇으로 판정해 차단하므로 curl UA 사용
UA_PLAIN = {"User-Agent": "curl/8.5.0"}

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


# ----------------------------------------------------------------------
# HTTP 유틸
# ----------------------------------------------------------------------
def _req(method: str, url: str, tries: int = 8, headers: dict | None = None,
         **kw) -> dict | list:
    """Akamai/CloudFront 계열은 확률적으로 403 을 반환하므로 지수 백오프 재시도."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.request(method, url, timeout=30,
                                 headers=headers or UA, **kw)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:180]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(1.5 * (attempt + 1), 8.0))
    raise RuntimeError(f"{url} -> {last}")


def get(url: str, **kw):
    return _req("GET", url, **kw)


def post(url: str, payload: dict):
    return _req("POST", url, json=payload)


def snap(ts_ms: float, grid: int = H8) -> int:
    return int(round(float(ts_ms) / grid) * grid)


def ms(dt: str) -> int:
    return int(datetime.strptime(dt, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def hourly_to_8h(hourly: Dict[int, float], s: int, e: int) -> Dict[int, float]:
    """시간당 펀딩비를 (T-8h, T] 구간 합으로 8시간 실현 펀딩비로 환산."""
    out: Dict[int, float] = {}
    t = snap(s)
    while t <= e:
        acc, n = 0.0, 0
        for k in range(1, 9):
            v = hourly.get(t - H8 + k * H1)
            if v is not None:
                acc += v
                n += 1
        if n >= 6:                      # 8개 중 6개 이상 있을 때만 유효 (결측 보정)
            out[t] = acc * (8.0 / n)
        t += H8
    return out


# ======================================================================
# 거래소별 어댑터 : (start_ms, end_ms) -> {ts_8h: value}
# ======================================================================

# ---------------------------- Binance ---------------------------------
def binance(s: int, e: int):
    f, p = {}, {}
    cur = s
    while cur < e:
        d = get("https://www.binance.com/fapi/v1/fundingRate",
                params={"symbol": "BTCUSDT", "startTime": cur, "endTime": e, "limit": 1000})
        if not d:
            break
        for row in d:
            t = snap(row["fundingTime"])
            f[t] = float(row["fundingRate"])
            p[t] = float(row["markPrice"])
        nxt = int(d[-1]["fundingTime"]) + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.3)
    return f, p


# ------------------------------ OKX -----------------------------------
def okx(s: int, e: int):
    f = {}
    after = e
    while after > s:
        d = get("https://www.okx.com/api/v5/public/funding-rate-history",
                params={"instId": "BTC-USDT-SWAP", "after": after, "limit": 100})["data"]
        if not d:
            break
        for row in d:
            f[snap(row["fundingTime"])] = float(row["realizedRate"] or row["fundingRate"])
        after = min(int(r["fundingTime"]) for r in d)
        time.sleep(0.25)

    p = {}
    after = e + H8
    while after > s:
        d = get("https://www.okx.com/api/v5/market/history-candles",
                params={"instId": "BTC-USDT-SWAP", "bar": "4H", "after": after, "limit": 100})["data"]
        if not d:
            break
        for row in d:
            t = int(row[0])
            if t % H8 == 0:
                p[t] = float(row[1])          # open
        after = min(int(r[0]) for r in d)
        time.sleep(0.25)
    return f, {k: v for k, v in p.items() if s <= k <= e}


# ----------------------------- Bitget ---------------------------------
def bitget(s: int, e: int):
    f = {}
    for page in range(1, 40):
        d = get("https://api.bitget.com/api/v2/mix/market/history-fund-rate",
                params={"symbol": "BTCUSDT", "productType": "usdt-futures",
                        "pageSize": 100, "pageNo": page})["data"]
        if not d:
            break
        for row in d:
            f[snap(row["fundingTime"])] = float(row["fundingRate"])
        if min(int(r["fundingTime"]) for r in d) < s:
            break
        time.sleep(0.25)

    p = {}
    end = e + H8
    while end > s:
        d = get("https://api.bitget.com/api/v2/mix/market/history-candles",
                params={"symbol": "BTCUSDT", "productType": "usdt-futures",
                        "granularity": "4H", "endTime": end, "limit": 200})["data"]
        if not d:
            break
        for row in d:
            t = int(row[0])
            if t % H8 == 0:
                p[t] = float(row[1])
        end = min(int(r[0]) for r in d)
        time.sleep(0.25)
    return ({k: v for k, v in f.items() if s <= k <= e},
            {k: v for k, v in p.items() if s <= k <= e})


# ------------------------------ Gate ----------------------------------
def gate(s: int, e: int):
    """funding_rate 는 1회 최대 90건 -> from/to 로 30일 단위 역방향 페이지네이션."""
    f = {}
    to = e // 1000 + 60
    lo = s // 1000 - 60
    while to > lo:
        d = get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
                params={"contract": "BTC_USDT", "limit": 100,
                        "from": max(to - 30 * 86400, lo), "to": to})
        if not d:
            break
        for r in d:
            f[snap(int(r["t"]) * 1000)] = float(r["r"])
        oldest = min(int(r["t"]) for r in d)
        if oldest >= to:
            break
        to = oldest - 1
        time.sleep(0.25)

    p = {}
    d = get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
            params={"contract": "BTC_USDT", "interval": "4h",
                    "from": s // 1000 - H8 // 1000, "to": e // 1000 + H8 // 1000})
    for r in d:
        t = int(r["t"]) * 1000
        if t % H8 == 0:
            p[t] = float(r["o"])
    return ({k: v for k, v in f.items() if s <= k <= e},
            {k: v for k, v in p.items() if s <= k <= e})


# ------------------------------ MEXC ----------------------------------
def mexc(s: int, e: int):
    f = {}
    for page in range(1, 40):
        d = get("https://contract.mexc.com/api/v1/contract/funding_rate/history",
                params={"symbol": "BTC_USDT", "page_num": page, "page_size": 100},
                headers=UA_PLAIN, tries=12)
        rows = d["data"]["resultList"]
        if not rows:
            break
        for r in rows:
            f[snap(r["settleTime"])] = float(r["fundingRate"])
        if min(int(r["settleTime"]) for r in rows) < s:
            break
        time.sleep(1.2)

    p = {}
    d = get("https://contract.mexc.com/api/v1/contract/kline/BTC_USDT",
            params={"interval": "Hour4", "start": s // 1000 - 28800, "end": e // 1000 + 28800},
            headers=UA_PLAIN, tries=12)["data"]
    for t, o in zip(d["time"], d["open"]):
        tm = int(t) * 1000
        if tm % H8 == 0:
            p[tm] = float(o)
    return ({k: v for k, v in f.items() if s <= k <= e},
            {k: v for k, v in p.items() if s <= k <= e})


# ------------------------------- HTX ----------------------------------
def htx(s: int, e: int):
    f = {}
    for page in range(1, 60):
        d = get("https://api.hbdm.com/linear-swap-api/v1/swap_historical_funding_rate",
                params={"contract_code": "BTC-USDT", "page_size": 50, "page_index": page})
        rows = d["data"]["data"]
        if not rows:
            break
        for r in rows:
            rate = r.get("realized_rate") or r.get("funding_rate")
            f[snap(r["funding_time"])] = float(rate)
        if min(int(r["funding_time"]) for r in rows) < s:
            break
        time.sleep(0.25)

    p = {}
    d = get("https://api.hbdm.com/linear-swap-ex/market/history/kline",
            params={"contract_code": "BTC-USDT", "period": "4hour",
                    "from": s // 1000 - 28800, "to": e // 1000 + 28800})["data"]
    for r in d:
        t = int(r["id"]) * 1000
        if t % H8 == 0:
            p[t] = float(r["open"])
    return ({k: v for k, v in f.items() if s <= k <= e},
            {k: v for k, v in p.items() if s <= k <= e})


# ----------------------------- KuCoin ---------------------------------
def kucoin(s: int, e: int):
    f, p = {}, {}
    step = 20 * 86_400_000
    cur = s
    while cur < e:
        hi = min(cur + step, e)
        d = get("https://api-futures.kucoin.com/api/v1/contract/funding-rates",
                params={"symbol": "XBTUSDTM", "from": cur, "to": hi}).get("data") or []
        for r in d:
            f[snap(r["timepoint"])] = float(r["fundingRate"])
        cur = hi + 1
        time.sleep(0.3)

    cur = s - H8
    step = 150 * H8
    while cur < e:
        hi = min(cur + step, e + H8)
        d = get("https://api-futures.kucoin.com/api/v1/kline/query",
                params={"symbol": "XBTUSDTM", "granularity": 480,
                        "from": cur, "to": hi}).get("data") or []
        for r in d:
            t = int(r[0])
            if t % H8 == 0:
                p[t] = float(r[1])
        cur = hi
        time.sleep(0.3)
    return ({k: v for k, v in f.items() if s <= k <= e},
            {k: v for k, v in p.items() if s <= k <= e})


# ------------------------------ BingX ---------------------------------
def bingx(s: int, e: int):
    f, p = {}, {}
    cur = s
    while cur < e:
        d = get("https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate",
                params={"symbol": "BTC-USDT", "startTime": cur, "endTime": e, "limit": 1000})["data"]
        if not d:
            break
        for r in d:
            t = snap(r["fundingTime"])
            f[t] = float(r["fundingRate"])
            p[t] = float(r["markPrice"])
        nxt = max(int(r["fundingTime"]) for r in d) + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.3)
    return f, p


# --------------------------- Hyperliquid ------------------------------
def hyperliquid(s: int, e: int):
    """펀딩 1시간 주기 -> 8시간 합산."""
    hourly = {}
    cur = s - H8
    while cur < e:
        d = post("https://api.hyperliquid.xyz/info",
                 {"type": "fundingHistory", "coin": "BTC",
                  "startTime": cur, "endTime": min(cur + 400 * H1, e)})
        if not d:
            cur += 400 * H1
            continue
        for r in d:
            hourly[snap(r["time"], H1)] = float(r["fundingRate"])
        nxt = max(int(r["time"]) for r in d) + 1
        cur = nxt if nxt > cur else cur + 400 * H1
        time.sleep(0.3)

    p = {}
    d = post("https://api.hyperliquid.xyz/info",
             {"type": "candleSnapshot",
              "req": {"coin": "BTC", "interval": "4h", "startTime": s - H8, "endTime": e + H8}})
    for r in d:
        t = int(r["t"])
        if t % H8 == 0:
            p[t] = float(r["o"])
    return (hourly_to_8h(hourly, s, e),
            {k: v for k, v in p.items() if s <= k <= e})


# ------------------------- Coinbase International ---------------------
def coinbase(s: int, e: int):
    """펀딩 1시간 주기 -> 8시간 합산. mark_price 동시 제공."""
    hourly, px = {}, {}
    offset = 0
    while offset < 4000:
        d = get("https://api.international.coinbase.com/api/v1/instruments/BTC-PERP/funding",
                params={"result_limit": 300, "result_offset": offset})["results"]
        if not d:
            break
        oldest = None
        for r in d:
            t = snap(int(datetime.strptime(r["event_time"], "%Y-%m-%dT%H:%M:%SZ")
                         .replace(tzinfo=timezone.utc).timestamp() * 1000), H1)
            hourly[t] = float(r["funding_rate"])
            px[t] = float(r["mark_price"])
            oldest = t if oldest is None else min(oldest, t)
        if oldest is not None and oldest < s - H8:
            break
        offset += 300
        time.sleep(0.3)
    return (hourly_to_8h(hourly, s, e),
            {k: v for k, v in px.items() if s <= k <= e and k % H8 == 0})


# ======================================================================
EXCHANGES: Dict[str, Callable] = {
    "binance": binance,
    "okx": okx,
    "bitget": bitget,
    "gate": gate,
    "mexc": mexc,
    "htx": htx,
    "kucoin": kucoin,
    "bingx": bingx,
    "hyperliquid": hyperliquid,
    "coinbase": coinbase,
}

FUNDING_INTERVAL_HOURS = {
    "binance": 8, "okx": 8, "bitget": 8, "gate": 8, "mexc": 8,
    "htx": 8, "kucoin": 8, "bingx": 8, "hyperliquid": 1, "coinbase": 1,
}


def collect(start: str, end: str, only: List[str] | None = None,
            use_cache: bool = True) -> dict:
    s, e = ms(start), ms(end)
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = {}
    names = only or list(EXCHANGES)
    for name in names:
        cache = os.path.join(CACHE_DIR, f"{name}_{start}_{end}.json")
        if use_cache and os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                d = json.load(fh)
            out[name] = ({int(k): v for k, v in d["funding"].items()},
                         {int(k): v for k, v in d["price"].items()})
            print(f"  [cache] {name:<12} funding={len(out[name][0]):>4} price={len(out[name][1]):>4}")
            continue
        try:
            f, p = EXCHANGES[name](s, e)
            out[name] = (f, p)
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"funding": f, "price": p}, fh)
            print(f"  [ok]    {name:<12} funding={len(f):>4} price={len(p):>4}")
        except Exception as ex:  # noqa: BLE001
            print(f"  [FAIL]  {name:<12} {type(ex).__name__}: {str(ex)[:160]}")
    return out


def build_matrix(data: dict, start: str, end: str, out_path: str,
                 min_coverage: float = 0.90) -> None:
    """모든 거래소가 데이터를 가진 8h 그리드만 남겨 통합 CSV 생성."""
    import csv

    s, e = ms(start), ms(end)
    grid = list(range(snap(s), snap(e) + 1, H8))
    names = [n for n in data if data[n][0] and data[n][1]]

    # 커버리지 미달 거래소 제외
    keep = []
    for n in names:
        f, p = data[n]
        cov = sum(1 for t in grid if t in f and t in p) / len(grid)
        flag = "OK " if cov >= min_coverage else "DROP"
        print(f"  {flag} {n:<12} coverage={cov*100:5.1f}%")
        if cov >= min_coverage:
            keep.append(n)

    rows = []
    for t in grid:
        if all(t in data[n][0] and t in data[n][1] for n in keep):
            row = {"ts": iso(t)}
            for n in keep:
                row[f"price_{n}"] = f"{data[n][1][t]:.2f}"
                row[f"funding_{n}"] = f"{data[n][0][t]:.10f}"
            rows.append(row)

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ts"] +
                           [f"{k}_{n}" for n in keep for k in ("price", "funding")])
        w.writeheader()
        w.writerows(rows)
    print(f"\n  통합 매트릭스: {len(rows)} 구간 x {len(keep)} 거래소 -> {out_path}")
    print(f"  포함 거래소: {', '.join(keep)}")


def main():
    p = argparse.ArgumentParser(description="상위 10개 거래소 펀딩비/가격 수집")
    p.add_argument("--start", default="2026-05-20")
    p.add_argument("--end", default="2026-08-20")
    p.add_argument("--only", nargs="*", default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--out", default="funding_arb/data/matrix.csv")
    a = p.parse_args()

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    print(f"수집 기간: {a.start} ~ {a.end}\n")
    data = collect(a.start, a.end, a.only, use_cache=not a.no_cache)
    print()
    build_matrix(data, a.start, a.end, a.out)


if __name__ == "__main__":
    main()
