"""헤르메스가 제어하는 '매매 백엔드'의 인터페이스.

에이전트는 이 인터페이스만 알고 있으면 되므로, 실제 구현이
tkinter GUI 봇이든 헤드리스 봇이든 테스트용 가짜 객체든 상관없다.
덕분에 제어 로직은 거래소나 GUI 없이도 전부 테스트할 수 있다.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


class BackendError(RuntimeError):
    """백엔드 기동/정지/주문이 실패했을 때 발생."""


@runtime_checkable
class TradingBackend(Protocol):
    """매매 엔진이 제공해야 하는 최소 기능."""

    def start(self, config: Mapping[str, Any]) -> None:
        """주어진 설정으로 매매 루프를 기동한다. 실패하면 BackendError를 던진다."""

    def stop(self) -> None:
        """매매 루프에 정지를 요청한다. 이미 정지 상태여도 예외를 던지지 않는다."""

    def is_alive(self) -> bool:
        """매매 루프 스레드가 아직 살아있는지."""

    def get_position(self) -> dict[str, Any]:
        """현재 포지션. 없으면 side='none', size=0.0인 딕셔너리를 돌려준다."""

    def get_balance(self) -> float | None:
        """선물 지갑 USDT 잔고. 조회 실패 시 None."""

    def flatten(self) -> dict[str, Any]:
        """보유 포지션을 전량 시장가 청산한다 (킬 스위치용).

        {'closed': bool, 'side': str, 'size': float, 'detail': str} 형태를 돌려준다.
        """
