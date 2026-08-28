"""
hedged_martingale_bot.py
비트코인 10배 레버리지 양방향(Hedge Mode) 마틴게일 자동매매 모듈

- 기본 실행 모드: 모의 매매(Paper Trading). 실거래는 --live 옵션 + API 키가 있을 때만 동작.
- Gate.io/Binance/Bybit/OKX/Bitget 무기한 선물(USDT-M)을 지원하며 GUI에서 거래소를 선택한다.
- 롱/숏은 완전히 독립된 상태머신(MartingaleModule)이며 서로의 진입/청산에 영향을 주지 않는다.
- 물타기 간격(STEP_TRIGGER_PCT)과 손절(STOP_LOSS_PCT)은 독립: 평단가 대비 STEP_TRIGGER_PCT마다
  1→2→4→8배 추가 진입(최대 MAX_STEPS단계)하고, 평단가 대비 STOP_LOSS_PCT에 도달하면 단계와
  무관하게 전량 손절한다. 익절은 평단가 대비 +TP_PCT.
- 시세(15분봉 종가)는 실제 매매할 거래소에서 ccxt 공개 API로 직접 가져온다(인증 불필요).
- 실거래 시에만 ccxt로 Hedge Mode(Gate.io는 Dual Mode) + 레버리지 10배를 계좌에 설정한다.
- Gate.io Dual Mode는 매수/매도 방향이 곧 롱/숏 슬롯을 가리키고, 청산 주문에만 reduceOnly를 붙인다.
  바이낸스 Hedge Mode는 반대로 positionSide로 방향을 지정하고 reduceOnly는 보내면 안 된다(거래소별 분기 처리).
- 추세장에서 마틴게일이 반복 손절되는 것을 막기 위해, 연속 손절이 MAX_CONSECUTIVE_SL(기본 3)회
  누적되면 해당 방향(롱 또는 숏)만 자동 정지되고 텔레그램으로 통지된다(수동 재시작 전까지 재진입 안 함).
- 실거래/모의매매 루프는 매 틱마다 롱/숏의 진행 상태(진입 단계, 체결 내역, 쿨다운, 연속손절 횟수)를
  STATE_PATH 파일에 저장한다. 프로그램을 껐다 켜도 이 파일에서 상태를 복원해 이어서 진행하며,
  실거래에서는 재시작 시 거래소의 실제 포지션 수량과 저장된 상태를 대조해 어긋나면 경고한다.

사전 준비: pip install ccxt requests  (--selftest만 쓸 경우 ccxt 없이도 동작)

사용법:
    python hedged_martingale_bot.py --selftest      # 가상 차트로 진입 조건 정성 테스트(네트워크 불필요)
    python hedged_martingale_bot.py                 # 모의 매매로 실시간 루프 실행 (Gate.io 실시세 사용)
    python hedged_martingale_bot.py --live           # 실거래 (EXCHANGE_API_KEY/SECRET 환경변수 필요)
"""

from __future__ import annotations

# 실행 중인 코드가 최신인지 로그로 바로 확인하기 위한 버전 표식.
# 코드를 의미 있게 바꿀 때마다 이 문자열을 갱신한다.
VERSION = "1.3.1"

import argparse
import json
import logging
import math
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple


def _ensure_ssl_cert_bundle() -> None:
    """PyInstaller onefile exe에서 certifi CA 번들 경로 문제로 TLS 연결이 실패하는 것을 방지한다.

    'Could not find a suitable TLS CA certificate bundle' 오류는 exe에 인증서 파일이 제대로
    포함/인식되지 않아 생긴다. certifi가 실제로 가진 cacert.pem 경로를 찾아 환경변수로 지정한다.
    (빌드 시 --collect-all certifi 옵션과 함께 쓰면 확실하다.)
    """
    try:
        import certifi
        path = certifi.where()
        if path and os.path.exists(path):
            os.environ["SSL_CERT_FILE"] = path
            os.environ["REQUESTS_CA_BUNDLE"] = path
    except Exception:
        pass


_ensure_ssl_cert_bundle()

import requests

try:
    import ccxt
except ImportError:
    ccxt = None

# ───────────── 지원 거래소 정의 ─────────────
# ccxt 버전에 따라 클래스 이름이 다를 수 있어(예: Gate.io는 gate/gateio) 후보를 순서대로 확인한다.
EXCHANGE_OPTIONS = {
    "Gate.io": {"ccxt_ids": ("gate", "gateio"), "options": {"defaultType": "swap"}, "needs_passphrase": False},
    "Binance": {"ccxt_ids": ("binance",), "options": {"defaultType": "future"}, "needs_passphrase": False},
    "Bybit":   {"ccxt_ids": ("bybit",), "options": {"defaultType": "swap", "subType": "linear"}, "needs_passphrase": False},
    "OKX":     {"ccxt_ids": ("okx",), "options": {"defaultType": "swap"}, "needs_passphrase": True},
    "Bitget":  {"ccxt_ids": ("bitget",), "options": {"defaultType": "swap"}, "needs_passphrase": True},
}

# 거래소별 무기한 선물 심볼(ccxt 통일 심볼)
EXCHANGE_SYMBOLS = {
    "Gate.io": "BTC/USDT:USDT",
    "Binance": "BTC/USDT:USDT",
    "Bybit":   "BTC/USDT:USDT",
    "OKX":     "BTC/USDT:USDT",
    "Bitget":  "BTC/USDT:USDT",
}


# 거래소별 가입(레퍼럴) 정보. 가입 시 코드를 입력하면 수수료 할인 혜택을 받을 수 있다.
EXCHANGE_REFERRALS = {
    "Gate.io": {
        "code": "VGAXVF1ZAG",
        "link": ("https://www.gate.com/referral/registry?ref=VGAXVF1ZAG&ref_type=103"
                 "&page=earnVoucher&utm_cmp=PEYEQdSb"),
    },
    "Binance": {
        "code": "CPA_00OGAWC3CJ",
        "link": "https://www.binance.com/en/activity/referral/offers/claim?ref=CPA_00OGAWC3CJ",
    },
    "Bybit": {
        "code": "99MWYJ",
        "link": "https://www.bybit.com/en/invite/?ref=99MWYJ&medium=referral&utm_campaign=evergreen",
    },
    "OKX": {
        "code": "83339059",
        "link": "https://www.okx.com/join/83339059",
    },
    "Bitget": {
        "code": "wxf3",
        "link": ("https://www.bitget.com/asia/expressly?channelCode=3y85&groupId=590374"
                 "&languageType=3&vipCode=wxf3"),
    },
}


def _referral_note(exchange_name: str) -> str:
    """API 발급 안내문 맨 앞에 붙일 가입(레퍼럴) 안내 문구."""
    ref = EXCHANGE_REFERRALS.get(exchange_name)
    if not ref:
        return ""
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"【{exchange_name} 계정이 아직 없다면 — 가입 안내】\n\n"
        f"  · 레퍼럴 코드 : {ref['code']}\n"
        f"  · 가입 링크 : {ref['link']}\n\n"
        "  아래쪽 '🔗 가입 링크 열기' 버튼을 누르면 브라우저에서 바로 열립니다.\n"
        "  '📋 레퍼럴 코드 복사' 버튼으로 코드를 복사해 가입 화면에 붙여넣으세요.\n"
        "  가입할 때 레퍼럴 코드를 입력하면 거래 수수료 할인 혜택을 받을 수 있습니다.\n"
        "  (이미 계정이 있다면 이 부분은 건너뛰고 아래 발급 방법부터 보시면 됩니다.)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "【API Key 발급 방법】\n\n"
    )


def _save_warning(needs_passphrase: bool) -> str:
    if needs_passphrase:
        return (
            "\n\n⚠️⚠️⚠️ 발급 완료 즉시, API Key / Secret Key / Passphrase 3가지를 "
            "메모장(또는 안전한 곳)에 반드시 복사해서 저장하세요!\n"
            "Secret Key와 Passphrase는 이 화면을 벗어나면 다시는 확인할 수 없고, "
            "분실 시 API Key를 처음부터 새로 발급받아야 합니다."
        )
    return (
        "\n\n⚠️⚠️⚠️ 발급 완료 즉시, API Key / Secret Key를 메모장(또는 안전한 곳)에 반드시 복사해서 저장하세요!\n"
        "Secret Key는 이 화면을 벗어나면 다시는 확인할 수 없고, 분실 시 API Key를 처음부터 새로 발급받아야 합니다."
    )


_COMMON_HEDGE_NOTE = (
    "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "【이 프로그램 사용을 위한 필수 준비】\n"
    "1) 선물(무기한) 지갑에 USDT를 입금해두세요. 현물 지갑에만 있으면 잔고가 0으로 조회됩니다.\n"
    "2) 이 봇은 롱/숏을 동시에 잡는 양방향(헤지) 모드를 사용합니다. 프로그램이 시작할 때\n"
    "   자동으로 양방향 모드를 켜지만, 이미 열려 있는 포지션이나 미체결 주문이 있으면 실패합니다.\n"
    "   → 처음 시작 전에는 포지션/주문을 모두 정리해두세요.\n"
    "3) API 권한은 '선물(Futures/Swap) 거래'가 반드시 켜져 있어야 합니다.\n"
    "4) 보안을 위해 '출금(Withdraw)' 권한은 절대 켜지 마세요. 거래 권한만 있으면 충분합니다.\n"
    "5) 가능하면 IP 화이트리스트에 이 프로그램을 돌릴 PC의 IP를 등록하세요."
)

