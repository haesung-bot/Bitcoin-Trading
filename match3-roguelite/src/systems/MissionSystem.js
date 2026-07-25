// =============================================================
//  MissionSystem.js  -  스테이지 목표(미션) 생성 & 진행 판정
//  스펙 확장: 순수 점수 목표 -> 색상 수집 / 젤리 제거 등 다양화.
// =============================================================

import { MissionType, ACTIVE_COLORS, COLOR_HEX } from '../core/Constants.js';

const COLOR_NAME = {
  0: '빨강', 1: '파랑', 2: '초록', 3: '노랑', 4: '보라', 5: '주황',
};

export class MissionSystem {
  /**
   * 스테이지 번호로 미션을 만든다.
   * 로테이션: 점수 -> 색상수집 -> 젤리 제거 반복하며 난이도 상승.
   * @param {number} stage
   * @returns {{type:string,target:number,progress:number,color?:number,label:string,seedJelly?:number}}
   */
  static forStage(stage) {
    const cycle = (stage - 1) % 3;

    if (cycle === 0) {
      const target = 1000 + (stage - 1) * 400;
      return { type: MissionType.SCORE, target, progress: 0, label: `점수 ${target} 달성` };
    }
    if (cycle === 1) {
      const color = ACTIVE_COLORS[(Math.random() * ACTIVE_COLORS.length) | 0];
      const target = 20 + Math.floor(stage / 3) * 6;
      return {
        type: MissionType.COLLECT, color, target, progress: 0,
        label: `${COLOR_NAME[color]} 타일 ${target}개 수집`,
      };
    }
    // JELLY
    const target = 8 + Math.floor(stage / 3) * 4;
    return {
      type: MissionType.JELLY, target, progress: 0, seedJelly: target,
      label: `젤리 ${target}개 제거`,
    };
  }

  /** 미션 색의 hex (UI 표시용) */
  static colorHex(mission) {
    return mission.color != null ? COLOR_HEX[mission.color] : null;
  }

  /** 목표 달성 여부 */
  static isComplete(mission) {
    return mission.progress >= mission.target;
  }
}
