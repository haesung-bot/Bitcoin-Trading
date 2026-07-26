// 코어 로직 헤드리스 검증 (브라우저 불필요)
import { Board } from '../src/core/Board.js';
import { MatchDetector } from '../src/core/MatchDetector.js';
import { GameEngine } from '../src/core/GameEngine.js';
import { TileColor, SpecialType, RocketDir, MissionType } from '../src/core/Constants.js';
import { Tile } from '../src/core/Tile.js';
import { MissionSystem } from '../src/systems/MissionSystem.js';

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log('  ✓', msg); }
  else { fail++; console.log('  ✗ FAIL:', msg); }
}

// 헬퍼: 색 배열로 보드 강제 세팅
function setBoard(board, colors) {
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++)
      board.grid[r][c] = new Tile(colors[r][c], r, c);
}
function uniformExcept() {
  // 매치가 절대 안 생기는 배경 패턴: (r+c)%3 -> 색 0,1,2 만 사용.
  //   가로/세로 연속 3칸은 항상 서로 다른 값이라 매치 불가.
  // 주입하는 테스트 도형은 색 3,4,5(YELLOW/PURPLE/ORANGE)만 써서
  //   배경(0,1,2)과 절대 충돌하지 않게 격리한다.
  const g = [];
  for (let r = 0; r < 8; r++) {
    const row = [];
    for (let c = 0; c < 8; c++) row.push((r + c) % 3);
    g.push(row);
  }
  return g;
}

console.log('\n[1] 초기 보드는 매치가 없어야 한다');
{
  const b = new Board();
  assert(!MatchDetector.hasAnyMatch(b), '새 보드에 즉시 매치 없음');
}

console.log('\n[2] Match-3 감지');
{
  const b = new Board();
  const g = uniformExcept();
  g[4][2] = TileColor.YELLOW; g[4][3] = TileColor.YELLOW; g[4][4] = TileColor.YELLOW;
  setBoard(b, g);
  const m = MatchDetector.findMatches(b);
  assert(m.cells.has('4,2') && m.cells.has('4,3') && m.cells.has('4,4'), '가로 3매치 셀 감지');
  assert(m.spawns.length === 0, 'Match-3은 특수타일 없음');
}

console.log('\n[3] Match-4 일자 -> ROCKET');
{
  const b = new Board();
  const g = uniformExcept();
  for (let c = 1; c <= 4; c++) g[3][c] = TileColor.PURPLE;
  setBoard(b, g);
  const m = MatchDetector.findMatches(b);
  const rocket = m.spawns.find(s => s.type === SpecialType.ROCKET);
  assert(!!rocket, '4일자 매치 -> ROCKET 스폰');
  assert(rocket && rocket.rocketDir === RocketDir.HORIZONTAL, '가로매치 -> 가로 로켓');
}

console.log('\n[4] Match-5 일자 -> LIGHT_BALL');
{
  const b = new Board();
  const g = uniformExcept();
  for (let c = 1; c <= 5; c++) g[2][c] = TileColor.ORANGE;
  setBoard(b, g);
  const m = MatchDetector.findMatches(b);
  assert(m.spawns.some(s => s.type === SpecialType.LIGHT_BALL), '5일자 -> LIGHT_BALL');
}

console.log('\n[5] L자 매치 -> BOMB');
{
  const b = new Board();
  const g = uniformExcept();
  // 세로 3 + 가로 3 교차 (L자)
  g[2][2] = TileColor.YELLOW; g[3][2] = TileColor.YELLOW; g[4][2] = TileColor.YELLOW;
  g[4][3] = TileColor.YELLOW; g[4][4] = TileColor.YELLOW;
  setBoard(b, g);
  const m = MatchDetector.findMatches(b);
  assert(m.spawns.some(s => s.type === SpecialType.BOMB), 'L자 매치 -> BOMB');
}

