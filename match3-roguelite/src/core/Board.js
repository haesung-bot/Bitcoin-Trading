// =============================================================
//  Board.js  -  8x8 보드 매트릭스 + 타일 조작(스왑/중력/리필/발동)
// =============================================================

import {
  BOARD_ROWS, BOARD_COLS, ACTIVE_COLORS, SpecialType, RocketDir, TIMING,
} from './Constants.js';
import { Tile } from './Tile.js';
import { MatchDetector } from './MatchDetector.js';

export class Board {
  constructor() {
    /** @type {(Tile|null)[][]} grid[row][col] */
    this.grid = [];
    this.reset();
  }

  /** 초기 매치가 없는 보드를 새로 생성 */
  reset() {
    do {
      this.grid = [];
      for (let r = 0; r < BOARD_ROWS; r++) {
        const row = [];
        for (let c = 0; c < BOARD_COLS; c++) {
          row.push(new Tile(this._randColor(), r, c));
        }
        this.grid.push(row);
      }
    } while (MatchDetector.hasAnyMatch(this)); // 시작부터 매치가 있으면 재생성
  }

  _randColor() {
    return ACTIVE_COLORS[(Math.random() * ACTIVE_COLORS.length) | 0];
  }

  inBounds(r, c) {
    return r >= 0 && r < BOARD_ROWS && c >= 0 && c < BOARD_COLS;
  }
  get(r, c) {
    return this.inBounds(r, c) ? this.grid[r][c] : null;
  }

  /** 두 칸이 상하좌우로 인접한가 */
  areAdjacent(a, b) {
    const dr = Math.abs(a.row - b.row);
    const dc = Math.abs(a.col - b.col);
    return dr + dc === 1;
  }

  /**
   * 논리상 두 타일 교환 (데이터만; 애니메이션은 GameEngine이 지시).
   * @param {Tile} a @param {Tile} b
   */
  swapLogical(a, b) {
    const ar = a.row, ac = a.col, br = b.row, bc = b.col;
    this.grid[ar][ac] = b;
    this.grid[br][bc] = a;
    a.row = br; a.col = bc;
    b.row = ar; b.col = ac;
  }

  /**
   * 매치된 셀들을 제거하고, 그 자리에 특수타일을 스폰한다.
   * @param {Set<string>} cells 제거 대상
   * @param {import('./MatchDetector.js').SpawnSpec[]} spawns
   * @returns {Tile[]} 제거된 타일 목록 (연출/점수용)
   */
  destroyCells(cells, spawns = []) {
    const spawnKeys = new Map(); // "r,c" -> spawnSpec (그 자리는 파괴 대신 특수타일로 대체)
    for (const s of spawns) spawnKeys.set(`${s.row},${s.col}`, s);

    const removed = [];
    for (const key of cells) {
      const [r, c] = key.split(',').map(Number);
      const t = this.grid[r][c];
      if (!t) continue;

      if (spawnKeys.has(key)) {
        // 이 자리에 특수타일 생성 -> 기존 타일은 색만 유지한 채 특수화
        const spec = spawnKeys.get(key);
        t.special = spec.type;
        t.rocketDir = spec.rocketDir || null;
        t.spawning = true;
        t.scale = 0.2;
        // 특수타일은 살아남으므로 removed 에 넣지 않음
      } else {
        t.dying = true;
        this.grid[r][c] = null;
        removed.push(t);
      }
    }
    return removed;
  }

  /**
   * 특수타일 발동. 발동 시 영향을 받는 셀 좌표 집합을 반환한다.
   * (연쇄 발동 지원: 영향 범위 안의 다른 특수타일도 함께 터지도록 GameEngine에서 재귀 처리)
   * @param {Tile} tile 발동시킬 특수타일
   * @param {{missionRow?:number,missionCol?:number}} ctx 프로펠러 유도 목표 등
   * @returns {Set<string>} 제거될 셀 좌표
   */
  activateSpecial(tile, ctx = {}) {
    const affected = new Set();
    const r = tile.row, c = tile.col;

    switch (tile.special) {
      case SpecialType.ROCKET: {
        if (tile.rocketDir === RocketDir.HORIZONTAL) {
          for (let cc = 0; cc < BOARD_COLS; cc++) affected.add(`${r},${cc}`);
        } else {
          for (let rr = 0; rr < BOARD_ROWS; rr++) affected.add(`${rr},${c}`);
        }
        break;
      }
      case SpecialType.BOMB: {
        // 3x3 반경
        for (let dr = -1; dr <= 1; dr++) {
          for (let dc = -1; dc <= 1; dc++) {
            const rr = r + dr, cc = c + dc;
            if (this.inBounds(rr, cc)) affected.add(`${rr},${cc}`);
          }
        }
        break;
      }
      case SpecialType.LIGHT_BALL: {
        // 같은 색 타일 전부 제거
        for (let rr = 0; rr < BOARD_ROWS; rr++)
          for (let cc = 0; cc < BOARD_COLS; cc++)
            if (this.grid[rr][cc] && this.grid[rr][cc].color === tile.color)
              affected.add(`${rr},${cc}`);
        affected.add(`${r},${c}`);
        break;
      }
      case SpecialType.PROPELLER: {
        // 주변(상하좌우) 제거 + 미션 목표(없으면 랜덤 타일)로 유도
        affected.add(`${r},${c}`);
        for (const [dr, dc] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
          const rr = r + dr, cc = c + dc;
          if (this.inBounds(rr, cc)) affected.add(`${rr},${cc}`);
        }
        const target = this._propellerTarget(ctx);
        if (target) affected.add(`${target.row},${target.col}`);
        break;
      }
      default:
        affected.add(`${r},${c}`);
    }
    return affected;
  }

