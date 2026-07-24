// =============================================================
//  MatchDetector.js  -  매치 판정 & 특수타일 분류
//  스펙 #2 담당:
//    - Match-3            : 기본 파괴
//    - Match-4 (일자)     : ROCKET  (가로/세로 한 줄 제거)
//    - Match-4 (2x2 박스) : PROPELLER (주변 제거 후 목표 유도)
//    - Match-5 (L/T)      : BOMB (3x3 폭발)
//    - Match-5 (일자)     : LIGHT_BALL (같은 색 전부 제거)
// =============================================================

import { BOARD_ROWS, BOARD_COLS, SpecialType, RocketDir } from './Constants.js';

/**
 * @typedef {Object} MatchResult
 * @property {Set<string>} cells          제거 대상 셀 좌표 집합 ("r,c")
 * @property {SpawnSpec[]} spawns         제거 후 생성할 특수타일 목록
 * @property {number}      groupCount     매치 그룹 개수 (연쇄/점수 계산용)
 */

/**
 * @typedef {Object} SpawnSpec
 * @property {number} row
 * @property {number} col
 * @property {string} type       SpecialType
 * @property {number} color      스폰될 타일 색
 * @property {string|null} rocketDir
 */

export class MatchDetector {
  /**
   * 보드 전체에서 매치를 찾는다.
   * @param {import('./Board.js').Board} board
   * @param {{row:number,col:number}|null} swapHint 스왑으로 유발된 위치(특수타일 스폰 위치 우선권)
   * @returns {MatchResult}
   */
  static findMatches(board, swapHint = null) {
    const grid = board.grid;
    const runs = []; // 길이 3 이상인 일자 매치 목록

    // ---- 1) 가로 런 탐색 ----
    for (let r = 0; r < BOARD_ROWS; r++) {
      let c = 0;
      while (c < BOARD_COLS) {
        const t = grid[r][c];
        if (!t) { c++; continue; }
        let end = c;
        while (end + 1 < BOARD_COLS && grid[r][end + 1] && grid[r][end + 1].color === t.color) {
          end++;
        }
        const len = end - c + 1;
        if (len >= 3) {
          runs.push(makeRun('H', r, c, len, t.color));
        }
        c = end + 1;
      }
    }

    // ---- 2) 세로 런 탐색 ----
    for (let c = 0; c < BOARD_COLS; c++) {
      let r = 0;
      while (r < BOARD_ROWS) {
        const t = grid[r][c];
        if (!t) { r++; continue; }
        let end = r;
        while (end + 1 < BOARD_ROWS && grid[end + 1][c] && grid[end + 1][c].color === t.color) {
          end++;
        }
        const len = end - r + 1;
        if (len >= 3) {
          runs.push(makeRun('V', c, r, len, t.color));
        }
        r = end + 1;
      }
    }

    const cells = new Set();
    const spawns = [];

    // ---- 3) 런들을 교차/색상 기준으로 그룹핑 (Union-Find) ----
    // 겹치는 셀을 공유하는 런들은 하나의 매치 그룹.
    const groups = MatchDetector._groupRuns(runs);

    for (const group of groups) {
      const groupCells = new Set();
      let maxLen = 0;
      let hasH = false, hasV = false;
      let longestRun = null;
      const color = group[0].color;

      for (const run of group) {
        for (const [rr, cc] of runCells(run)) groupCells.add(`${rr},${cc}`);
        if (run.len > maxLen) { maxLen = run.len; longestRun = run; }
        if (run.dir === 'H') hasH = true; else hasV = true;
      }

      // 그룹 셀 전부 제거 대상에 추가
      for (const key of groupCells) cells.add(key);

      // ---- 특수타일 분류 ----
      let type = SpecialType.NONE;
      let rocketDir = null;

      if (hasH && hasV) {
        // 가로+세로가 교차 => L/T 형태 (총 5칸 이상) => 폭탄
        type = SpecialType.BOMB;
      } else if (maxLen >= 5) {
        // 일자 5줄 => 라이트볼
        type = SpecialType.LIGHT_BALL;
      } else if (maxLen === 4) {
        // 일자 4줄 => 로켓 (런 방향으로 발사)
        type = SpecialType.ROCKET;
        rocketDir = longestRun.dir === 'H' ? RocketDir.HORIZONTAL : RocketDir.VERTICAL;
      }
      // maxLen === 3 이면 특수타일 없음 (기본 파괴)

      if (type !== SpecialType.NONE) {
        const pos = MatchDetector._pickSpawnPos(group, groupCells, swapHint);
        spawns.push({ row: pos.row, col: pos.col, type, color, rocketDir });
      }
    }

    // ---- 4) 2x2 박스 매치 => 프로펠러 (일자 런으로는 안 잡히므로 별도 탐색) ----
    MatchDetector._detectSquares(board, cells, spawns, swapHint);

    return { cells, spawns, groupCount: groups.length + spawns.filter(s => s.type === SpecialType.PROPELLER).length };
  }

