// Actual standard-glTF compatibility smoke. This does not validate the ORAS parser for these species.
import path from 'node:path';
import {mkdir,readFile,writeFile,lstat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {startPreview} from '../src/preview-server.mjs';
import {installGlProbe} from './verify-pica-browser.mjs';
const PROJECT=fileURLToPath(new URL('../',import.meta.url));
const ROOT='C:/rea3d-validation';
const INPUT_ROOT='C:/Users/USER/Documents/Codex/pokemon-firered-3d-20261022/pokemon-audit/models';
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const species=[['006','리자몽'],['009','거북왕'],['025','피카츄'],['094','팬텀'],['095','롱스톤'],['129','잉어킹'],['130','갸라도스'],['151','뮤']];
const output=path.join(ROOT,'generality-browser-001');
if(!process.argv.includes('--authorized-isolated-browser'))throw new Error('Explicit isolated Chromium authorization required');
await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
const receipt={schema:'rea-multispecies-gltf-actual-browser-v1',ok:false,cases:[],source:{},
  boundaries:{scope:'existing third-party GLB standard Three.js rendering compatibility',orasParserMultispeciesConversionVerified:false,originalGameProvenanceVerified:false,originalRenderingParity:false,mobileDevicePerformance:false,tusAccess:false,userBrowserProfile:false,productionFanServerChanged:false,recovery:'D: device errors kept separate; these existing C: GLBs are a different backup dataset'}};
for(const name of ['preview/app.js','preview/pica-player.js','preview/pica-runtime.js','preview/synchronized-player.js','src/preview-server.mjs','scripts/verify-generality-browser.mjs'])receipt.source[name]=sha(await readFile(path.join(PROJECT,name)));
const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);
for(const [id,name] of species){
  const entry={id,name,ok:false,input:{path:path.join(INPUT_ROOT,id,'model.glb')},checks:[],errors:[],console:[],blockedRequests:[]};
  const check=(name,passed,details={})=>{entry.checks.push({name,passed,...details});if(!passed)throw new Error(name);};
  let context,preview;
  try{
    entry.input.sha256Before=sha(await readFile(entry.input.path));preview=await startPreview({input:entry.input.path,port:0});entry.modelMetadata=preview.metadata;entry.localUrl=preview.url;
    context=await chromium.launchPersistentContext(path.join(output,`profile-${id}`),{executablePath:CHROMIUM,headless:true,viewport:{width:1020,height:760},deviceScaleFactor:1,args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
    const page=context.pages()[0]??await context.newPage();
    await page.route('**/*',route=>{const u=route.request().url();if(u.startsWith('blob:')||u.startsWith('data:')||new URL(u).origin===new URL(preview.url).origin)return route.continue();entry.blockedRequests.push(u);return route.abort('blockedbyclient');});
    page.on('pageerror',error=>entry.errors.push(error.message));page.on('console',m=>{if(['error','warning'].includes(m.type()))entry.console.push({type:m.type(),text:m.text()});});
    await page.addInitScript(installGlProbe);await page.goto(preview.url,{waitUntil:'networkidle'});
    await page.waitForFunction(async()=>{const a=await import('/app.js');return a.inspectPlayback().ready||document.getElementById('status').classList.contains('error');},null,{timeout:30000});
    const initial=await page.evaluate(async()=>(await import('/app.js')).inspectPlayback());check('actual GLB scene becomes ready',initial.ready,{status:await page.locator('#status').textContent()});
    if(!await page.locator('#material-mode').isDisabled())await page.locator('#material-mode').selectOption('fallback');
    await page.evaluate(async()=>{const a=await import('/app.js');a.configureComparisonView({canvas_size:[400,300]});document.getElementById('reset').click();});
    const live=await page.evaluate(async()=>{const a=await import('/app.js'),samples=[];await new Promise(resolve=>{function tick(){samples.push(a.inspectPlayback());if(samples.length>=16)resolve();else requestAnimationFrame(tick);}requestAnimationFrame(tick);});window.__REA_GL_PROBE__.arm();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {samples,last:a.inspectPlayback(),view:a.inspectComparisonView(),probe:window.__REA_GL_PROBE__.snapshot()};});
    entry.actual={gpu:live.probe.contexts,drawCalls:live.last.draw_calls,triangles:live.last.triangles,boneCount:live.last.bones.length,firstRenderFrame:live.samples[0].render_frame,lastRenderFrame:live.last.render_frame,camera:live.view,readback:live.probe.readback};
    check('actual draw and nonblank model pixels observed',live.last.draw_calls>0&&live.last.triangles>0&&live.probe.readback?.nonzeroRgbPixels>100&&live.last.gl_error===0);
    check('actual shader compilation and linking succeeded',live.probe.shaderEvents.length>0&&live.probe.shaderEvents.every(e=>e.ok)&&live.probe.links.every(e=>e.ok),{shaders:live.probe.shaderEvents.length,programs:live.probe.links.length});
    if(entry.modelMetadata.animationCount){
      const times=live.samples.map(s=>s.playback.time_seconds);check('first animation actually advances in RAF draws',new Set(times).size>=8);entry.animation={available:entry.modelMetadata.animationCount,firstClip:live.samples[0].playback.clip_index,timeSamples:times,bonePoseChanges:new Set(live.samples.map(s=>JSON.stringify(s.bones))).size};
      if(entry.modelMetadata.animationCount>1){const lastIndex=entry.modelMetadata.animationCount-1;await page.locator('#clip').selectOption(String(lastIndex));await page.waitForTimeout(180);const last=await page.evaluate(async()=>(await import('/app.js')).inspectPlayback());check('last available animation can be selected and rendered',last.playback.clip_index===lastIndex&&last.draw_calls>0&&last.gl_error===0);entry.animation.lastSelectedIndex=lastIndex;}
    }else entry.animation={available:0,animationPlaybackClaimed:false};
    await page.locator('#pause').click().catch(()=>{});await page.locator('#reset').click();await page.waitForTimeout(100);
    const pngPath=path.join(output,`species-${id}.png`),png=await page.locator('#viewport canvas').screenshot({path:pngPath});entry.screenshot={path:pngPath,bytes:png.length,sha256:sha(png)};
    check('no app error, CSP error or unexpected external request',entry.errors.length===0&&entry.blockedRequests.length===0&&!entry.console.some(m=>m.type==='error'));
    entry.input.sha256After=sha(await readFile(entry.input.path));check('source GLB unchanged',entry.input.sha256After===entry.input.sha256Before);entry.ok=true;
  }catch(error){entry.failure={message:error.message,stack:error.stack};}
  finally{if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}receipt.cases.push(entry);await writeFile(path.join(output,`species-${id}-receipt.json`),JSON.stringify(entry,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({species:id,name,ok:entry.ok,checks:entry.checks.length,failure:entry.failure?.message}));}
}
receipt.ok=receipt.cases.every(c=>c.ok);receipt.passed=receipt.cases.filter(c=>c.ok).length;receipt.failed=receipt.cases.length-receipt.passed;
await writeFile(path.join(output,'generality-browser-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({ok:receipt.ok,passed:receipt.passed,failed:receipt.failed,output}));if(!receipt.ok)process.exitCode=1;