console.log('\n[6] 2x2 박스 -> PROPELLER');
{
  const b = new Board();
  const g = uniformExcept();
  g[1][1] = TileColor.PURPLE; g[1][2] = TileColor.PURPLE;
  g[2][1] = TileColor.PURPLE; g[2][2] = TileColor.PURPLE;
  setBoard(b, g);
  const m = MatchDetector.findMatches(b);
  assert(m.spawns.some(s => s.type === SpecialType.PROPELLER), '2x2 -> PROPELLER');
}

console.log('\n[7] 로켓 발동 -> 한 줄 전체 제거');
{
  const b = new Board();
  setBoard(b, uniformExcept());
  const rocket = b.grid[3][3];
  rocket.special = SpecialType.ROCKET;
  rocket.rocketDir = RocketDir.HORIZONTAL;
  const affected = b.activateSpecial(rocket);
  assert(affected.size === 8, '가로 로켓 -> 8칸(한 행) 제거');
}

console.log('\n[8] 라이트볼 발동 -> 같은 색 전부 제거');
{
  const b = new Board();
  const g = uniformExcept();
  // RED 몇 개 심기
  g[0][0] = TileColor.YELLOW; g[5][5] = TileColor.YELLOW; g[7][1] = TileColor.YELLOW;
  setBoard(b, g);
  const lb = b.grid[0][0];
  lb.special = SpecialType.LIGHT_BALL;
  const affected = b.activateSpecial(lb);
  assert(affected.has('5,5') && affected.has('7,1'), '라이트볼 -> 흩어진 같은 색도 제거');
}

console.log('\n[9] 중력 + 리필로 빈 칸이 채워진다');
{
  const b = new Board();
  setBoard(b, uniformExcept());
  // 한 열 비우기
  for (let r = 0; r < 8; r++) { b.grid[r][0].dying = true; b.grid[r][0] = null; }
  b.applyGravity();
  b.refill();
  let filled = true;
  for (let r = 0; r < 8; r++) if (!b.grid[r][0]) filled = false;
  assert(filled, '중력+리필 후 모든 칸이 채워짐');
}

console.log('\n[10] 엔진 상태머신: 유효 스왑이 매치->파괴->리필 사이클을 돈다');
{
  const events = [];
  const engine = new GameEngine({ onEvent: (e) => events.push(e.type), onStateChange: () => {} });
  // 매치 되도록 강제 세팅: (4,2),(4,3) RED, (5,4) RED 를 (4,4)로 올리면 3매치
  const g = uniformExcept();
  g[4][2] = TileColor.YELLOW; g[4][3] = TileColor.YELLOW; g[5][4] = TileColor.YELLOW; g[4][4] = TileColor.PURPLE;
  setBoard(engine.board, g);
  const a = engine.board.grid[4][4]; // BLUE
  const bTile = engine.board.grid[5][4]; // RED (아래 인접)
  const movesBefore = engine.movesLeft;
  const ok = engine.requestSwap(a, bTile);
  assert(ok, '인접 스왑 요청 접수');
  // 여러 프레임 진행 (애니메이션 소요)
  let states = new Set();
  for (let i = 0; i < 600; i++) { engine.update(1 / 30); states.add(engine.state); }
  assert(states.has('DESTROY'), 'DESTROY 상태를 거침');
  assert(events.includes('score'), '점수 이벤트 발생');
  assert(engine.movesLeft === movesBefore - 1, '유효 스왑으로 이동 1 소비');
}

console.log('\n[11] 잘못된 스왑은 되돌아오고 이동을 소비하지 않는다');
{
  const engine = new GameEngine({ onStateChange: () => {} });
  const g = uniformExcept();
  // 매치 안 생기게: 인접 두 칸을 서로 다른 색, 스왑해도 매치 없게
  g[0][0] = TileColor.YELLOW; g[0][1] = TileColor.PURPLE;
  // 주변을 확실히 다르게
  g[0][2] = TileColor.ORANGE; g[1][0] = TileColor.ORANGE; g[1][1] = TileColor.YELLOW;
  setBoard(engine.board, g);
  const before = engine.movesLeft;
  engine.requestSwap(engine.board.grid[0][0], engine.board.grid[0][1]);
  for (let i = 0; i < 200; i++) engine.update(1 / 30);
  assert(engine.movesLeft === before, '무효 스왑은 이동 소비 안 함');
}

