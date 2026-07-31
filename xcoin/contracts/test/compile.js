/*
 * compile.js — XCoin.sol 을 컴파일해서 XCoin.json (abi + bytecode) 을 만든다.
 *
 *   npm install solc@0.8.20
 *   node compile.js
 */
const solc = require('solc');
const fs = require('fs');
const path = require('path');

const src = path.join(__dirname, '..', 'XCoin.sol');
const input = {
  language: 'Solidity',
  sources: { 'XCoin.sol': { content: fs.readFileSync(src, 'utf8') } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
  },
};

const out = JSON.parse(solc.compile(JSON.stringify(input)));
let failed = false;
(out.errors || []).forEach((e) => {
  console.log(`[${e.severity}] ${e.formattedMessage.trim()}`);
  if (e.severity === 'error') failed = true;
});
if (failed) process.exit(1);

const c = out.contracts['XCoin.sol'].XCoin;
fs.writeFileSync(
  path.join(__dirname, 'XCoin.json'),
  JSON.stringify({ abi: c.abi, bin: c.evm.bytecode.object })
);
console.log(`컴파일 성공 (solc ${solc.version()}) · ${c.evm.bytecode.object.length / 2} bytes`);
