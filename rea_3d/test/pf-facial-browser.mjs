// Explicit, task-scoped browser integration. It never joins a user profile or TUS.
import path from 'node:path';
import {readFile,writeFile,mkdir,lstat} from 'node:fs/promises';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {startPreview} from '../src/preview-server.mjs';
import {glbOracle,installGlProbe,pixelDifference} from '../scripts/verify-pica-browser.mjs';
import {evaluateMaterialFrame} from '../preview/pica-runtime.js';

const ROOT='D:/REA3D-ORAS-ORIGINAL-20261023-04/player-pf-facial';
const NATIVE_ROOT='D:/REA3D-ORAS-ORIGINAL-20261023-04/player-native-pf-001';
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const PROJECT=fileURLToPath(new URL('../',import.meta.url));
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const maxError=(actual,expected)=>Math.max(...expected.map((value,index)=>Math.abs(value-actual[index])));
function args(argv){
  const options={};
  for(let i=0;i<argv.length;i++){
    const key=argv[i];if(key==='--authorized-isolated-browser')options.authorized=true;
    else if(['--input','--expectations','--output-dir'].includes(key)){
      const value=argv[++i];if(!value||value.startsWith('--'))throw new Error(`Missing ${key}`);
      options[{'--input':'input','--expectations':'expectations','--output-dir':'output'}[key]]=value;
    }else throw new Error(`Unknown argument ${key}`);
  }return options;
}