console.log('\n[12] 라이트볼+라이트볼 콤보 -> 보드 전체 제거');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {} });
  setBoard(engine.board, uniformExcept());
  const a = engine.board.grid[3][3];
  const bTile = engine.board.grid[3][4];
  a.special = SpecialType.LIGHT_BALL;
  bTile.special = SpecialType.LIGHT_BALL;
  engine.requestSwap(a, bTile);
  for (let i = 0; i < 100; i++) engine.update(1 / 30);
  // 보드가 리필되긴 하지만 콤보 이벤트로 대규모 제거가 일어났는지 점수로 확인
  assert(engine.score > 0, '라이트볼 콤보로 대량 제거 & 점수 발생');
}

console.log('\n[13] 미션 로테이션: 점수 -> 수집 -> 젤리');
{
  assert(MissionSystem.forStage(1).type === MissionType.SCORE, 'stage1 = SCORE');
  assert(MissionSystem.forStage(2).type === MissionType.COLLECT, 'stage2 = COLLECT');
  assert(MissionSystem.forStage(3).type === MissionType.JELLY, 'stage3 = JELLY');
}

console.log('\n[14] 젤리 시딩 & 제거로 카운트 감소');
{
  const b = new Board();
  const n = b.seedJelly(10);
  assert(n === 10 && b.countJelly() === 10, '젤리 10개 시딩됨');
  // 젤리가 있는 아무 칸이나 찾아 제거
  let cleared = false;
  outer: for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++)
    if (b.hasJelly(r, c)) { cleared = b.clearJellyAt(r, c); break outer; }
  assert(cleared && b.countJelly() === 9, '젤리 1개 제거 -> 9개 남음');
}

console.log('\n[15] 엔진: 젤리 미션 스테이지에서 매치로 젤리 제거 & 진행 집계');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  engine.stage = 3;
  engine._startStage(); // 젤리 미션 재생성
  assert(engine.mission.type === MissionType.JELLY, '스테이지3 젤리 미션');
  const totalJelly = engine.board.countJelly();
  assert(totalJelly > 0, '젤리가 실제로 심어짐');
  // (4,2),(4,3) 색3 + (5,4) 색3 -> (4,4)로 3매치 유발, 그 칸들에 젤리 강제
  const g = uniformExcept();
  g[4][2] = TileColor.YELLOW; g[4][3] = TileColor.YELLOW; g[5][4] = TileColor.YELLOW; g[4][4] = TileColor.PURPLE;
  setBoard(engine.board, g);
  // 매치 나는 칸들에 젤리 배치
  engine.board.jelly = Array.from({ length: 8 }, () => new Array(8).fill(0));
  engine.board.jelly[4][2] = 1; engine.board.jelly[4][3] = 1; engine.board.jelly[4][4] = 1;
  engine.mission = { type: MissionType.JELLY, target: 99, progress: 0, label: 'test' };
  engine.requestSwap(engine.board.grid[4][4], engine.board.grid[5][4]);
  for (let i = 0; i < 400; i++) engine.update(1 / 30);
  assert(engine.mission.progress >= 3, `젤리 3칸 제거 진행 (progress=${engine.mission.progress})`);
}

console.log('\n[16] 엔진: 색상 수집 미션 진행 집계');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  const g = uniformExcept();
  g[4][2] = TileColor.YELLOW; g[4][3] = TileColor.YELLOW; g[5][4] = TileColor.YELLOW; g[4][4] = TileColor.PURPLE;
  setBoard(engine.board, g);
  engine.mission = { type: MissionType.COLLECT, color: TileColor.YELLOW, target: 99, progress: 0, label: 'test' };
  engine.requestSwap(engine.board.grid[4][4], engine.board.grid[5][4]);
  for (let i = 0; i < 120; i++) engine.update(1 / 30);
  assert(engine.mission.progress >= 3, `노랑 3개 수집 (progress=${engine.mission.progress})`);
}

