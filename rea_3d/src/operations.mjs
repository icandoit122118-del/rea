import { promises as fs } from 'node:fs';
import path from 'node:path';
import { ROOT, REA_ROOT, PYTHON, OperationError, regularFile, rejectLinks, newOutput, hashFile, runProcess, core, receipt } from './bridge.mjs';

function keys(args, allowed) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new OperationError('invalid_args', 'args must be an object');
  for (const key of Object.keys(args)) if (!allowed.includes(key)) throw new OperationError('unknown_argument', `Unsupported argument: ${key}`);
}
async function fileList(value, label) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 128) throw new OperationError('invalid_args', `${label} must be an array with at most 128 paths`);
  return Promise.all(value.map(v => regularFile(v, label)));
}
export async function capabilities(args = {}) {
  keys(args, []);
  let pythonReady = false, reaReady = false;
  try { await fs.access(PYTHON); pythonReady = true; } catch {}
  try { await fs.access(path.join(REA_ROOT, 'node_modules/rea-agents/scripts/rea.mjs')); reaReady = true; } catch {}
  return {
    name: 'rea-3d-lab', version: '0.1.0', execution: 'isolated_companion',
    production_rea_modified: false, tus_modified: false, host_registration: 'not_modified_by_this_tool',
    python_ready: pythonReady, installed_rea_cli_available: reaReady,
    asset_backend: pythonReady ? await core('capabilities') : null,
    methods: ['capabilities', 'inspect', 'export', 'archive_inspect', 'archive_extract', 'rea_analyze_code'],
    preview: { command: 'node src/preview-server.mjs --input ABSOLUTE_GLB', network: '127.0.0.1_only', external_cdn: false },
    limits: { archive_file_mib: 512, asset_file_mib: 128, companion_files: 128, asset_operation_timeout_seconds: 90, rea_code_timeout_seconds: 180 },
    limitations: [
      'This companion does not decrypt ROMs, CIA files, or encrypted NCCH content.',
      'Decoded BCH material/visibility tracks and PICA states require the included REA player; standard glTF viewers use a simpler fallback.',
      'Original-game light/camera state, hardware precision, original mip chains and pixel parity are not verified.',
      'Original-game asset and visual parity require caller-supplied files and separate verification.',
      'MCP stdio is available when launched; this does not mean the current host has loaded its tools.',
    ],
  };
}
export async function execute(method, args = {}) {
  let result;
  try {
    if (method === 'capabilities') result = await capabilities(args);
    else if (method === 'inspect') {
      keys(args, ['path']);
      result = await core('inspect', { path: await regularFile(args.path) });
    } else if (method === 'export') {
      keys(args, ['path', 'output_dir', 'motion_paths', 'texture_paths', 'fps', 'model_index']);
      const input = await regularFile(args.path);
      const output = await newOutput(args.output_dir);
      const motions = await fileList(args.motion_paths, 'motion_paths');
      const textures = await fileList(args.texture_paths, 'texture_paths');
      if (args.fps !== undefined && (!Number.isFinite(args.fps) || args.fps < 1 || args.fps > 240)) throw new OperationError('invalid_args', 'fps must be >= 1 and <= 240');
      if (args.model_index !== undefined && (!Number.isSafeInteger(args.model_index) || args.model_index < 0)) throw new OperationError('invalid_args', 'model_index must be a nonnegative integer');
      result = await core('export', { path: input, output_dir: output, motion_paths: motions, texture_paths: textures, fps: args.fps ?? 30, model_index: args.model_index ?? 0 });
    } else if (method === 'archive_inspect') {
      keys(args, ['path']);
      const input = await regularFile(args.path);
      const { inspectGarc } = await import('./garc.mjs');
      result = { ...inspectGarc(await fs.readFile(input)), source: { path: input, sha256: await hashFile(input) }, evidence_scope: 'archive_inventory_only' };
    } else if (method === 'archive_extract') {
      keys(args, ['path', 'output_dir']);
      const input = await regularFile(args.path);
      const output = await newOutput(args.output_dir);
      const { extractGarc } = await import('./garc.mjs');
      result = await extractGarc(input, output);
    } else if (method === 'rea_analyze_code') {
      keys(args, ['path']);
      if (typeof args.path !== 'string' || !path.isAbsolute(args.path)) throw new OperationError('invalid_path', 'path must be absolute');
      await rejectLinks(args.path);
      if (!(await fs.stat(args.path)).isDirectory()) throw new OperationError('invalid_input', 'REA code analysis takes a selected directory');
      const run = await runProcess(path.join(REA_ROOT, 'node-v24.21.0-win-x64/node.exe'), [
        path.join(REA_ROOT, 'node_modules/rea-agents/scripts/rea.mjs'), 'analyze-javascript-application',
        path.resolve(args.path), '--max-heap-mb', '256', '--analysis-timeout-ms', '15000', '--format', 'json',
      ], { timeout: 180000, maxBytes: 64 * 1024 * 1024 });
      if (run.code !== 0) throw new OperationError('rea_cli_failed', run.stderr.slice(0, 2000) || run.stdout.slice(0, 1000));
      const evidence = JSON.parse(run.stdout);
      if (evidence.operation !== 'analyze_javascript_application' || !evidence.evidence_id || !evidence.normalized_result?.statistics) throw new OperationError('invalid_rea_evidence', 'Official CLI did not return the expected analysis Evidence');
      const output = path.join(ROOT, 'evidence', `rea-code-${Date.now()}.json`);
      await fs.writeFile(output, JSON.stringify(evidence, null, 2), { flag: 'wx' });
      result = { execution: 'installed_rea_official_cli', evidence_id: evidence.evidence_id, evidence_path: output, sha256: await hashFile(output), statistics: evidence.normalized_result.statistics, limitations: evidence.limitations, evidence_type: 'static_javascript_analysis', note: 'Inspect the saved REA Evidence for actual coverage. This is not asset extraction or execution proof.' };
    } else throw new OperationError('unsupported_method', `Unknown method: ${method}`);
    const saved = await receipt(method, args, result);
    return { ok: true, method, result, receipt: saved };
  } catch (error) {
    const saved = await receipt(method, args, null, error);
    return { ok: false, method, error: { code: error.code || 'operation_failed', message: error.message }, receipt: saved };
  }
}