EXCHANGE_API_GUIDES = {
    "Gate.io": (
        _referral_note("Gate.io") +
        "1. Gate.io 로그인 → 우측 상단 프로필 아이콘 클릭 → 'API Management(API 관리)' 이동\n"
        "2. 'Create New Key(새 키 생성)' 클릭\n"
        "3. API Key Type은 'API v4 Key' 선택\n"
        "4. Permissions(권한)에서 'Perpetual Futures(무기한 선물)' 체크 + 'Read And Write' 권한 부여\n"
        "5. (선택) IP 화이트리스트 등록으로 보안 강화\n"
        "6. 자금 비밀번호 입력 + 2FA(구글 인증 등) 인증\n"
        "7. API Key, Secret Key 발급 완료\n\n"
        "※ 주의: 선물 지갑에 USDT를 한 번도 이체한 적이 없으면 'Perpetual Futures' 권한이 있어도 "
        "'USER_NOT_FOUND' 오류가 날 수 있습니다. 현물→선물 지갑으로 소액이라도 먼저 이체해두세요.\n"
        "※ Gate.io는 자금이 '통합계좌(Unified Account)'에 있으면 일반 선물계좌 조회로 잔고가 0으로 "
        "보일 수 있습니다. 이 프로그램은 두 곳을 모두 조회하니, 잔고가 0으로 뜨면 어느 지갑에 자금이 "
        "있는지 확인해보세요."
        + _save_warning(False) + _COMMON_HEDGE_NOTE
    ),
    "Binance": (
        _referral_note("Binance") +
        "1. Binance 로그인 → 우측 상단 프로필 아이콘 → 'API Management' 이동\n"
        "2. API 이름 입력 후 'Create' 클릭\n"
        "3. 이메일 인증 및 2FA(OTP) 인증 완료\n"
        "4. API Key, Secret Key 발급 (Secret Key는 이 화면에서만 표시됨)\n"
        "5. 'Edit restrictions' 클릭 → 'Enable Futures' 체크 → 저장\n"
        "6. (선택) 'Restrict access to trusted IPs only'로 IP 제한 설정\n\n"
        "※ 주의: 선물 계좌를 먼저 개설(활성화)해두지 않은 상태에서 만든 키는 'Enable Futures' 항목 자체가 "
        "없거나 선택할 수 없습니다. 반드시 선물 계좌 개설 후 키를 발급하세요.\n"
        "※ 바이낸스는 계정 설정에서 'Hedge Mode(양방향 포지션)'를 미리 켜두면 더 안정적입니다. "
        "(선물 화면 → 설정 → 포지션 모드 → 헤지 모드)"
        + _save_warning(False) + _COMMON_HEDGE_NOTE
    ),
    "Bybit": (
        _referral_note("Bybit") +
        "1. Bybit 로그인 → 프로필 아이콘 → 'API' 클릭\n"
        "2. 'API Management' → 'Create New Key' → 'System-generated API Keys' 선택\n"
        "3. 용도(API Transaction) 선택 및 이름 입력\n"
        "4. 권한에서 'Contract(파생상품/Derivatives) - Orders & Positions' 체크\n"
        "5. (선택) IP 제한 설정 — Bybit는 IP 미설정 시 키 유효기간이 90일로 제한됩니다\n"
        "6. 2FA 인증 후 생성\n\n"
        "※ 참고: Bybit는 통합계좌(UTA) 방식이라 선물 지갑(Derivatives/Unified)에 자금이 있어야 "
        "잔고가 정상 조회됩니다.\n"
        "※ 중요: 통합계좌(UTA)는 일방향(One-way) 모드만 지원하는 경우가 있습니다. 양방향(헤지) 모드가 "
        "지원되지 않으면 이 봇의 롱/숏 동시 운용이 정상 동작하지 않을 수 있으니, Bybit 선물 화면에서 "
        "포지션 모드를 '헤지 모드'로 바꿀 수 있는지 먼저 확인하세요."
        + _save_warning(False) + _COMMON_HEDGE_NOTE
    ),
    "OKX": (
        _referral_note("OKX") +
        "1. OKX 로그인 → 프로필 아이콘 → 'API' 또는 'API Key' 메뉴 이동\n"
        "2. 'Create API Key' 클릭\n"
        "3. API 이름 입력, 'Passphrase(비밀번호)' 설정 — API Key/Secret Key와는 별개의 항목입니다\n"
        "4. Permissions(권한)에서 'Trade(거래)' 체크\n"
        "5. (선택) IP 화이트리스트 등록\n"
        "6. 2FA 인증 후 생성\n\n"
        "※ 중요: OKX는 API Key, Secret Key 외에 'Passphrase'까지 총 3개의 값이 필요합니다. "
        "이 프로그램 화면의 'Passphrase' 입력칸에도 반드시 함께 입력해주세요.\n"
        "※ OKX는 계정 모드가 중요합니다. 거래 화면에서 계정 모드를 '단일 통화 마진' 이상으로 설정하고, "
        "포지션 모드를 '양방향 포지션(Long/Short mode)'으로 바꿔두세요."
        + _save_warning(True) + _COMMON_HEDGE_NOTE
    ),
    "Bitget": (
        _referral_note("Bitget") +
        "1. Bitget 로그인 → 프로필 아이콘 → 'API Management' 이동\n"
        "2. 'Create New API' → 'System-generated API Key' 선택\n"
        "3. 'Notes(이름)' 입력, 'Passphrase' 설정 (영문/숫자 8자 이상, 특수문자 사용 불가)\n"
        "4. 권한에서 'Read-write' + 'Futures(선물) - Orders & Holdings' 체크\n"
        "5. (선택) IP 화이트리스트 등록\n"
        "6. 2FA 인증 후 생성\n\n"
        "※ 중요: Bitget도 API Key, Secret Key 외에 'Passphrase'까지 총 3개의 값이 필요합니다. "
        "이 프로그램 화면의 'Passphrase' 입력칸에도 반드시 함께 입력해주세요. "
        "Passphrase를 잊으면 키를 재발급해야 합니다.\n"
        "※ Bitget 선물 화면에서 포지션 모드를 '양방향(Hedge) 모드'로, 마진 모드를 원하는 값으로 "
        "미리 설정해두세요."
        + _save_warning(True) + _COMMON_HEDGE_NOTE
    ),
}

# 사용자에게 보여줄 주의사항 안내문 (배포용이므로 내부 전략은 언급하지 않는다)
CAUTION_TEXT = """■ 반드시 먼저 읽어주세요

이 프로그램은 레버리지를 사용하는 선물 자동매매 프로그램입니다.
레버리지 거래는 원금 전액을 잃을 수 있으며, 시장 상황에 따라 손실이 예금액보다
빠르게 커질 수 있습니다. 아래 내용을 이해하신 후 사용해주세요.


【1. 투자 위험】

· 자동매매는 수익을 보장하지 않습니다. 손실이 발생할 수 있습니다.
· 레버리지가 높을수록 수익과 손실이 함께 커집니다. 처음에는 낮은 레버리지와
  작은 포지션 크기로 시작해서, 며칠간 실제 동작을 확인한 뒤 늘리시길 권합니다.
· 반드시 '잃어도 생활에 지장이 없는 여유 자금'으로만 운용하세요.
· 급격한 시세 변동(뉴스, 급등락) 시에는 예상보다 큰 손실이 날 수 있습니다.
· 손실이 반복되면 프로그램이 해당 방향의 매매를 자동으로 멈춥니다.
  이때는 시장 상황을 확인하시고, 계속할지 직접 판단하신 뒤 재시작하세요.


【2. 마틴게일 방식의 위험 — 꼭 이해하셔야 합니다】

이 프로그램은 마틴게일(분할 추가매수) 방식을 사용합니다.
진입 후 가격이 불리하게 움직이면, 한 번에 손절하지 않고 정해진 간격마다 수량을
늘려가며 나눠서 추가 진입합니다. 평균단가를 끌어와 작은 반등에도 빠져나오는 방식이라
승률은 높은 편이지만, 아래와 같은 구조적 위험이 있습니다.

· 승률이 높다고 안전한 것이 아닙니다.
  작은 수익을 여러 번 쌓다가, 손절 한 번에 그동안의 수익을 모두 잃을 수 있습니다.
  '10번 이기고 1번 크게 지는' 형태의 손익 구조라고 이해하시면 됩니다.

· 한 방향으로 계속 밀리는 장(강한 상승장 또는 하락장)에서 가장 위험합니다.
  가격이 회복되지 않고 계속 밀리면 추가 진입이 반복되며 포지션이 커지고,
  결국 손절될 때의 손실 금액도 그만큼 커집니다.

· 포지션이 커질수록 청산가가 진입가에 가까워집니다.
  추가 진입이 진행된 상태에서 급락·급등이 나오면, 손절선에 닿기 전에 거래소에서
  강제 청산(리퀴데이션)될 수 있습니다. 이 경우 손실이 훨씬 커집니다.

· 잔고가 부족하면 중간에 추가 진입이 실패할 수 있습니다.
  이렇게 되면 의도한 대로 대응하지 못한 채 손실만 커질 수 있으므로,
  포지션 크기를 무리하게 키우지 마세요.

· 손실을 만회하려고 설정값을 급하게 키우지 마세요.
  손실 후 포지션 크기나 레버리지를 올리는 것은 위험을 몇 배로 키우는 행동이며,
  마틴게일 방식에서 계좌가 한 번에 무너지는 가장 흔한 원인입니다.


【3. 추천 설정값(레버리지 10배 / 포지션 크기 2%)과 그 이유】

· 레버리지 10배를 추천하는 이유
  레버리지가 높아질수록 청산가가 현재가에 가까워집니다. 10배에서는 대략 10% 정도의
  가격 여유가 있지만, 20배에서는 그 절반, 50배에서는 5분의 1로 줄어듭니다.
  비트코인은 하루에 5% 넘게 움직이는 일이 드물지 않기 때문에, 고배율에서는 매매가
  정상적으로 진행되기도 전에 강제 청산될 위험이 큽니다.
  반대로 레버리지를 너무 낮추면 주문 금액이 거래소 최소 주문 단위에 못 미쳐
  매매 자체가 시작되지 않을 수 있습니다.
  10배는 이 두 가지 사이에서 청산까지의 여유를 확보하면서도 매매가 정상 동작하는
  구간이라 기본값으로 두었습니다.

· 포지션 크기 2%를 추천하는 이유
  이 방식은 한 번에 다 진입하지 않고 여러 차례 나눠서 추가 진입하기 때문에,
  처음 잡은 크기보다 포지션이 훨씬 커질 수 있습니다.
  2%로 시작하면 추가 진입이 끝까지 진행된 상태에서도 증거금이 잔고의 3분의 1 수준에
  머물러, 나머지 잔고가 청산을 막아주는 완충 역할을 합니다.
  이 값을 크게 올리면 추가 진입 도중 증거금이 모자라 주문이 실패하거나,
  가격이 조금만 밀려도 강제 청산될 수 있습니다.
  (프로그램은 안전을 위해 일정 비율을 넘는 값은 입력을 막습니다.)

· 처음에는 추천값보다 더 보수적으로(레버리지 5배, 포지션 1% 등) 소액으로
  며칠 돌려보시고, 동작을 충분히 확인한 뒤 조정하시길 권합니다.


【4. 시작하기 전 준비】

· 거래소의 '선물(무기한) 지갑'에 USDT가 들어 있어야 합니다.
  현물 지갑에만 있으면 잔고가 0으로 조회되어 매매가 시작되지 않습니다.
· 프로그램을 처음 시작할 때는 그 거래소에 열려 있는 포지션과 미체결 주문을
  모두 정리해두세요. 남아 있으면 초기 설정에 실패할 수 있습니다.
· API 키에는 '선물 거래' 권한이 반드시 있어야 합니다.
· API 키에 '출금(Withdraw)' 권한은 절대 켜지 마세요. 거래 권한만 있으면 충분합니다.
· 가능하면 거래소에서 IP 화이트리스트를 설정해 보안을 강화하세요.


【5. 실행 중 지켜야 할 것】

· 프로그램이 켜져 있는 동안 PC가 꺼지거나 인터넷이 끊기면 매매가 중단됩니다.
  포지션이 열린 상태로 방치되지 않도록 주의하세요.
· 프로그램이 돌아가는 중에는 같은 계좌에서 수동으로 매매하지 마세요.
  프로그램이 파악한 포지션과 실제가 어긋나 예상치 못한 주문이 나갈 수 있습니다.
· 종료하실 때는 '정지' 버튼을 누른 뒤 창을 닫아주세요.
· 프로그램을 껐다 켜도 진행 중이던 매매는 이어서 관리됩니다. 다시 켰을 때는
  거래소에 실제로 열려 있는 포지션을 기준으로 자동으로 맞춥니다.


【6. 수수료와 비용】

· 매매할 때마다 거래소 수수료가 발생하며, 이는 수익에서 차감됩니다.
· 포지션을 오래 들고 있으면 펀딩비가 추가로 발생할 수 있습니다.
· 화면에 표시되는 손익은 참고용입니다. 정확한 정산 내역은 거래소에서 확인하세요.


【7. API 키 보안】

· 'API 키 저장'을 체크하면 이 PC에 암호화 없이 저장됩니다.
  여러 사람이 함께 쓰는 PC에서는 체크를 해제하거나, 사용 후 '저장된 키 삭제'를 누르세요.
· API 키는 절대 타인에게 알려주지 마세요.
· 키가 유출된 것 같으면 즉시 거래소에서 해당 키를 삭제하고 새로 발급받으세요.


【8. 책임 안내】

· 이 프로그램은 매매를 돕는 도구일 뿐이며, 투자 판단과 그 결과에 대한 책임은
  전적으로 사용자 본인에게 있습니다.
· 거래소 점검, 서버 장애, 네트워크 오류 등 외부 요인으로 주문이 실패하거나
  지연될 수 있습니다.
· 소액으로 충분히 테스트해보신 뒤 본격적으로 사용하시길 권합니다."""


def _resolve_exchange_class(exchange_name: str):
    """거래소 표시 이름(예: 'Gate.io')에 맞는 ccxt 클래스를 반환한다."""
    if ccxt is None:
        raise RuntimeError("ccxt가 설치되어 있지 않습니다. 'pip install ccxt'를 실행하세요.")
    spec = EXCHANGE_OPTIONS.get(exchange_name)
    if spec is None:
        raise RuntimeError(f"지원하지 않는 거래소입니다: {exchange_name}")
    for candidate in spec["ccxt_ids"]:
        cls = getattr(ccxt, candidate, None)
        if cls is not None:
            return cls
    raise RuntimeError(
        f"ccxt 라이브러리에서 {exchange_name} 지원을 찾을 수 없습니다. 'pip install -U ccxt'로 업데이트하세요."
    )