console.log('\n[17] 미션 완료 -> 결과 대기 -> 계속 -> 다음 스테이지');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  const startStage = engine.stage;
  // 점수 미션을 아주 낮게 만들어 한 번의 매치로 클리어되게
  engine.mission = { type: MissionType.SCORE, target: 1, progress: 0, label: 'test' };
  const g = uniformExcept();
  g[4][2] = TileColor.YELLOW; g[4][3] = TileColor.YELLOW; g[5][4] = TileColor.YELLOW; g[4][4] = TileColor.PURPLE;
  setBoard(engine.board, g);
  engine.requestSwap(engine.board.grid[4][4], engine.board.grid[5][4]);
  for (let i = 0; i < 400; i++) engine.update(1 / 30);
  assert(engine.awaitingContinue === true, '클리어 후 결과 대기 상태(자동 진행 안 함)');
  assert(engine.stage === startStage, '계속 누르기 전엔 스테이지 유지');
  engine.continueToNextStage();
  assert(engine.stage === startStage + 1, `계속 -> 스테이지 진행 ${startStage} -> ${engine.stage}`);
  assert(engine.awaitingContinue === false, '진행 후 대기 해제');
}

console.log('\n[18] 스피드 시간 보너스: stageTime 되감기');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  engine.stageTime = 10;
  engine._applyTimeBonus(3, { row: 3, col: 3 });
  assert(Math.abs(engine.stageTime - 7) < 1e-9, `10초 -> 3초 차감 -> ${engine.stageTime}`);
  engine.stageTime = 1;
  engine._applyTimeBonus(5, { row: 0, col: 0 });
  assert(engine.stageTime === 0, '0 미만으로는 안 내려감(클램프)');
}

console.log('\n[19] 라이트볼 콤보 -> 시간 보너스 이벤트 발생');
{
  let maxBonus = 0;
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: (e) => { if (e.type === 'time-bonus') maxBonus = Math.max(maxBonus, e.seconds); }, onFx: () => {} });
  engine.stageTime = 20;
  setBoard(engine.board, uniformExcept());
  const a = engine.board.grid[3][3], b = engine.board.grid[3][4];
  a.special = SpecialType.LIGHT_BALL; b.special = SpecialType.LIGHT_BALL;
  engine.requestSwap(a, b);
  for (let i = 0; i < 60; i++) engine.update(1 / 30);
  // 라이트볼 콤보는 3초 보너스 (이후 연쇄의 작은 보너스와 별개로 최댓값 확인)
  assert(maxBonus >= 3, `콤보 시간 보너스 발생 (max ${maxBonus}s)`);
}

console.log('\n[20] 타이머 일시정지 시 시간이 흐르지 않음');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  engine.stageTime = 0;
  engine.setPaused(true);
  for (let i = 0; i < 60; i++) engine.update(1 / 30);
  assert(engine.stageTime === 0, '일시정지 중 stageTime 불변');
  engine.setPaused(false);
  engine.update(0.5);
  assert(engine.stageTime > 0, '재개 후 시간 진행');
}

console.log('\n[21] 20스테이지 이후 보드 10x10 확장');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  engine.stage = 21;
  engine._startStage();
  assert(engine.board.grid.length === 10 && engine.board.grid[0].length === 10, '스테이지21 -> 10x10 보드');
  assert(!MatchDetector.hasAnyMatch(engine.board), '확장 보드도 초기 매치 없음');
  // 8x8로 복귀 확인 (전역 상태 원복)
  engine.stage = 1;
  engine._startStage();
  assert(engine.board.grid.length === 8, '낮은 스테이지 -> 8x8 복귀');
}

