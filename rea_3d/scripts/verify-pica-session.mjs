import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createPicaFragment,desktopVertexShader,evaluateMaterialFrame} from '../preview/pica-runtime.js';

const [work,source,previewUrl,requestedOutput]=process.argv.slice(2);
if (![work,source].every(p=>p&&path.isAbsolute(p))) throw new Error('Usage: node verify-pica-session.mjs ABS_WORK ABS_SOURCE [LOCAL_PREVIEW_URL] [NEW_ABS_JSON]');
if(requestedOutput&&!path.isAbsolute(requestedOutput))throw new Error('Output must be an absolute path');
const root=path.resolve(import.meta.dirname,'..');
const hash=data=>createHash('sha256').update(data).digest('hex');
const read=async p=>JSON.parse((await fs.readFile(p,'utf8')).replace(/^\uFEFF/,''));
const record=async p=>({path:p,sha256:hash(await fs.readFile(p))});
const native=await read(path.join(work,'native-final-export.json'));
assert.equal(native.ok,true);
assert.equal(hash(await fs.readFile(native.receipt.path)),native.receipt.sha256);
const operation=await read(native.receipt.path);
assert.equal(operation.id,native.receipt.id);assert.equal(operation.ok,true);
const bytes=await fs.readFile(native.result.glb_path),glbHash=hash(bytes);
assert.equal(glbHash,native.result.sha256);assert.equal(operation.result.sha256,glbHash);
const doc=JSON.parse(bytes.subarray(20,20+bytes.readUInt32LE(12)).toString());
const runtime=doc.extras.reaPicaRuntime;
assert.deepEqual(runtime,await read(path.join(work,'pica-final/pica-runtime.json')));
assert.equal(runtime.materials.length,5);assert.equal(doc.animations.length,30);
assert.equal(runtime.clips.filter(c=>c.material_animations.length).length,28);
assert.equal(runtime.clips.filter(c=>c.visibility_animations.length).length,28);
assert.deepEqual(runtime.source_reports,{material_errors:[],visual_pack_errors:[[],[]]});
assert.ok(runtime.materials.every(m=>m.unsupported.length===0));

const manifest=await read(path.join(source,'members-first20/manifest.json'));
const inputs=[operation.request.path,...operation.request.texture_paths,...operation.request.motion_paths];
const sourceHandle=await fs.open(manifest.source.path,'r');
const selected=[];
try {
  for (const p of inputs) {
    const entry=manifest.entries.find(e=>path.resolve(e.output_path)===path.resolve(p));assert.ok(entry);
    const raw=await fs.readFile(p);assert.equal(hash(raw),entry.sha256);
    const buffer=Buffer.alloc(entry.size);
    const {bytesRead}=await sourceHandle.read(buffer,0,entry.size,entry.rom_absolute_offset);
    assert.equal(bytesRead,entry.size);assert.equal(hash(buffer),entry.sha256);
    selected.push({path:p,sha256:entry.sha256,rom_offset:entry.rom_absolute_offset,bytes:entry.size});
  }
} finally {await sourceHandle.close();}

