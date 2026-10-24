/** Owned isolated Chromium A/B and real-RAF replay; no original-game access. */
import path from 'node:path';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {startPreview} from '../src/preview-server.mjs';
import {parseGlb,installStateProbe,compareMaterial} from './audit-shader-runtime.mjs';
import {pixelDifference} from './verify-pica-browser.mjs';
import {evaluateMaterialFrame} from '../preview/pica-runtime.js';

const project=fileURLToPath(new URL('../',import.meta.url));
const input='C:/rea3d-validation/baseline/model.glb';
const beforeFile=path.join(project,'evidence/iris-blend-20261024-01/before/pica-player.js');
const afterFile=path.join(project,'preview/pica-player.js');
const chromium='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const playwrightFile='C:/browser-qa/node_modules/playwright-core/index.mjs';
const sha=b=>createHash('sha256').update(b).digest('hex');
const frameCases=[[0,0],[0,12],[0,30],[5,0],[5,12],[5,30],[11,0],[11,12],[14,65]];
const close=(a,b,eps=2e-6)=>a.length===b.length&&a.every((v,i)=>Math.abs(v-b[i])<=eps);

export function compactEvidence(key,value){
  if(key==='pica')return undefined; // raw words remain in the attested input GLB.
  if(key==='material_states'&&value)return Object.fromEntries(Object.entries(value).map(([name,m])=>[name,{name:m.name,texture_names:m.texture_names,colors:m.colors,uv_matrices:m.uv_matrices}]));
  return value;
}

export function cameraFor(base,{yaw=0,wide=1}={}){
  const d=base.position.map((v,i)=>(v-base.target[i])*wide),c=Math.cos(yaw),s=Math.sin(yaw);
  return {...base,position:[base.target[0]+d[0]*c+d[2]*s,base.target[1]+d[1],base.target[2]-d[0]*s+d[2]*c],canvas_size:[640,480],aspect:4/3};
}
export function validateCapture(sample,runtime){
  const playback=sample.typed.playback,expected=evaluateMaterialFrame(runtime,playback.clip_index,playback.frame),meshes=sample.typed.actual_materials.meshes;
  const visibility=meshes.map(m=>({mesh:m.name,node:m.source_node,expected:expected.model_visible&&(expected.visibility[m.source_node]??true),actual:m.visible}));
  const uniforms=sample.draws.flatMap(draw=>Object.entries(draw.actual_uv_matrices).map(([name,actual])=>({material:draw.tag.material,name,passed:close(actual,draw.tag.expected_uv[Number(name.at(-1))])})));
  return {clock_synchronized:Math.abs(playback.frame-playback.visual_state.frame)<1e-7,visibility_matches:visibility.every(x=>x.expected===x.actual),visibility,actual_GL_UV_matches:uniforms.length>0&&uniforms.every(x=>x.passed),uniform_checks:uniforms.length,gl_error:sample.typed.gl_error};
}

