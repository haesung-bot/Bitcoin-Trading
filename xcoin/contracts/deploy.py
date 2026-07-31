"""
deploy.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Xcoin(XCN) 토큰을 테스트넷/메인넷에 배포합니다.

  # 0단계. 진짜 배포 전에 가상 체인에서 똑같은 과정을 리허설 (가스 안 듦)
  python xcoin/contracts/deploy.py --dry-run

  # 1단계. 실제 테스트넷 배포
  python xcoin/contracts/deploy.py --network amoy

개인키는 명령줄 인자로 받지 않습니다. 셸 히스토리에 남기 때문입니다.
환경변수 XCOIN_DEPLOYER_KEY 에 넣거나, 물어볼 때 붙여넣으세요.

배포가 끝나면 deployments/<네트워크>.json 에 기록이 남고,
서버에 넣을 환경변수를 그대로 출력해 줍니다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import getpass
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACT_PATH = os.path.join(HERE, "XCoin.build.json")
DEPLOY_DIR = os.path.join(HERE, "deployments")

E18 = 10 ** 18

# 자주 쓰는 네트워크. 여기 없으면 --rpc / --chain-id 로 직접 지정하면 됩니다.
NETWORKS = {
    "amoy": {
        "name": "Polygon Amoy (테스트넷)",
        "chain_id": 80002,
        "rpc": "https://rpc-amoy.polygon.technology",
        "explorer": "https://amoy.polygonscan.com",
        "currency": "POL",
        "faucet": "https://faucet.polygon.technology",
        "testnet": True,
    },
    "polygon": {
        "name": "Polygon 메인넷",
        "chain_id": 137,
        "rpc": "https://polygon-rpc.com",
        "explorer": "https://polygonscan.com",
        "currency": "POL",
        "faucet": "",
        "testnet": False,
    },
    "bsc-testnet": {
        "name": "BNB Chain 테스트넷",
        "chain_id": 97,
        "rpc": "https://data-seed-prebsc-1-s1.binance.org:8545",
        "explorer": "https://testnet.bscscan.com",
        "currency": "tBNB",
        "faucet": "https://testnet.bnbchain.org/faucet-smart",
        "testnet": True,
    },
    "base-sepolia": {
        "name": "Base Sepolia (테스트넷)",
        "chain_id": 84532,
        "rpc": "https://sepolia.base.org",
        "explorer": "https://sepolia.basescan.org",
        "currency": "ETH",
        "faucet": "https://www.alchemy.com/faucets/base-sepolia",
        "testnet": True,
    },
}


def say(msg=""):
    print(msg, flush=True)


def die(msg):
    say(f"\n✖ {msg}\n")
    sys.exit(1)


def load_artifact() -> dict:
    if not os.path.exists(ARTIFACT_PATH):
        die(f"컴파일 결과가 없습니다: {ARTIFACT_PATH}\n"
            f"  contracts/test 에서 `npm install solc@0.8.20 && node compile.js` 를 실행하거나,\n"
            f"  저장소에 있는 XCoin.build.json 을 그대로 쓰세요.")
    with open(ARTIFACT_PATH) as f:
        return json.load(f)


def load_key(dry_run: bool) -> str:
    """개인키를 환경변수나 입력으로 받는다. 인자로는 받지 않는다."""
    key = os.environ.get("XCOIN_DEPLOYER_KEY", "").strip()
    if not key and not dry_run:
        say("배포에 쓸 지갑의 개인키를 입력하세요. (화면에 표시되지 않습니다)")
        say("  메타마스크 → 계정 세부정보 → 개인 키 표시")
        key = getpass.getpass("  개인키: ").strip()
    if not key:
        return ""
    if not key.startswith("0x"):
        key = "0x" + key
    if len(key) != 66:
        die("개인키 형식이 이상합니다. 0x 를 포함해 66자여야 합니다.")
    return key


def fmt(wei: int) -> str:
    return f"{wei / E18:,.6f}".rstrip("0").rstrip(".")


def main():
    ap = argparse.ArgumentParser(description="Xcoin 토큰 배포")
    ap.add_argument("--network", default="amoy", choices=list(NETWORKS),
                    help="배포할 네트워크 (기본: amoy 테스트넷)")
    ap.add_argument("--rpc", default="", help="RPC 주소를 직접 지정할 때")
    ap.add_argument("--chain-id", type=int, default=0, help="체인 ID를 직접 지정할 때")
    ap.add_argument("--initial-supply", type=int, default=10_000_000,
                    help="배포 즉시 발행할 XCN 개수 (기본 1000만)")
    ap.add_argument("--treasury", default="",
                    help="서버가 지급에 쓸 재무 지갑 주소. 주면 물량 일부를 옮기거나 minter로 지정한다.")
    ap.add_argument("--treasury-fund", type=int, default=0,
                    help="재무 지갑으로 옮길 XCN 개수 (transfer 방식일 때)")
    ap.add_argument("--treasury-minter", action="store_true",
                    help="재무 지갑을 minter로 지정한다 (mint 방식일 때)")
    ap.add_argument("--dry-run", action="store_true",
                    help="가상 체인에서 리허설만 한다. 가스가 들지 않는다.")
    ap.add_argument("--yes", action="store_true", help="확인 절차를 건너뛴다")
    args = ap.parse_args()

    try:
        from web3 import Web3
        from eth_account import Account
    except ImportError:
        die("web3 가 필요합니다.  pip install web3")

    from web3 import Web3
    from eth_account import Account

    art = load_artifact()
    net = dict(NETWORKS[args.network])
    if args.rpc:
        net["rpc"] = args.rpc
    if args.chain_id:
        net["chain_id"] = args.chain_id

    say("━" * 62)
    say("  Xcoin(XCN) 배포")
    say("━" * 62)
    say(f"  컨트랙트 : XCoin.sol")
    say(f"  컴파일러 : {art['compiler']}")
    say(f"  소스 해시 : {art['sourceSha256'][:32]}…")

    # ---------- 연결 ----------
    if args.dry_run:
        say(f"  네트워크 : 가상 체인 (리허설 — 실제 배포 아님)")
        try:
            from web3 import EthereumTesterProvider
        except ImportError:
            die('리허설에는 eth-tester 가 필요합니다.  pip install "web3[tester]"')
        w3 = Web3(EthereumTesterProvider())
        deployer = w3.eth.accounts[0]
        acct = None
        chain_id = w3.eth.chain_id
    else:
        say(f"  네트워크 : {net['name']}  (chain id {net['chain_id']})")
        say(f"  RPC      : {net['rpc']}")
        w3 = Web3(Web3.HTTPProvider(net["rpc"], request_kwargs={"timeout": 40}))
        if not w3.is_connected():
            die(f"RPC 에 연결할 수 없습니다: {net['rpc']}\n"
                f"  인터넷 연결을 확인하거나 --rpc 로 다른 주소를 지정하세요.")
        chain_id = w3.eth.chain_id
        if chain_id != net["chain_id"]:
            die(f"체인 ID가 다릅니다. 기대 {net['chain_id']}, 실제 {chain_id}\n"
                f"  RPC 주소가 맞는지 확인하세요.")
        key = load_key(False)
        acct = Account.from_key(key)
        deployer = acct.address

    say(f"  배포 지갑 : {deployer}")

    # ---------- 가스 잔고 ----------
    balance = w3.eth.get_balance(deployer)
    say(f"  가스 잔고 : {fmt(balance)} {net.get('currency', 'ETH')}")
    if not args.dry_run and balance == 0:
        msg = f"가스가 없습니다. 이 주소로 {net['currency']} 를 먼저 받으세요:\n  {deployer}"
        if net.get("faucet"):
            msg += f"\n\n  무료 수도꼭지: {net['faucet']}"
        die(msg)

    initial_wei = args.initial_supply * E18
    say()
    say(f"  초기 발행 : {args.initial_supply:,} XCN")
    if args.treasury:
        say(f"  재무 지갑 : {args.treasury}")
        if args.treasury_fund:
            say(f"    └ 이관   : {args.treasury_fund:,} XCN (transfer 방식)")
        if args.treasury_minter:
            say(f"    └ 권한   : minter 지정 (mint 방식)")
    say("━" * 62)

    if not args.dry_run and not net["testnet"] and not args.yes:
        say()
        say("  ⚠️  메인넷입니다. 실제 비용이 발생하고 되돌릴 수 없습니다.")
        if input("  계속하려면 '배포' 를 입력하세요: ").strip() != "배포":
            die("취소되었습니다.")

    # ---------- 전송 도우미 ----------
    def tx_params() -> dict:
        """
        수수료 항목을 build_transaction 에 넘길 dict 에 미리 넣어둔다.
        빌드한 뒤에 넣으면 web3 가 자동으로 채운 항목과 겹쳐서
        (gasPrice 와 maxFeePerGas 가 동시에 존재) 서명이 실패한다.
        """
        params = {
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer, "pending"),
            "chainId": chain_id,
        }
        _apply_fees(w3, params)
        return params

    def broadcast(fn, label):
        """트랜잭션을 보내고 영수증을 기다린다."""
        say(f"\n▸ {label}")
        if args.dry_run:
            tx_hash = fn.transact({"from": deployer})
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        else:
            tx = fn.build_transaction(tx_params())
            tx["gas"] = int(tx["gas"] * 1.25)   # 추정치에 여유를 둔다
            signed = acct.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            tx_hash = _hash_str(w3.eth.send_raw_transaction(raw))
            say(f"   보냄: {tx_hash}")
            say(f"   블록에 들어가길 기다리는 중…")
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if receipt["status"] != 1:
            die(f"{label} 실패. 익스플로러에서 확인하세요.")
        say(f"   완료 (가스 {receipt['gasUsed']:,})")
        return receipt

    # ---------- 배포 ----------
    Contract = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    say(f"\n▸ 컨트랙트 배포")
    if args.dry_run:
        tx_hash = Contract.constructor(initial_wei).transact({"from": deployer})
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    else:
        tx = Contract.constructor(initial_wei).build_transaction(tx_params())
        tx["gas"] = int(tx["gas"] * 1.25)
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = _hash_str(w3.eth.send_raw_transaction(raw))
        say(f"   보냄: {tx_hash}")
        say(f"   블록에 들어가길 기다리는 중… (보통 5~30초)")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

    if receipt["status"] != 1:
        die("배포 실패.")
    address = receipt["contractAddress"]
    say(f"   완료 (가스 {receipt['gasUsed']:,})")
    say(f"   주소: {address}")

    token = w3.eth.contract(address=address, abi=art["abi"])

    # ---------- 검증 ----------
    say(f"\n▸ 배포 결과 확인")
    checks = [
        ("이름", token.functions.name().call(), "Xcoin"),
        ("심볼", token.functions.symbol().call(), "XCN"),
        ("소수점", token.functions.decimals().call(), 18),
        ("총발행", token.functions.totalSupply().call(), initial_wei),
        ("발행상한", token.functions.MAX_SUPPLY().call(), 1_000_000_000 * E18),
        ("배포자 잔액", token.functions.balanceOf(deployer).call(), initial_wei),
        ("owner", token.functions.owner().call(), deployer),
    ]
    for label, actual, expected in checks:
        mark = "✓" if actual == expected else "✖"
        shown = f"{actual / E18:,.0f}" if isinstance(actual, int) and actual > 10 ** 12 else actual
        say(f"   {mark} {label}: {shown}")
        if actual != expected:
            die(f"{label} 이(가) 예상과 다릅니다. 배포가 잘못되었습니다.")

    # ---------- 재무 지갑 설정 ----------
    treasury = args.treasury
    if treasury:
        treasury = Web3.to_checksum_address(treasury)
        if args.treasury_minter:
            broadcast(token.functions.setMinter(treasury, True), "재무 지갑을 minter 로 지정")
            assert token.functions.isMinter(treasury).call()
            say("   ✓ minter 권한 확인")
        if args.treasury_fund:
            amount = args.treasury_fund * E18
            broadcast(token.functions.transfer(treasury, amount),
                      f"재무 지갑으로 {args.treasury_fund:,} XCN 이관")
            got = token.functions.balanceOf(treasury).call()
            say(f"   ✓ 재무 지갑 잔액: {got / E18:,.0f} XCN")

    # ---------- 기록 ----------
    record = {
        "network": args.network,
        "network_name": net["name"],
        "chain_id": chain_id,
        "address": address,
        "deployer": deployer,
        "treasury": treasury or None,
        "initial_supply_xcn": args.initial_supply,
        "payout_mode": "mint" if args.treasury_minter else "transfer",
        "compiler": art["compiler"],
        "source_sha256": art["sourceSha256"],
        "deployed_at": int(time.time()),
        "tx_hash": tx_hash if not args.dry_run else "(리허설)",
        "explorer": f"{net['explorer']}/address/{address}" if net.get("explorer") else "",
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        os.makedirs(DEPLOY_DIR, exist_ok=True)
        path = os.path.join(DEPLOY_DIR, f"{args.network}.json")
        with open(path, "w") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        say(f"\n▸ 배포 기록 저장: {path}")

    # ---------- 다음 단계 안내 ----------
    say()
    say("━" * 62)
    if args.dry_run:
        say("  리허설 완료. 전 과정이 문제없이 돌았습니다.")
        say("  이제 진짜로 올리려면:")
        say(f"    python xcoin/contracts/deploy.py --network amoy \\")
        say(f"      --treasury 0x재무지갑주소 --treasury-fund 1000000")
        say("━" * 62)
        return

    say("  배포 완료")
    say("━" * 62)
    if net.get("explorer"):
        say(f"\n  익스플로러에서 보기:")
        say(f"    {net['explorer']}/address/{address}")

    say(f"\n  서버에 넣을 환경변수 (그대로 복사하세요):")
    say(f"")
    say(f'    export XCOIN_RPC_URL="{net["rpc"]}"')
    say(f'    export XCOIN_CHAIN_ID={chain_id}')
    say(f'    export XCOIN_CONTRACT_ADDRESS="{address}"')
    say(f'    export XCOIN_PAYOUT_MODE={record["payout_mode"]}')
    say(f'    export XCOIN_TREASURY_PRIVATE_KEY="0x재무지갑_개인키"   # ← 직접 채우세요')
    say(f"")
    say(f"  그다음:")
    say(f"    python xcoin/contracts/check_live.py --network {args.network}")
    say(f"    python xcoin/run_server.py")
    say()


def _hash_str(value) -> str:
    """트랜잭션 해시를 항상 0x 접두사가 붙은 문자열로 맞춘다."""
    text = value.hex() if isinstance(value, (bytes, bytearray)) else str(value)
    return text if text.startswith("0x") else "0x" + text


def _apply_fees(w3, tx: dict) -> None:
    """EIP-1559 를 쓰는 체인이면 그쪽으로, 아니면 gasPrice 로."""
    try:
        base_fee = w3.eth.get_block("latest").get("baseFeePerGas")
    except Exception:
        base_fee = None
    if base_fee is not None:
        priority = w3.to_wei(30, "gwei")
        tx["maxPriorityFeePerGas"] = priority
        tx["maxFeePerGas"] = base_fee * 2 + priority
    else:
        tx["gasPrice"] = w3.eth.gas_price


if __name__ == "__main__":
    main()