# ───────────── 전략 설정값 ─────────────
EXCHANGE_NAME = os.environ.get("EXCHANGE_NAME", "Gate.io")  # EXCHANGE_OPTIONS의 키 (Gate.io/Binance/Bybit/OKX/Bitget)
SYMBOL = os.environ.get("SYMBOL", "BTC/USDT:USDT")
TIMEFRAME = "15m"
LEVERAGE = 10
# 기본값은 2026-08-01 실제 BTC 15분봉 125일(코인베이스, 수수료 0.05%/체결 반영) 백테스트에서
# 하드손절·최대낙폭 최소화 기준으로 고른 자산보호형 조합이다. GUI에서 언제든 변경 가능.
#  - 물타기 간격을 넓게(1.2%) 잡으면 4단계까지 몰리는 일 자체가 줄어 하드손절이 급감했고
#    (125일 동안 6회, 좁은 0.3% 간격은 19회), 보수적 시뮬 기준 MDD 8~16%로 방어됨.
INITIAL_MARGIN_PCT = 0.02       # 1차 진입 마진 = 계좌 잔고의 2% (화면 추천값과 동일)
STEP_TRIGGER_PCT = 0.012        # 평단가 대비 1.2% 역방향 이동마다 물타기(추가 진입) 트리거
TP_PCT = 0.003                  # 평단가 대비 0.3% 순방향 이동 시 익절
STOP_LOSS_PCT = 0.025           # 평단가 대비 2.5% 역방향 이동 시 전량 하드손절(물타기 간격과 독립)
MAX_STEPS = 4                   # 1배 -> 2배 -> 4배 -> 8배
COOLDOWN_SEC = 180              # 청산 후 재진입 대기 3분
MAX_CONSECUTIVE_SL = int(os.environ.get("MAX_CONSECUTIVE_SL", "3"))  # 연속 손절 N회 시 해당 방향 자동 정지
RSI_PERIOD = 14
RSI_LONG_TRIGGER = 40.0
RSI_SHORT_TRIGGER = 60.0
BB_PERIOD = 20
BB_STDDEV_MULT = 2.0
PAPER_START_BALANCE = float(os.environ.get("PAPER_START_BALANCE", "10000"))
POLL_SEC = int(os.environ.get("POLL_SEC", "30"))  # 가격 조회 주기(초)
STATE_PATH = os.environ.get(
    "STATE_PATH", os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_state.json")
)  # 진행 중인 매매 상태(진입 단계/체결 내역/쿨다운) 저장 위치. 재시작 시 여기서 복원한다.
TRADE_LOG_PATH = os.environ.get(
    "TRADE_LOG_PATH", os.path.join(os.path.expanduser("~"), ".hedged_martingale_bot_trades.json")
)  # 청산될 때마다 쌓이는 매매 기록(시각/방향/진입가/청산가/수익금/수익률) 저장 위치.

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hedged_martingale_bot")


# ───────────── 지표 계산 (pandas/numpy 없이 순수 파이썬) ─────────────
class Indicators:
    @staticmethod
    def rsi(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def ema(closes: List[float], period: int) -> Optional[float]:
        """마지막 값 기준 지수이동평균. 데이터가 기간보다 짧으면 None."""
        if not closes or period <= 0 or len(closes) < period:
            return None
        k = 2.0 / (period + 1)
        value = sum(closes[:period]) / period      # 첫 구간은 단순평균으로 시작
        for c in closes[period:]:
            value = c * k + value * (1 - k)
        return value

    @staticmethod
    def bollinger(
        closes: List[float], period: int = BB_PERIOD, mult: float = BB_STDDEV_MULT
    ) -> Optional[Tuple[float, float, float]]:
        if len(closes) < period:
            return None
        window = closes[-period:]
        mid = sum(window) / period
        variance = sum((c - mid) ** 2 for c in window) / period
        std = math.sqrt(variance)
        return mid, mid + mult * std, mid - mult * std


# ───────────── 텔레그램 알림 ─────────────
# 개인용 화면에서는 BTC 수량/계약수 대신 USDT 금액만 보고 싶다는 요청이 있어,
# 표시 방식을 이 플래그로 고른다. 기본값(True)은 배포용의 기존 표시를 그대로 유지한다.
# 증거금 모드: "cross"(교차) 또는 "isolated"(격리).
# 격리는 레버리지가 높을 때 봇의 하드손절보다 거래소 강제청산이 먼저 와서 위험하다.
# 추세 필터: 0이면 사용 안 함. 값을 주면 그 기간의 EMA를 기준으로,
# 추세를 거스르는 방향의 신규 진입(1차)을 막는다. 물타기·익절·손절은 그대로 동작한다.
# 상승추세(가격 > EMA)면 숏 신규진입을 막고, 하락추세면 롱 신규진입을 막는다.
# 추세장에서 반대편이 4단계까지 끌려가 하드손절되는 것을 막아주지만,
# 박스권에서는 진입 기회가 줄어 수익도 함께 줄어든다.
TREND_EMA_PERIOD = int(os.environ.get("TREND_EMA_PERIOD", "0"))

# 3차 헷지: 0이면 사용 안 함. N을 주면 그 단계에 도달했을 때 같은 수량의 반대 포지션을
# 열어 손실을 그 자리에 고정한다. 가격이 원래 포지션의 평단가로 돌아오면 양쪽을 함께
# 정리한다(시간 제한 없음). 4단계(8배 물량)를 태우지 않으므로 한 번의 손실이 크게 줄지만,
# 헷지가 풀릴 때까지 그 방향은 새 매매를 하지 않는다.
HEDGE_AT_STEP = int(os.environ.get("HEDGE_AT_STEP", "0"))
# 헷지 주문이 실패했을 때 다시 시도하기까지 쉬는 시간(초).
HEDGE_RETRY_SEC = int(os.environ.get("HEDGE_RETRY_SEC", "300"))


MARGIN_MODE = os.environ.get("MARGIN_MODE", "cross")
# 증거금 모드 전환이 거부됐을 때(대개 포지션/미체결이 남아 있을 때) 다시 시도하기까지 쉬는 시간(초).
MARGIN_RETRY_SEC = int(os.environ.get("MARGIN_RETRY_SEC", "60"))

SHOW_QTY_DETAIL = True

# 텔레그램으로 나가는 알림에 잔고를 넣을지. False면 화면 로그에는 잔고가 계속 보이고
# 텔레그램(그룹방 포함)으로 나가는 메시지에서만 잔고가 빠진다.
TELEGRAM_SHOW_BALANCE = True

# 진입까지 몇 분씩 아무 로그도 안 뜨면 프로그램이 살아있는지 알 수 없다. 그래서 조회할
# 때마다 현재 시세와 포지션 상태를 한 줄로 남긴다. 이 표시가 붙은 줄은 화면에서 계속
# 쌓이지 않고 직전 줄을 갈아끼우므로, 매매 기록이 밀려나지 않는다.
HEARTBEAT_MARK = "⏱"


def martingale_ladder(side, fills, max_steps: int = None) -> List[dict]:
    """지금 체결 내역을 기준으로 1~4단계의 진입가와 진입금액(USDT)을 계산한다.

    이미 체결된 단계는 실제 값을, 아직 안 온 단계는 예상 값을 돌려준다. 예상값은
    봇이 실제로 쓰는 규칙(평단가 대비 STEP_TRIGGER_PCT마다 직전 수량의 2배)을
    그대로 한 단계씩 굴려서 계산하므로, 실제 진입가와 같은 값이 나온다.
    """
    if not fills:
        return []
    if max_steps is None:
        max_steps = MAX_STEPS
    sim = [Fill(f.price, f.qty) for f in fills]
    out = [{"stage": i + 1, "price": f.price, "usdt": f.price * f.qty, "done": True}
           for i, f in enumerate(sim)]
    while len(sim) < max_steps:
        total_qty = sum(f.qty for f in sim)
        avg = sum(f.price * f.qty for f in sim) / total_qty
        trigger = avg * (1 - STEP_TRIGGER_PCT) if side == Side.LONG else avg * (1 + STEP_TRIGGER_PCT)
        qty = sim[-1].qty * 2
        sim.append(Fill(trigger, qty))
        out.append({"stage": len(sim), "price": trigger, "usdt": trigger * qty, "done": False})
    return out


def parse_chat_ids(raw: str) -> List[str]:
    """Chat ID 입력값을 여러 개로 쪼갠다.

    쉼표/공백/세미콜론/줄바꿈 아무거나로 구분해서 여러 곳(개인 DM + 그룹방 등)에
    동시에 보낼 수 있게 한다. 그룹방 ID는 보통 음수(-100...)이고, 공개 채널은
    @채널이름 형태도 텔레그램이 받아준다.
    """
    if not raw:
        return []
    out = []
    for token in re.split(r"[,\s;]+", str(raw).strip()):
        token = token.strip()
        if token and token not in out:
            out.append(token)
    return out


class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.chat_ids = parse_chat_ids(chat_id)
        # 같은 대상에 대해 같은 실패 사유를 매 체결마다 반복 경고하지 않도록 기억해둔다.
        self._warned: set = set()

    def send(self, text: str, telegram_text: Optional[str] = None) -> None:
        """화면 로그에는 text를, 텔레그램에는 telegram_text를 보낸다.

        telegram_text를 주면 텔레그램으로 나가는 내용만 따로 줄일 수 있다.
        그룹방처럼 여러 사람이 보는 곳에 잔고 같은 개인 정보를 흘리지 않기 위한 것이다.
        """
        logger.info("%s", text)
        if not self.token or not self.chat_ids:
            return  # 텔레그램 미설정 시 위 로그(화면/파일)로만 알린다
        text = text if telegram_text is None else telegram_text
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for chat_id in self.chat_ids:
            # 한 대상이 실패해도 나머지 대상에는 계속 보낸다(그룹방 하나가 막혀도 DM은 와야 함).
            try:
                resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
                body = resp.json()
                if not body.get("ok"):
                    # 텔레그램이 왜 거부했는지(예: chat not found, 봇이 그룹에 없음)를 그대로 보여준다.
                    key = (chat_id, body.get("description"))
                    if key not in self._warned:
                        self._warned.add(key)
                        logger.warning(
                            "텔레그램 전송 실패 [%s] (code=%s): %s | 그룹방이면 봇을 그룹에 초대했는지, "
                            "Chat ID가 -100으로 시작하는 값인지 확인하세요.",
                            chat_id, body.get("error_code"), body.get("description"),
                        )
            except Exception as e:
                key = (chat_id, str(e))
                if key not in self._warned:
                    self._warned.add(key)
                    logger.warning("텔레그램 전송 오류 [%s] (네트워크/방화벽 확인): %s", chat_id, e)


# ───────────── 시세 데이터 (공개 API, 인증 불필요) ─────────────
class PublicMarketData:
    """실제 매매할 거래소와 동일한 곳에서 ccxt 공개 API로 캔들을 가져온다. API 키 불필요."""

    def __init__(self, exchange_name: str = None, symbol: str = None, timeframe: str = TIMEFRAME,
                 limit: int = None):
        # 추세 필터를 켜면 EMA 기간만큼 과거 캔들이 더 필요하다.
        if limit is None:
            limit = max(100, TREND_EMA_PERIOD + 50) if TREND_EMA_PERIOD else 100
        exchange_name = exchange_name or EXCHANGE_NAME
        symbol = symbol or EXCHANGE_SYMBOLS.get(exchange_name, SYMBOL)
        exchange_cls = _resolve_exchange_class(exchange_name)
        opts = dict(EXCHANGE_OPTIONS[exchange_name]["options"])
        self.exchange = exchange_cls({"enableRateLimit": True, "options": opts})
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit

    def get_closes(self) -> List[float]:
        candles = self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=self.limit)
        return [float(c[4]) for c in candles]


