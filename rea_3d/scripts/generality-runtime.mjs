import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import validator from 'gltf-validator';
const sourceRoot = 'C:/Users/USER/Documents/Codex/pokemon-firered-3d-20261022/pokemon-audit';
const output = process.argv[2];
if (!output || !path.isAbsolute(output)) throw new Error('Pass a new absolute output directory');
const sourceFiles = ['source-receipt.json', 'animatedModels.json', 'upstream-tree.json', 'upstream-commit.json'];
const records = {};
for (const name of sourceFiles) records[name] = JSON.parse(await fs.readFile(path.join(sourceRoot, 'source', name), 'utf8'));
const receipt = records['source-receipt.json'];
if (receipt.commit !== records['upstream-commit.json'].sha || records['upstream-tree.json'].truncated || records['upstream-commit.json'].commit.tree.sha !== records['upstream-tree.json'].sha) throw new Error('Local source provenance records disagree or tree truncated');
const samples = [
  ['006', '리자몽', '날개·긴 꼬리·다관절'], ['009', '거북왕', '껍질·포신·복수 재질'],
  ['025', '피카츄', '긴 귀·꼬리·작은 체형'], ['094', '팬텀', '둥근 몸체·얼굴·발톱'],
  ['095', '롱스톤', '긴 분절 몸체'], ['129', '잉어킹', '지느러미·물고기 체형'],
  ['130', '갸라도스', '긴 다관절 몸체·지느러미'], ['151', '뮤', '작은 몸체·긴 꼬리'],
];
await fs.mkdir(output, { recursive: false });
const write = (name, data) => fs.writeFile(path.join(output, name), JSON.stringify(data, null, 2) + '\n', { flag: 'wx' });
const sha256 = b => createHash('sha256').update(b).digest('hex');
const inputs = [];
for (const [id, name, selectionReason] of samples) {
  const entry = records['animatedModels.json'][id];
  if (entry?.model !== `models/${id}/model.glb`) throw new Error(`Manifest path mismatch ${id}`);
  const absolutePath = path.join(sourceRoot, entry.model);
  const bytes = await fs.readFile(absolutePath);
  const tree = records['upstream-tree.json'].tree.find(x => x.path === `public/${entry.model}`);
  const gitBlob = createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
  if (!tree || tree.sha !== gitBlob || tree.size !== bytes.length || entry.bytes !== bytes.length) throw new Error(`Source identity mismatch ${id}`);
  const glb = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));
  const internalIdentity = [...new Set(JSON.stringify(glb).match(/pm\d{4}_\d{2}/g) ?? [])];
  const expectedIdentity = `pm${id.padStart(4, '0')}_00`;
  const allNames = glb.animations?.map(a => a.name) ?? [];
  const manifestNames = entry.animations.map(a => a.name);
  if (allNames.length !== manifestNames.length || allNames.some(n => !manifestNames.includes(n))) throw new Error(`Clip names differ from manifest ${id}`);
  if (internalIdentity.length && !internalIdentity.includes(expectedIdentity)) throw new Error(`Internal model identifier mismatch ${id}`);
  inputs.push({ id, name, selection_reason: selectionReason, path: absolutePath, bytes: bytes.length, sha256: sha256(bytes), git_blob_sha1: gitBlob, upstream_tree_blob_matches: true, upstream_manifest_source: entry.source, expected_internal_identity: expectedIdentity, observed_internal_identities: internalIdentity, manifest_fps: entry.fps, animation_names: allNames, manifest_animations: entry.animations, source_game_per_species_verified: false, original_ORAS_source_verified: false, rea_BCH_parser_exercised: false, rea_PF_profile_enabled: false });
}
const manifest = { schema: 'rea3d-generality-runtime-input-manifest-v1', measured_at: new Date().toISOString(), scope: '8 selected existing upstream GLBs; runtime compatibility, independent from blocked ORAS extraction', source_repository: receipt.repository, source_commit: receipt.commit, source_collection_receipt: path.join(sourceRoot, 'source/source-receipt.json'), inputs, original_game_provenance: receipt.originalGameProvenance, source_files_read_only: true, all_species_coverage: false };
await write('input-manifest.json', manifest);
process.stdout.write(JSON.stringify({ phase: 'manifest_ready', manifest: path.join(output, 'input-manifest.json'), inputs: inputs.map(x => ({ id: x.id, path: x.path })) }) + '\n');
const results = [];
for (const input of inputs) {
  const result = { id: input.id, path: input.path, status: 'started' }; results.push(result);
  try {
    const bytes = await fs.readFile(input.path);
    if (bytes.readUInt32LE(0) !== 0x46546c67 || bytes.readUInt32LE(4) !== 2 || bytes.readUInt32LE(8) !== bytes.length) throw new Error('GLB header mismatch');
    const doc = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));
    const validation = await validator.validateBytes(new Uint8Array(bytes), { uri: path.basename(input.path), maxIssues: 1000 });
    await write(`${input.id}-khronos.json`, validation);
    const counts = Object.fromEntries(['meshes', 'nodes', 'skins', 'materials', 'textures', 'images', 'animations'].map(k => [k, (doc[k] ?? []).length]));
    const materialLinks = (doc.materials ?? []).map(m => ({ name: m.name, alpha_mode: m.alphaMode ?? 'OPAQUE', double_sided: m.doubleSided ?? false, normal_map: m.normalTexture?.index ?? null, base_color_texture: m.pbrMetallicRoughness?.baseColorTexture?.index ?? null, metallic_factor: m.pbrMetallicRoughness?.metallicFactor ?? 1, roughness_factor: m.pbrMetallicRoughness?.roughnessFactor ?? 1 }));
    const animations = (doc.animations ?? []).map(a => ({ name: a.name, channels: a.channels.length, animated_nodes: new Set(a.channels.map(c => c.target.node)).size, target_paths: [...new Set(a.channels.map(c => c.target.path))], durations: [...new Set(a.samplers.map(s => (doc.accessors[s.input].max?.[0] ?? 0) - (doc.accessors[s.input].min?.[0] ?? 0)))] }));
    const skins = (doc.skins ?? []).map(s => ({ name: s.name, joints: s.joints.length, inverse_bind_accessor: s.inverseBindMatrices ?? null }));
    const primitiveCounts = (doc.meshes ?? []).flatMap(m => m.primitives.map(p => ({ mesh: m.name, vertices: doc.accessors[p.attributes.POSITION].count, indices: p.indices === undefined ? null : doc.accessors[p.indices].count, joints: Number.isInteger(p.attributes.JOINTS_0), weights: Number.isInteger(p.attributes.WEIGHTS_0), material: p.material, normal: Number.isInteger(p.attributes.NORMAL), tangent: Number.isInteger(p.attributes.TANGENT) })));
    const afterHash = sha256(await fs.readFile(input.path));
    if (afterHash !== input.sha256) throw new Error('Source GLB changed during audit');
    Object.assign(result, { status: validation.issues.numErrors ? 'khronos_errors' : 'validated_with_warnings_boundary', counts, skins, material_links: materialLinks, primitives: primitiveCounts, animations, has_rea_pica_runtime: Boolean(doc.extras?.reaPicaRuntime), embedded_images: (doc.images ?? []).every(i => Number.isInteger(i.bufferView) && !i.uri), external_uris: [...(doc.images ?? []), ...(doc.buffers ?? [])].filter(i => i.uri).map(i => i.uri), validator_errors: validation.issues.numErrors, validator_warnings: validation.issues.numWarnings, validator_infos: validation.issues.numInfos, warning_codes: [...new Set(validation.issues.messages.filter(m => m.severity === 1).map(m => m.code))], source_sha256_unchanged: afterHash === input.sha256, browser_runtime_tested: false, original_rendering_parity_verified: false });
    await write(`${input.id}-structure.json`, result);
    process.stdout.write(JSON.stringify({ phase: 'model_validated', id: input.id, status: result.status, errors: result.validator_errors, warnings: result.validator_warnings, counts }) + '\n');
  } catch (error) { Object.assign(result, { status: 'failed', error: String(error.stack ?? error) }); }
}
const summary = { schema: 'rea3d-generality-runtime-static-validation-v1', measured_at: new Date().toISOString(), input_manifest: path.join(output, 'input-manifest.json'), scope: manifest.scope, sample_count: results.length, structurally_valid_samples: results.filter(r => r.validator_errors === 0).length, validator_errors: results.reduce((s, r) => s + (r.validator_errors ?? 0), 0), validator_warnings: results.reduce((s, r) => s + (r.validator_warnings ?? 0), 0), failed: results.filter(r => r.status === 'failed' || r.validator_errors > 0).map(r => r.id), original_ORAS_extraction_validated: false, source_game_per_species_verified: false, browser_runtime_tested_here: false, results };
await write('structure-validation.json', summary);
const hashes = {};
for (const name of await fs.readdir(output)) hashes[name] = sha256(await fs.readFile(path.join(output, name)));
await write('SHA256SUMS.json', hashes);
process.stdout.write(JSON.stringify({ phase: 'complete', output, sample_count: summary.sample_count, structurally_valid_samples: summary.structurally_valid_samples, validator_errors: summary.validator_errors, validator_warnings: summary.validator_warnings, failed: summary.failed }) + '\n');
if (summary.failed.length) process.exitCode = 1;
