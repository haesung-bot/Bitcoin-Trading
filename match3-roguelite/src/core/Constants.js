// =============================================================
//  Constants.js  -  전역 상수 / 설정 정의
//  (게임 밸런스 값은 전부 여기서 관리 -> 기획 변경 시 이 파일만 수정)
// =============================================================

/** 보드 크기 (가로 x 세로 칸 수) */
export const BOARD_COLS = 8;
export const BOARD_ROWS = 8;

/** 타일 한 칸의 픽셀 크기 & 여백 */
export const TILE_SIZE = 72;
export const BOARD_PADDING = 24;

/**
 * 보드 상태머신(State Machine)의 상태 정의.
 * 매 프레임 현재 상태에 따라 다른 로직이 실행된다.
 */
export const GameState = Object.freeze({
  IDLE: 'IDLE',                 // 입력 대기 (플레이어가 타일을 고르는 중)
  SWAP: 'SWAP',                 // 두 타일 교환 애니메이션 재생 중
  MATCH_CHECK: 'MATCH_CHECK',   // 매치 성립 여부 판정
  DESTROY: 'DESTROY',           // 매치된 타일 파괴 애니메이션
  GRAVITY_FALL: 'GRAVITY_FALL', // 빈 칸으로 위 타일이 낙하
  REFILL: 'REFILL',             // 상단에서 새 타일 생성/투입
  CHECK_GAME_OVER: 'CHECK_GAME_OVER', // 스테이지 클리어 / 이동 소진 판정
});

/**
 * 일반 타일 색상 종류. (숫자 인덱스로 관리 -> 스프라이트 매핑에 유리)
 * 값은 렌더러에서 색으로 그린다.
 */
export const TileColor = Object.freeze({
  RED: 0,
  BLUE: 1,
  GREEN: 2,
  YELLOW: 3,
  PURPLE: 4,
  ORANGE: 5,
});

/** 실제로 스폰에 쓸 색 목록 (개수 조절로 난이도 튜닝) */
export const ACTIVE_COLORS = [
  TileColor.RED,
  TileColor.BLUE,
  TileColor.GREEN,
  TileColor.YELLOW,
  TileColor.PURPLE,
  TileColor.ORANGE,
];

/** 렌더링용 색상 팔레트 (TileColor 인덱스 -> hex) */
export const COLOR_HEX = {
  [TileColor.RED]: '#ff5a5f',
  [TileColor.BLUE]: '#3d9bff',
  [TileColor.GREEN]: '#42d97a',
  [TileColor.YELLOW]: '#ffd23f',
  [TileColor.PURPLE]: '#b06bff',
  [TileColor.ORANGE]: '#ff9636',
};

/**
 * 특수타일(부스터) 종류.
 *  - NONE      : 일반 타일
 *  - ROCKET    : 4개 일자매치 -> 가로 또는 세로 한 줄 전체 제거
 *  - PROPELLER : 4개 2x2 매치 -> 주변 제거 후 목표물로 유도
 *  - BOMB      : 5개 L/T 매치 -> 3x3 폭발
 *  - LIGHT_BALL: 5개 일자매치 -> 같은 색 전부 제거
 */
export const SpecialType = Object.freeze({
  NONE: 'NONE',
  ROCKET: 'ROCKET',
  PROPELLER: 'PROPELLER',
  BOMB: 'BOMB',
  LIGHT_BALL: 'LIGHT_BALL',
});

/** 로켓 방향 (일자매치가 가로였는지 세로였는지에 따라 결정) */
export const RocketDir = Object.freeze({
  HORIZONTAL: 'H', // 가로 한 줄 제거
  VERTICAL: 'V',   // 세로 한 줄 제거
});

/** 애니메이션 지속 시간 (초) */
export const TIMING = Object.freeze({
  SWAP: 0.18,
  SWAP_BACK: 0.18,   // 매치 실패 시 되돌리기
  DESTROY: 0.22,
  FALL_PER_TILE: 0.06, // 한 칸 낙하당 시간
  REFILL: 0.20,
});

/** 스테이지 기본 설정 */
export const STAGE_DEFAULTS = Object.freeze({
  BASE_MOVES: 25,           // 기본 이동 횟수
  PERK_EVERY: 3,            // 몇 스테이지마다 퍽 선택을 띄울지
  REWARD_AD_MOVES: 5,       // 광고 보상 이동 횟수
  MAX_HEARTS: 5,            // 최대 하트(라이프)
});

/** 점수 규칙 */
export const SCORE = Object.freeze({
  PER_TILE: 10,
  CASCADE_BONUS: 5, // 연쇄 단계마다 타일당 추가 점수
});