# ───────────── 브로커(체결/잔고) ─────────────
class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PaperBroker:
    """모의 매매 전용 가상 체결/잔고 관리자. 실제 주문을 전송하지 않는다."""

    def __init__(self, starting_balance: float = PAPER_START_BALANCE):
        self.balance = starting_balance

    def get_balance(self) -> float:
        return self.balance

    def quantize_qty(self, qty_btc: float, price: float) -> float:
        return qty_btc  # 모의매매는 거래소 계약 단위가 없으므로 계산된 수량을 그대로 사용

    def fill_order(self, side: Side, is_entry: bool, qty: float, price: float) -> None:
        logger.debug(
            "[모의체결] %s %s qty=%.6f price=%.2f",
            side.value, "진입" if is_entry else "청산", qty, price,
        )

    def apply_pnl(self, pnl: float) -> None:
        self.balance += pnl

    def fetch_position(self, side: Side) -> Optional[dict]:
        return None  # 모의매매는 거래소 실포지션이 없으므로 동기화하지 않음(저장된 상태 유지)


class LiveBroker:
    """실거래 브로커. 최초 연결 시 계좌에 양방향(Hedge/Dual) 모드와 레버리지를 설정한다."""

    def __init__(self, exchange_name: str, api_key: str, api_secret: str, passphrase: str = "",
                 symbol: str = None, leverage: Optional[int] = None):
        exchange_cls = _resolve_exchange_class(exchange_name)
        spec = EXCHANGE_OPTIONS[exchange_name]
        self.exchange_name = exchange_name
        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": dict(spec["options"]),
        }
        if spec["needs_passphrase"]:
            if not passphrase:
                raise RuntimeError(f"{exchange_name}는 Passphrase가 필요합니다. 입력해주세요.")
            config["password"] = passphrase
        self.exchange = exchange_cls(config)
        self.margin_mode_ok = False                      # 요청한 증거금 모드가 실제로 걸렸는지
        self._margin_retry_after = 0.0                   # 다음 재시도 가능 시각
        self._last_balance_error: Optional[str] = None   # 같은 오류 반복 안내를 막기 위해 기억
        self.last_error_kind: Optional[str] = None       # "fatal"이면 스스로 낫지 않는 설정 문제
        self.symbol = symbol or EXCHANGE_SYMBOLS.get(exchange_name, SYMBOL)
        self.exchange.load_markets()  # market()/set_position_mode()가 미리 로드된 마켓 정보를 필요로 함
        # leverage 미지정 시 실행 시점의 전역 LEVERAGE를 사용(GUI에서 변경한 값이 반영되도록).
        self._setup_account(LEVERAGE if leverage is None else leverage)

    def _setup_account(self, leverage: int) -> None:
        try:
            self.exchange.set_position_mode(hedged=True, symbol=self.symbol)
        except Exception as e:
            if "NO_CHANGE" in str(e):
                # Gate.io는 이미 설정된 값으로 다시 설정을 시도하면 에러 형태로 응답한다.
                # 즉 이미 Dual Mode가 켜져 있다는 뜻이라 정상 상태다.
                logger.debug("Hedge/Dual Mode는 이미 설정되어 있습니다 (정상).")
            else:
                logger.debug("Hedge/Dual Mode 설정 실패: %s", e)
        self._setup_margin_mode(leverage)

    @staticmethod
    def safe_isolated_leverage(mmr: float = 0.005) -> float:
        """격리 마진에서 하드손절이 강제청산보다 먼저 오려면 넘지 말아야 할 레버리지."""
        return 1.0 / (1.0 - (1.0 - STOP_LOSS_PCT) * (1.0 - mmr))

    def _isolated_warning(self, leverage: int) -> str:
        limit = self.safe_isolated_leverage()
        if leverage <= limit:
            return (f"격리 마진입니다. 지금 레버리지({leverage}배)는 한도({limit:.0f}배) 안이라 "
                    f"하드손절이 정상 작동합니다.")
        return (f"⚠️ 격리 마진 + {leverage}배는 위험합니다. 이 조합은 봇의 하드손절(평단 -"
                f"{STOP_LOSS_PCT*100:g}%)이 작동하기 전에 거래소 강제청산이 먼저 옵니다. "
                f"교차(Cross)로 바꾸거나 레버리지를 {limit:.0f}배 이하로 낮춰주세요. "
                f"(모드 변경은 포지션과 미체결 주문이 없을 때만 가능합니다)")

    def read_margin_mode(self) -> Optional[str]:
        """거래소에 실제로 걸려 있는 증거금 모드를 읽는다. 못 읽으면 None."""
        try:
            positions = self.exchange.fetch_positions([self.symbol])
        except Exception as e:
            logger.debug("증거금 모드 조회 실패: %s", e)
            return None
        for p in positions or []:
            mm = p.get("marginMode") or p.get("marginType")
            if mm:
                mm = str(mm).lower()
                return "cross" if "cross" in mm else "isolated"
            info = p.get("info") or {}
            # Gate.io는 포지션의 leverage가 "0"이면 교차 마진이다.
            lv = info.get("leverage")
            if lv is not None:
                try:
                    return "cross" if float(lv) == 0 else "isolated"
                except (TypeError, ValueError):
                    pass
        return None

    def ensure_margin_mode(self, leverage: int, now: Optional[float] = None) -> bool:
        """증거금 모드가 아직 원하는 값이 아니면 다시 걸어본다.

        포지션이나 미체결 주문이 있으면 거래소가 변경을 거부하므로, 시작할 때 한 번
        실패하면 그대로 굳어버린다. 포지션이 정리된 뒤 이 함수가 다시 불리면 그때
        자동으로 전환된다. 호출 쪽에서 '지금 포지션이 없다'는 것을 확인하고 부른다.
        """
        if self.margin_mode_ok:
            return True
        t = time.time() if now is None else now
        if t < self._margin_retry_after:
            return False
        self._margin_retry_after = t + MARGIN_RETRY_SEC
        before = self.margin_mode_ok
        self._setup_margin_mode(leverage, retry=True)
        if self.margin_mode_ok and not before:
            logger.info("증거금 모드를 이제 %s로 바꿨습니다.",
                        "교차(Cross)" if MARGIN_MODE != "isolated" else "격리(Isolated)")
        return self.margin_mode_ok

    def _setup_margin_mode(self, leverage: int, retry: bool = False) -> None:
        """증거금 모드(교차/격리)와 레버리지를 설정한다.

        격리(Isolated)는 그 포지션에 배정된 증거금(명목/레버리지)만 손실을 받아낸다.
        레버리지가 높으면 그 증거금이 얇아져서, 봇의 하드손절이 작동하기도 전에 거래소가
        먼저 강제청산해 버린다(예: 격리 50배면 평단 대비 약 -3.6%에서 청산되는데
        하드손절은 -2.5%에서 작동해야 하므로 순서가 뒤집힌다). 그래서 이 봇은 계좌 잔고
        전체가 포지션을 받쳐주는 교차(Cross)를 기본으로 쓴다.

        거래소마다 지정 방법이 다르다.
          · Gate.io: 별도 API가 없고, set_leverage에 marginMode를 넘기면
            leverage=0 + cross_leverage_limit 형태로 나간다(0이 곧 교차 마진).
          · 나머지: set_margin_mode로 지정한 뒤 레버리지를 따로 건다.
        """
        mode = "cross" if MARGIN_MODE != "isolated" else "isolated"
        ko = "교차(Cross)" if mode == "cross" else "격리(Isolated)"
        applied = False
        setup_error: Optional[Exception] = None
        try:
            if self.exchange_name == "Gate.io":
                self.exchange.set_leverage(leverage, self.symbol, {"marginMode": mode})
            else:
                try:
                    self.exchange.set_margin_mode(mode, self.symbol)
                except Exception as e:
                    # 이미 같은 모드면 거래소가 에러로 응답하는 경우가 있다(정상).
                    if not any(k in str(e).lower() for k in ("no change", "not modified", "same", "not changed")):
                        logger.debug("증거금 모드 설정 실패: %s", e)
                self.exchange.set_leverage(leverage, self.symbol)
            applied = True
        except Exception as e:
            setup_error = e
            logger.debug("증거금 모드/레버리지 설정 실패: %s", e)

        if applied:
            actual = self.read_margin_mode()
            # 읽어서 확인된 경우에만 확정으로 본다. 못 읽으면 일단 됐다고 보되,
            # 확인이 안 됐으므로 나중에 포지션이 비면 한 번 더 시도한다.
            self.margin_mode_ok = (actual == mode) if actual else False
            if actual and actual != mode:
                # 설정 호출은 통과했는데 계좌는 여전히 다른 모드인 경우가 있다.
                # (포지션/미체결이 남아 있으면 거래소가 조용히 무시하기도 한다)
                logger.warning(
                    "증거금 모드를 %s로 바꾸지 못했습니다. 계좌는 지금 %s 입니다. %s",
                    ko, "교차(Cross)" if actual == "cross" else "격리(Isolated)",
                    self._isolated_warning(leverage) if actual == "isolated" else
                    "거래소에서 직접 확인해주세요.",
                )
            elif retry:
                pass    # 재시도 중에는 성공했을 때만 알린다(위 ensure_margin_mode에서 처리)
            else:
                shown = actual or mode
                logger.info("증거금 모드: %s / 레버리지 %s배",
                            "교차(Cross)" if shown == "cross" else "격리(Isolated)", leverage)
                if shown == "isolated":
                    logger.warning("%s", self._isolated_warning(leverage))
        elif retry:
            logger.debug("증거금 모드 재시도 실패: %s", setup_error)
        elif setup_error is not None and self.explain_api_error(setup_error):
            # 키가 틀렸거나 IP가 막힌 경우다. '포지션 때문'이라고 잘못 안내하면 안 된다.
            logger.warning("증거금 모드/레버리지를 설정하지 못했습니다. %s",
                           self.explain_api_error(setup_error))
        else:
            # 포지션이 열려 있으면 거래소가 변경을 거부한다. 이 경우 계좌에 이미 걸려 있는
            # 모드로 매매되므로, 사용자가 직접 확인할 수 있게 경고로 남긴다.
            actual = self.read_margin_mode()
            now_ko = ("교차(Cross)" if actual == "cross" else
                      "격리(Isolated)" if actual == "isolated" else "확인 불가")
            logger.warning(
                "증거금 모드/레버리지를 설정하지 못했습니다. 계좌는 지금 %s 입니다. "
                "(대개 포지션이나 미체결 주문이 있으면 변경이 거부됩니다. 포지션이 정리되면 자동으로 다시 시도합니다)",
                now_ko,
            )
            if actual == "isolated":
                logger.warning("%s", self._isolated_warning(leverage))
            elif actual is None:
                logger.warning("거래소에서 %s / %s배 인지 직접 확인해주세요.", ko, leverage)

    def _read_usdt_balance(self, params: dict) -> float:
        bal = self.exchange.fetch_balance(params)
        usdt = bal.get("USDT", {}) or {}
        free = usdt.get("free")
        total = usdt.get("total")
        # 자금이 포지션 증거금에 묶이면 free=0이 될 수 있어, free가 0이면 total로 대체한다.
        return float(free if free else (total or 0.0))

    @staticmethod
    def classify_api_error(err: Exception) -> Optional[Tuple[str, str]]:
        """오류를 (성격, 안내문)으로 분류한다.

        성격이 "fatal"이면 사용자가 설정을 고치기 전까지 절대 스스로 낫지 않는다.
        이런 상태로 매매를 계속 돌려두면 화면은 '매매 중'인데 실제로는 아무것도 못 하는
        상태가 되므로, 시작 단계에서 잡아 멈추는 편이 낫다.
        "recoverable"은 사용자가 거래소에서 고치면 그대로 두어도 자동으로 복구된다.
        """
        hint = LiveBroker.explain_api_error(err)
        if hint is None:
            return None
        low = str(err).lower()
        if "not in whitelist" in low or "ip not allow" in low or "invalid ip" in low:
            return ("recoverable", hint)   # IP만 추가하면 다음 조회에서 바로 통과한다
        return ("fatal", hint)

    @staticmethod
    def explain_api_error(err: Exception) -> Optional[str]:
        """거래소가 돌려준 오류를 보고, 사용자가 실제로 뭘 해야 하는지 알려준다.

        '잔고 조회 실패: {"message":"Request IP not in whitelist..."}' 처럼 원문만 찍으면
        무엇을 고쳐야 하는지 알기 어렵다. 스스로 낫지 않는 설정 문제는 조치 방법까지 짚어준다.
        """
        text = str(err)
        low = text.lower()
        # 서버 IP가 API 키의 허용 목록에 없음 — 가장 흔하고, 안내가 없으면 원인을 찾기 어렵다
        if "not in whitelist" in low or "ip not allow" in low or "invalid ip" in low:
            ip = ""
            m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", text)
            if m:
                ip = m.group(1)
            return (
                "이 서버의 IP가 API 키에 허용되어 있지 않습니다"
                + (f" (차단된 IP: {ip})" if ip else "")
                + ".\n"
                "   거래소 API 키 설정에서 'IP 허용 목록(IP whitelist)'에 위 IP를 추가하거나,\n"
                "   허용 목록 자체를 비워주세요. 추가 후 몇 분 뒤 자동으로 다시 시도합니다."
            )
        if any(k in low for k in ("invalid key", "invalid api", "apikey", "api_key", "api key",
                                  "api-key", "signature", "invalid sign", "authentication",
                                  "unauthorized", "invalid_key")):
            return ("API Key 또는 Secret Key가 올바르지 않습니다.\n"
                    "   오타가 없는지, 이 거래소용 키가 맞는지 확인해주세요.")
        if any(k in low for k in ("permission", "forbidden", "not authorized", "read only")):
            return ("API 키에 선물(Futures) 거래 권한이 없습니다.\n"
                    "   거래소에서 키 권한에 선물 거래를 켜주세요.")
        return None

    def get_balance(self) -> float:
        # 1) 기본 선물계좌(classic futures) 조회
        try:
            balance = self._read_usdt_balance({})
            if self._last_balance_error is not None:
                logger.info("거래소 연결이 정상으로 돌아왔습니다.")
                self._last_balance_error = None
            self.last_error_kind = None
        except Exception as e:
            # 같은 오류가 30초마다 반복되면 화면이 도배되므로, 사유가 바뀔 때만 안내한다.
            classified = self.classify_api_error(e)
            self.last_error_kind = classified[0] if classified else None
            hint = classified[1] if classified else None
            key = hint or str(e)
            if key != self._last_balance_error:
                self._last_balance_error = key
                if hint:
                    logger.warning("계좌를 조회하지 못했습니다. %s", hint)
                else:
                    logger.warning("잔고 조회 실패: %s", e)
            balance = 0.0
        # 2) 0이면 통합계좌(Unified Account)에 자금이 있을 수 있어 재조회
        if balance <= 0 and self.exchange_name == "Gate.io":
            try:
                balance = self._read_usdt_balance({"unifiedAccount": True})
                if balance > 0:
                    logger.debug("통합계좌(Unified Account)에서 USDT 잔고를 조회했습니다: %.4f", balance)
            except Exception as e:
                logger.debug("통합계좌 잔고 조회 실패: %s", e)
        return balance

    def quantize_qty(self, qty_btc: float, price: float) -> float:
        """계산된 BTC 수량을 거래소에서 실제 주문 가능한 값으로 보정해 BTC 단위로 돌려준다.

        - 계약 스텝(예: 1계약=0.0001 BTC)에 맞춰 내림
        - 그 결과가 거래소 최소 수량(보통 1계약)보다 작으면 최소 수량으로 상향
          (계좌가 작아 2% 마진이 1계약에 못 미쳐도 매매가 진행되게 함)
        """
        market = self.exchange.market(self.symbol)
        contract_size = market.get("contractSize") or 1
        limits = market.get("limits", {}) or {}
        min_contracts = (limits.get("amount") or {}).get("min") or 1
        try:
            contracts = float(self.exchange.amount_to_precision(self.symbol, round(qty_btc / contract_size, 8)))
        except Exception:
            contracts = 0.0
        if contracts < min_contracts:
            logger.debug(
                "계산된 수량(%.8f BTC)이 거래소 최소 주문(%s계약)보다 작아 최소 수량으로 올려서 주문합니다.",
                qty_btc, min_contracts,
            )
            contracts = float(min_contracts)
        return contracts * contract_size

    def fetch_position(self, side: Side) -> Optional[dict]:
        """재시작 시 거래소에 실제로 열려있는 롱/숏 포지션(BTC 수량 + 평단가)을 조회한다.

        반환값:
        - None            : 조회 실패(네트워크 등) → 저장된 상태를 그대로 유지해야 함
        - {"qty": 0, ...} : 해당 방향 포지션 없음(확실히 flat) → 내부 상태를 비워야 함
        - {"qty": X, ...} : 실제 포지션 존재 → 이 값으로 내부 상태를 동기화
        """
        try:
            positions = self.exchange.fetch_positions([self.symbol])
        except Exception as e:
            logger.debug("포지션 조회 실패(저장된 상태 유지): %s", e)
            return None
        contract_size = (self.exchange.market(self.symbol).get("contractSize")) or 1
        target = side.value.lower()
        for p in positions:
            p_side = (p.get("side") or "").lower()
            contracts = p.get("contracts") or 0
            if p_side == target and contracts:
                entry = p.get("entryPrice") or p.get("markPrice")
                return {
                    "qty": abs(contracts) * contract_size,
                    "entry_price": float(entry) if entry else None,
                    "contract_size": contract_size,
                }
        return {"qty": 0.0, "entry_price": None, "contract_size": contract_size}

    def _check_min_notional(self, qty_contracts: float, price: float) -> None:
        try:
            market = self.exchange.market(self.symbol)
            contract_size = market.get("contractSize") or 1
            limits = market.get("limits", {}) or {}
            min_amount = (limits.get("amount") or {}).get("min")
            min_cost = (limits.get("cost") or {}).get("min")
            if min_amount and qty_contracts < min_amount:
                logger.debug("주문 수량 %.8f계약이 거래소 최소 수량 %.8f계약보다 작습니다.", qty_contracts, min_amount)
            notional = qty_contracts * contract_size * price
            if min_cost and notional < min_cost:
                logger.debug("주문 금액 %.2f이 거래소 최소 주문금액 %.2f보다 작습니다.", notional, min_cost)
        except Exception as e:
            logger.debug("최소 주문 조건 확인 실패(무시): %s", e)

    def _order_params(self, side: Side, is_entry: bool) -> dict:
        """거래소별 양방향(헤지) 모드 주문 파라미터를 만든다.

        거래소마다 롱/숏 슬롯을 지정하는 방식이 완전히 다르다:
        - Binance : positionSide(LONG/SHORT)로 지정. reduceOnly를 함께 보내면 주문이 거부된다.
        - Gate.io : 매수/매도 방향이 곧 롱/숏 슬롯. 청산 주문에만 reduceOnly를 붙인다.
        - Bybit/OKX/Bitget : ccxt의 통일 파라미터 hedged=True를 쓰면 각각 positionIdx /
          posSide 를 알아서 채워준다. 청산은 hedged+reduceOnly 조합으로 반대 슬롯이 아닌
          해당 슬롯을 줄이도록 처리된다.
        """
        if self.exchange_name == "Binance":
            return {"positionSide": side.value}
        if self.exchange_name == "Gate.io":
            return {} if is_entry else {"reduceOnly": True}
        params = {"hedged": True}
        if not is_entry:
            params["reduceOnly"] = True
        return params

    def fill_order(self, side: Side, is_entry: bool, qty: float, price: float) -> None:
        # Gate.io 등 계약(contract) 기반 거래소는 주문 amount가 BTC가 아니라 '계약 수'이며,
        # 계약 1개 = market['contractSize']만큼의 BTC다. 진입 수량은 quantize_qty에서 이미
        # 계약 스텝에 맞게 보정되어 오므로, 여기서는 BTC→계약 변환만 하면 된다(부동소수점
        # 오차로 계약수가 0으로 잘리지 않게 round로 스냅한다).
        market = self.exchange.market(self.symbol)
        contract_size = market.get("contractSize") or 1
        try:
            qty_contracts = float(self.exchange.amount_to_precision(self.symbol, round(qty / contract_size, 8)))
        except Exception:
            qty_contracts = 0.0
        if SHOW_QTY_DETAIL:
            logger.debug(
                "주문 실행: BTC수량=%.8f / 계약크기=%s / 계약수=%.4f / 방향=%s / %s / 가격=%.2f",
                qty, contract_size, qty_contracts, side.value, "진입" if is_entry else "청산", price,
            )
        else:
            logger.debug(
                "주문 실행: %s USDT / 방향=%s / %s / 가격=%.2f",
                f"{qty * price:,.2f}", side.value, "진입" if is_entry else "청산", price,
            )
        if qty_contracts <= 0:
            raise RuntimeError("주문 가능 최소 금액에 미달합니다. 계좌 잔고를 확인해주세요.")
        self._check_min_notional(qty_contracts, price)
        if side == Side.LONG:
            order_side = "buy" if is_entry else "sell"
        else:
            order_side = "sell" if is_entry else "buy"
        params = self._order_params(side, is_entry)
        self.exchange.create_order(self.symbol, "market", order_side, qty_contracts, None, params)

    def apply_pnl(self, pnl: float) -> None:
        pass  # 실거래 잔고는 거래소가 직접 반영하므로 별도 처리 불필요


