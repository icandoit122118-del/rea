import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const [work,browserReport,previewUrl,requestedOutput]=process.argv.slice(2);
if(![work,browserReport].every(p=>p&&path.isAbsolute(p)))throw new Error('Usage: node verify-aux-session.mjs ABS_WORK ABS_BROWSER_RECEIPT');
const root=path.resolve(import.meta.dirname,'..'),hash=data=>createHash('sha256').update(data).digest('hex');
const json=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const file=async p=>({path:p,sha256:hash(await fs.readFile(p))});
const native=await json(path.join(work,'native-export.json'));
assert.equal(native.ok,true);assert.equal(hash(await fs.readFile(native.receipt.path)),native.receipt.sha256);
const operation=await json(native.receipt.path),bytes=await fs.readFile(native.result.glb_path),glbHash=hash(bytes);
assert.equal(glbHash,native.result.sha256);assert.equal(operation.result.sha256,glbHash);
const doc=JSON.parse(bytes.subarray(20,20+bytes.readUInt32LE(12)).toString()),runtime=doc.extras.reaPicaRuntime;
assert.equal(doc.animations.length,30);
assert.equal(runtime.clips.filter(c=>c.material_animations.length).length,28);
assert.equal(runtime.clips.filter(c=>c.visibility_animations.length).length,28);
assert.deepEqual(runtime.source_reports,{material_errors:[],visual_pack_errors:[[],[]]});
const aux=runtime.clips.map((c,index)=>({...c,index})).filter(c=>c.role==='auxiliary_pose');assert.equal(aux.length,2);
assert.deepEqual(aux.map(c=>c.source_slot),[25,33]);
for(const c of aux){
  assert.deepEqual(c.material_animations,[]);assert.deepEqual(c.visibility_animations,[]);
  assert.equal(c.auxiliary_pose.source_frame_count,71);assert.equal(c.auxiliary_pose.payload_length,136);
  assert.equal(c.auxiliary_pose.automatic_base_association_verified,false);
  assert.deepEqual(c.auxiliary_pose.channel_mask.map(t=>[t.bone,t.path,t.axes]),['LFoot','RFoot','Feeler','Jaw'].map(b=>[b,'rotation',[0,1,2]]));
  assert.equal(c.auxiliary_pose.constant_targets.length,11);
  assert.deepEqual(c.auxiliary_pose.variable_targets,[{bone:'Jaw',path:'rotation',axes:[1]}]);
}
assert.equal(aux[0].auxiliary_pose.payload_sha256,aux[1].auxiliary_pose.payload_sha256);
const evidencePath=path.join(work,'auxiliary-source-evidence.json'),evidence=await json(evidencePath);
assert.equal(evidence.identity_comparison.byte_equal,true);
assert.equal(evidence.identity_comparison.sha256,aux[0].auxiliary_pose.payload_sha256);
assert.equal(evidence.packs.reduce((n,p)=>n+p.production_parser_comparisons,0),1704);
for(const pack of evidence.packs){
  assert.equal(hash(await fs.readFile(pack.source_path)),pack.compressed_sha256);
  assert.equal(pack.independent_decode.decoded_components,450);assert.equal(pack.independent_decode.explicit_components,12);
  assert.equal(pack.independent_decode.consumed_end,pack.aux_end_in_gf1);
}
const sourceRoot=path.dirname(path.dirname(operation.request.path));
const manifest=await json(path.join(sourceRoot,'members-first20/manifest.json')),raw=[];
const rom=await fs.open(manifest.source.path,'r');
try{for(const p of [operation.request.path,...operation.request.texture_paths,...operation.request.motion_paths]){
  const entry=manifest.entries.find(e=>path.resolve(e.output_path)===path.resolve(p));assert.ok(entry);
  const data=await fs.readFile(p);assert.equal(hash(data),entry.sha256);
  const extracted=Buffer.alloc(entry.size),read=await rom.read(extracted,0,entry.size,entry.rom_absolute_offset);
  assert.equal(read.bytesRead,entry.size);assert.equal(hash(extracted),entry.sha256);
  raw.push({path:p,sha256:entry.sha256,rom_offset:entry.rom_absolute_offset,bytes:entry.size});
}}finally{await rom.close();}
const browser=await json(browserReport);
assert.equal(browser.ok,true);assert.equal(browser.input.sha256Before,glbHash);assert.equal(browser.input.sha256After,glbHash);
assert.equal(browser.boundaries.tusAccess,false);assert.equal(browser.boundaries.existingChromeProfiles,false);
assert.equal(browser.errors.length,0);assert.ok(browser.checks.length>=10);assert.ok(browser.checks.every(c=>c.passed));
for(const [p,sha] of Object.entries(browser.source))assert.equal(hash(await fs.readFile(path.join(root,p))),sha);
for(const capture of browser.captures){assert.equal(hash(await fs.readFile(capture.png.path)),capture.png.sha256);assert.equal(capture.state.typed.gl_error,0);}
assert.ok(browser.final.probe.shaderEvents.every(s=>s.ok));assert.ok(browser.final.probe.links.every(s=>s.ok));
assert.ok(browser.final.probe.shaderEvents.length>0&&browser.final.probe.links.length>0);
assert.deepEqual(browser.auxiliary.samples.map(s=>s.index),[9,29]);
for(const sample of browser.auxiliary.samples){assert.deepEqual(sample.changes.translation,[]);assert.deepEqual(sample.changes.scale,[]);
  assert.deepEqual([...sample.changes.rotation].sort(),['LFoot','RFoot','Feeler','Jaw'].sort());}