  /**
   * 스왑 없이 보드에 이미 매치가 존재하는지 (초기 보드 검증용 빠른 체크)
   * @returns {boolean}
   */
  static hasAnyMatch(board) {
    const grid = board.grid;
    for (let r = 0; r < BOARD_ROWS; r++) {
      for (let c = 0; c < BOARD_COLS; c++) {
        const t = grid[r][c];
        if (!t) continue;
        // 가로 3
        if (c + 2 < BOARD_COLS &&
            grid[r][c + 1]?.color === t.color &&
            grid[r][c + 2]?.color === t.color) return true;
        // 세로 3
        if (r + 2 < BOARD_ROWS &&
            grid[r + 1][c]?.color === t.color &&
            grid[r + 2][c]?.color === t.color) return true;
      }
    }
    return false;
  }

  // ---------- 내부 헬퍼 ----------

  /** 겹치는 셀을 공유하는 런들을 그룹으로 묶는다 */
  static _groupRuns(runs) {
    const n = runs.length;
    const parent = Array.from({ length: n }, (_, i) => i);
    const find = (x) => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
    const union = (a, b) => { parent[find(a)] = find(b); };

    // 셀 -> 그 셀을 포함하는 런 인덱스들
    const cellMap = new Map();
    runs.forEach((run, idx) => {
      for (const [r, c] of runCells(run)) {
        const key = `${r},${c}`;
        if (cellMap.has(key)) union(idx, cellMap.get(key));
        else cellMap.set(key, idx);
      }
    });

    const buckets = new Map();
    runs.forEach((run, idx) => {
      const root = find(idx);
      if (!buckets.has(root)) buckets.set(root, []);
      buckets.get(root).push(run);
    });
    return [...buckets.values()];
  }

  /** 특수타일 스폰 위치 결정: 스왑한 칸 우선, 없으면 교차점/중앙 */
  static _pickSpawnPos(group, groupCells, swapHint) {
    if (swapHint && groupCells.has(`${swapHint.row},${swapHint.col}`)) {
      return { row: swapHint.row, col: swapHint.col };
    }
    // 가로/세로 교차점 찾기 (L/T)
    const hCells = new Set();
    const vCells = new Set();
    for (const run of group) {
      const set = run.dir === 'H' ? hCells : vCells;
      for (const [r, c] of runCells(run)) set.add(`${r},${c}`);
    }
    for (const key of hCells) if (vCells.has(key)) {
      const [r, c] = key.split(',').map(Number);
      return { row: r, col: c };
    }
    // 가장 긴 런의 중앙
    let longest = group[0];
    for (const run of group) if (run.len > longest.len) longest = run;
    const cellsArr = runCells(longest);
    const mid = cellsArr[Math.floor(cellsArr.length / 2)];
    return { row: mid[0], col: mid[1] };
  }

  /** 2x2 동일 색 블록 탐색 -> 프로펠러 */
  static _detectSquares(board, cells, spawns, swapHint) {
    const grid = board.grid;
    for (let r = 0; r < BOARD_ROWS - 1; r++) {
      for (let c = 0; c < BOARD_COLS - 1; c++) {
        const a = grid[r][c], b = grid[r][c + 1], d = grid[r + 1][c], e = grid[r + 1][c + 1];
        if (!a || !b || !d || !e) continue;
        if (a.color === b.color && a.color === d.color && a.color === e.color) {
          const sq = [[r, c], [r, c + 1], [r + 1, c], [r + 1, c + 1]];
          // 이미 더 큰 특수타일(로켓/폭탄/라이트볼)이 이 칸을 점유했으면 프로펠러는 스킵
          const overlapsBiggerSpawn = spawns.some(s =>
            s.type !== SpecialType.PROPELLER &&
            sq.some(([sr, sc]) => sr === s.row && sc === s.col));
          if (overlapsBiggerSpawn) continue;

          for (const [sr, sc] of sq) cells.add(`${sr},${sc}`);
          const pos = swapHint && sq.some(([sr, sc]) => sr === swapHint.row && sc === swapHint.col)
            ? { row: swapHint.row, col: swapHint.col }
            : { row: r, col: c };
          spawns.push({ row: pos.row, col: pos.col, type: SpecialType.PROPELLER, color: a.color, rocketDir: null });
        }
      }
    }
  }
}

// ---- 런(run) 표현 ----
// dir: 'H'|'V', line: 가로면 row / 세로면 col, start: 시작 인덱스, len, color
function makeRun(dir, line, start, len, color) {
  return { dir, line, start, len, color };
}
/** 런이 차지하는 셀 좌표 배열 [[r,c],...] */
function runCells(run) {
  const out = [];
  for (let i = 0; i < run.len; i++) {
    if (run.dir === 'H') out.push([run.line, run.start + i]);
    else out.push([run.start + i, run.line]);
  }
  return out;
}
