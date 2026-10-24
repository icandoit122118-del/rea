import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import validator from 'gltf-validator';
import { ROOT } from '../src/bridge.mjs';

// This reads observations returned by actual host-native tools. It neither
// simulates calls nor opens a second SDK connection.
const input = process.argv[2];
assert.ok(input && path.isAbsolute(input), 'Supply the absolute host-observation.json path');
const observation = JSON.parse(await fs.readFile(input, 'utf8'));
assert.equal(observation.execution, 'current_conversation_registered_native_mcp');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const successful = observation.calls.filter(call => call.response.ok);
const successfulTools = [...new Set(successful.map(call => call.tool))];
assert.equal(successfulTools.length, 6);
for (const call of observation.calls) {
  assert.ok(observation.catalog.includes(call.tool));
  const receipt = call.response.receipt;
  const raw = await fs.readFile(receipt.path);
  assert.equal(digest(raw), receipt.sha256, 'Native tool receipt hash mismatch');
  const saved = JSON.parse(raw);
  assert.equal(saved.ok, call.response.ok);
  assert.deepEqual(saved.result, call.response.result ?? null);
  if (!saved.ok) assert.deepEqual(saved.error, call.response.error);
}
const exported = successful.find(call => call.tool.endsWith('__rea3d_export')).response.result;
const raw = await fs.readFile(exported.glb_path);
assert.equal(digest(raw), exported.sha256, 'Export changed after overwrite rejection');
const report = await validator.validateBytes(new Uint8Array(raw), { uri: 'native-mcp-model.glb', maxIssues: 100 });
assert.equal(report.issues.numErrors, 0);
assert.equal(report.issues.numWarnings, 0);
const docLength = raw.readUInt32LE(12);
const doc = JSON.parse(raw.subarray(20, 20 + docLength).toString());
const binary = raw.subarray(28 + docLength);
assert.equal(doc.meshes.length, 1);
assert.equal(doc.skins[0].joints.length, 2);
assert.equal(doc.images.length, 1);
assert.equal(doc.animations.length, 1);
const channel = doc.animations[0].channels.find(c => c.target.path === 'translation');
const sampler = doc.animations[0].samplers[channel.sampler];
const acc = doc.accessors[sampler.output];
const view = doc.bufferViews[acc.bufferView];
const offset = (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
const translations = Array.from({ length: acc.count }, (_, i) => [0, 1, 2].map(j => binary.readFloatLE(offset + i * 12 + j * 4)));
assert.deepEqual(translations, [[0, 0, 0], [1, 0, 0], [2, 0, 0]]);
assert.deepEqual(await fs.readFile(path.join(observation.context.members, '00000_00.bin')), await fs.readFile(observation.context.model));
assert.deepEqual(await fs.readFile(path.join(observation.context.members, '00001_00.bin')), await fs.readFile(observation.context.motion));
const failures = observation.calls.filter(c => !c.response.ok);
assert.ok(failures.some(c => c.response.error.code === 'output_exists'));
assert.ok(failures.some(c => c.response.error.code === 'invalid_path'));
const summary = {
  schema: 'rea3d.native-mcp-verification.v1', verified_at: new Date().toISOString(), ok: true,
  connection: 'current_conversation_native_mcp', catalog_visible: true, catalog_count: observation.catalog.length,
  successful_distinct_tools: successfulTools, successful_calls: successful.length,
  expected_failure_calls: failures.map(c => ({ tool: c.tool, code: c.response.error.code })),
  receipt_readbacks_verified: observation.calls.length,
  extracted_source_bytes_match: true, export_unchanged_after_rejected_overwrite: true,
  glb: { path: exported.glb_path, mesh_count: 1, bones: 2, textures: 1, animations: 1, translation_keys: translations },
  gltf_validator: report.issues, original_game_asset_verified: false,
  original_rendering_verified: false, sdk_fallback_used: false, app_restart_performed: false,
  observation_path: path.resolve(input),
};
await fs.writeFile(path.join(observation.context.directory, 'gltf-validator.json'), JSON.stringify(report, null, 2));
await fs.writeFile(path.join(observation.context.directory, 'verification.json'), JSON.stringify(summary, null, 2));
await fs.writeFile(path.join(ROOT, 'evidence/native-mcp-verification.json'), JSON.stringify(summary, null, 2));
process.stdout.write(JSON.stringify(summary, null, 2) + '\n');
