// =============================================================
//  GameEngine.js  -  코어 게임 엔진 (상태머신 오케스트레이션)
//
//  전체 흐름:
//    IDLE ──플레이어 스왑──> SWAP ──> MATCH_CHECK
//      MATCH_CHECK: 매치 있음 -> DESTROY / 없음(플레이어스왑) -> 되돌리기 -> IDLE
//                   없음(연쇄중) -> CHECK_GAME_OVER
//    DESTROY ──> GRAVITY_FALL ──> REFILL ──> MATCH_CHECK (연쇄 반복)
//    CHECK_GAME_OVER ──> IDLE(계속) 또는 게임종료 콜백
// =============================================================

import {
  GameState, SpecialType, STAGE_DEFAULTS, SCORE, TIMING, MissionType, TIME_BONUS,
  setBoardSize, boardSizeForStage,
} from './Constants.js';
import { Board } from './Board.js';
import { StateMachine } from './StateMachine.js';
import { MatchDetector } from './MatchDetector.js';
import { MissionSystem } from '../systems/MissionSystem.js';

export class GameEngine {
  /**
   * @param {Object} hooks 외부 콜백(광고/퍽/UI 연동)
   * @param {(n:number,time:number)=>void} [hooks.onStageClear]
   * @param {()=>void}         [hooks.onOutOfMoves]
   * @param {(choices:any[])=>void} [hooks.onPerkSelection]
   * @param {(state:string)=>void}  [hooks.onStateChange]
   * @param {(e:object)=>void}      [hooks.onEvent] 점수/연쇄 등 UI 이벤트
   */
  constructor(hooks = {}) {
    this.hooks = hooks;
    this.board = new Board();

    // --- 진행 상태 ---
    this.stage = 1;
    this.score = 0;
    this.cascadeLevel = 0;   // 연쇄 단계 (점수 배율)
    this.gameOver = false;

    // --- 타임 어택 (랭킹 기준) ---
    this.stageTime = 0;      // 현재 스테이지 경과 시간(초) — 빠를수록 상위 랭크
    this.paused = false;     // 퍽 선택/팝업 중 타이머 정지
    this.awaitingContinue = false; // 클리어 결과 화면에서 '계속' 대기 중
    this._lastClearTime = 0; // 직전 스테이지 클리어 소요 시간

    // 퍽(패시브) 효과 누적치 — PerkSystem이 채운다
    this.perks = {
      extraMoveOn4: 0,       // 4콤보 생성 시 이동 +N
      scoreMultiplier: 1,
      _startMovesBonus: 0,   // 스테이지 시작 이동 보너스
      // 타임어택/보드 관련 신규 퍽
      bombRadius: 1,         // 폭탄 반경 (1=3x3, 2=5x5)
      comboBonusExtra: 0,    // 콤보 시간 보너스 추가(초)
      cascadeTimeMult: 1,    // 연쇄 시간 보너스 배율
      clearCredit: 0,        // 스테이지 클리어 시 기록 시간 감면(초)
      startSpecials: 0,      // 스테이지 시작 시 배치할 랜덤 특수타일 수
    };

    // --- 스왑/연쇄 처리용 임시 상태 ---
    this._pendingSwap = null;    // {a, b, isPlayer}
    this._revertSwap = null;
    this._lastMatch = null;

    // 미션 & 이동 초기화 (보드/젤리 세팅 포함)
    this.mission = null;
    this._startStage();

    this._buildStateMachine();
  }

