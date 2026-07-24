// =============================================================
//  PerkSystem.js  -  로그라이트 퍽(패시브 스킬) 시스템
//  스펙 #4: 3스테이지 클리어마다 3개 선택지 제공.
// =============================================================

/**
 * 각 퍽은 { id, name, desc, apply(engine) } 구조.
 * apply()는 engine.perks 수치를 조정한다 (GameEngine이 실제 효과 반영).
 */
export const PERK_POOL = [
  {
    id: 'bomb_range',
    name: '강화 화약',
    desc: '폭탄 폭발 범위 +15%',
    apply: (engine) => { engine.perks.bombRangeBonus += 0.15; },
  },
  {
    id: 'extra_move_4combo',
    name: '전술적 여유',
    desc: '4콤보 생성 시 이동 +1',
    apply: (engine) => { engine.perks.extraMoveOn4 += 1; },
  },
  {
    id: 'score_boost',
    name: '점수 증폭기',
    desc: '획득 점수 +20%',
    apply: (engine) => { engine.perks.scoreMultiplier += 0.2; },
  },
  {
    id: 'starting_moves',
    name: '넉넉한 준비',
    desc: '스테이지 시작 이동 +3',
    apply: (engine) => { engine.perks._startMovesBonus = (engine.perks._startMovesBonus || 0) + 3; engine.movesLeft += 3; },
  },
  {
    id: 'cascade_master',
    name: '연쇄의 달인',
    desc: '연쇄 보너스 점수 2배',
    apply: (engine) => { engine.perks.scoreMultiplier += 0.15; },
  },
  {
    id: 'lucky_rocket',
    name: '행운의 로켓',
    desc: '4콤보 생성 시 이동 +1 (중첩)',
    apply: (engine) => { engine.perks.extraMoveOn4 += 1; },
  },
];

export class PerkSystem {
  constructor(pool = PERK_POOL) {
    this.pool = pool;
    this.acquired = []; // 이미 획득한 퍽 id (중복 방지 옵션)
  }

  /**
   * 무작위 3개 선택지 생성 (이미 가진 퍽 제외).
   * @param {number} count
   * @returns {Array} 퍽 선택지
   */
  rollChoices(count = 3) {
    const available = this.pool.filter(p => !this.acquired.includes(p.id) || p.repeatable);
    const shuffled = [...available].sort(() => Math.random() - 0.5);
    return shuffled.slice(0, count);
  }

  /** 선택 확정 */
  choose(perk, engine) {
    this.acquired.push(perk.id);
    engine.applyPerk(perk);
  }
}