assert.deepEqual(browser.realtimeAuxiliary.map(s=>s.index),[9,29]);
for(const observed of browser.realtimeAuxiliary){
  assert.ok(observed.samples.length>=3);assert.equal(observed.validation.length,observed.samples.length);
  assert.ok(new Set(observed.samples.map(s=>s.state.playback.time_seconds)).size>=3);
  for(const sample of observed.validation){
    assert.ok(sample.frameSynchronized&&sample.auxiliaryTimeSynchronized&&sample.actualGlUvMatchesSource);
    assert.equal(sample.glError,0);assert.equal(sample.rotationChecks.length,4);assert.ok(sample.rotationChecks.every(c=>c.passed));
  }
  if(observed.recording?.bytes>0)assert.equal(hash(await fs.readFile(observed.recording.path)),observed.recording.sha256);
}
assert.ok(browser.realtimeAuxiliary.some(s=>s.recording?.bytes>0));
assert.ok(browser.ab.available);assert.ok(browser.ab.pixelDifference.changedPixels>0);
const equivalencePath=path.join(work,'mcp-cli-comparison/equivalence.json'),equivalence=await json(equivalencePath);
assert.ok(equivalence.ok&&equivalence.byte_identical_glb);assert.equal(equivalence.native.sha256,glbHash);
assert.equal(hash(await fs.readFile(equivalence.cli.glb)),glbHash);
const validation=await json(path.join(work,'aux-final/gltf-validation.json'));
assert.equal(validation.sha256,glbHash);assert.equal(validation.validator.issues.numErrors,0);assert.equal(validation.validator.issues.numWarnings,0);
const regression=await json(path.join(root,'evidence/verification.json'));assert.equal(regression.ok,true);
const files=['preview/app.js','preview/pica-player.js','preview/pica-runtime.js','preview/synchronized-player.js',
  'src/preview-server.mjs','python/rea3d_core/bch_animation.py','python/rea3d_core/runtime_export.py','python/rea3d_core/glb.py',
  'scripts/verify-pica-browser.mjs','scripts/verify-mcp-cli-equivalence.mjs','scripts/verify-aux-session.mjs','evidence/verification.json'];
let http=null;
if(previewUrl){
  const url=new URL(previewUrl);assert.equal(url.hostname,'127.0.0.1');const routes=[];
  for(const route of ['','model.glb','app.js','pica-player.js','pica-runtime.js','synchronized-player.js']){
    const response=await fetch(new URL(route,url));assert.equal(response.status,200);
    const data=Buffer.from(await response.arrayBuffer());if(route==='model.glb')assert.equal(hash(data),glbHash);
    if(route.endsWith('.js'))assert.equal(hash(data),hash(await fs.readFile(path.join(root,'preview',route))));
    if(route==='')assert.ok(response.headers.get('content-security-policy').includes("connect-src 'self' blob:"));
    routes.push({route,sha256:hash(data),bytes:data.length});
  }
  http={url:previewUrl,model_and_module_delivery_verified:true,routes};
}
const report={schema:'rea3d.actual-auxiliary-player-verification.v1',ok:true,measured_at:new Date().toISOString(),
  source_kind:'User supplied modified Personal Omega Ruby; not verified pristine retail.',selected_raw_members_checked_against_rom:raw,
  native_export:{receipt:native.receipt,glb:native.result.glb_path,sha256:glbHash},
  counts:{primary_visual_pairs:28,auxiliary_layers:2,unique_auxiliary_payloads:1,source_scalar_comparisons:1704},
  auxiliary_metadata:aux.map(c=>({index:c.index,source_slot:c.source_slot,source_package:c.source_package,metadata:c.auxiliary_pose})),
  source_analysis:await file(evidencePath),actual_browser:await file(browserReport),browser_checks:browser.checks,
  realtime_playback:browser.realtimeAuxiliary.map(s=>({index:s.index,samples:s.samples.length,validation:s.validation,recording:s.recording})),
  browser_driver:browser.final.typed.webgl_renderer,ab_comparison:browser.ab.pixelDifference,
  http,
  mcp_cli_equivalence:await file(equivalencePath),tests:{node:regression.node,python:regression.python},gltf:{errors:0,warnings:0,infos:validation.validator.issues.numInfos},
  code:await Promise.all(files.map(p=>file(path.join(root,p)))),
  boundaries:{original_game_executed:false,original_game_auto_base_selection_verified:false,original_game_pixel_parity:false,
    playback_policy:'User selects a primary and auxiliary; source masks override absolute local rotation at the same primary time. Original scheduling is not inferred.',
    quality:'Native MCP and ordinary CLI output are byte identical. Source-material versus unlit comparison measures display differences, not an original-game fidelity percentage.'}};
const output=requestedOutput??path.join(work,'actual-auxiliary-player-verification.json');
if(!path.isAbsolute(output))throw new Error('Verification output must be an absolute path');
await fs.writeFile(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
await fs.writeFile(path.join(root,'evidence/aux-latest.json'),JSON.stringify({work,verification:output,sha256:hash(await fs.readFile(output)),glb:native.result.glb_path,browser_receipt:browserReport,preview_url:previewUrl??null},null,2)+'\n');
await fs.copyFile(path.join(root,'evidence/aux-latest.json'),path.join(root,'evidence/pica-latest.json'));
console.log(JSON.stringify({ok:true,output,counts:report.counts,browser_driver:report.browser_driver,ab_comparison:report.ab_comparison,tests:report.tests}));