  /** 새 스테이지 준비: 보드 크기/리셋 + 미션 생성 + 젤리 시딩 + 이동/타이머 초기화 */
  _startStage() {
    // 스테이지에 맞춰 보드 크기 결정 (20스테이지 이후 10x10). reset 전에 설정.
    const size = boardSizeForStage(this.stage);
    setBoardSize(size, size);
    this.board.reset();

    this.score = 0;
    this.cascadeLevel = 0;
    this.stageTime = 0;      // 스테이지마다 스톱워치 리셋
    this.movesLeft = STAGE_DEFAULTS.BASE_MOVES + (this.perks._startMovesBonus || 0);
    this.mission = MissionSystem.forStage(this.stage);
    if (this.mission.seedJelly) this.board.seedJelly(this.mission.seedJelly);

    // 퍽: 스테이지 시작 시 랜덤 특수타일 배치
    for (let i = 0; i < (this.perks.startSpecials || 0); i++) this.board.placeRandomSpecial();

    this._syncMissionProgress();
    this._emit({ type: 'mission', mission: this.mission, stage: this.stage, boardSize: size });
  }

  /** 미션 진행도를 현재 보드 상태에 맞춰 동기화 */
  _syncMissionProgress() {
    if (!this.mission) return;
    if (this.mission.type === MissionType.SCORE) {
      this.mission.progress = this.score;
    } else if (this.mission.type === MissionType.JELLY) {
      // 진행도 = 처음 심은 젤리 - 남은 젤리
      this.mission.progress = this.mission.target - this.board.countJelly();
    }
    // COLLECT 는 파괴 시점에 누적되므로 여기서 손대지 않음
  }

