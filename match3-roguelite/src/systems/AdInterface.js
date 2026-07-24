// =============================================================
//  AdInterface.js  -  광고 연동 인터페이스 (IAP 없음, 보상형/전면 광고만)
//  스펙 #5: 실제 광고 SDK(AdMob/Unity Ads 등)는 여기서 어댑터로 교체.
//  게임 코어는 이 인터페이스만 알고, SDK 세부는 모른다.
// =============================================================

import { STAGE_DEFAULTS } from '../core/Constants.js';

/**
 * 광고 제공자 어댑터가 구현해야 할 형태:
 *   showRewarded(placement): Promise<boolean>  // 시청 완료 시 true
 *   showInterstitial(placement): Promise<void>
 * 기본 제공은 개발용 MockAdProvider.
 */
export class AdInterface {
  /**
   * @param {GameEngine} engine
   * @param {object} provider 광고 제공자 어댑터
   */
  constructor(engine, provider = new MockAdProvider()) {
    this.engine = engine;
    this.provider = provider;
  }

  /**
   * 이동 소진 -> +5 이동 보상형 광고 팝업.
   * @returns {Promise<boolean>} 보상 지급 여부
   */
  async onOutOfMoves() {
    const rewarded = await this.provider.showRewarded('extra_moves');
    if (rewarded) {
      this.engine.grantRewardMoves(STAGE_DEFAULTS.REWARD_AD_MOVES);
      return true;
    }
    return false;
  }

  /**
   * 하트 소진 -> 하트 리필 보상형 광고 팝업.
   * @returns {Promise<boolean>}
   */
  async onHeartEmpty() {
    const rewarded = await this.provider.showRewarded('heart_refill');
    if (rewarded) {
      this.engine.grantHeartRefill();
      return true;
    }
    return false;
  }

  /**
   * 스테이지 클리어 -> 3의 배수 스테이지에서 전면광고.
   * @param {number} stageNum
   */
  async onStageClear(stageNum) {
    if (stageNum % 3 === 0) {
      await this.provider.showInterstitial('stage_clear');
    }
  }
}

/** 개발/테스트용 가짜 광고 제공자 (항상 보상 지급) */
export class MockAdProvider {
  showRewarded(placement) {
    console.log(`[MockAd] 보상형 광고 시청: ${placement}`);
    return Promise.resolve(true);
  }
  showInterstitial(placement) {
    console.log(`[MockAd] 전면 광고 노출: ${placement}`);
    return Promise.resolve();
  }
}
