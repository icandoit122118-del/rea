// Explicit integration runner. It never joins a user browser or accesses TUS.
// Run manually with --authorized-isolated-browser --input ABS --output-dir NEW_ABS.
import path from 'node:path';
import {readFile,writeFile,mkdir,lstat} from 'node:fs/promises';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {Quaternion} from 'three';
import {startPreview} from '../src/preview-server.mjs';
import {glbOracle,installGlProbe,pixelDifference} from '../scripts/verify-pica-browser.mjs';

const ROOT='D:/REA3D-ORAS-ORIGINAL-20261023-04/player-face-control';
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const project=fileURLToPath(new URL('../',import.meta.url));
const sha=data=>createHash('sha256').update(data).digest('hex');
// Animation keys are float32 and can have length 1 +/- a few ulps. Angular
// distance compares orientations, so normalize both without changing the app.
const angle=(a,b)=>new Quaternion(...a).normalize().angleTo(new Quaternion(...b).normalize());
function args(argv){
  const options={};
  for(let i=0;i<argv.length;i++){
    const key=argv[i];
    if(key==='--authorized-isolated-browser')options.authorized=true;
    else if(['--input','--output-dir'].includes(key)){const value=argv[++i];if(!value||value.startsWith('--'))throw new Error(`Missing ${key}`);options[key==='--input'?'input':'output']=value;}
    else throw new Error(`Unknown argument ${key}`);
  }
  return options;
}

