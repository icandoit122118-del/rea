import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const PYTHON = path.join(ROOT, 'runtime/python/python.exe');
export const REA_ROOT = path.resolve(process.env.LOCALAPPDATA || 'C:/Users/USER/AppData/Local', 'Programs/REA');
export const MAX_INPUT = 512 * 1024 * 1024;
export class OperationError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
export async function regularFile(value, label = 'path') {
  if (typeof value !== 'string' || !path.isAbsolute(value)) throw new OperationError('invalid_path', `${label} must be absolute`);
  const resolved = path.resolve(value);
  await rejectLinks(resolved);
  const stat = await fs.lstat(resolved);
  if (!stat.isFile()) throw new OperationError('invalid_input', `${label} must be a regular file`);
  if (stat.size > MAX_INPUT) throw new OperationError('input_limit', `${label} exceeds the 512 MiB limit`);
  return resolved;
}
export async function rejectLinks(value) {
  let current = path.resolve(value);
  for (;;) {
    try { if ((await fs.lstat(current)).isSymbolicLink()) throw new OperationError('link_rejected', `Links and junctions are not accepted: ${current}`); }
    catch (e) { if (e.code !== 'ENOENT') throw e; }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
}
export async function newOutput(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value)) throw new OperationError('invalid_path', 'output_dir must be absolute');
  const resolved = path.resolve(value);
  await rejectLinks(resolved);
  try { await fs.lstat(resolved); throw new OperationError('output_exists', 'Output directory already exists; choose a new directory'); }
  catch (e) { if (e.code !== 'ENOENT') throw e; }
  return resolved;
}
export async function hashFile(value) { return createHash('sha256').update(await fs.readFile(value)).digest('hex'); }
export async function runProcess(command, args, { timeout = 90000, maxBytes = 32 * 1024 * 1024, env = process.env, signal } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: ROOT, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], signal });
    const stdoutChunks = [], stderrChunks = [];
    let bytes = 0, reason;
    const timer = setTimeout(() => { reason = new OperationError('timeout', 'Owned analysis subprocess exceeded its deadline'); child.kill(); }, timeout);
    const collect = (kind, chunk) => {
      bytes += chunk.length;
      if (bytes > maxBytes) { reason = new OperationError('output_limit', 'Subprocess output exceeded its bounded capture'); child.kill(); return; }
      (kind === 'out' ? stdoutChunks : stderrChunks).push(chunk);
    };
    child.stdout.on('data', c => collect('out', c));
    child.stderr.on('data', c => collect('err', c));
    child.on('error', e => { clearTimeout(timer); reject(e); });
    child.on('close', code => { clearTimeout(timer); if (reason) reject(reason); else resolve({ code, stdout: Buffer.concat(stdoutChunks).toString('utf8'), stderr: Buffer.concat(stderrChunks).toString('utf8') }); });
  });
}
export async function core(method, args = {}) {
  const requests = path.join(ROOT, 'evidence/requests');
  await fs.mkdir(requests, { recursive: true });
  const requestPath = path.join(requests, `${randomUUID()}.json`);
  await fs.writeFile(requestPath, JSON.stringify({ method, args }), { flag: 'wx' });
  const run = await runProcess(PYTHON, ['-I', '-m', 'rea3d_core', '--request', requestPath]);
  let result;
  try { result = JSON.parse(run.stdout); }
  catch { throw new OperationError('invalid_core_response', `Python bridge did not return JSON (exit ${run.code}): ${run.stderr.slice(0, 1200)}`); }
  if (run.code !== 0 || !result.ok) throw new OperationError(result.error?.code || 'core_failed', result.error?.message || run.stderr.slice(0, 1200));
  return result.result;
}
export async function receipt(method, args, result, error = null) {
  const id = randomUUID();
  const directory = path.join(ROOT, 'evidence/operations');
  await fs.mkdir(directory, { recursive: true });
  const output = path.join(directory, `${id}.json`);
  const record = {
    schema: 'rea3d.operation.v1', id, measured_at: new Date().toISOString(), method,
    execution: 'isolated_asset_tool', evidence_scope: 'local_file_analysis_and_conversion',
    game_runtime_verified: false, request: args, ok: !error, result,
    error: error ? { code: error.code || 'operation_failed', message: error.message } : null,
  };
  await fs.writeFile(output, JSON.stringify(record, null, 2), { flag: 'wx' });
  return { id, path: output, sha256: await hashFile(output) };
}