export async function runPfFacialBrowser(options){
  if(!options.authorized)throw new Error('Explicit isolated Chromium authorization required');
  for(const field of ['input','expectations','output'])if(!options[field]||!path.isAbsolute(options[field]))throw new Error('Absolute input, expectations and new output paths required');
  const output=path.resolve(options.output),roots=[ROOT,NATIVE_ROOT].map(root=>path.resolve(root));
  if(!roots.some(root=>output===root||output.startsWith(root+path.sep)))throw new Error('Output must remain within a dedicated PF test root');
  for(let current=path.dirname(output);;current=path.dirname(current)){
    try{if((await lstat(current)).isSymbolicLink())throw new Error('Output symlink ancestor rejected');}catch(error){if(error.code!=='ENOENT')throw error;}
    if(path.dirname(current)===current)break;
  }
  const bytes=await readFile(options.input),expectationBytes=await readFile(options.expectations),oracle=glbOracle(bytes),runtime=oracle.document.extras.reaPicaRuntime;
  const expectations=JSON.parse(expectationBytes);
  if(expectations.schema!=='rea3d-pf-facial-expectations-v1'||expectations.source_sha256!==runtime.facial_animation?.source_sha256)throw new Error('Independent Python source expectations must match the embedded PF source identity');
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const receipt={schema:'rea-pf-facial-browser-v1',ok:false,execution:'actual_new_isolated_chromium_real_three_preview',
    input:{path:options.input,sha256Before:sha(bytes)},expectations:{path:options.expectations,sha256:sha(expectationBytes),sourceSha256:expectations.source_sha256,origin:'independent Python source curve evaluator'},
    source:{},checks:[],selections:[],blinkReplay:[],captures:[],errors:[],console:[],blockedRequests:[],
    boundaries:{originalGameVisualParity:false,originalRuntimeEventTriggerVerified:false,automaticBlinkEnabled:false,replayOrderFromOriginalCode:true,replayTimingFromOriginalGame:false,originalBlinkUpdateRateHz:null,runtimeMasks6And7Emulated:false,manualSourceInspectionBounds:true,tusAccess:false,userBrowserProfile:false,mcpExportCapabilityEvaluated:false}};
  const check=(name,passed,details={})=>{receipt.checks.push({name,passed,...details});if(!passed)throw new Error(name);};
  for(const name of ['preview/app.js','preview/index.html','preview/pica-runtime.js','preview/pica-player.js','preview/synchronized-player.js','test/pf-facial-browser.mjs'])receipt.source[name]=sha(await readFile(path.join(PROJECT,name)));
  let preview,context;
  try{
    const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);preview=await startPreview({input:options.input,port:0});receipt.previewUrl=preview.url;
    context=await chromium.launchPersistentContext(path.join(output,'browser-profile'),{executablePath:CHROMIUM,headless:true,viewport:{width:1100,height:900},deviceScaleFactor:1,args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
    receipt.browserVersion=context.browser()?.version()??null;
    const page=context.pages()[0]??await context.newPage();
    await page.route('**/*',route=>{const url=route.request().url();if(url.startsWith('blob:')||url.startsWith('data:')||new URL(url).origin===new URL(preview.url).origin)return route.continue();receipt.blockedRequests.push(url);return route.abort('blockedbyclient');});
    page.on('pageerror',error=>receipt.errors.push(error.message));page.on('console',message=>{if(['error','warning'].includes(message.type()))receipt.console.push({type:message.type(),text:message.text()});});
    await page.addInitScript(installGlProbe);await page.goto(preview.url,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>!document.getElementById('clip').disabled||document.getElementById('status').classList.contains('error'));
    const inspect=()=>page.evaluate(async()=>{const app=await import('/app.js');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {typed:app.inspectPlayback(),facial:app.inspectFacialOverlay(),probe:window.__REA_GL_PROBE__.snapshot()};});
    check('PF controls are visible only for the source-bearing bridge model',await page.locator('#facial-controls').isVisible());
    await page.locator('#pause').click();await page.locator('#clip').selectOption('11');
    await page.locator('#timeline').evaluate(element=>{element.value='.4';element.dispatchEvent(new Event('input',{bubbles:true}));});
    const initial=await inspect();check('source PF channel0 and channel3 are available',initial.facial.groups.some(group=>group.channel===0)&&initial.facial.groups.some(group=>group.channel===3));
    const validate=(observed,values)=>{
      const state=observed.typed.playback.visual_state,expected=evaluateMaterialFrame(runtime,observed.typed.playback.clip_index,observed.typed.playback.frame,values);
      const cpu=[];
      for(const [name,material] of Object.entries(expected.material_states))for(let unit=0;unit<3;unit++){
        const error=maxError(state.material_states[name].uv_matrices[unit],material.uv_matrices[unit]);cpu.push({name,unit,error});
      }
      const latest=new Map();for(const draw of observed.probe.drawSamples)if(draw.tag?.material)latest.set(draw.tag.mesh,draw);
      const gpu=[];
      for(const draw of latest.values()){
        const material=expected.material_states[draw.tag.material];if(!material)continue;
        for(let unit=0;unit<3;unit++){
          const actual=draw.actualUv[`uUvMatrix${unit}`];
          gpu.push({mesh:draw.tag.mesh,material:draw.tag.material,unit,drawCount:draw.drawCount,error:actual?.length===9?maxError(actual,material.uv_matrices[unit]):Infinity});
        }
      }
      return {primaryFrame:state.frame,facialOverlays:state.facial_overlays,cpu,gpu,glError:observed.typed.gl_error,drawCount:observed.probe.drawCount,visible:observed.typed.actual_materials.meshes.filter(mesh=>mesh.visible).map(mesh=>mesh.source_node)};
    };
    for(const sample of expectations.samples){
      const values={'0':-1,'3':-1,[sample.channel]:sample.face_index};
      await page.evaluate(async values=>(await import('/app.js')).configureFacialOverlay(values),values);
      const observed=await inspect(),result=validate(observed,values),sourceChecks=[];
      for(const element of sample.materials){
        const unit=(element.target_id-21)/3;
        if([0,1,2].includes(unit))sourceChecks.push({material:element.name,unit,error:maxError(observed.typed.playback.visual_state.material_states[element.name].tex_coords[unit].translation,element.values)});
      }
      check(`source PF channel ${sample.channel} value ${sample.face_index} matches independent Python curves`,sourceChecks.every(item=>item.error<1e-8),{samples:sourceChecks.length});
      check(`source PF channel ${sample.channel} value ${sample.face_index} reaches actual GPU UV uniforms`,result.cpu.every(item=>item.error<1e-8)&&result.gpu.length>=9&&result.gpu.every(item=>item.error<5e-7)&&result.gpu.some(item=>item.material==='Eye')&&result.glError===0);
      check(`source PF channel ${sample.channel} value ${sample.face_index} retains paused primary bones`,JSON.stringify(observed.typed.bones)===JSON.stringify(initial.typed.bones));
      receipt.selections.push({channel:sample.channel,faceIndex:sample.face_index,sourceChecks,result});
      if(sample.channel===0&&[0,1,2,4,8].includes(sample.face_index)){
        const filename=path.join(output,`eye-value-${sample.face_index}.png`),image=await page.locator('#viewport canvas').screenshot({path:filename});receipt.captures.push({faceIndex:sample.face_index,path:filename,sha256:sha(image),bytes:image.length});
      }
    }
    const open=receipt.captures.find(c=>c.faceIndex===0),closed=receipt.captures.find(c=>c.faceIndex===2);
    receipt.eyePixelDifference=pixelDifference(await readFile(open.path),await readFile(closed.path));
    check('PF open and closed eye source values change actual rendered pixels',receipt.eyePixelDifference.changedPixels>0);
    const beforeInvalid=await inspect();
    const invalid=await page.evaluate(async()=>{const app=await import('/app.js'),results=[];
      for(const spec of [null,[],{'0':9},{'3':8},{'0':1,'3':8},{'00':1},{'1':0},{'0':1.5}]){
        const before=JSON.stringify(app.inspectFacialOverlay());let rejected=false;try{app.configureFacialOverlay(spec);}catch{rejected=true;}
        results.push({spec,rejected,stateUnchanged:before===JSON.stringify(app.inspectFacialOverlay())});
      }return results;
    });receipt.invalidArguments=invalid;
    check('invalid facial requests are rejected atomically',invalid.every(item=>item.rejected&&item.stateUnchanged));
    const originalOrder=runtime.facial_animation.control.automatic_blink.values;
    await page.locator('#clip').selectOption('5');await page.locator('#play').click();
    for(const value of originalOrder){
      await page.evaluate(async value=>(await import('/app.js')).configureFacialOverlay({'0':value,'3':-1}),value);
      const observed=await inspect(),result=validate(observed,{'0':value,'3':-1});
      receipt.blinkReplay.push({value,sourceFrame:value*10,renderFrame:observed.typed.render_frame,playing:observed.typed.playback.playing,bones:observed.typed.bones,result});
    }
    const replay=receipt.blinkReplay;
    check('source blink order replays during real primary RAF draws with independent material time',replay.length===9&&replay.every(item=>item.playing&&item.result.gpu.every(draw=>draw.error<5e-7)&&item.result.glError===0)&&new Set(replay.map(item=>item.result.primaryFrame)).size>1&&replay.at(-1).renderFrame>replay[0].renderFrame);
    check('primary bones keep moving during explicit eye replay',new Set(replay.map(item=>JSON.stringify(item.bones))).size>1);
    await page.locator('#pause').click();
    const final=await inspect();receipt.finalProbe=final.probe;
    check('all actual shader compilations and program links succeeded',final.probe.shaderEvents.length>0&&final.probe.shaderEvents.every(event=>event.ok)&&final.probe.links.length>0&&final.probe.links.every(event=>event.ok));
    check('no app errors or external requests occurred',receipt.errors.length===0&&receipt.blockedRequests.length===0);
    receipt.input.sha256After=sha(await readFile(options.input));check('input GLB bytes remain unchanged',receipt.input.sha256After===receipt.input.sha256Before);receipt.ok=true;
  }catch(error){receipt.failure={message:error.message,stack:error.stack};}
  finally{
    if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}
    await writeFile(path.join(output,'pf-facial-browser-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  }return receipt;
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  try{const options=args(process.argv.slice(2)),receipt=await runPfFacialBrowser(options);console.log(JSON.stringify({ok:receipt.ok,checks:receipt.checks.length,selections:receipt.selections.length,replaySamples:receipt.blinkReplay.length,output:options.output,failure:receipt.failure?.message}));if(!receipt.ok)process.exitCode=1;}
  catch(error){console.error(error.message);process.exitCode=1;}
}
