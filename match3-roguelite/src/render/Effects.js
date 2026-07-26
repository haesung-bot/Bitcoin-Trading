// =============================================================
//  Effects.js  -  연출 이펙트 레이어 (파티클/화면흔들림/특수연출/점수팝업)
//  * 게임 코어와 완전 분리. 엔진의 onFx 이벤트를 받아 시각효과만 담당.
//  * 보드 그리드와 독립적으로 살아있어, 이미 제거된 타일 자리도 연출 가능.
// =============================================================

import {
  TILE_SIZE, BOARD_PADDING, BOARD_ROWS, BOARD_COLS,
  COLOR_HEX, SpecialType, RocketDir, FX,
} from '../core/Constants.js';

const cellCenterX = (col) => BOARD_PADDING + col * TILE_SIZE + TILE_SIZE / 2;
const cellCenterY = (row) => BOARD_PADDING + row * TILE_SIZE + TILE_SIZE / 2;

export class Effects {
  constructor() {
    this.particles = [];   // 파편
    this.floaters = [];    // 점수 텍스트
    this.beams = [];       // 로켓 광선
    this.rings = [];       // 폭발/충격파 링
    this.flashes = [];     // 전체 섬광(라이트볼/콤보)
    this.banners = [];     // 콤보 메시지 배너 (오래 표시)
    this.shakeTime = 0;    // 남은 흔들림 시간
    this.shakeMag = 0;     // 흔들림 강도
    this.intensity = FX.GLOBAL_INTENSITY; // 전역 연출 세기 (Constants.FX에서 튜닝)
    this._reduced = typeof window !== 'undefined'
      && window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  // ---------- 엔진 fx 이벤트 처리 ----------
  handle(fx) {
    if (fx.kind === 'destroy') this._onDestroy(fx);
    else if (fx.kind === 'combo') this._onCombo(fx);
    else if (fx.kind === 'jelly') this._onJelly(fx);
    else if (fx.kind === 'timebonus') this._onTimeBonus(fx);
  }

  /** 스피드 시간 보너스: 초록 "-X.Xs" 텍스트가 떠오름 */
  _onTimeBonus(fx) {
    const x = cellCenterX(fx.at.col);
    const y = cellCenterY(fx.at.row) - TILE_SIZE * 0.5;
    this.floaters.push({
      x, y, text: `-${fx.seconds.toFixed(1)}s`,
      vy: -40, life: 1.0, max: 1.0, size: 22, color: '#42d97a',
    });
  }

  /** 젤리 파괴: 얼음빛 파편 튐 */
  _onJelly(fx) {
    for (const c of fx.cells) {
      this._burst(cellCenterX(c.col), cellCenterY(c.row), '#bdf0ff', 6);
    }
  }

  _onDestroy(fx) {
    // 파괴된 일반 타일마다 파편 폭발
    for (const t of fx.tiles) {
      this._burst(cellCenterX(t.col), cellCenterY(t.row), COLOR_HEX[t.color] || '#fff',
        fx.cascade > 1 ? FX.BURST_CASCADE : FX.BURST_BASE);
    }
    // 발동된 특수타일별 연출
    for (const s of fx.specials) this._specialFx(s);

    // 점수 팝업 (파괴 중심점)
    if (fx.score > 0 && fx.tiles.length) {
      const cx = avg(fx.tiles.map(t => cellCenterX(t.col)));
      const cy = avg(fx.tiles.map(t => cellCenterY(t.row)));
      this._floater(cx, cy, `+${fx.score}`, fx.cascade);
    }
    // 연쇄가 깊을수록 살짝 흔들림
    if (fx.cascade >= 2) this.shake(FX.SHAKE_CASCADE_STEP * fx.cascade, 0.18);
  }

  _onCombo(fx) {
    for (const t of fx.tiles) {
      this._burst(cellCenterX(t.col), cellCenterY(t.row), COLOR_HEX[t.color] || '#fff', 6);
    }
    for (const s of fx.specials) this._specialFx(s);

    let label, color;
    if (fx.combo === 'LIGHTBALL_LIGHTBALL') {
      this._flash('rgba(255,255,255,0.9)', 0.5);
      this.shake(FX.SHAKE_COMBO_LIGHTBALL, 0.5);
      label = '⚡ 라이트볼 콤보!'; color = '#ffffff';
    } else if (fx.combo === 'ROCKET_BOMB') {
      this._flash('rgba(255,210,63,0.5)', 0.35);
      this.shake(FX.SHAKE_COMBO_ROCKETBOMB, 0.45);
      // 발동 지점 기준 십자 광선
      this.beams.push(makeBeam(fx.at.row, fx.at.col, RocketDir.HORIZONTAL, '#ffd23f'));
      this.beams.push(makeBeam(fx.at.row, fx.at.col, RocketDir.VERTICAL, '#ffd23f'));
      label = '🚀💣 로켓 + 폭탄!'; color = '#ffd23f';
    } else {
      this.shake(FX.SHAKE_COMBO_ROCKETBOMB * 0.8, 0.35);
      label = '💥 콤보!'; color = '#ffd23f';
    }
    // 콤보 메시지 배너 (오래 표시)
    this._banner(label, color);
    // 콤보 점수 텍스트 (일반보다 오래 유지)
    if (fx.score > 0) {
      this._floater(cellCenterX(fx.at.col), cellCenterY(fx.at.row), `+${fx.score}`, 3, true, FX.COMBO_FLOATER_SEC);
    }
  }

  /** 콤보 배너 메시지 (화면 중앙 상단, 길게 표시) */
  _banner(text, color) {
    this.banners.push({ text, color, life: FX.COMBO_BANNER_SEC, max: FX.COMBO_BANNER_SEC });
  }

  /** 특수타일 종류별 시각 연출 */
  _specialFx(s) {
    const x = cellCenterX(s.col), y = cellCenterY(s.row);
    switch (s.type) {
      case SpecialType.ROCKET:
        this.beams.push(makeBeam(s.row, s.col, s.rocketDir, '#ffffff'));
        this.shake(FX.SHAKE_ROCKET, 0.2);
        break;
      case SpecialType.BOMB:
        this.rings.push(makeRing(x, y, TILE_SIZE * 2.2, '#ff9636'));
        this._burst(x, y, '#ff9636', 18);
        this.shake(FX.SHAKE_BOMB, 0.3);
        break;
      case SpecialType.LIGHT_BALL:
        this.rings.push(makeRing(x, y, TILE_SIZE * 4.5, '#ffffff'));
        this._flash('rgba(255,255,255,0.35)', 0.25);
        this.shake(FX.SHAKE_LIGHTBALL, 0.25);
        break;
      case SpecialType.PROPELLER:
        this._burst(x, y, '#42d97a', 14);
        this.shake(FX.SHAKE_ROCKET, 0.2);
        break;
    }
  }

  // ---------- 스폰 헬퍼 ----------
  _burst(x, y, color, count) {
    count = Math.round(count * this.intensity);
    if (this._reduced) count = Math.min(count, 4);
    for (let i = 0; i < count; i++) {
      const a = Math.random() * Math.PI * 2;
      const sp = 60 + Math.random() * 160;
      this.particles.push({
        x, y,
        vx: Math.cos(a) * sp,
        vy: Math.sin(a) * sp - 40,
        life: 0.4 + Math.random() * 0.35,
        max: 0.75,
        size: 3 + Math.random() * 4,
        color,
      });
    }
  }

  _floater(x, y, text, cascade, big = false, life = 0.9) {
    this.floaters.push({
      x, y, text,
      vy: -46,
      life, max: life,
      size: big ? 30 : (16 + Math.min(cascade, 5) * 3),
      color: cascade >= 3 ? '#ffd23f' : '#ffffff',
    });
  }

  _flash(color, dur) {
    if (this._reduced) return;
    this.flashes.push({ color, life: dur, max: dur });
  }

  /** 화면 흔들림 요청 (기존보다 강하면 갱신) */
  shake(mag, dur) {
    if (this._reduced) return;
    mag *= this.intensity;
    if (mag >= this.shakeMag || this.shakeTime <= 0) { this.shakeMag = mag; }
    this.shakeTime = Math.max(this.shakeTime, dur);
  }

  /** 현재 프레임의 흔들림 오프셋 */
  getShakeOffset() {
    if (this.shakeTime <= 0) return { x: 0, y: 0 };
    const m = this.shakeMag * (this.shakeTime / 0.4);
    return { x: (Math.random() * 2 - 1) * m, y: (Math.random() * 2 - 1) * m };
  }

  // ---------- 갱신 & 그리기 ----------
  update(dt) {
    if (this.shakeTime > 0) this.shakeTime -= dt;

    for (const p of this.particles) {
      p.life -= dt;
      p.vy += 520 * dt; // 중력
      p.x += p.vx * dt;
      p.y += p.vy * dt;
    }
    this.particles = this.particles.filter(p => p.life > 0);

    for (const f of this.floaters) { f.life -= dt; f.y += f.vy * dt; f.vy *= 0.92; }
    this.floaters = this.floaters.filter(f => f.life > 0);

    for (const b of this.beams) b.life -= dt;
    this.beams = this.beams.filter(b => b.life > 0);

    for (const r of this.rings) r.life -= dt;
    this.rings = this.rings.filter(r => r.life > 0);

    for (const fl of this.flashes) fl.life -= dt;
    this.flashes = this.flashes.filter(fl => fl.life > 0);

    for (const bn of this.banners) bn.life -= dt;
    this.banners = this.banners.filter(bn => bn.life > 0);
  }

  /** 보드/타일 위에 겹쳐 그린다 (renderer가 흔들림 변환 안에서 호출) */
  draw(ctx) {
    // 로켓 광선
    for (const b of this.beams) {
      const a = Math.max(0, b.life / b.max);
      ctx.save();
      ctx.globalAlpha = a;
      const grad = b.dir === RocketDir.HORIZONTAL
        ? ctx.createLinearGradient(0, b.y - b.thick, 0, b.y + b.thick)
        : ctx.createLinearGradient(b.x - b.thick, 0, b.x + b.thick, 0);
      grad.addColorStop(0, 'rgba(255,255,255,0)');
      grad.addColorStop(0.5, b.color);
      grad.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = grad;
      if (b.dir === RocketDir.HORIZONTAL) {
        ctx.fillRect(BOARD_PADDING, b.y - b.thick, BOARD_COLS * TILE_SIZE, b.thick * 2);
      } else {
        ctx.fillRect(b.x - b.thick, BOARD_PADDING, b.thick * 2, BOARD_ROWS * TILE_SIZE);
      }
      ctx.restore();
    }

    // 충격파 링
    for (const r of this.rings) {
      const t = 1 - r.life / r.max;
      const rad = r.maxR * easeOut(t);
      ctx.save();
      ctx.globalAlpha = Math.max(0, r.life / r.max) * 0.8;
      ctx.strokeStyle = r.color;
      ctx.lineWidth = 6 * (1 - t) + 1;
      ctx.beginPath();
      ctx.arc(r.x, r.y, rad, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // 파편
    for (const p of this.particles) {
      const a = Math.max(0, p.life / p.max);
      ctx.save();
      ctx.globalAlpha = a;
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size * a, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    // 점수 텍스트
    for (const f of this.floaters) {
      const a = Math.min(1, f.life / 0.4);
      ctx.save();
      ctx.globalAlpha = a;
      ctx.fillStyle = f.color;
      ctx.font = `bold ${f.size}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.lineWidth = 4;
      ctx.strokeStyle = 'rgba(0,0,0,0.55)';
      ctx.strokeText(f.text, f.x, f.y);
      ctx.fillText(f.text, f.x, f.y);
      ctx.restore();
    }
  }

  /** 전체 화면 섬광 + 콤보 배너 (흔들림 변환 밖에서, 캔버스 좌표로 그린다) */
  drawOverlay(ctx, w, h) {
    for (const fl of this.flashes) {
      ctx.save();
      ctx.globalAlpha = Math.max(0, fl.life / fl.max);
      ctx.fillStyle = fl.color;
      ctx.fillRect(0, 0, w, h);
      ctx.restore();
    }

    // 콤보 배너 (여러 개면 아래로 쌓임). 등장 팝 + 마지막 0.5초 페이드.
    this.banners.forEach((bn, i) => {
      const t = 1 - bn.life / bn.max;              // 0 -> 1 경과
      const appear = Math.min(1, t / 0.12);         // 등장 이징
      const alpha = bn.life < 0.5 ? bn.life / 0.5 : 1;
      const scale = 0.7 + 0.3 * appear;
      const cx = w / 2;
      const cy = h * 0.3 + i * 46;
      ctx.save();
      ctx.globalAlpha = Math.max(0, alpha);
      ctx.translate(cx, cy);
      ctx.scale(scale, scale);
      ctx.font = 'bold 40px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.lineWidth = 7;
      ctx.strokeStyle = 'rgba(0,0,0,0.6)';
      ctx.strokeText(bn.text, 0, 0);
      ctx.fillStyle = bn.color;
      ctx.fillText(bn.text, 0, 0);
      ctx.restore();
    });
  }
}

// ---- 팩토리 ----
function makeBeam(row, col, dir, color) {
  return {
    dir, color, life: 0.35, max: 0.35, thick: TILE_SIZE * 0.4,
    x: cellCenterX(col), y: cellCenterY(row),
  };
}
function makeRing(x, y, maxR, color) {
  return { x, y, maxR, color, life: 0.45, max: 0.45 };
}
function avg(arr) { return arr.reduce((s, v) => s + v, 0) / arr.length; }
function easeOut(t) { return 1 - (1 - t) * (1 - t); }
