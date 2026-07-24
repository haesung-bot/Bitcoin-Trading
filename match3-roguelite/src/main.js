// =============================================================
//  main.js  -  엔트리 포인트: 엔진 + 렌더러 + 입력 + 시스템 배선
// =============================================================

import { GameEngine } from './core/GameEngine.js';
import { Renderer } from './render/Renderer.js';
import { InputController } from './render/InputController.js';
import { AdInterface } from './systems/AdInterface.js';
import { PerkSystem } from './systems/PerkSystem.js';

const canvas = document.getElementById('game');
const hud = {
  stage: document.getElementById('stage'),
  moves: document.getElementById('moves'),
  score: document.getElementById('score'),
  target: document.getElementById('target'),
  hearts: document.getElementById('hearts'),
  log: document.getElementById('log'),
};
const perkOverlay = document.getElementById('perk-overlay');
const perkChoices = document.getElementById('perk-choices');

// --- 시스템 인스턴스 ---
const perkSystem = new PerkSystem();
let adInterface; // engine 생성 후 배선

// --- 엔진 생성 (외부 콜백 연결) ---
const engine = new GameEngine({
  onStateChange: (to) => { /* 디버그용: console.log(to) */ },
  onEvent: (e) => logEvent(e),
  onOutOfMoves: () => {
    // 스펙 #5: 이동 소진 -> 보상형 광고
    adInterface.onOutOfMoves();
  },
  onInterstitialCheck: (stage) => {
    // 스펙 #5: 3의 배수 스테이지 전면광고
    adInterface.onStageClear(stage);
  },
  onPerkSelection: () => {
    // 스펙 #4: 퍽 3개 선택 UI
    showPerkSelection();
  },
});

adInterface = new AdInterface(engine);

const renderer = new Renderer(canvas, engine);
new InputController(canvas, engine, renderer);

// --- HUD 갱신 ---
function updateHud() {
  hud.stage.textContent = engine.stage;
  hud.moves.textContent = engine.movesLeft;
  hud.score.textContent = engine.score;
  hud.target.textContent = engine.targetScore;
  hud.hearts.textContent = '❤'.repeat(engine.hearts);
}

function logEvent(e) {
  if (!hud.log) return;
  const labels = {
    'stage-clear': `🎉 스테이지 ${e.stage} 클리어!`,
    'combo': `💥 콤보 ${e.combo} → ${e.cleared}칸 제거`,
    'out-of-moves': `⛔ 이동 소진! 광고 보상 확인`,
    'reward-moves': `➕ 광고 보상: 이동 +${e.amount}`,
    'shuffle': `🔀 이동 불가 → 보드 셔플`,
    'perk-applied': `✨ 퍽 획득: ${e.perk}`,
  };
  if (labels[e.type]) {
    const line = document.createElement('div');
    line.textContent = labels[e.type];
    hud.log.prepend(line);
    while (hud.log.childElementCount > 8) hud.log.lastChild.remove();
  }
}

// --- 퍽 선택 UI ---
function showPerkSelection() {
  const choices = perkSystem.rollChoices(3);
  perkChoices.innerHTML = '';
  choices.forEach((perk) => {
    const btn = document.createElement('button');
    btn.className = 'perk-card';
    btn.innerHTML = `<strong>${perk.name}</strong><span>${perk.desc}</span>`;
    btn.onclick = () => {
      perkSystem.choose(perk, engine);
      perkOverlay.classList.add('hidden');
    };
    perkChoices.appendChild(btn);
  });
  perkOverlay.classList.remove('hidden');
}

// --- 게임 루프 ---
let last = performance.now();
function loop(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  engine.update(dt);
  renderer.draw();
  updateHud();
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

// 디버그: 전역 노출
window.__game = { engine, renderer, perkSystem };
