// =============================================================
//  main.js  -  엔트리 포인트: 엔진 + 렌더러 + 입력 + 시스템 배선
// =============================================================

import { GameEngine } from './core/GameEngine.js';
import { Renderer } from './render/Renderer.js';
import { Effects } from './render/Effects.js';
import { InputController } from './render/InputController.js';
import { AdInterface } from './systems/AdInterface.js';
import { PerkSystem } from './systems/PerkSystem.js';
import { SoundManager } from './systems/SoundManager.js';
import { MissionSystem } from './systems/MissionSystem.js';
import { MissionType } from './core/Constants.js';

const canvas = document.getElementById('game');
const hud = {
  stage: document.getElementById('stage'),
  moves: document.getElementById('moves'),
  score: document.getElementById('score'),
  time: document.getElementById('time'),
  record: document.getElementById('record'),
  missionLabel: document.getElementById('mission-label'),
  missionBar: document.getElementById('mission-bar'),
  missionCount: document.getElementById('mission-count'),
  missionDot: document.getElementById('mission-dot'),
  log: document.getElementById('log'),
  muteBtn: document.getElementById('mute-btn'),
};

// --- 타임어택 기록 (localStorage, 스테이지별 최고=최단 시간) ---
let _newRecord = false; // 직전 클리어가 신기록이었는지 (로그 표기용) — 엔진 생성 전에 선언
const BEST_KEY = (stage) => `m3_best_time_${stage}`;
// localStorage 접근을 방어적으로 (file:// / 프라이빗모드 등에서 예외 대비)
function safeGet(k) { try { return localStorage.getItem(k); } catch { return _memStore[k] ?? null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { _memStore[k] = v; } }
const _memStore = {}; // localStorage 불가 시 메모리 폴백
function getBest(stage) {
  const v = safeGet(BEST_KEY(stage));
  return v == null ? null : parseFloat(v);
}
function saveBest(stage, time) {
  const prev = getBest(stage);
  if (prev == null || time < prev) { safeSet(BEST_KEY(stage), String(time)); return true; }
  return false;
}
/** 초 -> "M:SS.d" */
function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const d = Math.floor((sec * 10) % 10);
  return `${m}:${String(s).padStart(2, '0')}.${d}`;
}
function refreshRecord(stage) {
  const best = getBest(stage);
  hud.record.textContent = best == null
    ? '🏆 이 스테이지 최고기록: --'
    : `🏆 이 스테이지 최고기록: ${fmtTime(best)}`;
}
const perkOverlay = document.getElementById('perk-overlay');
const perkChoices = document.getElementById('perk-choices');

// --- 시스템 인스턴스 ---
const perkSystem = new PerkSystem();
const effects = new Effects();
const sound = new SoundManager();
let adInterface; // engine 생성 후 배선

// --- 엔진 생성 (외부 콜백 연결) ---
const engine = new GameEngine({
  onStateChange: (to) => { /* 디버그용: console.log(to) */ },
  onEvent: (e) => { handleRecords(e); logEvent(e); soundForEvent(e); },
  onFx: (fx) => { effects.handle(fx); soundForFx(fx); },
  onOutOfMoves: () => adInterface.onOutOfMoves(),       // 스펙 #5
  onInterstitialCheck: (stage) => adInterface.onStageClear(stage),
  onPerkSelection: () => showPerkSelection(),           // 스펙 #4
});

adInterface = new AdInterface(engine);

const renderer = new Renderer(canvas, engine, effects);
new InputController(canvas, engine, renderer);

// --- 오디오 잠금 해제 (브라우저 자동재생 정책) ---
function unlockAudio() { sound.unlock(); }
['pointerdown', 'touchstart', 'keydown'].forEach(ev =>
  window.addEventListener(ev, unlockAudio, { once: true }));

// --- 음소거 토글 ---
if (hud.muteBtn) {
  hud.muteBtn.onclick = () => {
    sound.unlock();
    const muted = sound.toggleMute();
    hud.muteBtn.textContent = muted ? '🔇' : '🔊';
  };
}

// --- 소리 라우팅 ---
function soundForEvent(e) {
  switch (e.type) {
    case 'swap': sound.swap(); break;
    case 'invalid-swap': sound.invalid(); break;
    case 'stage-clear': sound.stageClear(); break;
    case 'perk-applied': sound.perk(); break;
    case 'reward-moves': sound.reward(); break;
    case 'shuffle': sound.shuffle(); break;
  }
}