async function run(){
  const args=process.argv.slice(2);if(!args.includes('--authorized-isolated-browser'))throw new Error('Task-specific isolated Chromium authorization flag required');
  const at=args.indexOf('--output-dir'),output=at>=0?path.resolve(args[at+1]):null;
  const allowed=path.resolve('C:/rea3d-iris-blend');
  if(!output||!output.startsWith(allowed+path.sep))throw new Error('New output must be inside current iris QA scope');
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const bytes=await readFile(input),before=await readFile(beforeFile),after=await readFile(afterFile),{runtime}=parseGlb(bytes);
  if(sha(bytes)!=='e8bd5ccb7524170842037ef5d67286d1f3e2c197896bad2812bde673c63a3b76'||sha(after)!=='6f2b9621362d0deecc9ae88953dd30a33941c237043c9687a0da40156a87fbc7')throw new Error('Frozen input/after source identity mismatch');
  const playwright=await import(pathToFileURL(playwrightFile).href),receipt={schema:'rea3d.iris-blend.actual-AB-and-replay.v1',ok:false,started_at:new Date().toISOString(),input:{path:input,sha256:sha(bytes)},renderer:{before:{path:beforeFile,sha256:sha(before)},after:{path:afterFile,sha256:sha(after)}},phases:{},comparisons:[],errors:[],boundaries:{original_pixel_parity:false,original_game_access:false,tus_access:false,existing_browser_profiles:false,source_mutated_by_QA:false,exact_frame_input:'QA slider step=any to avoid source-frame boundary rounding'}};
  let baseView;
  const twoFrames=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  for(const phase of ['before','after']){
    let preview,context;const result={errors:[],console:[],captures:[],live:[]};receipt.phases[phase]=result;
    const phaseDir=path.join(output,phase);await mkdir(phaseDir);
    try{
      preview=await startPreview({input,port:0});result.url=preview.url;
      context=await playwright.chromium.launchPersistentContext(path.join(phaseDir,'browser-profile'),{executablePath:chromium,headless:true,viewport:{width:1000,height:800},deviceScaleFactor:1,args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
      const page=context.pages()[0]??await context.newPage();
      const setup=async target=>{
        await target.route('**/*',route=>{const url=route.request().url();if(!url.startsWith(preview.url)&&!url.startsWith('data:')&&!url.startsWith('blob:'))return route.abort();if(phase==='before'&&url===new URL('pica-player.js',preview.url).href)return route.fulfill({status:200,contentType:'text/javascript; charset=utf-8',body:before});return route.continue();});
        target.on('pageerror',e=>result.errors.push(e.message));target.on('console',m=>{if(['error','warning'].includes(m.type()))result.console.push({type:m.type(),text:m.text()});});await target.addInitScript(installStateProbe);
      };
      await setup(page);await page.goto(preview.url,{waitUntil:'networkidle',timeout:30000});
      await page.waitForFunction(()=>!document.getElementById('clip')?.disabled,null,{timeout:30000});await page.locator('#pause').click();
      if(!baseView){const view=await page.evaluate(async()=>{const app=await import('/app.js');return app.inspectComparisonView();});baseView={position:view.camera.position,target:view.camera.target,fov:view.camera.fov,near:view.camera.near,far:view.camera.far};receipt.base_camera=baseView;}
      const inspect=async since=>page.evaluate(async min=>{const app=await import('/app.js'),p=window.__REA_SHADER_STATE_AUDIT__.snapshot();return {typed:app.inspectPlayback(),view:app.inspectComparisonView(),draws:p.draws.filter(d=>d.serial>min)};},since??0);
      const capture=async(key,spec)=>{
        const beforeSerial=await page.evaluate(()=>window.__REA_SHADER_STATE_AUDIT__.snapshot().draws.at(-1)?.serial??0);
        await page.locator('#clip').selectOption(String(spec.clip));
        await page.evaluate(async data=>{const app=await import('/app.js');app.configureComparisonView(data.view);const slider=document.getElementById('timeline');slider.step='any';slider.value=String(data.seconds);slider.dispatchEvent(new Event('input',{bubbles:true}));},{view:cameraFor(baseView,spec),seconds:spec.frame/30});
        await twoFrames(page);const state=await inspect(beforeSerial),pngPath=path.join(phaseDir,key+'.png'),png=await page.locator('#viewport canvas').screenshot({path:pngPath});
        const record={key,requested:spec,png:{path:pngPath,sha256:sha(png),bytes:png.length},...state};record.validation=validateCapture(record,runtime);result.captures.push(record);return record;
      };
      for(const[clip,frame]of frameCases)await capture(`clip-${clip}-frame-${frame}`,{clip,frame,wide:clip===5?2:1});
      await capture('side-clip-0-frame-12',{clip:0,frame:12,yaw:Math.PI/2});await capture('back-clip-0-frame-12',{clip:0,frame:12,yaw:Math.PI});
      for(const clip of [5,11]){
        await page.locator('#clip').selectOption(String(clip));
        await page.evaluate(async view=>{const app=await import('/app.js');app.configureComparisonView(view);const slider=document.getElementById('timeline');slider.step='any';slider.value='0';slider.dispatchEvent(new Event('input',{bubbles:true}));},cameraFor(baseView,{wide:clip===5?2:1}));
        await page.locator('#play').click();
        const live=await page.evaluate(async({clip})=>{
          const app=await import('/app.js'),canvas=document.querySelector('#viewport canvas'),samples=[],targets=clip===5?[0,250,500,750,1000,1250,1500,1750,1950]:[0,250,500,750,1000,1200],start=performance.now();
          for(const target of targets){while(performance.now()-start<target)await new Promise(resolve=>requestAnimationFrame(resolve));const p=window.__REA_SHADER_STATE_AUDIT__.snapshot();samples.push({elapsed_ms:performance.now()-start,typed:app.inspectPlayback(),draws:p.draws.slice(-12),png:canvas.toDataURL('image/png')});}
          return {clip,actual_RAF_playback:true,samples};
        },{clip});
        await page.locator('#pause').click();
        for(const[index,sample]of live.samples.entries()){
          const png=Buffer.from(sample.png.split(',')[1],'base64'),pngPath=path.join(phaseDir,`live-clip-${clip}-${index}.png`);await writeFile(pngPath,png,{flag:'wx'});sample.png={path:pngPath,bytes:png.length,sha256:sha(png)};sample.validation=validateCapture(sample,runtime);
        }
        live.distinct_frames=new Set(live.samples.map(s=>s.typed.playback.frame)).size;live.distinct_pixel_hashes=new Set(live.samples.map(s=>s.png.sha256)).size;result.live.push(live);
      }
      await page.locator('#material-mode').selectOption('fallback');await twoFrames(page);result.fallback=await inspect();
      await page.locator('#material-mode').selectOption('source');const restoreSerial=await page.evaluate(()=>window.__REA_SHADER_STATE_AUDIT__.snapshot().draws.at(-1)?.serial??0);await twoFrames(page);result.restored=await inspect(restoreSerial);result.restored_materials=runtime.materials.map(m=>compareMaterial(m,result.restored.draws));
      result.probe=await page.evaluate(()=>window.__REA_SHADER_STATE_AUDIT__.freeze());
      for(const event of result.probe.compile){event.source_sha256=sha(Buffer.from(event.source??''));delete event.source;}
      result.materials=runtime.materials.map(m=>compareMaterial(m,result.probe.draws));
      if(phase==='after'){
        const fan=await context.newPage();await setup(fan);await page.close();await fan.goto(new URL('fan',preview.url).href,{waitUntil:'networkidle',timeout:30000});await fan.waitForFunction(()=>document.getElementById('fan-loading')?.hidden,null,{timeout:30000});
        const first=await fan.evaluate(async()=>{const m=await import('/fan.js');return m.inspectFan();});await fan.evaluate(()=>new Promise(resolve=>{const start=performance.now();function next(){if(performance.now()-start>=350)resolve();else requestAnimationFrame(next);}requestAnimationFrame(next);}));
        const last=await fan.evaluate(async()=>{const m=await import('/fan.js');return m.inspectFan();}),probe=await fan.evaluate(()=>window.__REA_SHADER_STATE_AUDIT__.freeze()),pngPath=path.join(phaseDir,'fan-live.png'),png=await fan.screenshot({path:pngPath});
        for(const event of probe.compile){event.source_sha256=sha(Buffer.from(event.source??''));delete event.source;}
        result.fan={first,last,probe,materials:runtime.materials.map(m=>compareMaterial(m,probe.draws)),png:{path:pngPath,bytes:png.length,sha256:sha(png)},actual_time_advanced:first.playback.time_seconds!==last.playback.time_seconds};
      }
    }catch(error){result.errors.push(String(error?.stack??error));receipt.errors.push(`${phase}: ${error}`);}
    finally{if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}}
    result.context_and_server_closed=true;
  }
  for(const a of receipt.phases.before.captures){const b=receipt.phases.after.captures.find(x=>x.key===a.key);if(!b)continue;receipt.comparisons.push({key:a.key,camera_identical:close(a.view.camera.viewMatrix,b.view.camera.viewMatrix,1e-10)&&close(a.view.camera.projectionMatrix,b.view.camera.projectionMatrix,1e-10),source_frame_error:Math.abs(a.typed.playback.frame-b.typed.playback.frame),bone_rotations_identical:a.typed.bones.every((bone,i)=>close(bone.rotation,b.typed.bones[i].rotation,1e-10)),...pixelDifference(await readFile(a.png.path),await readFile(b.png.path))});}
  const a=receipt.phases.after;
  receipt.input.sha256_after=sha(await readFile(input));receipt.renderer.after.sha256_after=sha(await readFile(afterFile));
  receipt.ok=receipt.errors.length===0&&a.errors.length===0&&a.probe?.errors.length===0&&a.probe.compile.every(x=>x.ok)&&a.probe.links.every(x=>x.ok)&&a.materials.every(m=>m.semantic_mismatch_fields.length===0)&&a.restored_materials.every(m=>m.semantic_mismatch_fields.length===0)&&a.captures.every(c=>Object.entries(c.validation).filter(([k])=>!['visibility','uniform_checks'].includes(k)).every(([k,v])=>k==='gl_error'?v===0:v===true))&&a.live.every(l=>l.distinct_frames>=3&&l.distinct_pixel_hashes>=2&&l.samples.every(s=>s.typed.playback.playing&&s.validation.clock_synchronized&&s.validation.visibility_matches&&s.validation.actual_GL_UV_matches&&s.validation.gl_error===0))&&a.fan?.actual_time_advanced&&a.fan.materials.every(m=>m.semantic_mismatch_fields.length===0)&&receipt.comparisons.every(c=>c.camera_identical&&c.source_frame_error<1e-7&&c.bone_rotations_identical)&&receipt.input.sha256_after===receipt.input.sha256&&receipt.renderer.after.sha256_after===receipt.renderer.after.sha256;
  receipt.finished_at=new Date().toISOString();const target=path.join(output,'iris-playback-receipt.json');await writeFile(target,JSON.stringify(receipt,compactEvidence,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({ok:receipt.ok,receipt:target,sha256:sha(await readFile(target)),errors:receipt.errors,before_errors:receipt.phases.before.errors,after_errors:a.errors,comparisons:receipt.comparisons,actual_after_live:a.live.map(l=>({clip:l.clip,samples:l.samples.length,distinct_frames:l.distinct_frames,distinct_pixels:l.distinct_pixel_hashes})),fan:a.fan?{ready:a.fan.last.ready,time_advanced:a.fan.actual_time_advanced,gl_error:a.fan.last.glError}:null},null,2));if(!receipt.ok)process.exitCode=1;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))run().catch(error=>{console.error(error);process.exitCode=1;});
