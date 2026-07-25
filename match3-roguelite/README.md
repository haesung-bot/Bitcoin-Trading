# Match-3 Rogue-Lite — 코어 게임 엔진

HTML5 Canvas + 바닐라 ES6 모듈로 만든 매치3 로그라이트 퍼즐 게임의 **코어 엔진**.
빌드 과정 없이 브라우저에서 바로 실행됩니다.

## 실행

정적 서버가 필요합니다 (ES 모듈은 `file://`에서 CORS로 막힘).

```bash
cd match3-roguelite
python3 -m http.server 8080
# 브라우저에서 http://localhost:8080 접속
```

타일을 드래그해서 인접 타일과 교환하세요.

## 테스트 (브라우저 불필요)

```bash
node test/core.test.mjs
```

## 아키텍처

```
src/
├── core/                    # 게임 코어 (렌더링/DOM 비의존)
│   ├── Constants.js         # 모든 밸런스 값·상태·색·타이밍 정의
│   ├── Tile.js              # 타일 데이터 + 이동/소멸 애니메이션 보간
│   ├── Board.js             # 8x8 매트릭스, 스왑·중력·리필·특수타일 발동
│   ├── MatchDetector.js     # 매치 판정 + 특수타일 분류 (스펙 #2)
│   ├── StateMachine.js      # 범용 FSM
│   └── GameEngine.js        # 상태머신 오케스트레이션 (스펙 #1,3,4,5)
├── systems/
│   ├── PerkSystem.js        # 로그라이트 퍽 풀 + 선택 (스펙 #4)
│   └── AdInterface.js       # 광고 훅 인터페이스 (스펙 #5, IAP 없음)
└── render/
    ├── Renderer.js          # Canvas 렌더링 (교체 가능)
    └── InputController.js   # 마우스/터치 드래그 스왑
```

### 보드 상태머신 흐름 (스펙 #1)

```
IDLE ──플레이어 스왑──▶ SWAP ──▶ MATCH_CHECK
                                    │
              ┌── 매치 있음 ────────┤
              ▼                     └── 매치 없음(플레이어) ─▶ 되돌리기 ─▶ IDLE
           DESTROY                  └── 매치 없음(연쇄종료) ─▶ CHECK_GAME_OVER
              │
              ▼
        GRAVITY_FALL ──▶ REFILL ──▶ MATCH_CHECK (연쇄 반복)

CHECK_GAME_OVER: 목표점수 달성→스테이지클리어 / 이동소진→광고훅 / 데드락→셔플
```

### 매치 → 특수타일 규칙 (스펙 #2)

| 매치 형태            | 생성 부스터   | 효과                        |
|---------------------|--------------|-----------------------------|
| 3개                 | (없음)       | 기본 파괴                   |
| 4개 일자            | ROCKET       | 가로/세로 한 줄 전체 제거   |
| 4개 2x2 박스        | PROPELLER    | 주변 제거 + 목표물 유도     |
| 5개 L/T             | BOMB         | 3x3 폭발                    |
| 5개 일자            | LIGHT_BALL   | 같은 색 전부 제거           |

### 콤보 (스펙 #3)

- **ROCKET + BOMB** → 가로 3줄 + 세로 3줄 제거
- **LIGHT_BALL + LIGHT_BALL** → 보드 전체 제거
- 그 외 특수타일이 서로/연쇄로 닿으면 효과가 자동 확장 (`_expandSpecials`)

### 광고 훅 (스펙 #5)

`AdInterface`가 게임 코어와 광고 SDK를 분리. 실제 배포 시 `MockAdProvider`를
AdMob/Unity Ads 어댑터로 교체하면 됩니다.

- `onOutOfMoves()` → 보상형 광고 → +5 이동
- `onStageClear(n)` → `n % 3 === 0`일 때 전면광고

## 타임어택 & 랭킹 (하트 대체)

라이프(하트) 시스템을 제거하고 **시간 기반 랭킹**으로 전환했다.

- 스테이지마다 스톱워치가 0부터 카운트업 (`engine.stageTime`), 미션을
  **빨리 깰수록 상위**.
- 퍽 선택 중·게임오버 시 타이머 정지 (`engine.setPaused()`).
- 스테이지별 최단 기록을 `localStorage`에 저장, 갱신 시 "🏆 신기록".
- HUD에 현재 시간 + 해당 스테이지 최고기록 표시.

## 연출 · 사운드 · 미션 (추가됨)

- **Effects 레이어** (`src/render/Effects.js`): 파티클 폭발, 화면 흔들림,
  로켓 광선, 폭탄/라이트볼 충격파 링, 전체 섬광, 점수 플로팅 텍스트.
  세기는 `Constants.js`의 `FX` 블록에서 한 번에 튜닝.
- **SoundManager** (`src/systems/SoundManager.js`): Web Audio 실시간 합성
  효과음 (외부 음원 파일 0). 첫 입력에서 오디오 잠금 해제, 🔊 음소거 토글.
- **MissionSystem** (`src/systems/MissionSystem.js`): 스테이지 목표 로테이션
  점수 → 색상 수집 → 젤리 제거. 젤리는 보드의 별도 레이어(`board.jelly`)로
  관리되고 프로펠러의 유도 대상이 된다.

### 연출 튜닝 (Constants.js `FX`)

```js
GLOBAL_INTENSITY: 0.85,  // 전역 세기 (과하면 낮추기)
BURST_BASE: 7,           // 파괴 파편 수
SHAKE_BOMB / SHAKE_ROCKET / SHAKE_LIGHTBALL ...
```

## 다음 작업 후보

- [x] 특수타일 발동 시 파티클/이펙트 연출
- [x] 미션 목표(젤리/장애물 타일) 시스템 → 프로펠러 유도 대상 구체화
- [x] 사운드 (Web Audio 합성)
- [ ] 하트(라이프) 회복 타이머 & 로컬 저장 (메타 진행)
- [ ] 스테이지 선택 맵 & 진행 저장
- [ ] 장애물 다양화 (다층 젤리, 잠긴 타일, 폭탄 카운트다운)
