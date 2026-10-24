// User-authorized isolated Chromium only. The supplied loopback preview stays running.
import path from 'node:path';
import {mkdir,readFile,writeFile,lstat} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {pixelDifference} from './verify-pica-browser.mjs';
const PROJECT=fileURLToPath(new URL('../',import.meta.url)),ROOT='D:/REA3D-ORAS-ORIGINAL-20261023-04/fan-ui-001';
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
function args(argv){const o={};for(let i=0;i<argv.length;i++){const k=argv[i];if(k==='--authorized-isolated-browser')o.authorized=true;else if(['--url','--input','--output-dir'].includes(k)){const v=argv[++i];if(!v||v.startsWith('--'))throw new Error(`Missing ${k}`);o[{'--url':'url','--input':'input','--output-dir':'output'}[k]]=v;}else throw new Error(`Unknown ${k}`);}return o;}
export async function playtestFan(options){
  if(!options.authorized)throw new Error('Explicit isolated Chromium authorization required');
  const url=new URL(options.url);if(url.hostname!=='127.0.0.1'||url.protocol!=='http:'||url.pathname!=='/fan'||url.search)throw new Error('Exact /fan on an explicit IPv4 loopback preview required');
  const output=path.resolve(options.output),root=path.resolve(ROOT);if(!path.isAbsolute(options.input)||!path.isAbsolute(options.output)||(output!==root&&!output.startsWith(root+path.sep)))throw new Error('Absolute model and dedicated new output paths required');
  for(let current=path.dirname(output);;current=path.dirname(current)){try{if((await lstat(current)).isSymbolicLink())throw new Error('Output symlink ancestor rejected');}catch(e){if(e.code!=='ENOENT')throw e;}if(path.dirname(current)===current)break;}
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const receipt={schema:'rea-fan-ui-playtest-v1',ok:false,url:url.href,input:{path:options.input,sha256Before:sha(await readFile(options.input))},checks:[],captures:[],source:{},errors:[],console:[],blockedRequests:[],states:{},boundaries:{newFanUi:true,originalBottomScreenReplica:false,originalSaveLinked:false,battleImplemented:false,itemUseImplemented:false,uiSaveScope:'single versioned localStorage key: trainer name and view settings',tusAccess:false,userBrowserProfile:false,originalRenderingParity:false}};
  const check=(name,passed,details={})=>{receipt.checks.push({name,passed,...details});if(!passed)throw new Error(name);};
  for(const f of ['preview/fan.html','preview/fan.js','preview/fan-state.js','preview/fan.css','src/preview-server.mjs','scripts/playtest-fan.mjs'])receipt.source[f]=sha(await readFile(path.join(PROJECT,f)));
  let context;
  try{
    const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);context=await chromium.launchPersistentContext(path.join(output,'browser-profile'),{executablePath:CHROMIUM,headless:true,viewport:{width:1200,height:960},deviceScaleFactor:1,args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});receipt.browserVersion=context.browser()?.version();
    const page=context.pages()[0]??await context.newPage();await page.route('**/*',route=>{const u=route.request().url();if(u.startsWith('blob:')||u.startsWith('data:')||new URL(u).origin===url.origin)return route.continue();receipt.blockedRequests.push(u);return route.abort('blockedbyclient');});
    page.on('pageerror',error=>receipt.errors.push(error.message));page.on('console',m=>{if(['error','warning'].includes(m.type()))receipt.console.push({type:m.type(),text:m.text()});});
    const inspect=()=>page.evaluate(async()=>(await import('/fan.js')).inspectFan());
    const capture=async(name)=>{await page.waitForTimeout(220);const filename=path.join(output,name+'.png'),data=await page.screenshot({path:filename,fullPage:true});const item={name,path:filename,bytes:data.length,sha256:sha(data)};receipt.captures.push(item);return item;};
    await page.goto(url.href,{waitUntil:'networkidle'});await page.waitForFunction(async()=>{const app=await import('/fan.js');return app.inspectFan().ready;},{timeout:30000});
    await page.waitForTimeout(180);const home=await inspect();receipt.states.desktopHome=home;
    check('actual corrected native GLB is served without changes',sha(Buffer.from(await page.request.get(url.origin+'/model.glb').then(r=>r.body())))===receipt.input.sha256Before);
    check('first action screen has six named menu buttons and real WebGL draws',await page.locator('.menu-button').count()===6&&home.drawCalls>0&&home.glError===0&&home.renderFrame>0);
    check('demo party exposes the actual isolated fixture values',home.party.level===15&&home.party.hp===41&&home.party.maxHp===41&&home.party.moves.length===4&&!home.originalSaveLinked);
    const layout=()=>page.evaluate(()=>{const w=document.querySelector('.world').getBoundingClientRect(),lower=document.querySelector('.lower-screen').getBoundingClientRect(),canvas=document.querySelector('#fan-viewport canvas').getBoundingClientRect();return {width:innerWidth,bodyWidth:document.body.scrollWidth,world:{top:w.top,bottom:w.bottom,height:w.height},lower:{top:lower.top,bottom:lower.bottom},canvas:{top:canvas.top,bottom:canvas.bottom},noHorizontalOverflow:document.body.scrollWidth<=innerWidth,menuDoesNotOverlap3d:lower.top>=w.bottom-1};});
    receipt.desktopLayout=await layout();check('desktop lower menu protects the separate 3D playfield',receipt.desktopLayout.menuDoesNotOverlap3d&&receipt.desktopLayout.noHorizontalOverflow);
    await capture('desktop-home');
    await page.getByRole('button',{name:'포켓몬',exact:true}).focus();await page.keyboard.press('ArrowRight');check('arrow key moves between menu choices',await page.evaluate(()=>document.activeElement.dataset.menu)==='bag');await page.keyboard.press('Enter');check('Enter opens the selected bag',((await inspect()).currentMenu)==='bag');
    check('bag communicates its actual empty prototype state',await page.getByText('아직 아이템이 없어요',{exact:true}).isVisible());await page.keyboard.press('Escape');check('Escape returns home and restores camera input',(await inspect()).currentMenu==='home'&&(await inspect()).cameraInputEnabled);
    for(const menu of ['party','bag','dex','trainer','save','settings']){
      await page.locator(`[data-menu="${menu}"]`).click();const s=await inspect();check(`${menu} menu opens and gates camera input`,s.currentMenu===menu&&!s.cameraInputEnabled);
      if(menu==='party')await capture('desktop-party');if(menu==='settings')await capture('desktop-settings');
      await page.locator('#menu-back').click();check(`${menu} back button returns to home`,(await inspect()).currentMenu==='home');
    }
    await page.locator('[data-menu="party"]').click();const gatedBefore=(await inspect()).cameraPosition;
    const worldBox=await page.locator('.world').boundingBox();await page.mouse.move(worldBox.x+worldBox.width*.65,worldBox.y+worldBox.height*.45);await page.mouse.down();await page.mouse.move(worldBox.x+worldBox.width*.8,worldBox.y+worldBox.height*.5,{steps:4});await page.mouse.up();
    check('3D drag is actually gated while a lower menu is active',JSON.stringify((await inspect()).cameraPosition)===JSON.stringify(gatedBefore));await page.keyboard.press('Escape');
    const dragBefore=(await inspect()).cameraPosition;await page.mouse.move(worldBox.x+worldBox.width*.65,worldBox.y+worldBox.height*.45);await page.mouse.down();await page.mouse.move(worldBox.x+worldBox.width*.8,worldBox.y+worldBox.height*.5,{steps:4});await page.mouse.up();await page.waitForTimeout(200);
    check('home drag actually rotates the 3D camera',JSON.stringify((await inspect()).cameraPosition)!==JSON.stringify(dragBefore));await page.locator('#scene-reset').click();await page.waitForTimeout(2700);
    await page.locator('[data-menu="settings"]').click();await page.locator('#setting-animation').uncheck();const paused=await inspect();await page.waitForTimeout(250);check('animation switch actually pauses the 3D source clock',!(await inspect()).playback.playing&&(await inspect()).playback.time_seconds===paused.playback.time_seconds);
    await page.locator('#setting-eye').selectOption('2');const eye=await inspect();check('eye setting applies PF source frame20 to the actual player',eye.facial.values['0']===2&&eye.playback.visual_state.facial_overlays.some(layer=>layer.channel===0&&layer.source_frame===20));
    await page.locator('#setting-rotate').check();check('rotation setting changes real OrbitControls',(await inspect()).autoRotate===true);await page.locator('#setting-clip').selectOption('11');check('motion selection changes the actual source clip',(await inspect()).playback.clip_index===11);
    await page.keyboard.press('Escape');const cameraBefore=(await inspect()).cameraPosition;await page.waitForTimeout(250);check('enabled auto rotation actually changes the camera at home',JSON.stringify((await inspect()).cameraPosition)!==JSON.stringify(cameraBefore));
    await page.locator('[data-menu="trainer"]').click();await page.locator('#trainer-name').fill('새잎테스트');await page.getByRole('button',{name:'이름 적용',exact:true}).click();check('trainer form applies the entered local name',(await inspect()).state.trainerName==='새잎테스트');await page.keyboard.press('Escape');
    await page.locator('[data-menu="save"]').click();await page.locator('#save-now').click();const saved=await inspect();check('save is read back from the single scoped versioned key',saved.state.savedAt!==null&&await page.evaluate(()=>{const value=JSON.parse(localStorage.getItem('rea3d.fan.newleaf.v1'));return value.version===1&&value.trainerName==='새잎테스트'&&value.settings.eyeValue===2;}));
    receipt.states.saved=saved;await page.reload({waitUntil:'networkidle'});await page.waitForFunction(async()=>(await import('/fan.js')).inspectFan().ready);const restored=await inspect();receipt.states.restored=restored;
    check('reload restores name, paused animation, source clip, eye and rotation settings',restored.state.trainerName==='새잎테스트'&&restored.state.settings.animation===false&&restored.playback.clip_index===11&&restored.facial.values['0']===2&&restored.autoRotate===true);
    await page.locator('[data-menu="trainer"]').click();await page.locator('#trainer-name').fill('저장안한이름');await page.getByRole('button',{name:'이름 적용',exact:true}).click();await page.keyboard.press('Escape');await page.locator('[data-menu="save"]').click();await page.locator('#load-save').click();check('explicit load restores the saved name over unsaved edits',(await inspect()).state.trainerName==='새잎테스트');
    const oldSave=await page.evaluate(()=>localStorage.getItem('rea3d.fan.newleaf.v1'));await page.evaluate(()=>localStorage.setItem('rea3d.fan.newleaf.v1','{"version":999}'));const beforeBad=await inspect();await page.locator('[data-menu="save"]').click();await page.locator('#load-save').click();check('malformed save is rejected without replacing current settings',JSON.stringify((await inspect()).state)===JSON.stringify(beforeBad.state));await page.evaluate(value=>localStorage.setItem('rea3d.fan.newleaf.v1',value),oldSave);await page.keyboard.press('Escape');
    await page.waitForTimeout(2700);
    await page.setViewportSize({width:390,height:844});await page.waitForTimeout(150);receipt.mobileLayout=await layout();check('mobile layout has no horizontal overflow or playfield/menu overlap',receipt.mobileLayout.noHorizontalOverflow&&receipt.mobileLayout.menuDoesNotOverlap3d&&receipt.mobileLayout.world.height>=300);
    await page.locator('[data-menu="party"]').focus();await page.keyboard.press('ArrowDown');
    check('mobile vertical arrow follows the actual computed grid columns',await page.evaluate(()=>{const buttons=[...document.querySelectorAll('.menu-button')],columns=getComputedStyle(document.getElementById('home-menu')).gridTemplateColumns.split(/\s+/).length;return buttons.indexOf(document.activeElement)===columns;}));
    await page.locator('[data-menu="party"]').focus();await capture('mobile-home');
    for(const menu of ['party','settings','save']){await page.locator(`[data-menu="${menu}"]`).click();check(`mobile ${menu} panel remains in the lower screen`,await page.locator(`[data-panel="${menu}"]`).isVisible()&&(await layout()).noHorizontalOverflow);await capture(`mobile-${menu}`);await page.locator('#menu-back').click();}
    await page.emulateMedia({reducedMotion:'reduce'});check('reduced motion disables ornamental panel transitions',await page.evaluate(()=>getComputedStyle(document.querySelector('.menu-button')).transitionDuration.split(',').every(v=>parseFloat(v)===0)));
    receipt.states.final=await inspect();check('final renderer remains active and error free',receipt.states.final.renderFrame>home.renderFrame&&receipt.states.final.drawCalls>0&&receipt.states.final.glError===0);
    check('no runtime error, CSP failure or external request',receipt.errors.length===0&&receipt.blockedRequests.length===0&&!receipt.console.some(m=>m.type==='error'));
    receipt.input.sha256After=sha(await readFile(options.input));check('source corrected GLB is unchanged',receipt.input.sha256After===receipt.input.sha256Before);receipt.ok=true;
  }catch(error){receipt.failure={message:error.message,stack:error.stack};}
  finally{if(context)await context.close();await writeFile(path.join(output,'fan-playtest-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});}
  return receipt;
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){try{const options=args(process.argv.slice(2)),r=await playtestFan(options);console.log(JSON.stringify({ok:r.ok,url:r.url,checks:r.checks.length,captures:r.captures.length,output:options.output,failure:r.failure?.message}));if(!r.ok)process.exitCode=1;}catch(e){console.error(e.message);process.exitCode=1;}}
