// Explicit, bounded capture of a fresh isolated preview. No existing browser/TUS access.
import path from 'node:path';
import {readFile,writeFile,mkdir,lstat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {startPreview} from '../src/preview-server.mjs';
import {glbOracle} from './verify-pica-browser.mjs';
import {validateFacialValue} from '../preview/pica-runtime.js';

export const OUTPUT_ROOT='D:/REA3D-ORAS-ORIGINAL-20261023-04/frame-comparison';
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const PROJECT=fileURLToPath(new URL('../',import.meta.url));
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const HELP=`Capture selected source frames from the existing local GLB in a new isolated Chromium.
node scripts/capture-player-frames.mjs --authorized-isolated-browser --input ABS_GLB --clip-index INTEGER --frames '[0,10,20]' --output-dir NEW_ABS_DIR [--width 400 --height 240] [--crop '[x,y,width,height]'] [--view-config ABS_JSON] [--auxiliary-index INTEGER --face-value INTEGER] [--facial-values '{"0":1,"3":0}']
Output must be ${OUTPUT_ROOT} or a new child. Frames are integer source frames, not wall-clock ticks.
Auxiliary poses remain detached unless both bounded manual face arguments are given. Camera uses the existing fit by default or an explicit view-config; original camera/clip/phase/face triggers and visual parity are unverified.
No original-reference match/pass is assigned. The runner refuses existing output directories.`;

export function parseCaptureArgs(argv){
  const options={width:400,height:240};const seen=new Set();
  for(let i=0;i<argv.length;i++){
    const key=argv[i];if(seen.has(key))throw new Error(`Duplicate argument ${key}`);seen.add(key);
    if(key==='--help'){options.help=true;continue;}
    if(key==='--authorized-isolated-browser'){options.authorized=true;continue;}
    const keys={'--input':'input','--clip-index':'clipIndex','--frames':'frames','--output-dir':'output','--width':'width','--height':'height','--crop':'crop','--view-config':'viewConfig','--auxiliary-index':'auxiliaryIndex','--face-value':'faceValue','--facial-values':'facialValues'};
    if(!keys[key])throw new Error(`Unsupported argument ${key}`);
    const value=argv[++i];if(value===undefined||value.startsWith('--'))throw new Error(`Missing value for ${key}`);
    if(['--frames','--crop','--facial-values'].includes(key)){try{options[keys[key]]=JSON.parse(value);}catch{throw new Error(`Invalid JSON for ${key}`);}}
    else options[keys[key]]=['--clip-index','--width','--height','--auxiliary-index','--face-value'].includes(key)?Number(value):value;
  }
  return options;
}

export function validateCaptureRequest(options,oracle){
  if(!Number.isInteger(options.clipIndex)||options.clipIndex<0||!oracle.document.animations?.[options.clipIndex])throw new Error('An explicit existing primary clip index is required');
  const runtime=oracle.document.extras?.reaPicaRuntime,binding=runtime?.clips?.[options.clipIndex];
  if(!binding||binding.role==='auxiliary_pose')throw new Error('Select a primary clip, not an auxiliary pose');
  const fps=runtime.source_fps,duration=oracle.duration(options.clipIndex);
  if(!Number.isFinite(fps)||fps<=0||!Number.isFinite(duration)||duration<0)throw new Error('Verified exported source-fps interpretation and finite clip duration required');
  if(!Array.isArray(options.frames)||options.frames.length<1||options.frames.length>64||new Set(options.frames).size!==options.frames.length)throw new Error('Explicit frames must contain 1..64 unique source frames');
  for(const frame of options.frames)if(!Number.isInteger(frame)||frame<0||frame>100000||frame/fps>duration+1e-6)throw new Error(`Source frame ${frame} is outside the clip bounds`);
  if(!Number.isInteger(options.width)||!Number.isInteger(options.height)||options.width<16||options.height<16||options.width>2048||options.height>2048)throw new Error('Canvas width and height must be integers within 16..2048');
  const crop=options.crop??[0,0,options.width,options.height];
  if(!Array.isArray(crop)||crop.length!==4||crop.some(v=>!Number.isInteger(v))||crop[0]<0||crop[1]<0||crop[2]<1||crop[3]<1||crop[0]+crop[2]>options.width||crop[1]+crop[3]>options.height)throw new Error('Explicit crop must remain within the canvas');
  const manualFace=options.auxiliaryIndex!==undefined||options.faceValue!==undefined;
  let face=null;
  if(manualFace){
    const auxiliary=runtime.clips[options.auxiliaryIndex],metadata=auxiliary?.auxiliary_pose;
    if(!Number.isInteger(options.auxiliaryIndex)||auxiliary?.role!=='auxiliary_pose'||!metadata?.channel_mask?.length)throw new Error('Explicit auxiliary index must identify a verified source mask');
    if(!Number.isInteger(options.faceValue)||options.faceValue<0||options.faceValue>7)throw new Error('Explicit manual face value must be an integer from 0 to 7');
    const frame=options.faceValue*10,time=frame/fps,auxiliaryDuration=oracle.duration(options.auxiliaryIndex);
    if(!Number.isInteger(metadata.source_frame_count)||frame>=metadata.source_frame_count||!Number.isFinite(auxiliaryDuration)||time>auxiliaryDuration+1e-6)throw new Error('Explicit manual face value exceeds the auxiliary source bounds');
    face={auxiliaryIndex:options.auxiliaryIndex,value:options.faceValue,sourceFrame:frame,sourceTimeSeconds:time,manualSourcePose:true,originalRuntimeTriggerVerified:false};
  }
  const facialValues=options.facialValues===undefined?{}:options.facialValues;
  if(typeof facialValues!=='object'||facialValues===null||Array.isArray(facialValues)||Object.keys(facialValues).length>6)throw new Error('Facial values must be a bounded channel-value object');
  for(const [key,value] of Object.entries(facialValues)){
    const channel=Number(key);if(String(channel)!==key)throw new Error('Facial channel keys must be canonical integers');
    validateFacialValue(runtime,channel,value);
  }
  return {fps,duration,clipName:oracle.document.animations[options.clipIndex].name,binding,crop,face,facialValues};
}

// Installed only before app load in a newly created task-specific test page.
// draw callbacks queue a microtask; readback occurs after the synchronous render has ended.
export function installFrameCaptureObserver(){
  let reader=null,pending=null,scheduled=false,drawCount=0,lastCamera=null;
  const programs=new WeakMap();
  function draw(gl){
    drawCount++;
    const program=gl.getParameter(gl.CURRENT_PROGRAM),locations=programs.get(program);
    if(locations){const get=name=>locations[name]?Array.from(gl.getUniform(program,locations[name])??[]):null;
      lastCamera={projectionMatrix:get('projectionMatrix'),viewMatrix:get('viewMatrix')};}
    if(!pending||scheduled||gl.getParameter(gl.FRAMEBUFFER_BINDING)!==null)return;
    scheduled=true;queueMicrotask(()=>{
      const request=pending;pending=null;scheduled=false;
      try{
        if(!reader)throw new Error('Read-only QA snapshot is not bound');
        const width=gl.drawingBufferWidth,height=gl.drawingBufferHeight;
        if(width*height>2048*2048)throw new Error('Readback exceeds the bounded canvas area');
        const pixels=new Uint8Array(width*height*4);gl.finish();gl.readPixels(0,0,width,height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
        const state=reader(),glError=gl.getError();
        const [x,y,w,h]=request.crop;
        if(x<0||y<0||x+w>width||y+h>height)throw new Error('Crop no longer fits the actual framebuffer');
        const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
        const context=canvas.getContext('2d'),image=context.createImageData(w,h);
        for(let row=0;row<h;row++)image.data.set(pixels.subarray(((height-1-y-row)*width+x)*4,((height-1-y-row)*width+x+w)*4),row*w*4);
        context.putImageData(image,0,0);
        request.resolve({token:request.token,afterRenderObserver:true,drawCount,width,height,crop:request.crop,glError,camera:lastCamera,state,pngDataUrl:canvas.toDataURL('image/png')});
      }catch(error){request.reject(error);}
    });
  }
  for(const prototype of [window.WebGLRenderingContext?.prototype,window.WebGL2RenderingContext?.prototype].filter(Boolean)){
    const locate=prototype.getUniformLocation;prototype.getUniformLocation=function(program,name){const result=locate.call(this,program,name);if(result&&['projectionMatrix','viewMatrix'].includes(name)){let locations=programs.get(program);if(!locations){locations={};programs.set(program,locations);}locations[name]=result;}return result;};
    for(const name of ['drawElements','drawArrays']){const original=prototype[name];prototype[name]=function(...args){const result=original.apply(this,args);draw(this);return result;};}
  }
  Object.defineProperty(window,'__REA_FRAME_CAPTURE__',{value:{bindSnapshot(fn){reader=fn;},capture(token,crop){
    if(pending)throw new Error('A frame capture is already armed');
    return new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{if(pending?.token===token)pending=null;reject(new Error('No rendered frame within ten seconds'));},10000);
      pending={token,crop,resolve:value=>{clearTimeout(timeout);resolve(value);},reject:error=>{clearTimeout(timeout);reject(error);}};
    });
  }}});
}

