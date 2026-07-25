// =============================================================
//  SoundManager.js  -  Web Audio 합성 효과음 (외부 음원 파일 없음)
//  * 모든 소리를 오실레이터/노이즈로 실시간 합성 -> 자체 완결(에셋 0).
//  * 브라우저 자동재생 정책 대응: 첫 사용자 입력에서 resume().
// =============================================================

export class SoundManager {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.muted = false;
    this._ready = false;
  }

  /** 첫 사용자 제스처에서 호출 (오디오 컨텍스트 활성화) */
  unlock() {
    if (this._ready) { this._resume(); return; }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = 0.5;
    this.master.connect(this.ctx.destination);
    this._ready = true;
    this._resume();
  }

  _resume() {
    if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume();
  }

  setMuted(m) {
    this.muted = m;
    if (this.master) this.master.gain.value = m ? 0 : 0.5;
  }
  toggleMute() { this.setMuted(!this.muted); return this.muted; }

  // ---------- 저수준 합성 헬퍼 ----------

  /** 단순 음 하나 (엔벨로프 포함) */
  _tone(freq, dur, { type = 'sine', vol = 0.3, attack = 0.005, decay = null, when = 0 } = {}) {
    if (!this._ready || this.muted) return;
    const t0 = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(vol, t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + (decay ?? dur));
    osc.connect(g).connect(this.master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  /** 주파수 스윕 (로켓/라이트볼) */
  _sweep(f0, f1, dur, { type = 'sawtooth', vol = 0.25, when = 0 } = {}) {
    if (!this._ready || this.muted) return;
    const t0 = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(f0, t0);
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, f1), t0 + dur);
    g.gain.setValueAtTime(vol, t0);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(this.master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  /** 노이즈 버스트 (폭발/타격) */
  _noise(dur, { vol = 0.3, lp = 1200, when = 0 } = {}) {
    if (!this._ready || this.muted) return;
    const t0 = this.ctx.currentTime + when;
    const n = Math.floor(this.ctx.sampleRate * dur);
    const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < n; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    const filter = this.ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = lp;
    const g = this.ctx.createGain();
    g.gain.value = vol;
    src.connect(filter).connect(g).connect(this.master);
    src.start(t0);
  }

  // ---------- 게임 효과음 ----------

  swap()    { this._tone(440, 0.08, { type: 'triangle', vol: 0.18 }); this._tone(620, 0.08, { type: 'triangle', vol: 0.14, when: 0.04 }); }
  invalid() { this._tone(180, 0.16, { type: 'sawtooth', vol: 0.2 }); this._tone(140, 0.16, { type: 'sawtooth', vol: 0.16, when: 0.05 }); }

  /** 매치 팝: 연쇄가 깊을수록 음이 높아진다 */
  pop(cascade = 1) {
    const base = 500 + Math.min(cascade, 8) * 70;
    this._tone(base, 0.12, { type: 'sine', vol: 0.22 });
    this._tone(base * 1.5, 0.1, { type: 'sine', vol: 0.12, when: 0.02 });
  }

  jelly()   { this._tone(900, 0.07, { type: 'triangle', vol: 0.18 }); this._noise(0.08, { vol: 0.1, lp: 4000 }); }
  rocket()  { this._sweep(1200, 200, 0.28, { type: 'sawtooth', vol: 0.22 }); this._noise(0.28, { vol: 0.12, lp: 2500 }); }
  bomb()    { this._noise(0.35, { vol: 0.4, lp: 900 }); this._tone(90, 0.4, { type: 'sine', vol: 0.35 }); }
  lightball(){ [0, 1, 2, 3].forEach(i => this._tone(660 * Math.pow(1.26, i), 0.25, { type: 'sine', vol: 0.16, when: i * 0.05 })); }
  propeller(){ this._sweep(300, 700, 0.2, { type: 'square', vol: 0.14 }); }

  combo() {
    // 강한 화음 + 저음 붐
    [523, 659, 784, 1046].forEach((f, i) => this._tone(f, 0.5, { type: 'sine', vol: 0.16, when: i * 0.02 }));
    this._noise(0.4, { vol: 0.3, lp: 1000 });
    this._tone(80, 0.5, { type: 'sine', vol: 0.3 });
  }

  stageClear() {
    // 상승 아르페지오 징글
    [523, 659, 784, 1046, 1318].forEach((f, i) =>
      this._tone(f, 0.3, { type: 'triangle', vol: 0.2, when: i * 0.09 }));
  }

  perk()   { [784, 988, 1318].forEach((f, i) => this._tone(f, 0.25, { type: 'sine', vol: 0.18, when: i * 0.07 })); }
  reward() { [659, 988].forEach((f, i) => this._tone(f, 0.18, { type: 'square', vol: 0.16, when: i * 0.08 })); }
  shuffle(){ this._sweep(200, 600, 0.25, { type: 'triangle', vol: 0.14 }); }
}