  /** 프로펠러가 유도할 목표: 미션 좌표가 있으면 그곳, 없으면 임의 타일 */
  _propellerTarget(ctx) {
    if (ctx.missionRow != null && ctx.missionCol != null) {
      return { row: ctx.missionRow, col: ctx.missionCol };
    }
    const candidates = [];
    for (let r = 0; r < BOARD_ROWS; r++)
      for (let c = 0; c < BOARD_COLS; c++)
        if (this.grid[r][c]) candidates.push({ row: r, col: c });
    if (!candidates.length) return null;
    return candidates[(Math.random() * candidates.length) | 0];
  }

  /**
   * 중력 적용: 각 열에서 빈 칸(null) 아래로 타일을 떨어뜨린다.
   * @returns {boolean} 하나라도 이동했으면 true
   */
  applyGravity() {
    let moved = false;
    for (let c = 0; c < BOARD_COLS; c++) {
      let writeRow = BOARD_ROWS - 1; // 아래에서부터 채운다
      for (let r = BOARD_ROWS - 1; r >= 0; r--) {
        const t = this.grid[r][c];
        if (t) {
          if (writeRow !== r) {
            this.grid[writeRow][c] = t;
            this.grid[r][c] = null;
            const fallDist = writeRow - r;
            t.moveTo(writeRow, c, TIMING.FALL_PER_TILE * fallDist);
            moved = true;
          }
          writeRow--;
        }
      }
    }
    return moved;
  }

  /**
   * 리필: 각 열의 빈 칸을 상단에서 새 타일로 채운다.
   * 새 타일은 화면 위쪽에서 떨어지도록 시작 위치를 보드 밖으로 잡는다.
   * @returns {Tile[]} 새로 생성된 타일들
   */
  refill() {
    const created = [];
    for (let c = 0; c < BOARD_COLS; c++) {
      // 해당 열의 빈 칸 수 계산
      let empties = 0;
      for (let r = 0; r < BOARD_ROWS; r++) if (!this.grid[r][c]) empties++;

      let spawnIndex = 0;
      for (let r = 0; r < BOARD_ROWS; r++) {
        if (this.grid[r][c]) continue;
        const t = new Tile(this._randColor(), r, c);
        // 보드 위쪽(음수 행)에서 시작 -> 목표 행으로 낙하
        const startRow = -(empties - spawnIndex);
        t.px = Tile.colToX(c);
        t.py = Tile.rowToY(startRow);
        t.moveTo(r, c, TIMING.FALL_PER_TILE * (r - startRow));
        this.grid[r][c] = t;
        created.push(t);
        spawnIndex++;
      }
    }
    return created;
  }

  /** 애니메이션 진행 중인 타일이 하나라도 있으면 true */
  isAnimating() {
    for (let r = 0; r < BOARD_ROWS; r++)
      for (let c = 0; c < BOARD_COLS; c++) {
        const t = this.grid[r][c];
        if (t && (t.moving || t.spawning)) return true;
      }
    return false;
  }

  /** 모든 타일 프레임 갱신 */
  update(dt) {
    let busy = false;
    for (let r = 0; r < BOARD_ROWS; r++)
      for (let c = 0; c < BOARD_COLS; c++) {
        const t = this.grid[r][c];
        if (t && t.update(dt)) busy = true;
      }
    return busy;
  }

  /** 현재 보드에 가능한 이동(매치를 만드는 스왑)이 하나라도 있는지 검사 */
  hasPossibleMove() {
    for (let r = 0; r < BOARD_ROWS; r++) {
      for (let c = 0; c < BOARD_COLS; c++) {
        // 오른쪽/아래와 스왑 시도 후 매치 여부 확인
        for (const [dr, dc] of [[0, 1], [1, 0]]) {
          const nr = r + dr, nc = c + dc;
          if (!this.inBounds(nr, nc)) continue;
          this._swapCells(r, c, nr, nc);
          const has = MatchDetector.hasAnyMatch(this);
          this._swapCells(r, c, nr, nc); // 원복
          if (has) return true;
        }
      }
    }
    return false;
  }

  /** 좌표 기반 즉시 스왑 (검사용, 애니메이션 없음) */
  _swapCells(r1, c1, r2, c2) {
    const a = this.grid[r1][c1], b = this.grid[r2][c2];
    this.grid[r1][c1] = b; this.grid[r2][c2] = a;
    if (a) { a.row = r2; a.col = c2; }
    if (b) { b.row = r1; b.col = c1; }
  }
}
