"""테스트 공통 설정 — 로그 소음을 줄인다."""

import logging

logging.getLogger("autocommerce").setLevel(logging.CRITICAL)
