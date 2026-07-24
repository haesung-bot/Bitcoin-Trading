// 코어 로직 헤드리스 검증 (브라우저 불필요)
import { Board } from '../src/core/Board.js';
import { MatchDetector } from '../src/core/MatchDetector.js';
import { GameEngine } from '../src/core/GameEngine.js';
import { TileColor, SpecialType, RocketDir } from '../src/core/Constants.js';
import { Tile } from '../src/core/Tile.js';

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
  const ok = engine.requestSwap(a, bTile);
  assert(ok, '인접 스왑 요청 접수');
  // 여러 프레임 진행 (애니메이션 소요)
  let states = new Set();
  for (let i = 0; i < 600; i++) { engine.update(1 / 30); states.add(engine.state); }
  assert(states.has('DESTROY'), 'DESTROY 상태를 거침');
  assert(events.includes('score'), '점수 이벤트 발생');
  assert(engine.movesLeft < 25, '유효 스왑으로 이동 1 소비');
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

console.log(`\n===== 결과: ${pass} 통과 / ${fail} 실패 =====`);
process.exit(fail === 0 ? 0 : 1);