  // ---------------------------------------------------------
  //  상태머신 구성
  // ---------------------------------------------------------
  _buildStateMachine() {
    const S = GameState;
    this.fsm = new StateMachine(S.IDLE, {

      // --- 입력 대기 ---
      [S.IDLE]: {
        enter: () => { this.cascadeLevel = 0; },
        update: () => {
          // 스왑 요청이 들어오면 SWAP 으로
          if (this._pendingSwap) return S.SWAP;
          return null;
        },
      },

      // --- 스왑 애니메이션 ---
      [S.SWAP]: {
        enter: () => {
          const { a, b } = this._pendingSwap;
          // 논리 교환 + 애니메이션 지시
          this.board.swapLogical(a, b);
          a.moveTo(a.row, a.col, TIMING.SWAP);
          b.moveTo(b.row, b.col, TIMING.SWAP);
          this._emit({ type: 'swap' });
        },
        update: () => {
          if (this.board.isAnimating()) return null;
          // 콤보(특수+특수) 스왑이면 즉시 콤보 발동
          const { a, b, isPlayer } = this._pendingSwap;
          if (isPlayer && a.isSpecial && b.isSpecial) {
            this._triggerCombo(a, b);
            this._pendingSwap = null;
            return S.DESTROY;
          }
          return GameState.MATCH_CHECK;
        },
      },

      // --- 매치 판정 ---
      [S.MATCH_CHECK]: {
        enter: () => {
          const hint = this._pendingSwap
            ? { row: this._pendingSwap.b.row, col: this._pendingSwap.b.col }
            : null;
          this._lastMatch = MatchDetector.findMatches(this.board, hint);
        },
        update: () => {
          const m = this._lastMatch;
          const hadPlayerSwap = this._pendingSwap && this._pendingSwap.isPlayer;

          if (m.cells.size > 0) {
            // 매치 성립 -> 이동 소비(플레이어 스왑일 때만) 후 파괴
            if (hadPlayerSwap) {
              this._consumeMove(m);
            }
            this._pendingSwap = null;
            return GameState.DESTROY;
          }

          // 매치 없음
          if (hadPlayerSwap) {
            // 잘못된 스왑 -> 되돌리기
            this._revertSwap = this._pendingSwap;
            this._pendingSwap = null;
            const { a, b } = this._revertSwap;
            this.board.swapLogical(a, b);
            a.moveTo(a.row, a.col, TIMING.SWAP_BACK);
            b.moveTo(b.row, b.col, TIMING.SWAP_BACK);
            this._emit({ type: 'invalid-swap' });
            return GameState.IDLE; // 되돌린 뒤 대기
          }
          // 연쇄 종료 -> 게임오버/클리어 판정
          return GameState.CHECK_GAME_OVER;
        },
      },

      // --- 파괴 (특수타일 연쇄 발동 포함) ---
      [S.DESTROY]: {
        enter: () => {
          this.cascadeLevel++;
          const m = this._lastMatch || { cells: new Set(), spawns: [] };

          // 1) 매치에 포함된 특수타일들을 연쇄 발동시켜 제거 범위를 확장
          //    (발동된 특수타일 정보는 연출용으로 fxSpecials에 수집)
          const fxSpecials = [];
          const cells = this._expandSpecials(m.cells, fxSpecials);

          // 2) 점수 계산
          const gained = this._awardScore(cells.size);

          // 3) 파괴 전에 위치/색 스냅샷 (연출용) — destroyCells가 null로 지우기 때문
          const destroyed = this._snapshotCells(cells, m.spawns || []);

          // 4) 제거 + 특수타일 스폰
          this.board.destroyCells(cells, m.spawns || []);

          // 4-1) 미션 진행 집계 (젤리 제거 / 색 수집 / 점수)
          const jellyCleared = this._processClears(cells, destroyed);

          // 4콤보(로켓급) 생성 시 퍽 보너스 이동
          if ((m.spawns || []).some(s => s.type === SpecialType.ROCKET) && this.perks.extraMoveOn4 > 0) {
            this.movesLeft += this.perks.extraMoveOn4;
            this._emit({ type: 'perk-extra-move', amount: this.perks.extraMoveOn4 });
          }

          // 4-2) 스피드 보너스: 깊은 연쇄 + 특수타일 생성 시 시간 되감기
          let bonus = 0;
          if (this.cascadeLevel >= 2) bonus += TIME_BONUS.CASCADE_STEP * (this.cascadeLevel - 1) * this.perks.cascadeTimeMult;
          if ((m.spawns || []).length > 0) bonus += TIME_BONUS.SPECIAL_CREATE;
          if (bonus > 0) this._applyTimeBonus(bonus, cells);

          // 5) 연출 이벤트 방출
          this._emitFx({
            kind: 'destroy',
            tiles: destroyed,
            specials: fxSpecials,
            spawns: m.spawns || [],
            cascade: this.cascadeLevel,
            score: gained,
            jellyCleared,
          });

          this._lastMatch = null;
        },
        update: () => {
          if (this.board.isAnimating()) return null;
          return GameState.GRAVITY_FALL;
        },
      },

      // --- 중력 낙하 ---
      [S.GRAVITY_FALL]: {
        enter: () => { this.board.applyGravity(); },
        update: () => {
          if (this.board.isAnimating()) return null;
          return GameState.REFILL;
        },
      },

      // --- 리필 ---
      [S.REFILL]: {
        enter: () => { this.board.refill(); },
        update: () => {
          if (this.board.isAnimating()) return null;
          // 리필 후 다시 매치 판정 (연쇄)
          return GameState.MATCH_CHECK;
        },
      },

      // --- 클리어 / 게임오버 판정 ---
      [S.CHECK_GAME_OVER]: {
        enter: () => {
          if (MissionSystem.isComplete(this.mission)) {
            this._onStageClear();
          } else if (this.movesLeft <= 0) {
            this._onOutOfMoves();
          } else if (!this.board.hasPossibleMove()) {
            // 데드락: 이동 가능한 수가 없음 -> 보드 셔플
            this._emit({ type: 'shuffle' });
            this.board.reset();
          }
        },
        update: () => GameState.IDLE,
      },

    }, {
      onChange: (from, to) => {
        if (this.hooks.onStateChange) this.hooks.onStateChange(to, from);
      },
    });
  }

  // ---------------------------------------------------------
  //  공개 API
  // ---------------------------------------------------------

  /** 매 프레임 갱신 (렌더 루프에서 호출) */
  update(dt) {
    this.board.update(dt);
    if (!this.gameOver) {
      // 타임어택 스톱워치 (일시정지/게임오버가 아닐 때만 진행)
      if (!this.paused) this.stageTime += dt;
      this.fsm.update(dt);
    }
  }

