// =============================================================
//  Tile.js  -  개별 타일의 데이터 + 애니메이션 상태
// =============================================================

import { TILE_SIZE, BOARD_PADDING, SpecialType, RocketDir } from './Constants.js';

let _tileIdSeq = 1; // 타일 고유 ID 시퀀스 (애니메이션 추적용)

/**
 * 보드 위 한 칸을 차지하는 타일.
 * 논리 좌표(row,col)와 별개로, 화면상 픽셀 좌표(px,py)를 따로 가진다.
 * -> 스왑/낙하 애니메이션은 픽셀 좌표를 목표점(targetPx/targetPy)으로
 *    부드럽게 보간(lerp)하는 방식으로 처리한다.
 */
export class Tile {
  /**
   * @param {number} color   TileColor 인덱스
   * @param {number} row      논리 행
   * @param {number} col      논리 열
   */
  constructor(color, row, col) {
    this.id = _tileIdSeq++;
    this.color = color;
    this.special = SpecialType.NONE;
    this.rocketDir = null; // 로켓일 때만 사용 (RocketDir)

    this.row = row;
    this.col = col;

    // 화면상 현재 위치 & 목표 위치 (픽셀)
    this.px = Tile.colToX(col);
    this.py = Tile.rowToY(row);
    this.targetPx = this.px;
    this.targetPy = this.py;

    // 애니메이션 진행값
    this.moving = false;
    this.moveElapsed = 0;
    this.moveDuration = 0;
    this.fromPx = this.px;
    this.fromPy = this.py;

    // 파괴 연출용
    this.dying = false;
    this.deathElapsed = 0;
    this.scale = 1;      // 등장/소멸 스케일 연출
    this.spawning = false;
  }

  /** 논리 열 -> 화면 X 픽셀 (좌상단 기준) */
  static colToX(col) {
    return BOARD_PADDING + col * TILE_SIZE;
  }
  /** 논리 행 -> 화면 Y 픽셀 */
  static rowToY(row) {
    return BOARD_PADDING + row * TILE_SIZE;
  }

  get isSpecial() {
    return this.special !== SpecialType.NONE;
  }

  /**
   * 목표 위치로의 이동 애니메이션 시작.
   * @param {number} row 목표 행
   * @param {number} col 목표 열
   * @param {number} duration 소요 시간(초)
   */
  moveTo(row, col, duration) {
    this.row = row;
    this.col = col;
    this.fromPx = this.px;
    this.fromPy = this.py;
    this.targetPx = Tile.colToX(col);
    this.targetPy = Tile.rowToY(row);
    this.moveElapsed = 0;
    this.moveDuration = Math.max(0.0001, duration);
    this.moving = true;
  }

  /** 화면 위치만 즉시 목표점으로 스냅 (애니메이션 없음) */
  snapToLogical() {
    this.px = this.targetPx = Tile.colToX(this.col);
    this.py = this.targetPy = Tile.rowToY(this.row);
    this.moving = false;
  }

  /**
   * 프레임 갱신. 이동/소멸 애니메이션을 진행한다.
   * @param {number} dt 델타타임(초)
   * @returns {boolean} 아직 애니메이션 중이면 true
   */
  update(dt) {
    let busy = false;

    if (this.moving) {
      this.moveElapsed += dt;
      const t = Math.min(1, this.moveElapsed / this.moveDuration);
      const e = easeOutQuad(t); // 감속 이징으로 자연스럽게
      this.px = lerp(this.fromPx, this.targetPx, e);
      this.py = lerp(this.fromPy, this.targetPy, e);
      if (t >= 1) {
        this.moving = false;
        this.px = this.targetPx;
        this.py = this.targetPy;
      } else {
        busy = true;
      }
    }

    if (this.spawning) {
      this.moveElapsed += 0; // 스폰 스케일은 death와 별도 타이머 없이 간단 처리
      this.scale = Math.min(1, this.scale + dt * 6);
      if (this.scale >= 1) this.spawning = false;
      else busy = true;
    }

    if (this.dying) {
      this.deathElapsed += dt;
      this.scale = Math.max(0, 1 - this.deathElapsed / 0.22);
      if (this.scale <= 0) this.scale = 0;
      else busy = true;
    }

    return busy;
  }
}

// ---- 보간 유틸 ----
function lerp(a, b, t) { return a + (b - a) * t; }
function easeOutQuad(t) { return 1 - (1 - t) * (1 - t); }
