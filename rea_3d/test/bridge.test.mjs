import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { ROOT, newOutput, regularFile, runProcess } from '../src/bridge.mjs';
import { execute } from '../src/operations.mjs';

test('rejects relative files and output paths', async () => {
  await assert.rejects(regularFile('model.bin'), /absolute/);
  await assert.rejects(newOutput('out'), /absolute/);
});
test('refuses overwrite and unknown operations with failure receipts', async () => {
  await assert.rejects(newOutput(ROOT), /already exists/);
  const result = await execute('invented_success', {});
  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'unsupported_method');
  const record = JSON.parse(await fs.readFile(result.receipt.path, 'utf8'));
  assert.equal(record.ok, false);
  assert.equal(record.game_runtime_verified, false);
});
test('inspection rejects unsupported arguments before parsing', async () => {
  const result = await execute('inspect', { path: path.join(ROOT, 'package.json'), execute: true });
  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'unknown_argument');
});
test('subprocess deadline stops only its owned child', async () => {
  await assert.rejects(runProcess(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeout: 100 }), /deadline/);
});
test('CLI returns machine readable error and nonzero status', async () => {
  const result = await runProcess(process.execPath, [path.join(ROOT, 'src/cli.mjs'), 'inspect', '--input', 'relative.bin']);
  assert.equal(result.code, 1);
  const response = JSON.parse(result.stdout);
  assert.equal(response.ok, false);
  assert.equal(response.error.code, 'invalid_path');
});
