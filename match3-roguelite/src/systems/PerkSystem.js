// =============================================================
//  PerkSystem.js  -  로그라이트 퍽(패시브 스킬) 시스템
//  스펙 #4: 3스테이지 클리어마다 3개 선택지 제공.
// =============================================================

/**
 * 각 퍽은 { id, name, desc, apply(engine) } 구조.
 * apply()는 engine.perks 수치를 조정한다 (GameEngine이 실제 효과 반영).
 */
export const PERK_POOL = [
  // --- 타임어택 특화 (신규) ---
  {
    id: 'combo_boost', name: '콤보 증폭기', icon: '💥', repeatable: true,
    desc: '콤보 시간 보너스 +1초',
    apply: (engine) => { engine.perks.comboBonusExtra += 1; },
  },
  {
    id: 'cascade_speed', name: '연쇄 가속', icon: '⚡', repeatable: true,
    desc: '연쇄 시간 보너스 +50%',
    apply: (engine) => { engine.perks.cascadeTimeMult += 0.5; },
  },
  {
    id: 'time_saver', name: '기록 단축', icon: '⏱️', repeatable: true,
    desc: '스테이지 클리어 시 기록 -1.5초',
    apply: (engine) => { engine.perks.clearCredit += 1.5; },
  },
  // --- 보드/특수타일 (신규) ---
  {
    id: 'big_bomb', name: '대형 폭탄', icon: '💣', repeatable: true,
    desc: '폭탄 폭발 범위 확대 (최대 7x7)',
    apply: (engine) => { engine.perks.bombRadius = Math.min(3, engine.perks.bombRadius + 1); },
  },
  {
    id: 'lucky_special', name: '행운의 시작', icon: '🍀', repeatable: true,
    desc: '스테이지 시작 시 특수타일 +1개',
    apply: (engine) => { engine.perks.startSpecials += 1; },
  },
  {
    id: 'propeller_target', name: '정밀 유도', icon: '✈️', repeatable: true,
    desc: '프로펠러 타격 목표 +1',
    apply: (engine) => { engine.perks.propellerTargets += 1; },
  },
  // --- 기존 ---
  {
    id: 'extra_move_4combo', name: '전술적 여유', icon: '🎯', repeatable: true,
    desc: '4콤보 생성 시 이동 +1',
    apply: (engine) => { engine.perks.extraMoveOn4 += 1; },
  },
  {
    id: 'score_boost', name: '점수 증폭기', icon: '📈', repeatable: true,
    desc: '획득 점수 +20%',
    apply: (engine) => { engine.perks.scoreMultiplier += 0.2; },
  },
  {
    id: 'starting_moves', name: '넉넉한 준비', icon: '➕', repeatable: true,
    desc: '스테이지 시작 이동 +3',
    apply: (engine) => { engine.perks._startMovesBonus += 3; engine.movesLeft += 3; },
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