  /** 타이머 일시정지/재개 (퍽 선택 등 팝업 중) */
  setPaused(p) { this.paused = p; }

  get state() { return this.fsm.current; }

  /** IDLE 상태에서만 플레이어 스왑을 받는다 (결과화면/일시정지 중엔 거부) */
  canAcceptInput() {
    return this.fsm.is(GameState.IDLE) && !this.gameOver
      && !this.awaitingContinue && !this.paused && !this.board.isAnimating();
  }

  /**
   * 플레이어가 두 타일을 스왑 시도.
   * @returns {boolean} 접수되었으면 true
   */
  requestSwap(a, b) {
    if (!this.canAcceptInput()) return false;
    if (!a || !b || a === b) return false;
    if (!this.board.areAdjacent(a, b)) return false;
    this._pendingSwap = { a, b, isPlayer: true };
    return true;
  }

  /** 광고 보상: 이동 +N */
  grantRewardMoves(n = STAGE_DEFAULTS.REWARD_AD_MOVES) {
    this.movesLeft += n;
    this.gameOver = false;
    this._emit({ type: 'reward-moves', amount: n });
  }

  /** 퍽 적용 (PerkSystem이 선택 결과를 넘겨줌) */
  applyPerk(perk) {
    if (typeof perk.apply === 'function') perk.apply(this);
    this._emit({ type: 'perk-applied', perk: perk.id });
  }

  // ---------------------------------------------------------
  //  내부 로직
  // ---------------------------------------------------------

  /** 이동 소비 + 4콤보 퍽 보너스 처리 */
  _consumeMove() {
    this.movesLeft = Math.max(0, this.movesLeft - 1);
    this._emit({ type: 'move-used', movesLeft: this.movesLeft });
  }

  /**
   * 제거 대상 셀 중 특수타일이 있으면 그 효과 범위를 재귀적으로 합친다.
   * (로켓이 폭탄을 건드리면 폭탄도 터지는 연쇄)
   * @param {Set<string>} baseCells
   * @returns {Set<string>}
   */
  _expandSpecials(baseCells, fxOut = null) {
    const result = new Set(baseCells);
    const queue = [];
    for (const key of baseCells) {
      const [r, c] = key.split(',').map(Number);
      const t = this.board.grid[r][c];
      if (t && t.isSpecial) queue.push(t);
    }
    const activated = new Set();
    while (queue.length) {
      const t = queue.shift();
      if (activated.has(t.id)) continue;
      activated.add(t.id);
      // 연출용: 발동 순간의 특수타일 위치/종류 기록
      if (fxOut) fxOut.push({ type: t.special, row: t.row, col: t.col, rocketDir: t.rocketDir, color: t.color });
      // 프로펠러 유도 목표(미션색)와 폭탄 반경(퍽) 전달
      const affected = this.board.activateSpecial(t, {
        missionColor: this.mission ? this.mission.color : null,
        bombRadius: this.perks.bombRadius,
      });
      for (const key of affected) {
        result.add(key);
        const [r, c] = key.split(',').map(Number);
        const other = this.board.grid[r][c];
        if (other && other.isSpecial && !activated.has(other.id)) queue.push(other);
      }
    }
    return result;
  }

  /**
   * 파괴된 셀에서 미션 진행을 집계한다 (젤리 제거 / 색 수집 / 점수).
   * @param {Set<string>} cells 제거된 셀
   * @param {Array<{row,col,color}>} destroyedTiles 파괴된 타일 스냅샷
   * @returns {number} 이번에 제거된 젤리 수
   */
  _processClears(cells, destroyedTiles) {
    let jellyCleared = 0;
    const jellyPos = [];
    for (const key of cells) {
      const [r, c] = key.split(',').map(Number);
      if (this.board.clearJellyAt(r, c)) { jellyCleared++; jellyPos.push({ row: r, col: c }); }
    }
    if (this.mission) {
      if (this.mission.type === MissionType.JELLY) {
        this.mission.progress += jellyCleared;
      } else if (this.mission.type === MissionType.COLLECT) {
        for (const t of destroyedTiles) if (t.color === this.mission.color) this.mission.progress++;
      } else if (this.mission.type === MissionType.SCORE) {
        this.mission.progress = this.score;
      }
      this._emit({ type: 'mission-progress', mission: this.mission });
    }
    if (jellyCleared) this._emitFx({ kind: 'jelly', cells: jellyPos });
    return jellyCleared;
  }