const poseState=packet=>({playback:packet.state.playback,bones:packet.state.bones,actual_materials:packet.state.actual_materials});

export async function capturePlayerFrames(options){
  if(!options.authorized)throw new Error('Explicit isolated Chromium authorization flag required');
  if(!options.input||!path.isAbsolute(options.input)||!options.output||!path.isAbsolute(options.output))throw new Error('Absolute input and fresh output directory required');
  const output=path.resolve(options.output),root=path.resolve(OUTPUT_ROOT),normalized=output.toLowerCase(),base=root.toLowerCase();
  if(normalized!==base&&!normalized.startsWith(base+path.sep))throw new Error('Output must stay within the dedicated frame-comparison root');
  const sourceStat=await lstat(options.input);if(!sourceStat.isFile()||sourceStat.isSymbolicLink()||sourceStat.size<20||sourceStat.size>256*1024*1024)throw new Error('Input must be a regular GLB file no larger than 256 MiB');
  const bytes=await readFile(options.input),oracle=glbOracle(bytes),request=validateCaptureRequest(options,oracle);
  let viewConfig={basis:'existing_player_fitted_camera',view:{}};
  if(options.viewConfig){
    if(!path.isAbsolute(options.viewConfig))throw new Error('View-config path must be absolute');
    const info=await lstat(options.viewConfig);if(!info.isFile()||info.isSymbolicLink()||info.size>16384)throw new Error('View-config must be a regular JSON file no larger than 16 KiB');
    const data=await readFile(options.viewConfig);viewConfig=JSON.parse(data);
    if(!['inferred_alignment','original_measurement_claim'].includes(viewConfig.basis)||!viewConfig.view||typeof viewConfig.view!=='object'||Array.isArray(viewConfig.view))throw new Error('View-config requires basis inferred_alignment/original_measurement_claim and a view object');
    if('canvas_size'in viewConfig.view)throw new Error('Set capture canvas size with --width/--height');
    viewConfig={...viewConfig,path:options.viewConfig,sha256:sha(data)};
  }
  for(let current=path.dirname(output);;current=path.dirname(current)){try{if((await lstat(current)).isSymbolicLink())throw new Error('Output symlink ancestor rejected');}catch(error){if(error.code!=='ENOENT')throw error;}if(path.dirname(current)===current)break;}
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const receipt={schema:'rea-exact-source-frame-capture-v1',ok:false,execution:'actual_new_isolated_chromium_preview_framebuffer_readback',
    input:{path:options.input,sha256Before:sha(bytes)},request:{primaryClipIndex:options.clipIndex,sourceFrames:options.frames,sourceFps:request.fps,clipName:request.clipName,canvasWidth:options.width,canvasHeight:options.height,crop:request.crop,manualFace:request.face,facialValues:request.facialValues},
    camera:{mode:viewConfig.basis,customSetterAvailable:true,originalCameraVerified:false,requestedConfig:viewConfig},captures:[],source:{},errors:[],console:[],blockedRequests:[],
    boundaries:{originalReferenceMatchEvaluated:false,originalCameraClipPhaseConfirmed:false,originalRuntimeTriggerVerified:false,
      sourceFpsIsExportInterpretation:true,auxiliaryPoseDetached:request.face===null,manualSourcePoseSelection:request.face!==null,rawLuaCommandEmulation:false,originalFace6And7MayRemapTo0:true,pausedEffectiveMixerDelta:0,framebufferAlphaPreserved:true,cssBackgroundIncluded:false,tusAccess:false,userBrowserProfile:false,scriptOrSequenceLanguageUnconfirmed:true}};
  for(const name of ['preview/app.js','preview/synchronized-player.js','preview/pica-player.js','preview/pica-runtime.js','preview/index.html','src/preview-server.mjs','scripts/capture-player-frames.mjs'])receipt.source[name]=sha(await readFile(path.join(PROJECT,name)));
  let preview,context;
  try{
    const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);preview=await startPreview({input:options.input,port:0});
    if(preview.sha256!==receipt.input.sha256Before)throw new Error('Input changed before immutable preview snapshot');
    receipt.previewUrl=preview.url;
    context=await chromium.launchPersistentContext(path.join(output,'browser-profile'),{executablePath:CHROMIUM,headless:true,viewport:{width:Math.max(1000,options.width+320),height:Math.max(700,options.height+200)},deviceScaleFactor:1,
      args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
    receipt.browserVersion=context.browser()?.version()??null;
    const page=context.pages()[0]??await context.newPage();
    await page.route('**/*',route=>{const url=route.request().url();if(url.startsWith('blob:')||url.startsWith('data:')||new URL(url).origin===new URL(preview.url).origin)return route.continue();receipt.blockedRequests.push(url);return route.abort('blockedbyclient');});
    page.on('pageerror',error=>receipt.errors.push(error.message));page.on('console',message=>{if(['error','warning'].includes(message.type()))receipt.console.push({type:message.type(),text:message.text()});});
    await page.addInitScript(installFrameCaptureObserver);await page.goto(preview.url,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>!document.getElementById('clip').disabled||document.getElementById('status').classList.contains('error'));
    await page.locator('#pause').click();await page.locator('#clip').selectOption(String(options.clipIndex));await page.locator('#auxiliary').selectOption('');
    if(request.face){
      await page.locator('#auxiliary').selectOption(String(request.face.auxiliaryIndex));
      await page.locator('#face-value').selectOption(String(request.face.value));
    }
    if(Object.keys(request.facialValues).length)receipt.facialActual=await page.evaluate(async values=>(await import('/app.js')).configureFacialOverlay(values),request.facialValues);
    receipt.camera.actual=await page.evaluate(async({width,height,view})=>{
      const app=await import('/app.js');
      app.configureComparisonView({canvas_size:[width,height]});document.getElementById('reset').click();
      if(Object.keys(view).length)app.configureComparisonView(view);
      window.__REA_FRAME_CAPTURE__.bindSnapshot(()=>({...app.inspectPlayback(),comparisonView:app.inspectComparisonView(),facialOverlay:app.inspectFacialOverlay()}));
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return app.inspectComparisonView();
    },{width:options.width,height:options.height,view:viewConfig.view});
    receipt.camera.invalidArgumentRegression=await page.evaluate(async()=>{
      const app=await import('/app.js'),results=[];
      const before=JSON.stringify(app.inspectComparisonView());
      const target=app.inspectComparisonView().camera.target;
      for(const [name,spec] of [['null',null],['unknown',{unknown:1}],['fov',{fov:NaN}],['null-near',{near:null}],['near',{near:0}],['far',{far:-1}],['aspect',{aspect:Infinity}],['position',{position:[Infinity,0,0]}],['coincident-target',{position:target,target}],['canvas-size',{canvas_size:[0,240]}],['canvas-aspect',{canvas_size:[16,2048]}]]){
        let rejected=false;try{app.configureComparisonView(spec);}catch{rejected=true;}
        results.push({name,rejected,stateUnchanged:JSON.stringify(app.inspectComparisonView())===before});
      }
      return results;
    });
    if(receipt.camera.invalidArgumentRegression.some(result=>!result.rejected||!result.stateUnchanged))throw new Error('Invalid camera configuration changed QA state');
    for(const frame of options.frames){
      const seconds=frame/request.fps;
      // Avoid HTML range step rounding before the player's bounded seek handles GLB float32 endpoints.
      await page.locator('#timeline').evaluate((element,value)=>{element.step='any';element.max=String(Math.max(Number(element.max),value));element.value=String(value);element.dispatchEvent(new Event('input',{bubbles:true}));},seconds);
      const packet=await page.evaluate(({token,crop})=>window.__REA_FRAME_CAPTURE__.capture(token,crop),{token:`clip-${options.clipIndex}-frame-${frame}`,crop:request.crop});
      const stable=await page.evaluate(({token,crop})=>window.__REA_FRAME_CAPTURE__.capture(token,crop),{token:`clip-${options.clipIndex}-frame-${frame}-repeat`,crop:request.crop});
      for(const observed of [packet,stable]){
        const s=observed.state;
        if(!s.ready||s.playback.playing||s.playback.clip_index!==options.clipIndex||s.playback.auxiliary_index!==(request.face?.auxiliaryIndex??null)||Math.abs(s.playback.frame-frame)>1e-4)throw new Error(`Requested source frame ${frame} did not remain paused and exact`);
        if(request.face&&(s.playback.face_value!==request.face.value||s.playback.auxiliary_source_frame!==request.face.sourceFrame||Math.abs(s.playback.auxiliary_time_seconds-request.face.sourceTimeSeconds)>1e-6))throw new Error('Requested manual face did not remain at its exact fixed source frame');
        for(const [key,value] of Object.entries(request.facialValues))if(s.facialOverlay.values[key]!==value)throw new Error('Requested PF facial source state changed during capture');
        if(observed.glError||s.gl_error||!s.draw_calls||observed.width!==options.width||observed.height!==options.height)throw new Error('Frame readback or canvas-size validation failed');
      }
      const poseHash=sha(JSON.stringify(poseState(packet))),repeatPoseHash=sha(JSON.stringify(poseState(stable)));
      const png=Buffer.from(packet.pngDataUrl.split(',')[1],'base64'),repeatPng=Buffer.from(stable.pngDataUrl.split(',')[1],'base64');
      if(poseHash!==repeatPoseHash||sha(png)!==sha(repeatPng)||stable.state.render_frame<=packet.state.render_frame)throw new Error(`Paused source frame ${frame} changed across redraws`);
      const stem=`clip-${String(options.clipIndex).padStart(2,'0')}-source-${String(frame).padStart(5,'0')}`;
      const pngPath=path.join(output,stem+'.png'),statePath=path.join(output,stem+'.json');
      const metadata={schema:'rea-captured-source-frame-v1',primaryClipIndex:options.clipIndex,requestedSourceFrame:frame,requestedSeconds:seconds,
        sourceFps:request.fps,afterRenderObserver:true,renderFrame:packet.state.render_frame,repeatRenderFrame:stable.state.render_frame,drawCount:packet.drawCount,
        pausedEffectiveMixerDelta:0,poseSha256:poseHash,repeatPoseSha256:repeatPoseHash,repeatedPngIdentical:true,camera:packet.camera,comparisonView:packet.state.comparisonView,
        framebuffer:{width:packet.width,height:packet.height,crop:request.crop,alphaPreserved:true,cssBackgroundIncluded:false,glError:packet.glError},
        png:{path:pngPath,bytes:png.length,sha256:sha(png)},playback:packet.state.playback,bones:packet.state.bones,
        visualState:packet.state.playback.visual_state,actualMaterials:packet.state.actual_materials,facialOverlay:packet.state.facialOverlay,
        originalReferenceMatchEvaluated:false,originalCameraClipPhaseConfirmed:false};
      await writeFile(pngPath,png,{flag:'wx'});const json=JSON.stringify(metadata,null,2)+'\n';await writeFile(statePath,json,{flag:'wx'});
      receipt.captures.push({primaryClipIndex:options.clipIndex,sourceFrame:frame,png:metadata.png,state:{path:statePath,sha256:sha(json)},poseSha256:poseHash,renderFrame:metadata.renderFrame,repeatedPngIdentical:true});
    }
    if(receipt.errors.length||receipt.blockedRequests.length)throw new Error('App error or unexpected network request during isolated capture');
    receipt.ok=true;
  }catch(error){receipt.failure={message:error.message,stack:error.stack};}
  finally{
    if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}
    receipt.input.sha256After=sha(await readFile(options.input));if(receipt.input.sha256After!==receipt.input.sha256Before){receipt.ok=false;receipt.failure={message:'Source GLB changed'};}
    await writeFile(path.join(output,'frame-capture-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  }
  return receipt;
}

if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  try{const options=parseCaptureArgs(process.argv.slice(2));if(options.help)console.log(HELP);else{const result=await capturePlayerFrames(options);console.log(JSON.stringify({ok:result.ok,captures:result.captures.length,output:options.output,failure:result.failure?.message}));if(!result.ok)process.exitCode=1;}}
  catch(error){console.error(error.message);process.exitCode=1;}
}
