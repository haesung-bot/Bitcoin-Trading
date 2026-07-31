"""
check_live.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
배포한 토큰과 서버 설정이 실제로 잘 물려 있는지 점검합니다.
서버를 켜기 전에, 그리고 문제가 생겼을 때 제일 먼저 돌려보세요.

    python xcoin/contracts/check_live.py --network amoy

점검 항목
  1. RPC 연결과 체인 ID
  2. 그 주소에 컨트랙트가 실제로 존재하는지
  3. 토큰 정보 (이름/심볼/소수점/총발행)
  4. 재무 지갑의 가스 잔고 — 부족하면 지급이 전부 실패합니다
  5. 재무 지갑의 토큰 잔고 (transfer 방식) 또는 minter 권한 (mint 방식)
  6. 남은 잔고로 몇 건이나 더 지급할 수 있는지
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

E18 = 10 ** 18
PASS, FAIL, WARN = "✓", "✖", "!"

problems = []
warnings = []


def line(mark, label, value=""):
    print(f"  {mark} {label}{('  ' + str(value)) if value != '' else ''}")


def main():
    from xcoin.contracts.deploy import NETWORKS  # 네트워크 표를 공유한다

    ap = argparse.ArgumentParser(description="배포 상태 점검")
    ap.add_argument("--network", default="amoy", choices=list(NETWORKS))
    ap.add_argument("--rpc", default="")
    ap.add_argument("--address", default="", help="컨트랙트 주소 (없으면 배포 기록/환경변수에서 찾음)")
    ap.add_argument("--treasury", default="", help="재무 지갑 주소 (없으면 환경변수 개인키에서 유도)")
    args = ap.parse_args()

    try:
        from web3 import Web3
    except ImportError:
        sys.exit("web3 가 필요합니다.  pip install web3")

    with open(os.path.join(HERE, "XCoin.build.json")) as f:
        art = json.load(f)

    net = dict(NETWORKS[args.network])
    if args.rpc:
        net["rpc"] = args.rpc

    # 컨트랙트 주소 찾기: 인자 → 환경변수 → 배포 기록
    address = args.address or os.environ.get("XCOIN_CONTRACT_ADDRESS", "").strip()
    record = {}
    record_path = os.path.join(HERE, "deployments", f"{args.network}.json")
    if os.path.exists(record_path):
        with open(record_path) as f:
            record = json.load(f)
        address = address or record.get("address", "")
    if not address:
        sys.exit(f"컨트랙트 주소를 찾을 수 없습니다.\n"
                 f"  --address 로 직접 주거나, XCOIN_CONTRACT_ADDRESS 를 설정하거나,\n"
                 f"  먼저 deploy.py 로 배포하세요.")

    print("━" * 62)
    print(f"  Xcoin 배포 상태 점검 — {net['name']}")
    print("━" * 62)

    # ---------- 1. RPC ----------
    print("\n[1] 체인 연결")
    w3 = Web3(Web3.HTTPProvider(net["rpc"], request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        line(FAIL, "RPC 연결 실패", net["rpc"])
        problems.append(f"RPC({net['rpc']})에 연결되지 않습니다.")
        summary()
        return
    line(PASS, "RPC 연결", net["rpc"])

    chain_id = w3.eth.chain_id
    if chain_id == net["chain_id"]:
        line(PASS, "체인 ID", chain_id)
    else:
        line(FAIL, "체인 ID 불일치", f"기대 {net['chain_id']}, 실제 {chain_id}")
        problems.append("RPC 주소가 다른 체인을 가리키고 있습니다.")

    line(PASS, "최신 블록", f"{w3.eth.block_number:,}")

    # ---------- 2. 컨트랙트 존재 ----------
    print("\n[2] 컨트랙트")
    address = Web3.to_checksum_address(address)
    code = w3.eth.get_code(address)
    if len(code) <= 2:
        line(FAIL, "그 주소에 컨트랙트가 없습니다", address)
        problems.append(f"{address} 에는 배포된 코드가 없습니다. 주소나 네트워크를 확인하세요.")
        summary()
        return
    line(PASS, "컨트랙트 존재", f"{address} ({len(code)} bytes)")

    token = w3.eth.contract(address=address, abi=art["abi"])

    # ---------- 3. 토큰 정보 ----------
    print("\n[3] 토큰 정보")
    try:
        name = token.functions.name().call()
        symbol = token.functions.symbol().call()
        decimals = token.functions.decimals().call()
        supply = token.functions.totalSupply().call()
        cap = token.functions.MAX_SUPPLY().call()
        owner = token.functions.owner().call()
        paused = token.functions.paused().call()
    except Exception as exc:
        line(FAIL, "토큰 정보를 읽을 수 없습니다", str(exc)[:60])
        problems.append("이 주소는 XCoin 컨트랙트가 아닌 것 같습니다.")
        summary()
        return

    line(PASS if name == "Xcoin" else WARN, "이름", name)
    line(PASS if symbol == "XCN" else WARN, "심볼", symbol)
    line(PASS if decimals == 18 else FAIL, "소수점", decimals)
    line(PASS, "총 발행량", f"{supply / E18:,.0f} XCN")
    line(PASS, "발행 상한", f"{cap / E18:,.0f} XCN")
    line(PASS, "owner", owner)
    if paused:
        line(FAIL, "일시정지 상태", "전송이 전부 막혀 있습니다")
        problems.append("컨트랙트가 pause 상태입니다. owner 로 setPaused(false) 하세요.")
    else:
        line(PASS, "일시정지", "아님")

    # ---------- 4. 재무 지갑 ----------
    print("\n[4] 재무 지갑 (서버가 지급에 쓰는 지갑)")
    treasury = args.treasury or record.get("treasury") or ""
    key = os.environ.get("XCOIN_TREASURY_PRIVATE_KEY", "").strip()
    if key:
        from eth_account import Account
        treasury = Account.from_key(key).address
        line(PASS, "환경변수 개인키에서 확인", treasury)
    elif treasury:
        line(WARN, "주소만 확인 (개인키 미설정)", treasury)
        warnings.append("XCOIN_TREASURY_PRIVATE_KEY 가 없으면 서버가 지급할 수 없습니다.")
    else:
        line(WARN, "재무 지갑을 알 수 없습니다", "")
        warnings.append("--treasury 로 주소를 주거나 XCOIN_TREASURY_PRIVATE_KEY 를 설정하세요.")
        summary()
        return

    treasury = Web3.to_checksum_address(treasury)
    gas_balance = w3.eth.get_balance(treasury)
    currency = net.get("currency", "ETH")
    line(PASS if gas_balance > 0 else FAIL, "가스 잔고",
         f"{gas_balance / E18:,.6f} {currency}".replace(".000000", ""))
    if gas_balance == 0:
        problems.append(f"재무 지갑에 가스가 없습니다. 지급이 전부 실패합니다.\n"
                        f"    이 주소로 {currency} 를 보내세요: {treasury}"
                        + (f"\n    수도꼭지: {net['faucet']}" if net.get("faucet") else ""))

    token_balance = token.functions.balanceOf(treasury).call()
    is_minter = token.functions.isMinter(treasury).call()
    payout_mode = os.environ.get("XCOIN_PAYOUT_MODE", record.get("payout_mode", "transfer"))
    line(PASS, "지급 방식", payout_mode)

    if payout_mode == "mint":
        line(PASS if is_minter else FAIL, "minter 권한", "있음" if is_minter else "없음")
        if not is_minter:
            problems.append("mint 방식인데 재무 지갑에 minter 권한이 없습니다.\n"
                            f"    owner 지갑으로 setMinter({treasury}, true) 를 실행하세요.")
        remaining = cap - supply
        line(PASS, "발행 가능 잔여", f"{remaining / E18:,.0f} XCN")
    else:
        line(PASS if token_balance > 0 else FAIL, "토큰 잔고",
             f"{token_balance / E18:,.0f} XCN")
        if token_balance == 0:
            problems.append("transfer 방식인데 재무 지갑에 토큰이 없습니다.\n"
                            f"    owner 지갑에서 {treasury} 로 XCN 을 보내세요.")

    # ---------- 5. 여력 계산 ----------
    print("\n[5] 지급 여력")
    gas_per_tx = 65000 if payout_mode == "transfer" else 75000
    try:
        gas_price = w3.eth.gas_price
    except Exception:
        gas_price = w3.to_wei(30, "gwei")
    cost_per_tx = gas_per_tx * gas_price
    if cost_per_tx > 0:
        possible = gas_balance // cost_per_tx
        line(PASS if possible > 10 else WARN, "가스로 처리 가능한 건수", f"약 {possible:,} 건")
        line(PASS, "1건당 예상 가스비",
             f"{cost_per_tx / E18:.8f} {currency}".rstrip("0"))
        if possible < 50:
            warnings.append(f"가스가 {possible}건 분량밖에 없습니다. 미리 채워두세요.")

    if payout_mode == "transfer" and token_balance > 0:
        # 기본 정책 1000P = 1XCN, 최소 전환 1000P → 1건당 최소 1 XCN
        line(PASS, "토큰으로 처리 가능한 건수",
             f"약 {int(token_balance / E18):,} 건 (건당 1 XCN 기준)")

    summary()


def summary():
    print("\n" + "━" * 62)
    if problems:
        print(f"  문제 {len(problems)}건 — 고치기 전에는 지급이 실패합니다")
        print("━" * 62)
        for p in problems:
            print(f"\n  ✖ {p}")
    if warnings:
        if not problems:
            print(f"  경고 {len(warnings)}건")
            print("━" * 62)
        for wmsg in warnings:
            print(f"\n  ! {wmsg}")
    if not problems and not warnings:
        print("  전부 정상입니다. 서버를 켜도 됩니다.")
        print("━" * 62)
        print("\n    python xcoin/run_server.py\n")
    else:
        print()
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
