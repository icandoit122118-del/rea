import path from 'node:path';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {startPreview} from '../src/preview-server.mjs';
import {installBenchmarkProbe} from './benchmark-probe.mjs';
const PROJECT=fileURLToPath(new URL('../',import.meta.url)),ROOT='C:/rea3d-validation';
const INPUT=path.join(ROOT,'baseline/model.glb'),EXPECTED='e8bd5ccb7524170842037ef5d67286d1f3e2c197896bad2812bde673c63a3b76';
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const sha=data=>createHash('sha256').update(data).digest('hex');
if(!process.argv.includes('--authorized-isolated-browser'))throw new Error('Explicit isolated-browser authorization required');
const quick=process.argv.includes('--quick'),stamp=process.argv.find(a=>a.startsWith('--name='))?.slice(7)||'run-001';
if(!/^[a-z0-9-]+$/.test(stamp))throw new Error('Invalid new-output name');
const output=path.join(ROOT,'performance',stamp);await mkdir(path.dirname(output),{recursive:true});await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
if(sha(await readFile(INPUT))!==EXPECTED)throw new Error('Baseline hash mismatch');
const preview=await startPreview({input:INPUT,port:0}),origin=new URL(preview.url).origin;
const stored={};for(const route of ['/app.js','/fan.js','/style.css','/fan.css','/pica-player.js','/pica-runtime.js','/synchronized-player.js','/vendor/three/build/three.module.js','/vendor/three/build/three.core.js']){const response=await fetch(origin+route);if(!response.ok)throw new Error(`Snapshot ${route}`);stored[route]=await response.text();}
const harness=await readFile(path.join(PROJECT,'scripts/benchmark-scene.js'),'utf8');
function instrument(source,kind){
  const mark=key=>`globalThis.__REA_BENCH_PROBE__?.mark('${key}');`;
  const replacements=kind==='app'?[
    ['const data = await response.arrayBuffer();',`const data = await response.arrayBuffer();${mark('buffer_ready')}${mark('parse_started')}`],
    ["const gltf = await new Promise((resolve, reject) => loader.parse(data, '', resolve, reject));",`const gltf = await new Promise((resolve, reject) => loader.parse(data, '', resolve, reject));${mark('parse_completed')}`],
    ['picaPlayer = await attachPicaPlayer(gltf, THREE);',`${mark('pica_attach_started')}picaPlayer = await attachPicaPlayer(gltf, THREE);${mark('pica_attach_completed')}`]
  ]:[
    ["const buffer=await response.arrayBuffer();const loaded=await new Promise((resolve,reject)=>new GLTFLoader().parse(buffer,'',resolve,reject));",`const buffer=await response.arrayBuffer();${mark('buffer_ready')}${mark('parse_started')}const loaded=await new Promise((resolve,reject)=>new GLTFLoader().parse(buffer,'',resolve,reject));${mark('parse_completed')}`],
    ['picaPlayer=await attachPicaPlayer(loaded,THREE);',`${mark('pica_attach_started')}picaPlayer=await attachPicaPlayer(loaded,THREE);${mark('pica_attach_completed')}`]
  ];
  let result=source;for(const [before,after] of replacements){if(result.split(before).length!==2)throw new Error(`QA anchor changed: ${kind} ${before}`);result=result.replace(before,after);}
  const renderCall=kind==='app'?'renderer.render(scene, camera)':'renderer.render(scene,camera)';
  if(result.split(renderCall).length!==2)throw new Error('QA render-call anchor changed');
  result=result.replace(renderCall,`globalThis.__REA_BENCH_PROBE__.measureRender(()=>${renderCall})`);
  result+='\nexport function inspectBenchmarkCounters(){return renderer?{memory:{...renderer.info.memory},render:{...renderer.info.render},programCount:renderer.info.programs.length}:null;}\n';return result;
}
const instrumentedApp=instrument(stored['/app.js'],'app'),instrumentedFan=instrument(stored['/fan.js'],'fan');
const cases=[];for(const [width,height] of [[400,240],[1280,720],[1920,1080]])for(const mode of ['source','fallback'])cases.push({id:`player-${mode}-${width}x${height}`,kind:'player',mode,width,height,count:1});
for(const [width,height] of [[400,240],[1280,720],[1920,1080]])cases.push({id:`fan-source-${width}x${height}`,kind:'fan',mode:'source',width,height,count:1});
for(const count of [6,12])for(const mode of ['source','fallback'])cases.push({id:`scaling-${count}-${mode}-1280x720`,kind:'scaling',count,mode,width:1280,height:720});
const selected=quick?[cases[0],cases[1],cases[6],cases[9]]:cases;
const warmup=quick?500:5000,measure=quick?1000:10000,repeats=quick?1:3;
const receipt={schema:'rea3d-actual-instrumented-browser-performance-v1',startedAt:new Date().toISOString(),ok:false,output,input:{path:INPUT,sha256:preview.sha256,bytes:preview.bytes,recovery:'Exact known final model hash recovered by parent from existing owned fan server immutable snapshot after D: device access failures; no source ROM recovery'},configuration:{headless:true,deviceScaleFactor:1,warmup_ms:warmup,measure_ms:measure,repeats,quickCalibrationOnly:quick,browserExecutable:CHROMIUM,browserFlags:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],frameRateLimitDisabled:false},snapshot:{url:preview.url,sourceHashes:Object.fromEntries(Object.entries(stored).map(([k,v])=>[k,sha(v)])),qaTransforms:{appOriginal:sha(stored['/app.js']),appInstrumented:sha(instrumentedApp),fanOriginal:sha(stored['/fan.js']),fanInstrumented:sha(instrumentedFan),filesystemSourceChanged:false,description:'Only owned isolated-page response bytes get parse marks and read-only renderer counters; no production files/live server changed'},harnessSha256:sha(harness)},cases:[],boundaries:{originalGameRenderingParity:false,physicalPhonePerformance:false,actualVramBytes:null,vramReason:'WebGL provides no reliable per-process VRAM usage API',wholeRafCallbackCpuIncludes:'animation evaluation, material updates, DOM/control updates, renderer work and driver calls; instrumentation overhead is included',drawSubmissionCpuMeaning:'time spent synchronously inside actual WebGL draw* JS API calls, not full renderer CPU or GPU duration',gpuElapsedMeaning:'EXT_disjoint_timer_query_webgl2 time for the complete application RAF callback GPU command range, only if available and not disjoint',coldMeaning:'first navigation in a fresh isolated private profile; OS file cache and GPU driver caches are not cold/reset',warmMeaning:'subsequent reloads in same isolated context; server Cache-Control no-store means model is fetched again',fallbackLoad:'existing player loads the common GLB+PICA runtime before mode switch; initial common first draw may be PICA, so it is not an optimized fallback-only load',cloneScaling:'dedicated scene reusing production attachPicaPlayer/createSynchronizedPlayer and SkeletonUtils.clone; repeated Bulbasaur, shared geometry, independent bones/material state; not species generality or identical app layout',longIntervals:'counts of observed RAF intervals >=33ms and >=50ms; these are long intervals, not directly measured compositor dropped-frame counts'}};
for(const file of ['scripts/benchmark-probe.mjs','scripts/benchmark-player.mjs','scripts/benchmark-scene.js'])receipt.snapshot.sourceHashes[file]=sha(await readFile(path.join(PROJECT,file)));
await writeFile(path.join(output,'configuration.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);
let context;
try{
  for(const spec of selected){
    const entry={...spec,ok:false,runs:[],pageErrors:[],consoleErrors:[],blocked:[]};
    try{
      context=await chromium.launchPersistentContext(path.join(output,`profile-${spec.id}`),{executablePath:CHROMIUM,headless:true,viewport:{width:Math.max(1100,spec.width+360),height:Math.max(900,spec.height+450)},deviceScaleFactor:1,args:receipt.configuration.browserFlags,env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
      const page=context.pages()[0]??await context.newPage();
      page.on('pageerror',e=>entry.pageErrors.push(e.message));page.on('console',m=>{if(m.type()==='error')entry.consoleErrors.push(m.text());});
      await page.addInitScript(installBenchmarkProbe);await page.addInitScript(config=>{window.__REA_BENCH_CONFIG__=config;},spec);
      await page.route('**/*',async route=>{
        const u=route.request().url();if(u.startsWith('blob:')||u.startsWith('data:'))return route.continue();const parsed=new URL(u);
        if(parsed.origin!==origin){entry.blocked.push(u);return route.abort('blockedbyclient');}
        if(parsed.pathname==='/app.js')return route.fulfill({status:200,contentType:'text/javascript',body:instrumentedApp});
        if(parsed.pathname==='/fan.js')return route.fulfill({status:200,contentType:'text/javascript',body:instrumentedFan});
        if(parsed.pathname==='/style.css')return route.fulfill({status:200,contentType:'text/css',body:stored['/style.css']+`\n#viewport{width:${spec.width}px!important;height:${spec.height}px!important;min-height:0!important;min-width:0!important}main{grid-template-columns:${spec.width}px 290px!important;height:auto!important;min-height:0!important}`});
        if(parsed.pathname==='/fan.css')return route.fulfill({status:200,contentType:'text/css',body:stored['/fan.css']+`\n.world{height:${spec.height}px!important}.handheld{width:${spec.width+2}px!important;max-width:none!important;flex-shrink:0}#fan-viewport{width:${spec.width}px!important;height:${spec.height}px!important}`});
        if(parsed.pathname==='/benchmark')return route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><title>REA shared runtime scaling QA</title><script type="importmap">{"imports":{"three":"/vendor/three/build/three.module.js"}}</script><script type="module" src="/benchmark-scene.js"></script><body style="margin:0;background:#dce8cf"></body>'});
        if(parsed.pathname==='/benchmark-scene.js')return route.fulfill({status:200,contentType:'text/javascript',body:harness});
        return route.continue();
      });
      try{const cdp=await context.browser().newBrowserCDPSession();entry.chromiumSystemInfo=await cdp.send('SystemInfo.getInfo');await cdp.detach();}catch(error){entry.systemInfoUnavailable=error.message;}
      for(let run=1;run<=repeats;run++){
        const url=origin+(spec.kind==='fan'?'/fan':spec.kind==='scaling'?'/benchmark':'/');
        const loadStart=Date.now();await page.goto(url,{waitUntil:'networkidle'});
        await page.waitForFunction(async kind=>{const a=await import(kind==='fan'?'/fan.js':kind==='scaling'?'/benchmark-scene.js':'/app.js');const s=kind==='fan'?a.inspectFan():kind==='scaling'?a.inspectBenchmarkScene():a.inspectPlayback();return s.ready||s.failure;},spec.kind,{timeout:60000});
        const loaded=await page.evaluate(async kind=>{const a=await import(kind==='fan'?'/fan.js':kind==='scaling'?'/benchmark-scene.js':'/app.js');return kind==='fan'?a.inspectFan():kind==='scaling'?a.inspectBenchmarkScene():a.inspectPlayback();},spec.kind);
        if(!loaded.ready)throw new Error(`Model not ready: ${JSON.stringify(loaded)}`);const pageReadyWall_ms=Date.now()-loadStart;
        if(spec.kind==='player'){
          await page.locator('#material-mode').selectOption(spec.mode);await page.locator('#clip').selectOption('0');
          await page.evaluate(async spec=>{const a=await import('/app.js');a.configureComparisonView({position:[104.6595758588925,92.71265440104813,197.72526253672598],target:[-1.0332914919590817,31.21862248782538,5.556412807904937],fov:40,near:.06491964486825144,far:6719.739298812526,aspect:spec.width/spec.height,canvas_size:[spec.width,spec.height]});document.getElementById('play').click();window.__REA_BENCH_PROBE__.mark('target_configuration_ready');},spec);
        }else await page.evaluate(()=>window.__REA_BENCH_PROBE__.mark('target_configuration_ready'));
        await page.waitForTimeout(warmup);await page.evaluate(()=>window.__REA_BENCH_PROBE__.beginMeasure());await page.waitForTimeout(measure);await page.evaluate(()=>window.__REA_BENCH_PROBE__.endMeasure());await page.waitForTimeout(250);
        const actual=await page.evaluate(async kind=>{const module=await import(kind==='fan'?'/fan.js':kind==='scaling'?'/benchmark-scene.js':'/app.js');const state=kind==='fan'?module.inspectFan():kind==='scaling'?module.inspectBenchmarkScene():module.inspectPlayback();return {probe:window.__REA_BENCH_PROBE__.snapshot(),state,counters:kind==='scaling'?state.counters:module.inspectBenchmarkCounters(),camera:kind==='player'?module.inspectComparisonView():null};},spec.kind);
        const measured={run,loadState:run===1?'fresh-isolated-profile':'same-context-reload',pageReadyWall_ms,actual,ok:actual.probe.canvas?.width===spec.width&&actual.probe.canvas?.height===spec.height&&actual.probe.frames.length>10&&(actual.counters?.render.calls??0)>0&&actual.probe.shaderCompiles.every(s=>s.ok)&&actual.probe.programLinks.every(s=>s.ok)&&(spec.kind==='scaling'?actual.state.glError:spec.kind==='fan'?actual.state.glError:actual.state.gl_error)===0};
        entry.runs.push(measured);await writeFile(path.join(output,`${spec.id}-run-${run}.json`),JSON.stringify(measured,null,2)+'\n',{flag:'wx'});
        console.log(JSON.stringify({event:'run-complete',id:spec.id,run,ok:measured.ok,frames:actual.probe.frames.length,buffer:actual.probe.canvas,gpu:actual.probe.contexts[0]?.renderer,gpuTimer:actual.probe.contexts[0]?.timerQuerySupported}));
        if(!measured.ok)throw new Error('Measured run validity checks failed');
      }
      const canvas=page.locator(spec.kind==='fan'?'#fan-viewport canvas':spec.kind==='player'?'#viewport canvas':'canvas');const png=await canvas.screenshot({path:path.join(output,`${spec.id}.png`)});entry.screenshot={path:path.join(output,`${spec.id}.png`),sha256:sha(png),bytes:png.length};
      entry.ok=entry.runs.length===repeats&&entry.runs.every(r=>r.ok)&&entry.pageErrors.length===0&&entry.consoleErrors.length===0&&entry.blocked.length===0;
    }catch(error){entry.failure={message:error.message,stack:error.stack};}
    finally{if(context){await context.close();context=null;}receipt.cases.push(entry);await writeFile(path.join(output,`${spec.id}-receipt.json`),JSON.stringify(entry,null,2)+'\n',{flag:'wx'});await writeFile(path.join(output,'progress.json'),JSON.stringify({completed:receipt.cases.length,total:selected.length,cases:receipt.cases.map(c=>({id:c.id,ok:c.ok,runs:c.runs.length,failure:c.failure?.message}))},null,2)+'\n');console.log(JSON.stringify({event:'case-complete',id:spec.id,ok:entry.ok,completed:receipt.cases.length,total:selected.length,failure:entry.failure?.message}));}
  }
}finally{
  if(context)await context.close();preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));
  receipt.finishedAt=new Date().toISOString();receipt.ok=receipt.cases.length===selected.length&&receipt.cases.every(c=>c.ok);
  await writeFile(path.join(output,'benchmark-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({event:'finished',ok:receipt.ok,output}));
}
if(!receipt.ok)process.exitCode=1;
