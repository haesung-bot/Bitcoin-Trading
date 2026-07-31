"""
new_wallet.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
테스트넷용 지갑을 만들어 줍니다.

    python xcoin/contracts/new_wallet.py

★ 이 스크립트로 만든 지갑은 테스트넷 전용으로만 쓰세요 ★
개인키가 화면에 그대로 찍히므로, 화면 캡처·터미널 기록·클립보드에 남습니다.
실제 돈이 들어갈 메인넷 지갑은 반드시 메타마스크나 하드웨어 지갑에서 만드세요.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sys


def main():
    try:
        from eth_account import Account
    except ImportError:
        sys.exit("eth-account 가 필요합니다.  pip install eth-account")

    Account.enable_unaudited_hdwallet_features()

    print("━" * 62)
    print("  테스트넷 지갑 생성 (테스트 전용!)")
    print("━" * 62)

    for label, note in [
        ("오너 지갑", "컨트랙트를 배포하고 소유합니다. 배포할 때만 씁니다."),
        ("재무 지갑", "서버가 유저에게 코인을 보낼 때 씁니다. 서버 환경변수에 들어갑니다."),
    ]:
        acct, mnemonic = Account.create_with_mnemonic()
        print()
        print(f"  ▸ {label}")
        print(f"    {note}")
        print()
        print(f"    주소   : {acct.address}")
        print(f"    개인키 : {acct.key.hex()}")
        print(f"    복구구문: {mnemonic}")

    print()
    print("━" * 62)
    print("  다음 할 일")
    print("━" * 62)
    print("""
  1. 위 두 개를 안전한 곳(비밀번호 관리자 등)에 저장하세요.

  2. 메타마스크에 불러오기
     메타마스크 → 계정 → 계정 가져오기 → 개인 키 붙여넣기

  3. 메타마스크에 Polygon Amoy 테스트넷 추가
     설정 → 네트워크 → 네트워크 추가 → 직접 추가
       네트워크 이름 : Polygon Amoy
       RPC URL      : https://rpc-amoy.polygon.technology
       체인 ID      : 80002
       통화 기호     : POL
       익스플로러    : https://amoy.polygonscan.com

  4. 무료 테스트 POL 받기 (오너 지갑 주소로)
     https://faucet.polygon.technology
     → Amoy 선택 → 주소 붙여넣기 → 받기
     배포에 0.05 POL 정도면 충분합니다.

  5. 재무 지갑에도 조금 받아두기
     유저에게 코인 보낼 때 가스로 씁니다. 0.1 POL 정도면 수백 건 처리됩니다.
""")


if __name__ == "__main__":
    main()