// --- 타임어택 기록 처리 ---
function handleRecords(e) {
  if (e.type === 'stage-clear') {
    _newRecord = saveBest(e.stage, e.time);
  } else if (e.type === 'mission') {
    refreshRecord(e.stage); // 새 스테이지 진입 시 그 스테이지 기록으로 갱신
  }
}
function soundForFx(fx) {
  if (fx.kind === 'destroy') {
    sound.pop(fx.cascade || 1);
    if (fx.jellyCleared) sound.jelly();
    for (const s of (fx.specials || [])) {
      if (s.type === 'ROCKET') sound.rocket();
      else if (s.type === 'BOMB') sound.bomb();
      else if (s.type === 'LIGHT_BALL') sound.lightball();
      else if (s.type === 'PROPELLER') sound.propeller();
    }
  } else if (fx.kind === 'combo') {
    sound.combo();
  }
}

// --- HUD 갱신 ---
function updateHud() {
  hud.stage.textContent = engine.stage;
  hud.moves.textContent = engine.movesLeft;
  hud.score.textContent = engine.score;
  hud.time.textContent = fmtTime(engine.stageTime);
  updateMissionHud();
}

function updateMissionHud() {
  const m = engine.mission;
  if (!m) return;
  hud.missionLabel.textContent = m.label;
  const pct = Math.min(100, Math.round((m.progress / m.target) * 100));
  hud.missionBar.style.width = pct + '%';
  hud.missionCount.textContent = `${Math.min(m.progress, m.target)} / ${m.target}`;
  // 미션 종류별 색 표시
  const hex = MissionSystem.colorHex(m);
  if (m.type === MissionType.COLLECT && hex) {
    hud.missionDot.style.background = hex;
    hud.missionDot.style.display = 'inline-block';
  } else if (m.type === MissionType.JELLY) {
    hud.missionDot.style.background = 'rgba(180,240,255,0.9)';
    hud.missionDot.style.display = 'inline-block';
  } else {
    hud.missionDot.style.display = 'none';
  }
}

function logEvent(e) {
  if (!hud.log) return;
  // 타입별로 필요한 필드만 참조 (전체 즉시평가 금지 -> 필드 누락 크래시 방지)
  let msg = null;
  switch (e.type) {
    case 'stage-clear': msg = `🎉 스테이지 ${e.stage} 클리어! ⏱ ${fmtTime(e.time)}${_newRecord ? ' 🏆 신기록!' : ''}`; break;
    case 'mission': msg = `🎯 새 목표: ${e.mission.label}`; break;
    case 'combo': msg = `💥 콤보 ${e.combo} → ${e.cleared}칸 제거`; break;
    case 'out-of-moves': msg = '⛔ 이동 소진! 광고 보상 확인'; break;
    case 'reward-moves': msg = `➕ 광고 보상: 이동 +${e.amount}`; break;
    case 'shuffle': msg = '🔀 이동 불가 → 보드 셔플'; break;
    case 'perk-applied': msg = `✨ 퍽 획득: ${e.perk}`; break;
  }
  if (msg) {
    const line = document.createElement('div');
    line.textContent = msg;
    hud.log.prepend(line);
    while (hud.log.childElementCount > 8) hud.log.lastChild.remove();
  }
}

// --- 퍽 선택 UI ---
function showPerkSelection() {
  engine.setPaused(true); // 퍽 고르는 동안 타이머 정지 (공정성)
  const choices = perkSystem.rollChoices(3);
  perkChoices.innerHTML = '';
  choices.forEach((perk) => {
    const btn = document.createElement('button');
    btn.className = 'perk-card';
    btn.innerHTML = `<strong>${perk.name}</strong><span>${perk.desc}</span>`;
    btn.onclick = () => {
      perkSystem.choose(perk, engine);
      perkOverlay.classList.add('hidden');
      engine.setPaused(false); // 선택 완료 -> 타이머 재개
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
  effects.update(dt);
  renderer.draw();
  updateHud();
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

// 디버그: 전역 노출
window.__game = { engine, renderer, effects, sound, perkSystem };
