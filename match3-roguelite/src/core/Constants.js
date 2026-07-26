// =============================================================
//  Constants.js  -  전역 상수 / 설정 정의
//  (게임 밸런스 값은 전부 여기서 관리 -> 기획 변경 시 이 파일만 수정)
// =============================================================

/**
 * 보드 크기 (가로 x 세로 칸 수).
 * const 이 아니라 let 으로 두어, ES 모듈 라이브 바인딩을 통해
 * setBoardSize()로 런타임에 바꾸면 이 값을 참조하는 모든 코드가 즉시 따라간다.
 * (스테이지 진행에 따라 8x8 -> 10x10 확장에 사용)
 */
export let BOARD_COLS = 8;
export let BOARD_ROWS = 8;

/** 보드 확장 규칙: 이 스테이지를 넘어서면 큰 보드 */
export const BOARD_GROW_STAGE = 20;   // 20스테이지 이후
export const BIG_BOARD = 10;          // 10x10
export const SMALL_BOARD = 8;         // 기본 8x8

/** 런타임 보드 크기 변경 (라이브 바인딩 갱신) */
export function setBoardSize(rows, cols) {
  BOARD_ROWS = rows;
  BOARD_COLS = cols;
}
/** 스테이지에 맞는 보드 한 변 크기 */
export function boardSizeForStage(stage) {
  return stage > BOARD_GROW_STAGE ? BIG_BOARD : SMALL_BOARD;
}

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

/** 스테이지 기본 설정 (타임어택에 맞춰 튜닝) */
export const STAGE_DEFAULTS = Object.freeze({
  BASE_MOVES: 30,           // 기본 이동 횟수 (랭킹이 시간이라 여유 있게)
  PERK_EVERY: 3,            // 몇 스테이지마다 퍽 선택을 띄울지
  REWARD_AD_MOVES: 5,       // 광고 보상 이동 횟수
});

/**
 * 시간 보너스 (스피드런 요소): 특정 행동 시 스톱워치를 되감아
 * 기록 시간을 줄여준다. 값(초)만큼 stageTime에서 차감.
 */
export const TIME_BONUS = Object.freeze({
  CASCADE_STEP: 0.3,        // 연쇄 단계당 (2단부터)
  SPECIAL_CREATE: 0.5,      // 특수타일 생성
  COMBO_ROCKETBOMB: 2.0,    // 로켓+폭탄 콤보
  COMBO_LIGHTBALL: 3.0,     // 라이트볼+라이트볼 콤보
  COMBO_OTHER: 1.5,         // 그 외 특수 콤보
});

/** 점수 규칙 */
export const SCORE = Object.freeze({
  PER_TILE: 10,
  CASCADE_BONUS: 5, // 연쇄 단계마다 타일당 추가 점수
});

/**
 * 미션(스테이지 목표) 종류. 스펙 #2의 프로펠러 "목표 유도"가 이걸로 구체화된다.
 *  - SCORE   : 목표 점수 도달
 *  - COLLECT : 특정 색 타일 N개 수집
 *  - JELLY   : 젤리(장애물) N개 제거
 */
export const MissionType = Object.freeze({
  SCORE: 'SCORE',
  COLLECT: 'COLLECT',
  JELLY: 'JELLY',
});

/**
 * 젤리(장애물) 시각 스타일.
 * 타일 색이 잘 비치도록 중앙은 아주 옅게, 대신 뚜렷한 서리 테두리 + ❄ 아이콘으로
 * 젤리 여부를 명확히 구분한다.
 */
export const JELLY = Object.freeze({
  FILL: 'rgba(150, 230, 255, 0.14)',   // 아주 옅은 유리 코팅 (밑 색이 선명히 비침)
  GLOSS: 'rgba(255, 255, 255, 0.22)',  // 상단 광택
  EDGE: 'rgba(80, 205, 255, 0.98)',    // 뚜렷한 외곽선 (젤리 식별)
  EDGE_INNER: 'rgba(255, 255, 255, 0.85)', // 내부 하이라이트 선
  ICON: '❄',                            // 코너 서리 아이콘
});

/**
 * 연출 튜닝 — 세기/개수를 여기서 한 번에 조절.
 * 값을 키우면 화려하게, 줄이면 차분하게.
 */
export const FX = Object.freeze({
  GLOBAL_INTENSITY: 0.85,   // 전역 배율 (0~1.5 권장). 과하면 낮추기
  BURST_BASE: 7,            // 일반 타일 파괴 시 파편 수
  BURST_CASCADE: 10,        // 연쇄 중 파편 수
  SHAKE_BOMB: 8,
  SHAKE_ROCKET: 5,
  SHAKE_LIGHTBALL: 7,
  SHAKE_COMBO_ROCKETBOMB: 12,
  SHAKE_COMBO_LIGHTBALL: 14,
  SHAKE_CASCADE_STEP: 2.5,  // 연쇄 단계당 흔들림 증가
  COMBO_BANNER_SEC: 2.4,    // 콤보 메시지(배너) 표시 지속시간(초)
  COMBO_FLOATER_SEC: 1.6,   // 콤보 점수 텍스트 지속시간(초)
});