console.log('\n[22] 신규 퍽: 대형 폭탄 반경 확대 & 콤보 보너스 추가');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  // 대형 폭탄: bombRadius 1 -> 2, 폭탄 발동 시 5x5(25칸)
  engine.perks.bombRadius = 2;
  setBoard(engine.board, uniformExcept());
  const bomb = engine.board.grid[4][4];
  bomb.special = SpecialType.BOMB;
  const affected = engine.board.activateSpecial(bomb, { bombRadius: engine.perks.bombRadius });
  assert(affected.size === 25, `5x5 폭탄 -> 25칸 (got ${affected.size})`);
  // 콤보 보너스 추가 퍽
  engine.perks.comboBonusExtra = 2;
  engine.stageTime = 20;
  const a = engine.board.grid[2][2], b2 = engine.board.grid[2][3];
  a.special = SpecialType.LIGHT_BALL; b2.special = SpecialType.LIGHT_BALL;
  const before = engine.stageTime;
  engine._triggerCombo(a, b2);
  // 라이트볼 콤보 3.0 + 추가 2.0 = 5.0초 차감
  assert(before - engine.stageTime >= 5, `콤보 보너스 5초↑ 차감 (${(before - engine.stageTime).toFixed(1)})`);
}

console.log('\n[23] 프로펠러 다중 목표 (퍽 +N) & 젤리 우선');
{
  const b = new Board();
  setBoard(b, uniformExcept());
  const prop = b.grid[4][4];
  prop.special = SpecialType.PROPELLER;
  // 목표 3개 요청 -> 3개의 서로 다른 목표 선정 (발동 위치 제외)
  b.activateSpecial(prop, { propellerTargets: 3 });
  const tgts = b._lastPropellerTargets;
  assert(tgts.length === 3, `목표 3개 선정 (got ${tgts.length})`);
  const keys = new Set(tgts.map(t => `${t.row},${t.col}`));
  assert(keys.size === 3, '목표 중복 없음');
  assert(!keys.has('4,4'), '발동 위치는 목표에서 제외');

  // 젤리 우선: 젤리 2칸 심고 목표 3개 -> 젤리 2칸이 목표에 포함
  const b2 = new Board();
  setBoard(b2, uniformExcept());
  b2.jelly = Array.from({ length: 8 }, () => new Array(8).fill(0));
  b2.jelly[0][0] = 1; b2.jelly[7][7] = 1;
  const prop2 = b2.grid[4][4];
  prop2.special = SpecialType.PROPELLER;
  b2.activateSpecial(prop2, { propellerTargets: 3 });
  const keys2 = new Set(b2._lastPropellerTargets.map(t => `${t.row},${t.col}`));
  assert(keys2.has('0,0') && keys2.has('7,7'), '젤리 칸이 우선 목표로 선정됨');
}

console.log('\n[24] 정밀 유도 퍽 -> propellerTargets 증가 & 실제 목표 수 반영');
{
  const engine = new GameEngine({ onStateChange: () => {}, onEvent: () => {}, onFx: () => {} });
  assert(engine.perks.propellerTargets === 0, '기본 추가 목표 0');
  engine.perks.propellerTargets = 2; // 퍽 2회 획득 가정 -> 총 3목표
  setBoard(engine.board, uniformExcept());
  const prop = engine.board.grid[4][4];
  prop.special = SpecialType.PROPELLER;
  const fx = [];
  engine._expandSpecials(new Set(['4,4']), fx);
  const pfx = fx.find(f => f.type === SpecialType.PROPELLER);
  assert(pfx && pfx.targets && pfx.targets.length === 3, `연출용 목표 3개 첨부 (got ${pfx && pfx.targets && pfx.targets.length})`);
}

console.log(`\n===== 결과: ${pass} 통과 / ${fail} 실패 =====`);
process.exit(fail === 0 ? 0 : 1);
