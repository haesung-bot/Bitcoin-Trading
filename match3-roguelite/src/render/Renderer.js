// =============================================================
//  Renderer.js  -  Canvas 렌더링 (타일, 특수타일 마커, HUD)
//  * 코어 로직과 분리되어 있어 나중에 스프라이트/이펙트로 교체 가능.
// =============================================================

import {
  BOARD_ROWS, BOARD_COLS, TILE_SIZE, BOARD_PADDING,
  COLOR_HEX, SpecialType, RocketDir,
} from '../core/Constants.js';

export class Renderer {
  constructor(canvas, engine) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.engine = engine;
    this.selected = null; // 강조 표시할 타일

    const w = BOARD_PADDING * 2 + BOARD_COLS * TILE_SIZE;
    const h = BOARD_PADDING * 2 + BOARD_ROWS * TILE_SIZE;
    canvas.width = w;
    canvas.height = h;
  }

  draw() {
    const ctx = this.ctx;
    const { width, height } = this.canvas;

    // 배경
    ctx.fillStyle = '#141a2e';
    ctx.fillRect(0, 0, width, height);

    // 보드 격자 배경
    for (let r = 0; r < BOARD_ROWS; r++) {
      for (let c = 0; c < BOARD_COLS; c++) {
        const x = BOARD_PADDING + c * TILE_SIZE;
        const y = BOARD_PADDING + r * TILE_SIZE;
        ctx.fillStyle = (r + c) % 2 === 0 ? '#1e2743' : '#232e52';
        ctx.fillRect(x, y, TILE_SIZE, TILE_SIZE);
      }
    }

    // 타일 (파괴 애니메이션 중인 것 포함해서 그리려면 별도 리스트 필요하나
    //       여기서는 살아있는 그리드만 그린다)
    for (let r = 0; r < BOARD_ROWS; r++) {
      for (let c = 0; c < BOARD_COLS; c++) {
        const t = this.engine.board.grid[r][c];
        if (t) this._drawTile(t);
      }
    }

    // 선택 강조
    if (this.selected) {
      const t = this.selected;
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 4;
      ctx.strokeRect(t.px + 3, t.py + 3, TILE_SIZE - 6, TILE_SIZE - 6);
    }
  }

  _drawTile(t) {
    const ctx = this.ctx;
    const pad = 6;
    const s = (TILE_SIZE - pad * 2) * (t.scale ?? 1);
    const cx = t.px + TILE_SIZE / 2;
    const cy = t.py + TILE_SIZE / 2;
    const x = cx - s / 2;
    const y = cy - s / 2;

    // 본체
    ctx.fillStyle = COLOR_HEX[t.color] || '#888';
    roundRect(ctx, x, y, s, s, 10);
    ctx.fill();

    // 하이라이트
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    roundRect(ctx, x + s * 0.12, y + s * 0.1, s * 0.5, s * 0.28, 6);
    ctx.fill();

    // 특수타일 마커
    if (t.isSpecial) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.fillStyle = '#ffffff';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 3;
      switch (t.special) {
        case SpecialType.ROCKET: {
          // 방향 화살표
          ctx.font = `bold ${s * 0.5}px sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(t.rocketDir === RocketDir.HORIZONTAL ? '↔' : '↕', 0, 0);
          break;
        }
        case SpecialType.BOMB: {
          ctx.beginPath();
          ctx.arc(0, 0, s * 0.22, 0, Math.PI * 2);
          ctx.stroke();
          ctx.font = `bold ${s * 0.3}px sans-serif`;
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText('B', 0, 1);
          break;
        }
        case SpecialType.LIGHT_BALL: {
          const grad = ctx.createRadialGradient(0, 0, 2, 0, 0, s * 0.4);
          grad.addColorStop(0, '#ffffff');
          grad.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(0, 0, s * 0.4, 0, Math.PI * 2);
          ctx.fill();
          break;
        }
        case SpecialType.PROPELLER: {
          ctx.font = `bold ${s * 0.5}px sans-serif`;
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText('✈', 0, 0);
          break;
        }
      }
      ctx.restore();
    }
  }
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
