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

import { GameState, SpecialType, STAGE_DEFAULTS, SCORE, TIMING } from './Constants.js';
import { Board } from './Board.js';
import { StateMachine } from './StateMachine.js';
import { MatchDetector } from './MatchDetector.js';

export class GameEngine {
  /**
   * @param {Object} hooks 외부 콜백(광고/퍽/UI 연동)
   * @param {(n:number)=>void} [hooks.onStageClear]
   * @param {()=>void}         [hooks.onOutOfMoves]
   * @param {()=>void}         [hooks.onHeartEmpty]
   * @param {(choices:any[])=>void} [hooks.onPerkSelection]
   * @param {(state:string)=>void}  [hooks.onStateChange]
   * @param {(e:object)=>void}      [hooks.onEvent] 점수/연쇄 등 UI 이벤트
   */
  constructor(hooks = {}) {
    this.hooks = hooks;
    this.board = new Board();

    // --- 진행 상태 ---
    this.stage = 1;
    this.movesLeft = STAGE_DEFAULTS.BASE_MOVES;
    this.hearts = STAGE_DEFAULTS.MAX_HEARTS;
    this.score = 0;
    this.targetScore = this._computeTargetScore(this.stage);
    this.cascadeLevel = 0;   // 연쇄 단계 (점수 배율)
    this.gameOver = false;

    // 퍽(패시브) 효과 누적치 — PerkSystem이 채운다
    this.perks = {
      bombRangeBonus: 0,     // +N (3x3 -> (3+2N)x(3+2N) 식으로 확장 가능)
      extraMoveOn4: 0,       // 4콤보 생성 시 이동 +N
      scoreMultiplier: 1,
    };

    // --- 스왑/연쇄 처리용 임시 상태 ---
    this._pendingSwap = null;    // {a, b, isPlayer}
    this._revertSwap = null;
    this._lastMatch = null;

    this._buildStateMachine();
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
          const cells = this._expandSpecials(m.cells);

          // 2) 점수 계산
          this._awardScore(cells.size);

          // 3) 제거 + 특수타일 스폰
          this.board.destroyCells(cells, m.spawns || []);

          // 4콤보(로켓급) 생성 시 퍽 보너스 이동
          if ((m.spawns || []).some(s => s.type === SpecialType.ROCKET) && this.perks.extraMoveOn4 > 0) {
            this.movesLeft += this.perks.extraMoveOn4;
            this._emit({ type: 'perk-extra-move', amount: this.perks.extraMoveOn4 });
          }
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
          if (this.score >= this.targetScore) {
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
    if (!this.gameOver) this.fsm.update(dt);
  }

  get state() { return this.fsm.current; }

  /** IDLE 상태에서만 플레이어 스왑을 받는다 */
  canAcceptInput() {
    return this.fsm.is(GameState.IDLE) && !this.gameOver && !this.board.isAnimating();
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

  /** 광고 보상: 하트 리필 */
  grantHeartRefill() {
    this.hearts = STAGE_DEFAULTS.MAX_HEARTS;
    this._emit({ type: 'heart-refill' });
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
  _expandSpecials(baseCells) {
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
      const affected = this.board.activateSpecial(t);
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
    const expanded = this._expandSpecials(cells);
    this._awardScore(expanded.size);
    this.board.destroyCells(expanded, []);
    this._lastMatch = null;
    this._emit({ type: 'combo', combo: types.join('+'), cleared: expanded.size });
  }

  /** 점수 부여 (연쇄 단계 + 퍽 배율 반영) */
  _awardScore(tileCount) {
    const per = SCORE.PER_TILE + SCORE.CASCADE_BONUS * Math.max(0, this.cascadeLevel - 1);
    const gained = Math.round(tileCount * per * this.perks.scoreMultiplier);
    this.score += gained;
    this._emit({ type: 'score', gained, total: this.score, cascade: this.cascadeLevel });
  }

  _computeTargetScore(stage) {
    // 스테이지가 올라갈수록 목표 점수 증가
    return 1000 + (stage - 1) * 600;
  }

  _onStageClear() {
    const cleared = this.stage;
    this._emit({ type: 'stage-clear', stage: cleared });
    if (this.hooks.onStageClear) this.hooks.onStageClear(cleared);

    // 스펙 #5: 3스테이지마다 전면광고 체크
    if (cleared % 3 === 0 && this.hooks.onInterstitialCheck) {
      this.hooks.onInterstitialCheck(cleared);
    }
    // 스펙 #4: 3스테이지마다 퍽 선택
    if (cleared % STAGE_DEFAULTS.PERK_EVERY === 0 && this.hooks.onPerkSelection) {
      this.hooks.onPerkSelection(cleared);
    }

    // 다음 스테이지 준비
    this.stage++;
    this.movesLeft = STAGE_DEFAULTS.BASE_MOVES;
    this.score = 0;
    this.targetScore = this._computeTargetScore(this.stage);
    this.board.reset();
  }

  _onOutOfMoves() {
    // 스펙 #5: 이동 소진 -> 보상형 광고 훅
    this.gameOver = true;
    this._emit({ type: 'out-of-moves' });
    if (this.hooks.onOutOfMoves) this.hooks.onOutOfMoves();
  }

  _emit(e) { if (this.hooks.onEvent) this.hooks.onEvent(e); }
}
