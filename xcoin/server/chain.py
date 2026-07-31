"""
chain.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실제 블록체인에 Xcoin 을 보내는 부분.

두 가지 모드로 동작합니다.

  시뮬레이션 모드 (기본)
    XCOIN_RPC_URL / XCOIN_CONTRACT_ADDRESS / XCOIN_TREASURY_PRIVATE_KEY 중
    하나라도 비어 있으면 이 모드입니다. 실제 전송 없이 가짜 tx 해시를 만들어
    기록만 합니다. 게임 붙이고 포인트 흐름을 테스트할 때 씁니다.

  실제 모드
    web3.py 로 ERC-20 컨트랙트를 호출합니다.
      PAYOUT_MODE=transfer → 재무 지갑이 들고 있는 물량에서 보냄
      PAYOUT_MODE=mint     → 그 자리에서 새로 발행해서 보냄 (재무 지갑에 MINTER 권한 필요)

주의: 실제 모드에서는 가스비(POL/BNB 등)가 재무 지갑에서 나갑니다.
잔고가 떨어지면 전송이 실패하므로 /admin/chain/status 로 주기적으로 확인하세요.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import hashlib
import threading
import time

from . import config

# ERC-20 중 우리가 쓰는 함수만 추린 최소 ABI
ERC20_ABI = [
    {"constant": False,
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"constant": False,
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "mint", "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
    {"constant": True,
     "inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}],
     "stateMutability": "view", "type": "function"},
]


class ChainError(Exception):
    pass


def normalize_tx_hash(value) -> str:
    """
    트랜잭션 해시를 항상 '0x...' 형태로 맞춘다.

    web3 버전에 따라 HexBytes.hex() 가 0x 를 붙이기도 하고 안 붙이기도 한다.
    0x 가 빠지면 익스플로러 링크가 깨지고 나중에 영수증을 다시 조회할 때도 막힌다.
    """
    if isinstance(value, (bytes, bytearray)):
        text = value.hex()
    else:
        text = str(value)
    if not text.startswith("0x"):
        text = "0x" + text
    return text


# nonce 충돌을 막기 위해 전송은 한 번에 하나씩만 한다.
_send_lock = threading.Lock()
_w3 = None
_account = None
_contract = None


def _init_web3():
    """web3 객체를 한 번만 만들어 재사용한다."""
    global _w3, _account, _contract
    if _w3 is not None:
        return

    try:
        from web3 import Web3
        from eth_account import Account
    except ImportError as exc:
        raise ChainError(
            "web3 가 설치되어 있지 않습니다. pip install web3 (또는 시뮬레이션 모드로 두세요)"
        ) from exc

    w3 = Web3(Web3.HTTPProvider(config.CHAIN_RPC_URL, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise ChainError(f"RPC 에 연결할 수 없습니다: {config.CHAIN_RPC_URL}")

    account = Account.from_key(config.TREASURY_PRIVATE_KEY)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(config.TOKEN_CONTRACT_ADDRESS),
        abi=ERC20_ABI,
    )
    _w3, _account, _contract = w3, account, contract


def treasury_address() -> str:
    if config.is_simulation():
        return "(시뮬레이션)"
    _init_web3()
    return _account.address


def status() -> dict:
    """관리자 화면에서 보여줄 체인 상태."""
    if config.is_simulation():
        return {
            "mode": "simulation",
            "message": "온체인 설정이 없어 실제 전송은 일어나지 않습니다.",
            "chain_id": config.CHAIN_ID,
        }
    try:
        _init_web3()
        native = _w3.eth.get_balance(_account.address)
        token = _contract.functions.balanceOf(_account.address).call()
        return {
            "mode": "live",
            "chain_id": config.CHAIN_ID,
            "payout_mode": config.PAYOUT_MODE,
            "treasury": _account.address,
            "contract": config.TOKEN_CONTRACT_ADDRESS,
            "gas_balance_wei": str(native),
            "gas_balance": str(native / 10 ** 18),
            "token_balance_wei": str(token),
            "token_balance": str(token / 10 ** config.COIN_DECIMALS),
        }
    except Exception as exc:
        return {"mode": "live", "error": str(exc)}


def send_xcoin(to_address: str, amount_wei: int, ref: str) -> str:
    """
    실제 전송. 성공하면 tx 해시 문자열을 돌려준다.
    ref 는 시뮬레이션 모드에서 결정적인 가짜 해시를 만드는 데 쓰인다.
    """
    if amount_wei <= 0:
        raise ChainError("전송 수량이 0 이하입니다.")

    if config.is_simulation():
        fake = hashlib.sha256(f"{ref}|{to_address}|{amount_wei}".encode()).hexdigest()
        return "0xSIM" + fake[:61]

    with _send_lock:
        _init_web3()
        from web3 import Web3

        to = Web3.to_checksum_address(to_address)
        if config.PAYOUT_MODE == "mint":
            fn = _contract.functions.mint(to, int(amount_wei))
        else:
            fn = _contract.functions.transfer(to, int(amount_wei))

        nonce = _w3.eth.get_transaction_count(_account.address, "pending")
        tx = {
            "from": _account.address,
            "nonce": nonce,
            "chainId": config.CHAIN_ID,
        }
        # 가스 추정 실패는 대부분 잔고 부족/권한 부족이다. 원인을 그대로 올려준다.
        try:
            tx["gas"] = int(fn.estimate_gas({"from": _account.address}) * 1.2)
        except Exception as exc:
            raise ChainError(f"가스 추정 실패(잔고 또는 권한 확인 필요): {exc}") from exc

        try:
            latest = _w3.eth.get_block("latest")
            base_fee = latest.get("baseFeePerGas")
        except Exception:
            base_fee = None

        if base_fee is not None:
            priority = _w3.to_wei(30, "gwei")
            tx["maxPriorityFeePerGas"] = priority
            tx["maxFeePerGas"] = base_fee * 2 + priority
        else:
            tx["gasPrice"] = _w3.eth.gas_price

        built = fn.build_transaction(tx)
        signed = _account.sign_transaction(built)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = _w3.eth.send_raw_transaction(raw)
        return normalize_tx_hash(tx_hash)


def wait_for_receipt(tx_hash: str, timeout: int = 180) -> dict:
    """트랜잭션이 블록에 들어갈 때까지 기다린다. 시뮬레이션 모드면 바로 성공."""
    if config.is_simulation():
        return {"status": 1, "simulated": True}
    _init_web3()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            receipt = _w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                return {"status": int(receipt["status"]),
                        "block_number": int(receipt["blockNumber"])}
        except Exception:
            pass
        time.sleep(3)
    raise ChainError(f"영수증 대기 시간 초과: {tx_hash}")
