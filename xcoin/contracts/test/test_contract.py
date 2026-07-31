"""
test_contract.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
XCoin.sol 을 로컬 가상 EVM 에 실제로 배포해서 동작을 검증한다.
메인넷에 올리기 전에 반드시 한 번 돌려보세요.

    npm install solc@0.8.20
    pip install "web3[tester]"
    node compile.js
    python test_contract.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import os
import sys

from web3 import EthereumTesterProvider, Web3

ARTIFACT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "XCoin.json")
if not os.path.exists(ARTIFACT):
    sys.exit("XCoin.json 이 없습니다. 먼저 `npm install solc@0.8.20 && node compile.js` 를 실행하세요.")

art = json.load(open(ARTIFACT))
w3 = Web3(EthereumTesterProvider())
owner, treasury, alice, bob = w3.eth.accounts[:4]
E = 10 ** 18

def deploy(initial):
    C = w3.eth.contract(abi=art['abi'], bytecode=art['bin'])
    tx = C.constructor(initial).transact({'from': owner})
    return w3.eth.contract(address=w3.eth.get_transaction_receipt(tx)['contractAddress'], abi=art['abi'])

def expect_revert(label, fn):
    try:
        fn()
    except Exception as e:
        print(f"  PASS  {label} (revert: {str(e)[:60]})")
        return
    raise AssertionError(f"FAIL  {label} — 되돌려져야 하는데 성공함")

def ok(label, cond, extra=""):
    assert cond, f"FAIL  {label} {extra}"
    print(f"  PASS  {label} {extra}")

print("\n[1] 배포와 초기 발행")
c = deploy(1_000_000 * E)
ok("name/symbol/decimals", c.functions.name().call() == "Xcoin"
   and c.functions.symbol().call() == "XCN" and c.functions.decimals().call() == 18)
ok("초기 발행량", c.functions.balanceOf(owner).call() == 1_000_000 * E)
ok("totalSupply", c.functions.totalSupply().call() == 1_000_000 * E)
ok("상한 10억", c.functions.MAX_SUPPLY().call() == 1_000_000_000 * E)
ok("배포자가 minter", c.functions.isMinter(owner).call())

print("\n[2] 전송")
c.functions.transfer(alice, 100 * E).transact({'from': owner})
ok("transfer 반영", c.functions.balanceOf(alice).call() == 100 * E)
expect_revert("잔액 초과 전송 차단", lambda: c.functions.transfer(bob, 999999 * E).transact({'from': alice}))
expect_revert("0 주소 전송 차단",
              lambda: c.functions.transfer('0x' + '00'*20, 1 * E).transact({'from': owner}))

print("\n[3] approve / transferFrom")
c.functions.approve(bob, 50 * E).transact({'from': alice})
ok("allowance", c.functions.allowance(alice, bob).call() == 50 * E)
c.functions.transferFrom(alice, bob, 30 * E).transact({'from': bob})
ok("transferFrom 반영", c.functions.balanceOf(bob).call() == 30 * E)
ok("allowance 차감", c.functions.allowance(alice, bob).call() == 20 * E)
expect_revert("allowance 초과 차단",
              lambda: c.functions.transferFrom(alice, bob, 100 * E).transact({'from': bob}))

print("\n[4] 발행 권한 (게임 서버 재무 지갑 시나리오)")
expect_revert("minter 아닌 계정 발행 차단",
              lambda: c.functions.mint(alice, 1 * E).transact({'from': treasury}))
c.functions.setMinter(treasury, True).transact({'from': owner})
c.functions.mint(alice, 500 * E).transact({'from': treasury})
ok("minter 지정 후 발행", c.functions.balanceOf(alice).call() == 570 * E)
expect_revert("minter 지정은 owner만",
              lambda: c.functions.setMinter(bob, True).transact({'from': alice}))

print("\n[5] 총발행 상한")
c2 = deploy(0)
c2.functions.mint(owner, 1_000_000_000 * E).transact({'from': owner})
expect_revert("상한 초과 발행 차단",
              lambda: c2.functions.mint(owner, 1).transact({'from': owner}))

print("\n[6] 소각")
before = c.functions.totalSupply().call()
c.functions.burn(70 * E).transact({'from': alice})
ok("소각 후 잔액", c.functions.balanceOf(alice).call() == 500 * E)
ok("totalSupply 감소", c.functions.totalSupply().call() == before - 70 * E)

print("\n[7] 일시정지")
c.functions.setPaused(True).transact({'from': owner})
expect_revert("정지 중 전송 차단", lambda: c.functions.transfer(bob, 1 * E).transact({'from': alice}))
expect_revert("정지 중 발행 차단", lambda: c.functions.mint(bob, 1 * E).transact({'from': treasury}))
c.functions.setPaused(False).transact({'from': owner})
c.functions.transfer(bob, 1 * E).transact({'from': alice})
ok("해제 후 전송 정상", c.functions.balanceOf(bob).call() == 31 * E)

print("\n[8] 소유권 2단계 이전")
c.functions.transferOwnership(alice).transact({'from': owner})
ok("아직 owner 그대로", c.functions.owner().call() == owner)
expect_revert("엉뚱한 사람은 수락 불가", lambda: c.functions.acceptOwnership().transact({'from': bob}))
c.functions.acceptOwnership().transact({'from': alice})
ok("수락 후 owner 변경", c.functions.owner().call() == alice)
expect_revert("이전 owner 권한 상실",
              lambda: c.functions.setPaused(True).transact({'from': owner}))

print("\n[9] 배치 전송 (가스 절약용)")
c.functions.batchTransfer([bob, treasury], [5 * E, 5 * E]).transact({'from': alice})
ok("두 명 동시 전송", c.functions.balanceOf(bob).call() == 36 * E
   and c.functions.balanceOf(treasury).call() == 5 * E)
expect_revert("길이 불일치 차단",
              lambda: c.functions.batchTransfer([bob], [1 * E, 2 * E]).transact({'from': alice}))

print("\n[10] 관리 권한 영구 포기")
c.functions.renounceMinterAndPause().transact({'from': alice})
ok("adminRenounced", c.functions.adminRenounced().call())
expect_revert("포기 후 minter 추가 불가",
              lambda: c.functions.setMinter(bob, True).transact({'from': alice}))
expect_revert("포기 후 정지 불가",
              lambda: c.functions.setPaused(True).transact({'from': alice}))
c.functions.transfer(bob, 1 * E).transact({'from': alice})
ok("포기 후에도 전송은 정상", c.functions.balanceOf(bob).call() == 37 * E)

print("\n컨트랙트 전체 통과\n")