def make_qty_provider(broker) -> Callable[[float], float]:
    """1차 진입 규모(USDT) = 레버리지한 잔고(잔고 x 10배)의 2%.

    즉 주문 명목가치(USDT) = 잔고 x LEVERAGE x INITIAL_MARGIN_PCT = 잔고의 20%.
    이 USDT 금액을 현재가로 나눠 BTC 수량으로 바꾼 뒤, broker.quantize_qty로 거래소
    최소 주문 단위에 맞게 보정한다.
    """

    def _provider(price: float) -> float:
        balance = broker.get_balance()
        # 잔고를 못 읽었거나(네트워크/권한 오류) 0이면 진입 규모를 계산할 수 없다.
        # 이때 0을 돌려주지 않으면 최소 주문 수량으로 보정되어, 잘못된 근거로 실거래가
        # 시작되고 그 수량을 기준으로 물타기까지 이어지므로 반드시 진입을 막아야 한다.
        if balance <= 0:
            # 왜 못 읽었는지는 바로 위에서 get_balance가 이미 구체적으로 안내했다.
            # 여기서는 그래서 매매를 안 한다는 사실만 짧게 남긴다(같은 안내 중복 방지).
            logger.warning("계좌 잔고를 확인할 수 없어 진입하지 않습니다.")
            return 0.0
        if price is None or not (price > 0):
            logger.warning("시세가 비정상(%s)이라 진입하지 않습니다.", price)
            return 0.0
        notional_usdt = balance * LEVERAGE * INITIAL_MARGIN_PCT  # 레버리지한 잔고의 2%
        qty_btc = notional_usdt / price
        if SHOW_QTY_DETAIL:
            logger.debug(
                "진입 규모 계산: 잔고=%.4f x %sx x %.2f%% = %.4f USDT (= %.8f BTC)",
                balance, LEVERAGE, INITIAL_MARGIN_PCT * 100, notional_usdt, qty_btc,
            )
        else:
            logger.debug(
                "진입 규모 계산: 잔고 %s x %s배 x %.2f%% = %s USDT",
                f"{balance:,.2f}", LEVERAGE, INITIAL_MARGIN_PCT * 100, f"{notional_usdt:,.2f}",
            )
        return broker.quantize_qty(qty_btc, price)

    return _provider