  /**
   * 스피드 보너스: stageTime을 seconds 만큼 되감아 기록을 줄여준다.
   * @param {number} seconds 차감할 시간(초, 양수)
   * @param {Set<string>|{row,col}} where 연출 표시 위치 (셀 집합 또는 좌표)
   */
  _applyTimeBonus(seconds, where) {
    if (seconds <= 0) return;
    this.stageTime = Math.max(0, this.stageTime - seconds);
    let pos;
    if (where instanceof Set) {
      let sr = 0, sc = 0, n = 0;
      for (const key of where) { const [r, c] = key.split(',').map(Number); sr += r; sc += c; n++; }
      pos = n ? { row: sr / n, col: sc / n } : { row: 3.5, col: 3.5 };
    } else {
      pos = where || { row: 3.5, col: 3.5 };
    }
    this._emit({ type: 'time-bonus', seconds });
    this._emitFx({ kind: 'timebonus', at: pos, seconds });
  }

  /** 파괴 직전 셀들의 위치/색을 스냅샷 (특수타일로 대체되는 칸은 제외) */
  _snapshotCells(cells, spawns) {
    const spawnKeys = new Set(spawns.map(s => `${s.row},${s.col}`));
    const out = [];
    for (const key of cells) {
      if (spawnKeys.has(key)) continue; // 그 자리는 특수타일이 남으므로 파괴 연출 제외
      const [r, c] = key.split(',').map(Number);
      const t = this.board.grid[r][c];
      if (t) out.push({ row: r, col: c, color: t.color });
    }
    return out;
  }

  /**
   * 콤보 발동 (특수 + 특수 스왑).
   * 스펙 #3:
   *   - 로켓 + 폭탄  => 3줄 가로 + 3줄 세로 제거
   *   - 라이트볼 + 라이트볼 => 보드 전체 제거
   */
  _triggerCombo(a, b) {
    const types = [a.special, b.special].sort();
    const cells = new Set();
    const R = this.board.grid.length, C = this.board.grid[0].length;

    const bothLightBall = a.special === SpecialType.LIGHT_BALL && b.special === SpecialType.LIGHT_BALL;
    const rocketBomb = types.includes(SpecialType.ROCKET) && types.includes(SpecialType.BOMB);

    if (bothLightBall) {
      // 보드 전체
      for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) cells.add(`${r},${c}`);
    } else if (rocketBomb) {
      // 로켓 위치 기준 가로 3줄 + 세로 3줄
      const r = a.row, c = a.col;
      for (let dr = -1; dr <= 1; dr++)
        for (let cc = 0; cc < C; cc++)
          if (this.board.inBounds(r + dr, cc)) cells.add(`${r + dr},${cc}`);
      for (let dc = -1; dc <= 1; dc++)
        for (let rr = 0; rr < R; rr++)
          if (this.board.inBounds(rr, c + dc)) cells.add(`${rr},${c + dc}`);
    } else {
      // 그 외 조합은 각자 효과를 합산
      for (const k of this.board.activateSpecial(a)) cells.add(k);
      for (const k of this.board.activateSpecial(b)) cells.add(k);
    }

