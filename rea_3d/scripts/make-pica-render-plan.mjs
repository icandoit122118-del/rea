import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createPicaFragment, desktopVertexShader, evaluateMaterialFrame} from '../preview/pica-runtime.js';

const [input, output] = process.argv.slice(2);
if (![input,output].every(p => p && path.isAbsolute(p))) throw new Error('Usage: node make-pica-render-plan.mjs ABS_GLB NEW_DIRECTORY');
const data = await fs.readFile(input);
const doc = JSON.parse(data.subarray(20,20+data.readUInt32LE(12)).toString());
const runtime = doc.extras?.reaPicaRuntime;
if (!runtime?.materials?.length) throw new Error('No source PICA runtime in GLB');
await fs.mkdir(output);
const vertex = path.join(output,'pica.vert');
await fs.writeFile(vertex, desktopVertexShader, {flag:'wx'});
const materials = {};
for (const config of runtime.materials) {
  if (config.unsupported?.length) throw new Error(`Unsupported source material state: ${config.name}`);
  const fragment = path.join(output,`${config.name}.frag`);
  await fs.writeFile(fragment,createPicaFragment(config,{desktop:true,luts:runtime.luts}),{flag:'wx'});
  const depth = config.depth_color_mask;
  materials[config.name] = {vertex,fragment,config:{...config,
    depth_test:depth.enabled,depth_write:depth.depth_write,depth_func:depth.depth_function,
    buffer_color:config.texenv_buffer_color,
    cull_mode:config.face_culling==='BackFace'?'back':config.face_culling==='FrontFace'?'front':'none',
    blend:{enabled:config.blend.enabled,src_rgb:config.blend.color_src,dst_rgb:config.blend.color_dst,
      src_alpha:config.blend.alpha_src,dst_alpha:config.blend.alpha_dst,
      equation_rgb:config.blend.color_equation,equation_alpha:config.blend.alpha_equation},
  }};
}
const frames = [];
function frame(clipIndex, f, label, fixedPose) {
  const sampled = evaluateMaterialFrame(runtime,clipIndex,f);
  frames.push({clip_index:clipIndex,frame:f,time_seconds:f/runtime.source_fps,label,
    ...(fixedPose===undefined?{}:{pose_time_seconds:fixedPose}),
    visibility:sampled.visibility,model_visible:sampled.model_visible,
    material_states:Object.fromEntries(Object.entries(sampled.material_states).map(([name,state])=>[name,
      {texture_names:state.texture_names,uv_matrices:state.uv_matrices,tex_coords:state.tex_coords,colors:state.colors}]))});
}
const eyeClip = runtime.clips.findIndex(c=>c.source_name?.endsWith('kw20_drowseA01'));
if (eyeClip<0) throw new Error('Selected original drowse clip is absent');
for (const f of [0,5,10,15,30]) frame(eyeClip,f,`drowse_${f}`);
for (const f of [0,10,15]) frame(eyeClip,f,`eye_only_${f}`,0);
let vine = null;
for (let clip=0;clip<runtime.clips.length && !vine;clip++) {
  if (!runtime.clips[clip].visibility_animations.length) continue;
  const count=doc.animations[clip].extras.sourceFrames;
  for (let f=0;f<count;f++) {
    const visibility=evaluateMaterialFrame(runtime,clip,f).visibility;
    if (Object.entries(visibility).some(([name,shown])=>/TutaSkin$/.test(name)&&shown)) {vine={clip,frame:f};break;}
  }
}
if (vine) {
  frame(vine.clip,vine.frame,'source_vines_visible');
  frame(vine.clip,0,'visibility_only_hidden',vine.frame/runtime.source_fps);
  frame(vine.clip,vine.frame,'visibility_only_shown',vine.frame/runtime.source_fps);
  frame(vine.clip,doc.animations[vine.clip].extras.sourceFrames-1,'source_vines_end');
}
frame(0,0,'source_vines_hidden');
const fullSequence=process.argv.includes('--full-sequence');
if(fullSequence){
  frames.length=0;
  const eyeFrames=doc.animations[eyeClip].extras.sourceFrames;
  for(let f=0;f<eyeFrames;f++)frame(eyeClip,f,`eye_sequence_${f}`);
  if(vine){const count=doc.animations[vine.clip].extras.sourceFrames;for(let f=0;f<count;f++)frame(vine.clip,f,`vine_sequence_${f}`);}
}
const plan={schema:'rea.pica.render.plan.v1',glb:input,source_glb_sha256:createHash('sha256').update(data).digest('hex'),
  width:640,height:640,materials,frames,scene_lighting:{ambient:[.35,.35,.35],direction:[.35,.6,1],color:[1,1,1],
    provenance:'Controlled directional preview lighting. Original battle light/camera uniforms have not been captured.'},
  eye_clip:eyeClip,vine_sample:vine,source_material_count:runtime.materials.length,paired_clips:runtime.clips.filter(c=>c.material_animations.length).length};
plan.full_sequence=fullSequence;
plan.scene_ambient=plan.scene_lighting.ambient;plan.light_direction=plan.scene_lighting.direction;plan.light_color=plan.scene_lighting.color;
await fs.writeFile(path.join(output,'plan.json'),JSON.stringify(plan,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({plan:path.join(output,'plan.json'),eye_clip:eyeClip,vine_sample:vine,frames:frames.length,
  eye_translation:frames.filter(f=>f.label.startsWith('eye_only')).map(f=>({frame:f.frame,translation:f.material_states.Eye.tex_coords[0].translation}))},null,2));
