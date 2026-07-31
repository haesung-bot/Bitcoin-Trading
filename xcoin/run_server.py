"""
xcoin 서버 실행 진입점.

    cd Bitcoin-Trading
    pip install -r xcoin/requirements.txt
    python -m xcoin.run_server

또는 이 파일을 직접 실행해도 됩니다:

    python xcoin/run_server.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xcoin.server.app import main  # noqa: E402

if __name__ == "__main__":
    main()