const groups=[['gpu-final',13],['gpu-visibility-final',2],['gpu-eye-sequence',31],['gpu-vine-sequence',51]];
const gpu=[];
let samplerChecks=0;
for (const [folder,count] of groups) {
  const rp=path.join(work,folder,'pica-glb-render-receipt.json'),r=await read(rp);
  assert.equal(r.ok,true);assert.equal(r.inputs_unchanged,true);assert.equal(r.frames.length,count);
  assert.equal(r.glb.sha256_before,glbHash);assert.equal(r.glb.sha256_after,glbHash);
  assert.equal(r.evidence.actual_opengl_shader_compile_and_model_frame_readback,true);
  assert.equal(r.evidence.diagnostic_shader_used,false);
  assert.equal(hash(await fs.readFile(r.plan.path)),r.plan.sha256);
  const plan=await read(r.plan.path);
  assert.equal(plan.source_glb_sha256,glbHash);
  for(const config of runtime.materials){
    assert.equal(await fs.readFile(plan.materials[config.name].vertex,'utf8'),desktopVertexShader);
    assert.equal(await fs.readFile(plan.materials[config.name].fragment,'utf8'),createPicaFragment(config,{desktop:true,luts:runtime.luts}));
  }
  assert.equal(r.compiled_shaders.length,5);
  for(const [p,sha] of Object.entries(r.shader_inputs_sha256_before))assert.equal(hash(await fs.readFile(p)),sha);
  assert.deepEqual(r.shader_inputs_sha256_before,r.shader_inputs_sha256_after);
  for(let i=0;i<r.frames.length;i++) {
    const f=r.frames[i],planned=plan.frames[i];
    assert.equal(f.opengl_error,'GL_NO_ERROR');assert.deepEqual(f.unmatched_visibility_names,[]);
    assert.equal(hash(await fs.readFile(f.png.path)),f.png.sha256);
    const evaluated=evaluateMaterialFrame(runtime,f.clip_index,f.source_frame);
    assert.deepEqual(f.visibility_requested,evaluated.visibility);
    for(const draw of f.draws){
      // JSON receipts normalize IEEE negative zero to zero.
      const current=JSON.parse(JSON.stringify(evaluated.material_states[draw.material]));
      assert.deepEqual(draw.material_state.texture_names,current.texture_names);
      assert.deepEqual(draw.material_state.uv_matrices,current.uv_matrices);
      assert.deepEqual(draw.material_state.colors,current.colors);
      for(const t of draw.textures){const s=t.sampler,actual=s.gl_sampler_state_readback;
        assert.deepEqual(actual,{min_filter:s.gl_min_filter,mag_filter:s.gl_mag_filter,wrap_s:s.gl_wrap_s,wrap_t:s.gl_wrap_t});samplerChecks++;
      }
    }
    assert.equal(f.source_frame,planned.frame);
    if(folder.endsWith('-sequence')){assert.equal(f.source_frame,i);assert.equal(f.pose_time_seconds,f.time_seconds);}
  }
  gpu.push({folder,receipt:await record(rp),frames:count,driver:r.context.renderer,camera:r.camera,
    actual_gpu:true,source_states_recomputed:true,inputs_unchanged:true});
}
const eye=await read(path.join(work,'gpu-final/keyframe-verification.json'));
assert.ok(eye.pixel_comparisons.slice(0,2).every(p=>p.same_geometry_hash&&p.changed_pixels>0));
const visibility=await read(path.join(work,'gpu-visibility-final/visibility-verification.json'));
assert.ok(visibility.same_geometry_hash&&visibility.same_material_states&&visibility.same_pose_time);
assert.deepEqual(visibility.visible_mesh_counts,[6,8]);assert.ok(visibility.pixel_difference.changed_pixels>0);
const curves=await read(path.join(work,'visual-curve-crosscheck.json'));
assert.ok(curves.ok);assert.equal(curves.samples,30382);assert.equal(curves.max_numeric_error,0);
assert.equal(hash(await fs.readFile(path.join(work,'visual-curve-expectations.json'))),curves.expectation_sha256);
const validation=await read(path.join(work,'pica-final/gltf-validation.json'));
assert.equal(validation.sha256,glbHash);assert.equal(validation.validator.issues.numErrors,0);assert.equal(validation.validator.issues.numWarnings,0);
const regression=await read(path.join(root,'evidence/verification.json'));assert.ok(regression.ok);
const gpuTranscript=await fs.readFile(path.join(root,'evidence/pica-gpu-tests.txt'),'utf8');
assert.match(gpuTranscript,/Ran 4 tests/);assert.match(gpuTranscript,/\bOK\b/);
const apngs=[];
for(const [name,count] of [['eyes',31],['vines',51]]){
  const manifest=await read(path.join(work,`${name}-playback.json`));
  assert.equal(manifest.frames,count);assert.equal(manifest.fps,30);
  const buffer=await fs.readFile(manifest.apng);assert.equal(hash(buffer),manifest.sha256);
  let offset=8,controls=0,declared=0;
  while(offset<buffer.length){const len=buffer.readUInt32BE(offset),tag=buffer.subarray(offset+4,offset+8).toString();
    if(tag==='acTL')declared=buffer.readUInt32BE(offset+8);if(tag==='fcTL')controls++;offset+=len+12;
  }
  assert.equal(declared,count);assert.equal(controls,count);
  for(const png of manifest.source_pngs)assert.equal(hash(await fs.readFile(png.path)),png.sha256);
  apngs.push({path:manifest.apng,sha256:manifest.sha256,frames:count,fps:30,actual_gpu_png_frames:true});
}
let http=null;
if(previewUrl){
  const url=new URL(previewUrl);assert.equal(url.hostname,'127.0.0.1');
  const routes=[];
  for(const route of ['','model.glb','api/model','app.js','pica-player.js','pica-runtime.js']){
    const response=await fetch(new URL(route,url));assert.equal(response.status,200);
    const data=Buffer.from(await response.arrayBuffer());
    if(route==='model.glb')assert.equal(hash(data),glbHash);
    routes.push({route,status:response.status,bytes:data.length,sha256:hash(data)});
  }
  http={url:previewUrl,routes,delivery_verified:true,browser_webgl_execution_verified:false};
}
const result={schema:'rea3d.actual-material-verification.v1',measured_at:new Date().toISOString(),ok:true,
  source_kind:'user supplied modified Personal Omega Ruby; not verified pristine retail',
  source_identity:await record(path.join(source,'source-identity.json')),selected_raw_members_rechecked_against_rom:selected,
  native_export:{receipt:native.receipt,glb:native.result.glb_path,sha256:glbHash},
  counts:{meshes:doc.meshes.length,bones:doc.skins[0].joints.length,images:doc.images.length,skeletal_clips:doc.animations.length,
    source_materials:5,paired_material_clips:28,paired_visibility_clips:28,actual_gpu_frames:97,sampler_readbacks_checked:samplerChecks},
  gpu,eyes:eye.pixel_comparisons.slice(0,2),visibility,curves,apngs,http,
  tests:{node:regression.node,python:regression.python,gpu:{tests:4,passed:4,transcript:'evidence/pica-gpu-tests.txt'}},
  verification_code:await Promise.all(['preview/pica-runtime.js','preview/pica-player.js','preview/app.js',
    'python/rea3d_core/bch_animation.py','python/rea3d_core/pica_material.py','python/rea3d_core/runtime_export.py',
    'python/rea3d_core/bridge.py','python/rea3d_core/glb.py','scripts/render-pica-glb.py','scripts/verify-pica-session.mjs',
    'scripts/make-pica-render-plan.mjs','scripts/make-apng.py','evidence/verification.json','evidence/pica-gpu-tests.txt'].map(p=>record(path.join(root,p)))),
  gltf:{errors:0,warnings:0,infos:validation.validator.issues.numInfos},
  verification_boundary:{actual_original_game_execution:false,original_scene_light_camera_parity:false,
    original_gpu_numeric_precision_parity:false,original_mip_chain:false,browser_ui_execution:false,
    source_timing:'30 fps interpretation; GF1 slopes retain the SPICA experimental 1/30 assumption',
    camera:'Body-fit APNG can crop extreme vine tips. Wide-fit full frames are retained.',
    auxiliary_clips:'Two skeletal face/aux slots outside the source AABB association remain without speculative visual binding.',
    standard_gltf:'Standard viewers use a simpler skeletal/unlit fallback; decoded material/visibility require the included PICA player.'}};
const output=requestedOutput??path.join(work,'actual-material-verification.json');
await fs.writeFile(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
await fs.writeFile(path.join(root,'evidence/pica-latest.json'),JSON.stringify({work,verification:output,sha256:hash(await fs.readFile(output)),glb:native.result.glb_path,preview_url:previewUrl??null},null,2)+'\n');
console.log(JSON.stringify({ok:true,output,counts:result.counts,apngs,http:http?.url,gltf:result.gltf}));