export async function runFaceControlBrowser(options){
  if(!options.authorized)throw new Error('Explicit isolated Chromium authorization is required');
  if(!options.input||!path.isAbsolute(options.input)||!options.output||!path.isAbsolute(options.output))throw new Error('Absolute input and new output paths required');
  const output=path.resolve(options.output),root=path.resolve(ROOT);
  if(output!==root&&!output.startsWith(root+path.sep))throw new Error('Output must be the dedicated D: face-control root or a new child');
  // Refuse existing outputs and symlink ancestors before creating a private profile.
  for(let current=path.dirname(output);;current=path.dirname(current)){
    try{if((await lstat(current)).isSymbolicLink())throw new Error('Symlink output ancestor rejected');}
    catch(error){if(error.code!=='ENOENT')throw error;}
    if(path.dirname(current)===current)break;
  }
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const bytes=await readFile(options.input),oracle=glbOracle(bytes),runtime=oracle.document.extras.reaPicaRuntime;
  const receipt={schema:'rea-fixed-face-control-browser-v1',ok:false,execution:'actual_isolated_headless_chromium_real_three_app',
    input:{path:options.input,sha256Before:sha(bytes)},source:{},checks:[],poseSelections:[],rafSamples:[],captures:[],errors:[],console:[],blockedRequests:[],
    boundaries:{tusAccess:false,userChromeProfile:false,originalGameRuntimeTriggerVerified:false,originalGameVisualParity:false,
      nativeStaticMapping:'selected ORAS PB25/PK33, channel3, face_value*10 source frame',manualSourcePoseSelection:true,
      sourceFrameRateMeaning:'source fps interpretation from exported GLB',originalValues6And7MayRemapTo0:true,rawLuaCommandEmulation:false}};
  const check=(name,passed,details={})=>{receipt.checks.push({name,passed,...details});if(!passed)throw new Error(name);};
  for(const name of ['preview/synchronized-player.js','preview/app.js','preview/index.html','test/synchronized-player.test.mjs','test/face-control-browser.mjs'])receipt.source[name]=sha(await readFile(path.join(project,name)));
  let preview,context;
  try{
    const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);
    preview=await startPreview({input:options.input,port:0});receipt.localUrl=preview.url;
    context=await chromium.launchPersistentContext(path.join(output,'browser-profile'),{executablePath:CHROMIUM,headless:true,
      viewport:{width:1100,height:900},deviceScaleFactor:1,args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],
      env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
    receipt.browserVersion=context.browser()?.version()??null;
    const page=context.pages()[0]||await context.newPage();
    await page.route('**/*',route=>{const url=route.request().url();if(new URL(url).origin===new URL(preview.url).origin||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();receipt.blockedRequests.push(url);return route.abort('blockedbyclient');});
    page.on('pageerror',error=>receipt.errors.push(error.message));
    page.on('console',message=>{if(['error','warning'].includes(message.type()))receipt.console.push({type:message.type(),text:message.text()});});
    await page.addInitScript(installGlProbe);await page.goto(preview.url,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>!document.getElementById('clip').disabled||document.getElementById('status').classList.contains('error'));
    const inspect=()=>page.evaluate(async()=>{const app=await import('/app.js');return {typed:app.inspectPlayback(),face:app.inspectFaceControl(),selectedClip:document.getElementById('clip').value,selectedFace:document.getElementById('face-value').value,faceLabel:document.getElementById('face-frame').textContent,faceError:document.getElementById('face-control-status').textContent,probe:window.__REA_GL_PROBE__.snapshot()};});
    check('actual app loaded',Boolean((await inspect()).typed.ready));
    await page.locator('#pause').click();await page.locator('#clip').selectOption('11');
    await page.locator('#timeline').evaluate(element=>{element.value='.4';element.dispatchEvent(new Event('input',{bubbles:true}));});
    const base=await inspect(),auxiliary=runtime.clips.map((binding,index)=>({binding,index})).filter(({binding})=>binding.role==='auxiliary_pose');
    check('both selected PB25 and PK33 are available',auxiliary.length===2);
    for(const {binding,index} of auxiliary){
      await page.locator('#auxiliary').selectOption(String(index));
      for(let value=0;value<=7;value++){
        await page.locator('#face-value').selectOption(String(value));const state=await inspect(),sourceFrame=value*10;
        check(`auxiliary ${index} face ${value} preserves paused primary selection and time`,state.selectedClip==='11'&&state.typed.playback.time_seconds===base.typed.playback.time_seconds&&!state.typed.playback.playing);
        check(`auxiliary ${index} face ${value} exact source frame`,state.face.source_frame===sourceFrame&&state.face.mode==='fixed_source_pose'&&!state.faceError);
        const rotations=binding.auxiliary_pose.channel_mask.map(target=>({bone:target.bone,actual:state.typed.bones.find(bone=>bone.name===target.bone).rotation,expected:oracle.rotation(index,target.bone,sourceFrame/runtime.source_fps)}));
        check(`auxiliary ${index} face ${value} matches source quaternion oracle`,rotations.every(item=>angle(item.actual,item.expected)<1e-6),{maximumAngleRadians:Math.max(...rotations.map(item=>angle(item.actual,item.expected)))});
        receipt.poseSelections.push({auxiliaryIndex:index,faceValue:value,sourceFrame,primaryTime:state.typed.playback.time_seconds,rotations});
        if(index===auxiliary[0].index&&[0,1,3,7].includes(value)){
          const filename=path.join(output,`fixed-face-${value}-frame-${sourceFrame}.png`),image=await page.locator('#viewport canvas').screenshot({path:filename});
          receipt.captures.push({faceValue:value,path:filename,sha256:sha(image),bytes:image.length});
        }
      }
    }
    receipt.facePixelDifference=pixelDifference(await readFile(receipt.captures[0].path),await readFile(receipt.captures[1].path));
    check('fixed face changes actual rendered pixels',receipt.facePixelDifference.changedPixels>0);
    await page.locator('#face-value').selectOption('3');
    for(const primary of [11,5]){
      await page.locator('#pause').click();await page.locator('#clip').selectOption(String(primary));
      await page.locator('#timeline').evaluate(element=>{element.value='0';element.dispatchEvent(new Event('input',{bubbles:true}));});
      const before=await inspect();await page.locator('#play').click();
      const samples=await page.evaluate(async minimumSpan=>{
        const app=await import('/app.js'),samples=[],start=performance.now();
        await new Promise((resolve,reject)=>{const tick=()=>{const typed=app.inspectPlayback();if(!samples.length||typed.render_frame>samples.at(-1).render_frame)samples.push({...typed,probe:window.__REA_GL_PROBE__.snapshot()});
          if(samples.length>=8&&Math.max(...samples.map(s=>s.playback.time_seconds))-Math.min(...samples.map(s=>s.playback.time_seconds))>=minimumSpan)resolve();
          else if(performance.now()-start>8000)reject(new Error('Actual primary RAF time did not advance'));else requestAnimationFrame(tick);};requestAnimationFrame(tick);});return samples;
      },primary===5?1.45:.6);
      await page.locator('#pause').click();receipt.rafSamples.push({primaryIndex:primary,before:before.typed,samples});
      check(`primary ${primary} real RAF time advances while source frame remains 30`,samples.every(sample=>sample.playback.playing&&sample.playback.auxiliary_source_frame===30&&sample.playback.auxiliary_time_seconds===1)&&new Set(samples.map(sample=>sample.playback.time_seconds)).size>=8);
      const jaw=before.typed.bones.find(bone=>bone.name==='Jaw').rotation;
      check(`primary ${primary} Jaw remains fixed across actual RAF draws`,samples.every(sample=>angle(sample.bones.find(bone=>bone.name==='Jaw').rotation,jaw)<1e-6));
      check(`primary ${primary} visual layer uses primary time`,samples.every(sample=>Math.abs(sample.playback.visual_state.frame-sample.playback.time_seconds*runtime.source_fps)<1e-6));
      check(`primary ${primary} actual WebGL draws continue without error`,samples.every(sample=>sample.gl_error===0&&sample.draw_calls>0)&&samples.at(-1).probe.drawCount>samples[0].probe.drawCount);
      if(primary===11)check('actual primary material states change while Jaw fixed',new Set(samples.map(sample=>JSON.stringify(sample.playback.visual_state.material_states))).size>1);
      if(primary===5)check('actual primary visibility changes while Jaw fixed',new Set(samples.map(sample=>JSON.stringify(sample.playback.visual_state.visibility))).size>1);
    }
    const attached=await inspect();await page.locator('#auxiliary').selectOption('');const detached=await inspect();
    check('explicit detach retains primary selection and time',detached.selectedClip===attached.selectedClip&&detached.typed.playback.time_seconds===attached.typed.playback.time_seconds&&detached.face.source_frame===null&&detached.face.face_value===-1);
    const primaryJaw=oracle.rotation(Number(detached.selectedClip),'Jaw',detached.typed.playback.time_seconds);
    const detachedJaw=detached.typed.bones.find(bone=>bone.name==='Jaw').rotation;
    receipt.detach={attached:attached.typed,detached:detached.typed,expectedJaw:primaryJaw,actualJaw:detachedJaw,angularError:angle(detachedJaw,primaryJaw)};
    check('detach restores the primary Jaw quaternion',receipt.detach.angularError<1e-6,{angularError:receipt.detach.angularError});
    const probe=detached.probe;receipt.finalProbe=probe;
    check('all actual shader compilations and program links passed',probe.shaderEvents.length>0&&probe.shaderEvents.every(event=>event.ok)&&probe.links.length>0&&probe.links.every(event=>event.ok));
    check('no app errors or external requests',receipt.errors.length===0&&receipt.blockedRequests.length===0);
    receipt.input.sha256After=sha(await readFile(options.input));check('input GLB unchanged',receipt.input.sha256After===receipt.input.sha256Before);
    receipt.ok=true;
  }catch(error){receipt.failure={message:error.message,stack:error.stack};}
  finally{
    if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}
    await writeFile(path.join(output,'face-control-browser-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  }
  return receipt;
}

if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  try{const options=args(process.argv.slice(2)),receipt=await runFaceControlBrowser(options);console.log(JSON.stringify({ok:receipt.ok,output:options.output,checks:receipt.checks.length,failure:receipt.failure?.message}));if(!receipt.ok)process.exitCode=1;}
  catch(error){console.error(error.message);process.exitCode=1;}
}
