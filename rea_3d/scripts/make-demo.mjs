import { promises as fs } from 'node:fs';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import validator from 'gltf-validator';
import { ROOT } from '../src/bridge.mjs';
import { createFixture } from './fixture.mjs';

const base = path.join(ROOT, 'examples/synthetic', new Date().toISOString().replace(/[:.]/g, '-'));
const input = await createFixture(path.join(base, 'source'));
const client = new Client({ name: 'rea3d-demo-producer', version: '1.0.0' }, { capabilities: {} });
const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(ROOT, 'src/mcp.mjs')], cwd: ROOT, stderr: 'pipe' });
const calls = [];
try {
  await client.connect(transport);
  async function call(name, args) {
    const answer = await client.callTool({ name, arguments: args });
    const data = answer.structuredContent ?? JSON.parse(answer.content[0].text);
    calls.push({ name, args, response: data });
    if (!data.ok) throw new Error(JSON.stringify(data));
    return data.result;
  }
  await call('rea3d_archive_inspect', { path: input.archive });
  await call('rea3d_archive_extract', { path: input.archive, output_dir: path.join(base, 'members') });
  const source = path.join(base, 'members/00000_00.bin');
  const motion = path.join(base, 'members/00001_00.bin');
  await call('rea3d_inspect', { path: source });
  const exported = await call('rea3d_export', { path: source, motion_paths: [motion], output_dir: path.join(base, 'output') });
  const validation = await validator.validateBytes(new Uint8Array(await fs.readFile(exported.glb_path)), { uri: 'synthetic-model.glb', maxIssues: 100 });
  await fs.writeFile(path.join(base, 'gltf-validator.json'), JSON.stringify(validation, null, 2));
  if (validation.issues.numErrors) throw new Error('Official GLB validation failed');
  const result = { evidence_scope: 'synthetic_data_real_parser_and_mcp_execution', created_at: new Date().toISOString(), input, exported, validation: validation.issues, calls, visual_rendering_verified: false, original_game_asset_verified: false };
  await fs.writeFile(path.join(base, 'verification.json'), JSON.stringify(result, null, 2));
  await fs.writeFile(path.join(ROOT, 'examples/synthetic/latest.json'), JSON.stringify({ directory: base, glb: exported.glb_path, verification: path.join(base, 'verification.json') }, null, 2));
  process.stdout.write(JSON.stringify({ directory: base, glb: exported.glb_path, bones: exported.bone_count, animations: exported.animation_count, gltf_errors: validation.issues.numErrors, gltf_warnings: validation.issues.numWarnings }, null, 2) + '\n');
} finally { await client.close(); }
