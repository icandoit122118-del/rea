import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import validator from 'gltf-validator';

const input = process.argv[2];
const output = process.argv[3];
if (!input || !output || !path.isAbsolute(input) || !path.isAbsolute(output)) {
  throw new Error('Usage: node verify-asset-glb.mjs ABSOLUTE_GLB NEW_ABSOLUTE_JSON');
}
const data = await fs.readFile(input);
if (data.readUInt32LE(0) !== 0x46546c67 || data.readUInt32LE(4) !== 2 || data.readUInt32LE(8) !== data.length) throw new Error('Invalid GLB header');
const jsonBytes = data.readUInt32LE(12);
const doc = JSON.parse(data.subarray(20, 20 + jsonBytes).toString('utf8'));
const result = await validator.validateBytes(new Uint8Array(data), { uri: path.basename(input), maxIssues: 500 });
const linkedImages = (doc.materials ?? []).map(m => {
  const slot = m.pbrMetallicRoughness?.baseColorTexture;
  return { material: m.name, texture: slot?.index ?? null, image: slot ? doc.textures?.[slot.index]?.source ?? null : null };
});
const report = {
  schema: 'rea3d.actual-glb-validation.v1', measured_at: new Date().toISOString(),
  input, bytes: data.length, sha256: createHash('sha256').update(data).digest('hex'),
  validator: result,
  counts: Object.fromEntries(['meshes', 'nodes', 'skins', 'materials', 'textures', 'images', 'animations'].map(k => [k, (doc[k] ?? []).length])),
  skins: (doc.skins ?? []).map(s => ({ name: s.name, joints: s.joints.length })),
  material_image_links: linkedImages,
  embedded_images: (doc.images ?? []).every(i => Number.isInteger(i.bufferView) && i.mimeType === 'image/png'),
  animations: (doc.animations ?? []).map(a => ({
    name: a.name, channels: a.channels.length, samplers: a.samplers.length,
    animated_nodes: new Set(a.channels.map(c => c.target.node)).size,
    target_paths: [...new Set(a.channels.map(c => c.target.path))],
    time_bounds: a.samplers.map(s => ({ min: doc.accessors[s.input].min, max: doc.accessors[s.input].max, count: doc.accessors[s.input].count })).filter((v, i, rows) => rows.findIndex(w => JSON.stringify(w) === JSON.stringify(v)) === i),
  })),
  source_game_runtime_verified: false, original_shader_parity_verified: false,
};
await fs.writeFile(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
process.stdout.write(JSON.stringify({ output, counts: report.counts, skins: report.skins, embedded_images: report.embedded_images, errors: result.issues.numErrors, warnings: result.issues.numWarnings, infos: result.issues.numInfos, messages: result.issues.messages.slice(0, 8) }, null, 2) + '\n');
if (result.issues.numErrors) process.exitCode = 1;
