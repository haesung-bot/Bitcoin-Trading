// =============================================================
//  StateMachine.js  -  범용 유한 상태머신(FSM)
//  각 상태는 { enter?, update?, exit? } 핸들러를 가진다.
//  update(dt)가 다음 상태 이름을 반환하면 전이(transition)한다.
// =============================================================

export class StateMachine {
  /**
   * @param {string} initial 시작 상태 이름
   * @param {Object<string, {enter?:Function, update?:Function, exit?:Function}>} states
   * @param {{onChange?:(from:string,to:string)=>void}} [opts]
   */
  constructor(initial, states, opts = {}) {
    this.states = states;
    this.current = initial;
    this.onChange = opts.onChange || null;
    this.timeInState = 0;
    const s = this.states[initial];
    if (s && s.enter) s.enter();
  }

  /** 명시적 상태 전이 */
  change(to, payload) {
    if (to === this.current) return;
    const from = this.current;
    const prev = this.states[from];
    if (prev && prev.exit) prev.exit(to);

    this.current = to;
    this.timeInState = 0;
    const next = this.states[to];
    if (next && next.enter) next.enter(payload, from);
    if (this.onChange) this.onChange(from, to);
  }

  /**
   * 매 프레임 호출. 현재 상태의 update가 상태 이름 문자열을 반환하면 전이한다.
   * @param {number} dt
   */
  update(dt) {
    this.timeInState += dt;
    const s = this.states[this.current];
    if (s && s.update) {
      const next = s.update(dt, this.timeInState);
      if (next && next !== this.current) this.change(next);
    }
  }

  is(name) { return this.current === name; }
}
