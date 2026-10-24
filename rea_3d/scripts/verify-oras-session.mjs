import fs from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const [work, exportDir, ...renderDirs] = process.argv.slice(2);
if (![work, exportDir, ...renderDirs].every(p => p && path.isAbsolute(p)) || renderDirs.length < 1) throw new Error('Usage: node verify-oras-session.mjs ABS_WORK ABS_EXPORT ABS_RENDER [...]');
const readJson = async p => JSON.parse((await fs.readFile(p, 'utf8')).replace(/^\uFEFF/, ''));
const hash = async p => { const h = createHash('sha256'); for await (const chunk of createReadStream(p)) h.update(chunk); return h.digest('hex'); };
const requireFact = (fact, message) => { if (!fact) throw new Error(message); };
const identity = await readJson(path.join(work, 'source-identity.json'));
const members = await readJson(path.join(work, 'members-first20/manifest.json'));
const romfs = await readJson(path.join(work, 'romfs-inventory.json'));
const conversion = await readJson(path.join(exportDir, 'receipt.json'));
const gltf = await readJson(path.join(exportDir, 'gltf-validation.json'));
const observed = await readJson(path.join(work, 'native-observations-final.json'));
const regression = await readJson(new URL('../evidence/verification.json', import.meta.url));
requireFact(regression.ok && regression.node.failed === 0 && regression.python.exit_code === 0, 'Regression verification failed');

const sourceHashes = {};
for (const [name, file, expected] of [['zip', identity.source_zip, identity.source_zip_sha256], ['image', identity.staged_image, identity.image_sha256]]) {
  sourceHashes[name] = { path: file, expected, actual: await hash(file) };
  requireFact(sourceHashes[name].actual === expected, `${name} source hash changed`);
}
for (const entry of members.entries) requireFact(await hash(entry.output_path) === entry.sha256, `Member ${entry.index} readback mismatch`);
for (const source of conversion.sources) requireFact(members.entries.some(e => e.sha256 === source.sha256 && e.size === source.bytes), 'Converted input not found in the selected ROM member manifest');

const receipts = [];
for (const item of observed) {
  const receipt = item.response.receipt;
  requireFact(receipt && await hash(receipt.path) === receipt.sha256, `Native receipt hash mismatch: ${item.key}`);
  const saved = await readJson(receipt.path);
  requireFact(saved.id === receipt.id && saved.ok === item.response.ok && saved.method === item.response.method, 'Native result differs from persisted receipt');
  receipts.push({ key: item.key, method: saved.method, ok: saved.ok, path: receipt.path, sha256: receipt.sha256 });
}
const glbHash = await hash(conversion.glb_path);
requireFact(observed.some(item => item.response.ok && item.response.method === 'export' && item.response.result?.sha256 === glbHash), 'Final GLB lacks a matching observed native MCP export');
requireFact(glbHash === conversion.sha256 && glbHash === gltf.sha256, 'GLB hashes disagree');
requireFact(gltf.validator.issues.numErrors === 0 && gltf.validator.issues.numWarnings === 0, 'GLB validator issues');
requireFact(gltf.counts.animations === conversion.animation_count && gltf.skins[0].joints === conversion.bone_count, 'Converted rig counts disagree');
requireFact(gltf.material_image_links.every(m => Number.isInteger(m.image)), 'Some selected material base textures are unbound');

const renders = [];
for (const dir of renderDirs) {
  const receiptPath = path.join(dir, 'render-verification.json');
  const result = await readJson(receiptPath);
  requireFact(result.ok && result.source.unchanged && result.source.sha256_before === glbHash, 'Render is not from the final GLB');
  requireFact(result.sampled_motion_evidence.any_evaluated_vertices_changed_vs_start, 'No evaluated vertex motion was observed in the selected clip');
  requireFact(result.sampled_motion_evidence.any_bone_matrices_changed_vs_start, 'No bone motion was observed in the selected clip');
  for (const image of result.renders) requireFact(await hash(image.path) === image.sha256, 'Rendered PNG readback mismatch');
  renders.push({ receipt: receiptPath, execution: result.execution, blender: result.blender_version, import: result.import,
    selected_clip: result.selected_clip, motion: result.sampled_motion_evidence, samples: result.samples, renders: result.renders });
}
const report = {
  schema: 'rea3d.personal-oras-verification.v1', measured_at: new Date().toISOString(),
  status: 'selected_asset_partial_conversion_and_render_verified',
  source: { ...identity, source_hashes_rechecked: sourceHashes, pristine_retail_dump_verified: false },
  preprocessing: { execution: 'bounded_python_standard_library_staging_and_selection', romfs_files: romfs.file_count,
    romfs_metadata_sha256: romfs.metadata_sha256, archive: members.archive, table_sha256: members.table_sha256,
    selected_raw_members: members.entries.length, selected_hashes_rechecked: true, ivfc_hash_tree_verified: false },
  native_mcp: { execution: 'current_conversation_registered_native_tools', receipts },
  selected_conversion: conversion,
  gltf: { counts: gltf.counts, errors: gltf.validator.issues.numErrors, warnings: gltf.validator.issues.numWarnings,
    infos: gltf.validator.issues.numInfos, material_image_links: gltf.material_image_links },
  headless_blender: renders,
  regression_tests: { scope: regression.scope, node: regression.node, python: regression.python },
  boundaries: { tested_pokemon: ['pm0001_00'], all_archive_members_converted: false, game_executed: false,
    original_game_visual_parity_verified: false, original_game_animation_timing_verified: false,
    material_visibility_animation_exported: false, original_pica_shaders_recreated: false,
    note: 'Partial status is intentional. Skeletal motion is evaluated at integer source frames with fps=30 interpretation and experimental Hermite 1/30 slope scaling.' },
};
const output = path.join(work, 'actual-verification.json');
await fs.writeFile(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
process.stdout.write(JSON.stringify({ output, status: report.status, source_hashes_match: true,
  selected_members: members.entries.length, native_receipts: receipts.length, counts: gltf.counts,
  renders: renders.map(r => ({ receipt: r.receipt, selected_clip: r.selected_clip, motion: r.motion })) }, null, 2) + '\n');