    this._consumeMove();
    // 콤보 스피드 보너스 (조합별 차등 + 퍽 추가분)
    const comboBonus = (bothLightBall ? TIME_BONUS.COMBO_LIGHTBALL
      : rocketBomb ? TIME_BONUS.COMBO_ROCKETBOMB : TIME_BONUS.COMBO_OTHER) + this.perks.comboBonusExtra;
    this._applyTimeBonus(comboBonus, { row: a.row, col: a.col });
    const fxSpecials = [];
    const expanded = this._expandSpecials(cells, fxSpecials);
    const gained = this._awardScore(expanded.size);
    const destroyed = this._snapshotCells(expanded, []);
    this.board.destroyCells(expanded, []);
    this._processClears(expanded, destroyed);
    this._lastMatch = null;
    this._emit({ type: 'combo', combo: types.join('+'), cleared: expanded.size });
    // 콤보는 강력한 연출: 대형 화면 흔들림 + 발동 지점 표시
    this._emitFx({
      kind: 'combo',
      combo: bothLightBall ? 'LIGHTBALL_LIGHTBALL' : rocketBomb ? 'ROCKET_BOMB' : 'MIX',
      at: { row: a.row, col: a.col },
      tiles: destroyed,
      specials: fxSpecials,
      score: gained,
    });
  }

  /** 점수 부여 (연쇄 단계 + 퍽 배율 반영). @returns {number} 획득 점수 */
  _awardScore(tileCount) {
    const per = SCORE.PER_TILE + SCORE.CASCADE_BONUS * Math.max(0, this.cascadeLevel - 1);
    const gained = Math.round(tileCount * per * this.perks.scoreMultiplier);
    this.score += gained;
    this._emit({ type: 'score', gained, total: this.score, cascade: this.cascadeLevel });
    return gained;
  }

  _onStageClear() {
    const cleared = this.stage;
    // 퍽: 클리어 기록 시간 감면
    if (this.perks.clearCredit > 0) this.stageTime = Math.max(0, this.stageTime - this.perks.clearCredit);
    this._lastClearTime = this.stageTime;
    // 결과 화면을 위해 진행을 멈추고 '계속' 입력을 기다린다 (타이머 정지)
    this.awaitingContinue = true;
    this.paused = true;

    // 클리어에 걸린 시간(초)을 함께 알림 -> 랭킹/기록/결과화면에 사용
    this._emit({ type: 'stage-clear', stage: cleared, time: this.stageTime });
    if (this.hooks.onStageClear) this.hooks.onStageClear(cleared, this.stageTime);

    // 스펙 #5: 3스테이지마다 전면광고 체크
    if (cleared % 3 === 0 && this.hooks.onInterstitialCheck) {
      this.hooks.onInterstitialCheck(cleared);
    }
    // 다음 스테이지 준비는 continueToNextStage()에서 (결과 화면 '계속' 클릭 후)
  }

  /**
   * 결과 화면에서 '계속'을 눌렀을 때 호출. 다음 스테이지로 진행한다.
   * 퍽 경계(3스테이지)면 퍽 선택 UI를 띄우고, 그 외엔 타이머를 재개한다.
   */
  continueToNextStage() {
    if (!this.awaitingContinue) return;
    this.awaitingContinue = false;
    const cleared = this.stage;
    this.stage++;
    this._startStage(); // 미션 재생성 + 젤리 + 타이머 리셋 (paused 유지)

    if (cleared % STAGE_DEFAULTS.PERK_EVERY === 0 && this.hooks.onPerkSelection) {
      // 퍽 선택 중에는 계속 정지; 선택 완료 시 UI가 setPaused(false) 호출
      this.hooks.onPerkSelection(cleared);
    } else {
      this.paused = false; // 곧바로 다음 스테이지 타이머 시작
    }
  }

  _onOutOfMoves() {
    // 스펙 #5: 이동 소진 -> 보상형 광고 훅
    this.gameOver = true;
    this._emit({ type: 'out-of-moves' });
    if (this.hooks.onOutOfMoves) this.hooks.onOutOfMoves();
  }

  _emit(e) { if (this.hooks.onEvent) this.hooks.onEvent(e); }

  /** 연출 전용 이벤트 (위치·색 정보 포함). 렌더/이펙트 레이어가 구독. */
  _emitFx(fx) { if (this.hooks.onFx) this.hooks.onFx(fx); }
}