# ───────────── 마틴게일 상태머신 (롱/숏 독립 모듈) ─────────────
@dataclass
class Fill:
    price: float
    qty: float


class MartingaleModule:
    """롱 또는 숏 한쪽만 담당하는 완전 독립 상태머신. 반대편 모듈과 상태를 공유하지 않는다."""

    def __init__(self, side: Side, broker, notifier: TelegramNotifier, qty_provider: Callable[[float], float],
                 mode_label: str, on_trade_closed: Optional[Callable[[dict], None]] = None):
        self.side = side
        self.broker = broker
        self.notifier = notifier
        self.qty_provider = qty_provider
        self.mode_label = mode_label
        self.on_trade_closed = on_trade_closed  # 청산될 때마다 매매 기록을 남기기 위한 콜백
        self.consecutive_sl = 0
        self.halted = False
        self._reset()

    def _reset(self) -> None:
        self.step = 0
        self.fills: List[Fill] = []
        self.avg_price: Optional[float] = None
        self.total_qty = 0.0
        self.cooldown_until: Optional[float] = None

    def resume(self) -> None:
        """회로차단기로 정지된 모듈을 수동으로 재개한다."""
        self.halted = False
        self.consecutive_sl = 0

    def to_state(self) -> dict:
        return {
            "step": self.step,
            "fills": [{"price": f.price, "qty": f.qty} for f in self.fills],
            "cooldown_until": self.cooldown_until,
            "consecutive_sl": self.consecutive_sl,
            "halted": self.halted,
        }

    def load_state(self, state: dict) -> None:
        self.fills = [Fill(f["price"], f["qty"]) for f in state.get("fills", [])]
        self.step = state.get("step", 0)
        if self.fills:
            self._recalc_avg()
        else:
            self.avg_price = None
            self.total_qty = 0.0
        self.cooldown_until = state.get("cooldown_until")
        self.consecutive_sl = state.get("consecutive_sl", 0)
        self.halted = state.get("halted", False)

    def sync_from_exchange(self, actual_qty: float, entry_price: Optional[float], contract_size: float) -> bool:
        """거래소의 실제 포지션(수량 + 평단가)에 맞춰 내부 상태를 재구성한다.

        저장된 상태와 거래소가 어긋나도(수동 개입/누락 등) 거래소를 '진실'로 삼아 맞춘다.
        평단가/총수량은 거래소 값으로 정확히 맞추고, 물타기 단계(step)는 계약 수로 추정한다.
        반환값: 실제 포지션이 있어 동기화했으면 True, 포지션이 없어 비웠으면 False.
        """
        if not actual_qty or actual_qty <= 0 or not entry_price:
            # 쿨다운은 '언제까지 쉰다'는 시간 약속이라 포지션 유무와 무관하다.
            # _reset이 지워버리면 익절/손절 직후 재시작했을 때 대기 없이 바로 재진입한다.
            keep = self.cooldown_until
            self._reset()
            self.cooldown_until = keep
            return False
        # 단계 추정: 누적 수량은 1단계 수량의 (2^step - 1)배이므로, 지금 기준 1단계 수량과의
        # 비율로 역산한다. (예전에는 '1단계 = 1계약'을 전제해 계약수의 비트길이로 계산했는데,
        # 실제 1단계가 수십 계약이면 항상 최대 단계로 오판되어 추가 진입이 멈추는 버그가 있었다.)
        step = 1
        try:
            base_qty = self.qty_provider(entry_price)
        except Exception as e:
            logger.debug("단계 추정용 기준 수량 계산 실패: %s", e)
            base_qty = 0.0
        if base_qty > 0:
            ratio = actual_qty / base_qty
            if ratio > 0:
                # 기준 수량은 '지금' 잔고로 계산한 값이라, 진입 이후 잔고가 변했으면
                # 추정이 어긋난다. 어긋나는 방향에 따라 위험이 다르다.
                #   · 단계를 실제보다 높게 잡으면 → 최대 단계에 일찍 닿아 추가 진입을 덜 한다(안전)
                #   · 단계를 실제보다 낮게 잡으면 → 설계보다 한 번 더 물타기해 노출이 커진다(위험)
                # 그래서 반올림 대신 올림으로 안전한 쪽에 붙인다. 잔고가 딱 맞는 경우
                # (ratio = 2^step - 1)에는 부동소수점 오차로 한 단계 튀지 않게 여유를 준다.
                step = math.ceil(math.log2(ratio + 1) - 1e-9)
        step = min(MAX_STEPS, max(1, step))
        base = actual_qty / (2 ** step - 1)
        # 같은 평단가로 base, 2base, 4base... 형태의 체결 내역을 재구성(합계 = actual_qty)
        self.fills = [Fill(entry_price, base * (2 ** i)) for i in range(step)]
        self.step = step
        self._recalc_avg()  # avg_price=entry_price, total_qty=actual_qty 로 정리됨
        self.cooldown_until = None
        return True

    @property
    def in_position(self) -> bool:
        return self.step > 0

    def _in_cooldown(self, now: float) -> bool:
        return self.cooldown_until is not None and now < self.cooldown_until

    def _entry_signal(self, price: float, rsi: float, bb: Tuple[float, float, float],
                      trend_ema: Optional[float] = None) -> bool:
        _, upper, lower = bb
        if self.side == Side.LONG:
            triggered = rsi <= RSI_LONG_TRIGGER or price <= lower
        else:
            triggered = rsi >= RSI_SHORT_TRIGGER or price >= upper
        if not triggered:
            return False
        # 추세를 거스르는 방향이면 새로 들어가지 않는다. 이미 들고 있는 포지션은
        # 물타기/익절/손절이 그대로 돌아가므로, 관리 중인 자리를 방치하지는 않는다.
        if trend_ema:
            if self.side == Side.SHORT and price > trend_ema:
                return False    # 상승추세에서 숏 신규진입 금지
            if self.side == Side.LONG and price < trend_ema:
                return False    # 하락추세에서 롱 신규진입 금지
        return True

    def _pnl_pct(self, price: float) -> float:
        if self.avg_price is None:
            return 0.0
        if self.side == Side.LONG:
            return (price - self.avg_price) / self.avg_price
        return (self.avg_price - price) / self.avg_price

    def _realized_pnl(self, price: float) -> float:
        if self.side == Side.LONG:
            return (price - self.avg_price) * self.total_qty
        return (self.avg_price - price) * self.total_qty

    def on_tick(self, price: float, rsi: Optional[float], bb: Optional[Tuple[float, float, float]],
                now: Optional[float] = None, trend_ema: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if self.halted or rsi is None or bb is None:
            return
        # 시세가 0이거나 음수/NaN으로 들어오면(거래소 응답 이상, 캔들 누락 등) 계산이 전부
        # 망가진다. 특히 진입 수량은 금액/가격이라 0으로 나누며 매매 스레드가 통째로 죽는다.
        # 이런 값은 매매 판단에 쓰지 않고 그냥 넘긴다(다음 조회에서 정상 값이 오면 재개).
        if price is None or not (price > 0) or math.isinf(price):
            logger.debug("비정상 시세(%s)를 건너뜁니다.", price)
            return

        if not self.in_position:
            if self._in_cooldown(now):
                return
            if self._entry_signal(price, rsi, bb, trend_ema):
                self._enter_initial(price)
            return

        pnl_pct = self._pnl_pct(price)

        # 익절: 평단가 대비 +TP_PCT
        if pnl_pct >= TP_PCT:
            self._take_profit(price, now)
            return

        # 손절: 평단가 대비 -STOP_LOSS_PCT (물타기 단계와 무관하게 전량 청산)
        if pnl_pct <= -STOP_LOSS_PCT:
            self._stop_loss(price, now)
            return

        # 물타기: 아직 최대 단계가 아니고 평단가 대비 -STEP_TRIGGER_PCT 이상 빠졌을 때
        if self.step < MAX_STEPS and pnl_pct <= -STEP_TRIGGER_PCT:
            self._add_martingale(price)

    def _recalc_avg(self) -> None:
        self.total_qty = sum(f.qty for f in self.fills)
        self.avg_price = sum(f.price * f.qty for f in self.fills) / self.total_qty

    def _enter_initial(self, price: float) -> None:
        qty = self.qty_provider(price)
        if qty <= 0:
            return  # 진입 규모를 산출하지 못한 경우(잔고 미확인 등)에는 주문을 내지 않는다
        self.broker.fill_order(self.side, True, qty, price)
        self.fills = [Fill(price, qty)]
        self.step = 1
        self._recalc_avg()
        self._notify_entry(price, qty)

    def _add_martingale(self, price: float) -> None:
        # 거래소가 실제로 체결할 수 있는 단위로 먼저 맞춘다. 재시작 동기화로 재구성된
        # 체결 내역은 계약 단위에 딱 떨어지지 않을 수 있어, 이 보정을 하지 않으면
        # 내부 보유수량이 거래소 실제 수량과 조금씩 어긋나 손익 계산이 틀어진다.
        qty = self.broker.quantize_qty(self.fills[-1].qty * 2, price)
        if qty <= 0:
            return
        self.broker.fill_order(self.side, True, qty, price)
        self.fills.append(Fill(price, qty))
        self.step += 1
        self._recalc_avg()
        self._notify_entry(price, qty)

    def _close_all(self, price: float) -> float:
        pnl = self._realized_pnl(price)
        self.broker.fill_order(self.side, False, self.total_qty, price)
        self.broker.apply_pnl(pnl)
        return pnl

    def _record_trade(self, price: float, reason: str, pnl: float) -> None:
        """청산 1건을 매매 기록으로 남긴다(진입가=평단가, 청산가, 수익금, 레버리지 반영 수익률)."""
        if self.on_trade_closed is None:
            return
        avg = self.avg_price or price
        # 가격 변동률 x 레버리지 = 증거금 대비 실제 수익률
        raw_pct = ((price - avg) / avg) if self.side == Side.LONG else ((avg - price) / avg)
        try:
            self.on_trade_closed({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "side": self.side.value,
                "reason": reason,
                "entry_price": round(avg, 2),
                "exit_price": round(price, 2),
                "qty": round(self.total_qty, 8),
                "profit_usdt": round(pnl, 4),
                "leveraged_return_pct": round(raw_pct * 100 * LEVERAGE, 4),
            })
        except Exception as e:
            logger.warning("매매 기록 저장 실패: %s", e)

    def _take_profit(self, price: float, now: Optional[float] = None) -> None:
        pnl = self._close_all(price)
        self._notify_close(price, "익절", pnl)
        self._record_trade(price, "익절", pnl)
        self._reset()
        self.consecutive_sl = 0
        self.cooldown_until = (time.time() if now is None else now) + COOLDOWN_SEC

    def _stop_loss(self, price: float, now: Optional[float] = None) -> None:
        pnl = self._close_all(price)
        self._notify_close(price, "손절", pnl)
        self._record_trade(price, "손절", pnl)
        self._reset()
        self.consecutive_sl += 1
        self.cooldown_until = (time.time() if now is None else now) + COOLDOWN_SEC
        if self.consecutive_sl >= MAX_CONSECUTIVE_SL:
            self.halted = True
            self._notify_halt()

    # ───── 사용자 화면용 알림 ─────
    # 배포용이므로 내부 전략(진입 조건, 단계별 물타기, 쿨다운, 안전장치 기준 등)이 드러나지 않도록
    # 사용자가 필요한 정보(방향/금액/가격/보유 포지션/손익/잔고)만 간결하게 보여준다.
    @property
    def _side_ko(self) -> str:
        return "롱" if self.side == Side.LONG else "숏"

    def _notify_halt(self) -> None:
        self.notifier.send(
            f"⏸ {self._side_ko} 자동 정지 (안전장치 작동)\n"
            f"손실이 반복되어 {self._side_ko} 매매를 멈췄습니다. 다시 시작하려면 프로그램을 재시작하세요."
        )

    def _notify_entry(self, price: float, qty: float) -> None:
        amount_usdt = qty * price
        if SHOW_QTY_DETAIL:
            # 배포용: 진입 단계(차수)를 드러내지 않고 보유 수량만 보여준다.
            head = f"▶ {self._side_ko} 진입"
            holding = f"보유 {self.total_qty:.6f} BTC"
        else:
            head = f"▶ {self._side_ko} {self.step}차 진입"
            holding = f"누적 투입 {self.total_qty * self.avg_price:,.2f} USDT"
        body = (f"{head} | 금액 {amount_usdt:,.2f} USDT | 가격 {price:,.2f}\n"
                f"   {holding} | 평균단가 {self.avg_price:,.2f}")
        full = f"{body} | 잔고 {self.broker.get_balance():,.2f} USDT"
        self.notifier.send(full, body if not TELEGRAM_SHOW_BALANCE else None)

    def _notify_close(self, price: float, reason: str, pnl: float) -> None:
        result = "수익" if pnl >= 0 else "손실"
        head = f"■ {self._side_ko} 청산 | {result} {pnl:+,.2f} USDT | 가격 {price:,.2f}"
        full = f"{head}\n   잔고 {self.broker.get_balance():,.2f} USDT"
        self.notifier.send(full, head if not TELEGRAM_SHOW_BALANCE else None)


# ───────────── 봇 엔진 ─────────────
class HedgedMartingaleBot:
    def __init__(self, broker, notifier: TelegramNotifier, mode_label: str, state_path: Optional[str] = None,
                 on_trade_closed: Optional[Callable[[dict], None]] = None):
        self.broker = broker
        self.notifier = notifier
        self.state_path = state_path
        qty_provider = make_qty_provider(broker)
        self.long = MartingaleModule(Side.LONG, broker, notifier, qty_provider, mode_label, on_trade_closed)
        self.short = MartingaleModule(Side.SHORT, broker, notifier, qty_provider, mode_label, on_trade_closed)
        # 헷지 물량: 보호 대상 방향 -> Fill(진입가, 수량).
        # 거래소는 방향당 포지션이 하나뿐이라, 롱을 헷지한 숏은 숏 모듈의 포지션과 합쳐진다.
        # 그래서 헷지 물량을 여기서 따로 들고 있다가 청산/동기화 때 빼주어야 수량이 맞는다.
        self.hedges: dict = {}
        self._hedge_retry_after: dict = {}   # 헷지 주문 실패 시 재시도 대기 시각
        self._hedge_last_error: dict = {}    # 같은 사유 반복 안내 방지
        if self.state_path:
            self._load_state()
        # 상태 파일이 없거나 읽기에 실패해도 거래소 실포지션은 반드시 확인해야 한다.
        # (예전에는 _load_state 안에서만 동기화해서, 상태 파일이 유실되면 거래소에 포지션이
        #  살아있는데도 봇이 '포지션 없음'으로 시작해 그 위에 새로 1단계를 또 진입했다.)
        self._sync_with_exchange()

    def _load_state(self) -> None:
        # 상태 파일이 깨졌으면 직전 백업(.bak)으로 한 번 더 시도한다. 상태를 살려내면
        # 진입 단계가 정확히 복원되고, 수량만 보고 단계를 되짚는 추정 경로를 타지 않는다.
        data = None
        for path in (self.state_path, self.state_path + ".bak"):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except Exception as e:
                logger.debug("매매 상태 파일을 읽지 못했습니다(%s): %s", path, e)
        if data is None:
            return
        self.long.load_state(data.get("long", {}))
        self.short.load_state(data.get("short", {}))
        self.hedges = {}
        for name, h in (data.get("hedges") or {}).items():
            try:
                self.hedges[Side(name)] = Fill(float(h["price"]), float(h["qty"]))
            except Exception as e:
                logger.debug("헷지 상태 복원 실패(%s): %s", name, e)
        if self.long.in_position or self.short.in_position:
            logger.debug(
                "이전 매매 상태 복원 (LONG step=%d avg=%s / SHORT step=%d avg=%s)",
                self.long.step, self.long.avg_price, self.short.step, self.short.avg_price,
            )
            logger.info("이전 매매 상태를 불러왔습니다.")

    def _sync_with_exchange(self) -> None:
        """거래소의 실제 포지션을 '진실'로 삼아 내부 상태를 맞춘다(B안: 자동 동기화).

        - 조회 실패(None): 저장된 상태를 그대로 사용
        - 저장된 상태와 수량이 일치: 저장된 상태를 그대로 사용(진입 단계 정보가 정확하므로)
        - 수량이 어긋남: 거래소의 수량/평단가로 내부 상태를 재구성
        - 포지션 없음: 내부 상태를 비움(거래소가 flat이므로)
        """
        for module in (self.long, self.short):
            pos = self.broker.fetch_position(module.side)
            if pos is None:
                continue  # 조회 불가 → 저장된 상태 유지
            had = module.in_position
            actual_qty = pos["qty"]
            # 반대 방향을 보호하려고 열어둔 헷지 물량은 이 모듈 것이 아니므로 빼고 본다.
            opposite = Side.SHORT if module.side == Side.LONG else Side.LONG
            hedge = self.hedges.get(opposite)
            if hedge:
                actual_qty = max(0.0, actual_qty - hedge.qty)

            # 주문 최소 단위보다 작은 잔량(먼지)은 없는 것으로 본다.
            # 헷지 물량을 빼면 부동소수점 오차로 1e-17 같은 값이 남을 수 있는데, 이걸
            # 포지션으로 취급하면 수량이 0에 가까운 '유령 1차'가 만들어져 그 방향이
            # 익절도 손절도 못 하고 새 진입까지 막힌 채로 굳어버린다.
            dust = pos.get("contract_size") or 0.0
            if actual_qty <= max(dust, 1e-12):
                actual_qty = 0.0

            # 저장된 상태와 거래소 수량이 사실상 같으면 저장된 상태를 신뢰한다.
            # 저장된 상태에는 실제 진입 단계가 그대로 들어 있어, 수량만 보고 단계를 되짚는
            # 것보다 정확하다(단계를 잘못 추정하면 추가 진입이 멈추거나 과하게 나갈 수 있다).
            if had and actual_qty > 0 and abs(actual_qty - module.total_qty) <= max(module.total_qty * 0.02, 1e-9):
                logger.debug(
                    "%s 저장된 상태와 거래소 수량 일치 → 상태 유지 (수량=%.6f, step=%d)",
                    module.side.value, module.total_qty, module.step,
                )
                continue

            synced = module.sync_from_exchange(actual_qty, pos.get("entry_price"), pos.get("contract_size") or 1)
            if synced:
                logger.debug(
                    "%s 동기화 (수량=%.6f, 평단=%.2f, step=%d)",
                    module.side.value, module.total_qty, module.avg_price, module.step,
                )
                side_ko = "롱" if module.side == Side.LONG else "숏"
                self.notifier.send(
                    f"🔄 {side_ko} 기존 포지션 확인 | 보유 {module.total_qty:.6f} BTC | "
                    f"평균단가 {module.avg_price:,.2f}"
                )
            elif had:
                logger.debug("%s 거래소에 실제 포지션이 없어 내부 상태를 초기화했습니다.", module.side.value)

    def _save_state(self) -> None:
        """상태를 원자적으로 저장한다(임시파일에 다 쓴 뒤 교체).

        예전처럼 대상 파일을 직접 열어 쓰면, 쓰는 도중에 전원이 나가거나 프로그램이
        죽었을 때 파일이 잘린 채 남아 다음 실행에서 상태를 통째로 잃는다. 상태를 잃으면
        거래소 수량만 보고 진입 단계를 되짚어야 해서 정확도가 떨어지므로, 저장 자체를
        깨지지 않게 만드는 편이 낫다. 직전 상태는 .bak으로 남겨 한 번 더 보호한다.
        """
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"long": self.long.to_state(), "short": self.short.to_state(),
                           "hedges": {k.value: {"price": v.price, "qty": v.qty}
                                      for k, v in self.hedges.items()}}, f)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self.state_path):
                try:
                    os.replace(self.state_path, self.state_path + ".bak")
                except Exception:
                    pass    # 백업 실패는 치명적이지 않으므로 저장 자체는 계속 진행한다
            os.replace(tmp, self.state_path)
        except Exception as e:
            logger.debug("매매 상태 저장 실패: %s", e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _side_status(self, module, price: float) -> str:
        """하트비트에 넣을 방향별 한 줄 상태."""
        if not module.in_position:
            return "대기"
        pnl_pct = module._pnl_pct(price) * 100
        if SHOW_QTY_DETAIL:
            # 배포용: 진입 차수를 드러내지 않는다.
            return f"보유 · 평단 {module.avg_price:,.2f} · {pnl_pct:+.2f}%"
        return f"{module.step}차 · 평단 {module.avg_price:,.2f} · {pnl_pct:+.2f}%"

    def _log_heartbeat(self, price: float) -> None:
        logger.info(
            "%s 시세 %s | 롱 %s | 숏 %s",
            HEARTBEAT_MARK, f"{price:,.2f}",
            self._side_status(self.long, price), self._side_status(self.short, price),
        )

    def _hedge_pnl(self, side: Side, price: float) -> float:
        """보호 대상이 side인 헷지의 현재 손익(헷지는 반대 방향 포지션이다)."""
        h = self.hedges.get(side)
        if not h:
            return 0.0
        return (h.price - price) * h.qty if side == Side.LONG else (price - h.price) * h.qty

    def _hedge_retry_ok(self, side, now: Optional[float]) -> bool:
        """헷지 주문이 실패했을 때 곧바로 다시 던지지 않도록 잠시 쉰다.

        증거금 부족처럼 바로 낫지 않는 사유면 조회할 때마다 거부 주문을 계속 보내게 되어,
        거래소 요청 한도에 걸리거나 계정이 제한될 수 있다.
        """
        t = time.time() if now is None else now
        until = self._hedge_retry_after.get(side)
        return until is None or t >= until

    def _open_hedge(self, module, price: float, now: Optional[float] = None) -> None:
        """반대 방향으로 같은 수량을 열어 손실을 그 자리에 고정한다.

        진입가는 반드시 '지금 시세'여야 한다. 예전에는 마지막 체결가를 썼는데,
        재시작 직후처럼 그 값이 현재가와 크게 벌어져 있으면 실제 체결은 현재가에
        되면서 기록만 옛 가격으로 남아 고정 손익이 어긋났다.
        """
        qty = module.total_qty
        opposite = Side.SHORT if module.side == Side.LONG else Side.LONG
        try:
            self.broker.fill_order(opposite, True, qty, price)
        except Exception as e:
            t = time.time() if now is None else now
            self._hedge_retry_after[module.side] = t + HEDGE_RETRY_SEC
            if self._hedge_last_error.get(module.side) != str(e):
                self._hedge_last_error[module.side] = str(e)
                logger.warning("헷지 진입에 실패했습니다: %s (%d초 뒤 다시 시도합니다)",
                               e, HEDGE_RETRY_SEC)
            return
        self.hedges[module.side] = Fill(price, qty)
        self._hedge_retry_after.pop(module.side, None)
        self._hedge_last_error.pop(module.side, None)
        side_ko = "롱" if module.side == Side.LONG else "숏"
        locked = module._realized_pnl(price)
        self.notifier.send(
            f"🔒 {side_ko} 헷지 | 반대 포지션으로 손실을 고정했습니다\n"
            f"   고정 손익 {locked:+,.2f} USDT | 평단 {module.avg_price:,.2f} 복귀 시 정리"
        )

    def _close_hedge(self, module, price: float) -> None:
        """원래 포지션과 헷지를 함께 정리한다. 고정된 손익이 그대로 실현된다."""
        h = self.hedges.get(module.side)
        if not h:
            return
        opposite = Side.SHORT if module.side == Side.LONG else Side.LONG
        pnl = module._realized_pnl(price) + self._hedge_pnl(module.side, price)
        try:
            self.broker.fill_order(module.side, False, module.total_qty, price)
            self.broker.fill_order(opposite, False, h.qty, price)
        except Exception as e:
            logger.warning("헷지 정리에 실패했습니다: %s", e)
            return
        self.broker.apply_pnl(pnl)
        module._record_trade(price, "헷지정리", pnl)
        side_ko = "롱" if module.side == Side.LONG else "숏"
        result = "수익" if pnl >= 0 else "손실"
        self.notifier.send(f"🔓 {side_ko} 헷지 정리 | {result} {pnl:+,.2f} USDT | 가격 {price:,.2f}")
        del self.hedges[module.side]
        module._reset()

    def _handle_hedges(self, price: float, now: Optional[float]) -> set:
        """헷지 진입/해제를 처리하고, 이번 틱에 봇 로직을 건너뛸 방향을 돌려준다."""
        frozen = set()
        if not HEDGE_AT_STEP:
            return frozen
        for module in (self.long, self.short):
            if module.side in self.hedges:
                frozen.add(module.side)
                # 가격이 평단가로 돌아오면 정리한다(시간 제한 없음).
                back = (price >= module.avg_price if module.side == Side.LONG
                        else price <= module.avg_price)
                if back:
                    self._close_hedge(module, price)
                    frozen.discard(module.side)
            elif module.in_position and module.step >= HEDGE_AT_STEP:
                if not self._hedge_retry_ok(module.side, now):
                    continue
                self._open_hedge(module, price, now)
                if module.side in self.hedges:
                    frozen.add(module.side)
        return frozen

    def on_price(self, price: float, closes_window: List[float], now: Optional[float] = None) -> None:
        rsi = Indicators.rsi(closes_window)
        bb = Indicators.bollinger(closes_window)
        trend_ema = Indicators.ema(closes_window, TREND_EMA_PERIOD) if TREND_EMA_PERIOD else None
        frozen = self._handle_hedges(price, now)
        # 헷지가 걸린 방향은 손익이 고정되어 있으므로 물타기·익절·손절을 돌리지 않는다.
        if self.long.side not in frozen:
            self.long.on_tick(price, rsi, bb, now, trend_ema)
        if self.short.side not in frozen:
            self.short.on_tick(price, rsi, bb, now, trend_ema)
        # 진입으로 헷지 조건이 새로 충족됐을 수 있어 한 번 더 확인한다.
        self._handle_hedges(price, now)
        # 포지션이 하나도 없을 때만 증거금 모드를 다시 걸어본다. 시작 시점에 포지션이
        # 남아 있어 거부됐더라도, 그게 정리되면 여기서 자동으로 교차로 바뀐다.
        if not (self.long.in_position or self.short.in_position or self.hedges):
            ensure = getattr(self.broker, "ensure_margin_mode", None)
            if ensure:
                try:
                    ensure(LEVERAGE, now)
                except Exception as e:
                    logger.debug("증거금 모드 재시도 중 오류: %s", e)
        self._save_state()
        # 매매 판단이 끝난 뒤의 상태를 보여준다(진입/청산이 있었다면 그게 반영된 상태).
        if price is not None and price > 0:
            self._log_heartbeat(price)

    def run_forever(self, market_data: PublicMarketData, poll_sec: int = POLL_SEC, stop_event: Optional[threading.Event] = None) -> None:
        logger.debug("버전 %s / %s / 레버리지 %sx / %s", VERSION, self.long.mode_label, LEVERAGE, TIMEFRAME)
        balance = 0.0
        try:
            balance = self.broker.get_balance()
            logger.info("계좌 잔고: %s USDT", f"{balance:,.2f}")
        except Exception as e:
            logger.debug("잔고 조회 실패: %s", e)

        # 키가 틀렸거나 권한이 없으면 아무리 기다려도 낫지 않는다. 그런데도 '매매 중'으로
        # 계속 돌려두면, 화면은 정상인데 실제로는 한 건도 못 하는 상태가 오래 이어진다.
        # 그래서 시작 단계에서 끊고, 무엇을 고쳐야 하는지 알려준다.
        if balance <= 0 and getattr(self.broker, "last_error_kind", None) == "fatal":
            self.notifier.send(
                "⛔ 자동매매를 시작하지 못했습니다\n"
                "   거래소 계정 설정을 고친 뒤 다시 시작해주세요."
            )
            logger.error("자동매매를 시작하지 못했습니다. 위 안내대로 고친 뒤 다시 시작해주세요.")
            return

        if balance <= 0:
            logger.warning(
                "잔고가 0으로 조회됩니다. 선물 지갑에 USDT가 있는지 확인해주세요. "
                "(일시적인 조회 실패라면 그대로 두시면 자동으로 다시 시도합니다)"
            )
        logger.info("자동매매를 시작했습니다. 매수/매도 기회를 찾는 중입니다...")

        # 시세 조회 실패와 매매(주문) 실패는 원인도 조치도 다르므로 따로 안내한다.
        # 예전에는 둘을 한 덩어리로 잡아서, 주문이 거부돼도 "시세를 불러오지 못했습니다"라고
        # 나가 사용자가 엉뚱한 곳(네트워크)을 확인하게 됐다.
        feed_notified = False
        trade_notified = None
        while stop_event is None or not stop_event.is_set():
            closes = None
            try:
                closes = market_data.get_closes()
                feed_notified = False
            except Exception as e:
                logger.debug("시세 조회 오류: %s", e)
                if not feed_notified:
                    logger.warning("일시적으로 시세를 불러오지 못했습니다. 자동으로 다시 시도합니다.")
                    feed_notified = True
            if closes:
                try:
                    self.on_price(closes[-1], closes)
                    if trade_notified is not None:
                        logger.info("매매 처리가 정상으로 돌아왔습니다.")
                        trade_notified = None
                except Exception as e:
                    logger.debug("매매 처리 오류: %s", e)
                    hint = None
                    explain = getattr(self.broker, "explain_api_error", None)
                    if explain:
                        try:
                            hint = explain(e)
                        except Exception:
                            hint = None
                    msg = hint or str(e)
                    if msg != trade_notified:   # 같은 사유 반복은 한 번만 안내
                        trade_notified = msg
                        if hint:
                            logger.warning("주문을 처리하지 못했습니다. %s", hint)
                        else:
                            logger.warning(
                                "주문을 처리하지 못했습니다: %s "
                                "(거래소 최소 주문 금액·증거금·권한을 확인해주세요. 자동으로 다시 시도합니다)", e)
            if stop_event is not None:
                if stop_event.wait(poll_sec):
                    break
            else:
                time.sleep(poll_sec)
        logger.info("자동매매를 정지했습니다.")


# ───────────── 가상 차트 데이터 정성 테스트 ─────────────
def generate_synthetic_prices() -> List[float]:
    """롱/숏 1차 진입 조건이 각각 최소 한 번씩 발생하도록 설계한 가상 15분봉 종가 시퀀스."""
    random.seed(7)
    prices = [50_000.0]

    def add_walk(n: int, drift: float, noise: float) -> None:
        for _ in range(n):
            change = drift + random.uniform(-noise, noise)
            prices.append(prices[-1] * (1 + change))

    add_walk(25, 0.0, 0.0015)     # 초반 횡보(지표 워밍업)
    add_walk(20, -0.010, 0.002)   # 급락 구간 → RSI<=40 / 하단밴드 터치 유도
    add_walk(15, 0.0, 0.0015)     # 중간 횡보(재정비 + 3분 쿨다운 경과)
    add_walk(20, 0.010, 0.002)    # 급등 구간 → RSI>=60 / 상단밴드 터치 유도
    add_walk(15, 0.0, 0.0015)     # 마무리 횡보
    return prices


def run_self_test() -> None:
    print("=" * 60)
    print("가상 차트 데이터로 롱/숏 1차 진입 조건 정성 테스트")
    print("=" * 60)

    notifier = TelegramNotifier(token="", chat_id="")  # 콘솔 로그로만 출력(텔레그램 미설정)
    broker = PaperBroker(starting_balance=PAPER_START_BALANCE)
    bot = HedgedMartingaleBot(broker, notifier, mode_label="PAPER-SELFTEST")

    prices = generate_synthetic_prices()
    sim_time = time.time()
    long_triggered = False
    short_triggered = False

    for i in range(BB_PERIOD, len(prices)):
        window = prices[: i + 1]
        price = window[-1]
        rsi_val = Indicators.rsi(window)

        was_long_in_pos = bot.long.in_position
        was_short_in_pos = bot.short.in_position

        bot.on_price(price, window, now=sim_time)

        if not was_long_in_pos and bot.long.in_position:
            long_triggered = True
            print(f"[bar {i:3d}] LONG 1차 진입  price={price:,.2f}  RSI={rsi_val:.1f}")
        if not was_short_in_pos and bot.short.in_position:
            short_triggered = True
            print(f"[bar {i:3d}] SHORT 1차 진입  price={price:,.2f}  RSI={rsi_val:.1f}")

        sim_time += 900  # 15분봉 1개 = 900초

    print("-" * 60)
    print(f"LONG  1차 진입 트리거: {'성공' if long_triggered else '실패'}")
    print(f"SHORT 1차 진입 트리거: {'성공' if short_triggered else '실패'}")
    print(f"최종 모의 잔고: {broker.get_balance():,.2f} USDT")
    print("=" * 60)


def run_telegram_test() -> None:
    """TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수로 실제 발송이 되는지 확인한다."""
    print("=" * 60)
    print("텔레그램 알림 발송 테스트")
    print("=" * 60)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
        print("예) export TELEGRAM_BOT_TOKEN=... / export TELEGRAM_CHAT_ID=...")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    text = "[hedged_martingale_bot] 텔레그램 알림 발송 테스트입니다. 이 메시지가 보이면 정상 연결된 것입니다."
    try:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        body = resp.json()
    except Exception as e:
        print(f"전송 요청 자체가 실패했습니다(네트워크/토큰 확인): {e}")
        return
    if resp.status_code == 200 and body.get("ok"):
        print("전송 성공! 텔레그램 앱에서 메시지를 확인하세요.")
    else:
        print(f"전송 실패 (HTTP {resp.status_code}): {body}")
        print("chat_id가 정확한지, 봇에게 먼저 메시지를 보냈는지 확인하세요.")
    print("=" * 60)


# ───────────── 실행 진입점 ─────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="비트코인 10배 레버리지 양방향 마틴게일 자동매매 봇")
    parser.add_argument("--selftest", action="store_true", help="가상 차트 데이터로 진입 조건 정성 테스트만 실행")
    parser.add_argument("--telegram-test", action="store_true", help="텔레그램 알림이 실제로 오는지 테스트 발송만 하고 종료")
    parser.add_argument("--live", action="store_true", help="실거래 모드로 실행 (기본값: 모의매매)")
    parser.add_argument("--poll-sec", type=int, default=POLL_SEC, help="가격 조회 주기(초)")
    args = parser.parse_args()

    if args.selftest:
        run_self_test()
        return

    if args.telegram_test:
        run_telegram_test()
        return

    notifier = TelegramNotifier()

    if args.live:
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        api_secret = os.environ.get("EXCHANGE_API_SECRET", "")
        passphrase = os.environ.get("EXCHANGE_PASSPHRASE", "")
        if not api_key or not api_secret:
            raise SystemExit("실거래 모드는 EXCHANGE_API_KEY / EXCHANGE_API_SECRET 환경변수가 필요합니다.")
        broker = LiveBroker(EXCHANGE_NAME, api_key, api_secret, passphrase)
        mode_label = "LIVE"
    else:
        broker = PaperBroker()
        mode_label = "PAPER"

    bot = HedgedMartingaleBot(broker, notifier, mode_label, state_path=STATE_PATH)
    market_data = PublicMarketData()
    logger.info("거래소: %s / 심볼: %s / 상태 저장 위치: %s", EXCHANGE_NAME, SYMBOL, STATE_PATH)
    bot.run_forever(market_data, poll_sec=args.poll_sec)


if __name__ == "__main__":
    main()
