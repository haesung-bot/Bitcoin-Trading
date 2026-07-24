// =============================================================
//  InputController.js  -  마우스/터치 드래그로 타일 스왑
// =============================================================

import { BOARD_PADDING, TILE_SIZE, BOARD_ROWS, BOARD_COLS } from '../core/Constants.js';

export class InputController {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {GameEngine} engine
   * @param {Renderer} renderer 선택 강조 표시용
   */
  constructor(canvas, engine, renderer) {
    this.canvas = canvas;
    this.engine = engine;
    this.renderer = renderer;
    this.dragStart = null; // {row, col, tile}

    this._bind();
  }

  _bind() {
    const c = this.canvas;
    c.addEventListener('mousedown', (e) => this._onDown(e));
    c.addEventListener('mousemove', (e) => this._onMove(e));
    window.addEventListener('mouseup', () => this._onUp());

    c.addEventListener('touchstart', (e) => { e.preventDefault(); this._onDown(e.touches[0]); }, { passive: false });
    c.addEventListener('touchmove', (e) => { e.preventDefault(); this._onMove(e.touches[0]); }, { passive: false });
    window.addEventListener('touchend', () => this._onUp());
  }

  _cellFromEvent(e) {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top) * scaleY;
    const col = Math.floor((x - BOARD_PADDING) / TILE_SIZE);
    const row = Math.floor((y - BOARD_PADDING) / TILE_SIZE);
    if (row < 0 || row >= BOARD_ROWS || col < 0 || col >= BOARD_COLS) return null;
    return { row, col };
  }

  _onDown(e) {
    if (!this.engine.canAcceptInput()) return;
    const cell = this._cellFromEvent(e);
    if (!cell) return;
    const tile = this.engine.board.grid[cell.row][cell.col];
    if (!tile) return;
    this.dragStart = { ...cell, tile };
    this.renderer.selected = tile;
  }

  _onMove(e) {
    if (!this.dragStart) return;
    const cell = this._cellFromEvent(e);
    if (!cell) return;
    // 시작 칸과 인접한 다른 칸으로 드래그하면 스왑 시도
    const dr = Math.abs(cell.row - this.dragStart.row);
    const dc = Math.abs(cell.col - this.dragStart.col);
    if (dr + dc === 1) {
      const target = this.engine.board.grid[cell.row][cell.col];
      if (target && this.engine.requestSwap(this.dragStart.tile, target)) {
        this.dragStart = null;
        this.renderer.selected = null;
      }
    }
  }

  _onUp() {
    this.dragStart = null;
    this.renderer.selected = null;
  }
}
