// 모듈 소스들을 import/export 제거 후 하나로 합쳐 단일 HTML(dist)로 빌드.
// -> 서버 없이 브라우저에서 바로 실행 / 아티팩트로 배포 가능.
import { readFileSync, writeFileSync, mkdirSync } from 'fs';

const ORDER = [
  'src/core/Constants.js',
  'src/core/Tile.js',
  'src/core/MatchDetector.js',
  'src/core/StateMachine.js',
  'src/core/Board.js',
  'src/core/GameEngine.js',
  'src/systems/PerkSystem.js',
  'src/systems/AdInterface.js',
  'src/render/Renderer.js',
  'src/render/InputController.js',
  'src/main.js',
];

function strip(src) {
  return src
    // 여러 줄에 걸친 import 문까지 세미콜론째로 통째 제거
    .replace(/^\s*import\s[^;]*;\s*$/gm, '')
    // export 키워드만 제거 (선언 자체는 유지)
    .replace(/^(\s*)export\s+/gm, '$1');
}

const bundle = ORDER.map(f => `// ===== ${f} =====\n${strip(readFileSync(f, 'utf8'))}`).join('\n\n');

const html = readFileSync('index.html', 'utf8')
  .replace('<script type="module" src="./src/main.js"></script>',
    `<script>\n${bundle}\n</script>`);

mkdirSync('dist', { recursive: true });
writeFileSync('dist/match3-standalone.html', html);
console.log('빌드 완료: dist/match3-standalone.html (' + html.length + ' bytes)');

// --- 아티팩트용 버전: doctype/html/head/body 래퍼 제거, style+본문+script 만 ---
const style = html.match(/<style>[\s\S]*?<\/style>/)[0];
const bodyInner = html.match(/<body>([\s\S]*?)<\/body>/)[1];
const artifact = `${style}\n${bodyInner.trim()}`;
writeFileSync('dist/artifact.html', artifact);
console.log('빌드 완료: dist/artifact.html (' + artifact.length + ' bytes)');
