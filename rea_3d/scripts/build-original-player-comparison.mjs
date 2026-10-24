// Presentation of actual captured PNGs. No phase/camera fitting, pixel-parity score or source mutation.
import path from 'node:path';
import {mkdir,readFile,writeFile,lstat} from 'node:fs/promises';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const ROOT='D:/REA3D-ORAS-ORIGINAL-20261023-04/frame-comparison';
const PROJECT=fileURLToPath(new URL('../',import.meta.url));
const PLAYWRIGHT='C:/browser-qa/node_modules/playwright-core/index.mjs';
const CHROMIUM='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const sha=data=>createHash('sha256').update(data).digest('hex');
const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function args(argv){const o={};for(let i=0;i<argv.length;i++){const k=argv[i];if(k==='--authorized-isolated-browser')o.authorized=true;else if(['--manifest','--output-dir'].includes(k)){const v=argv[++i];if(!v||v.startsWith('--'))throw new Error(`Missing ${k}`);o[k==='--manifest'?'manifest':'output']=v;}else throw new Error(`Unknown ${k}`);}return o;}
async function pngSource(file,crop){
  if(!path.isAbsolute(file))throw new Error('Captured PNG paths must be absolute');
  const stat=await lstat(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.size>16*1024*1024)throw new Error('Captured PNG must be a regular file within 16 MiB');
  const bytes=await readFile(file);if(bytes.length<24||bytes.subarray(0,8).toString('hex')!=='89504e470d0a1a0a')throw new Error('Captured image must be PNG');
  const width=bytes.readUInt32BE(16),height=bytes.readUInt32BE(20);if(width<1||height<1||width>4096||height>4096)throw new Error('Captured PNG dimensions exceed the bounded comparison size');
  const c=crop??[0,0,width,height];if(!Array.isArray(c)||c.length!==4||c.some(n=>!Number.isInteger(n))||c[0]<0||c[1]<0||c[2]<1||c[3]<1||c[0]+c[2]>width||c[1]+c[3]>height)throw new Error('Comparison crop must remain inside the original image');
  return {path:file,sha256:sha(bytes),bytes:bytes.length,width,height,crop:c,dataUrl:'data:image/png;base64,'+bytes.toString('base64')};
}
function picture(item){const [x,y,w,h]=item.crop;return `<svg class="capture" width="${w}" height="${h}" viewBox="${x} ${y} ${w} ${h}" role="img" aria-label="실제 캡처 프레임"><image href="${item.dataUrl}" x="0" y="0" width="${item.width}" height="${item.height}"/></svg>`;}
export async function buildComparison(options){
  if(!options.authorized)throw new Error('Explicit isolated Chromium authorization required for artifact rendering');
  if(!options.manifest||!path.isAbsolute(options.manifest)||!options.output||!path.isAbsolute(options.output))throw new Error('Absolute manifest and new output paths required');
  const output=path.resolve(options.output),root=path.resolve(ROOT);if(output===root||!output.startsWith(root+path.sep))throw new Error('Use a new child of the dedicated frame-comparison root');
  for(let current=path.dirname(output);;current=path.dirname(current)){try{if((await lstat(current)).isSymbolicLink())throw new Error('Output symlink ancestor rejected');}catch(e){if(e.code!=='ENOENT')throw e;}if(path.dirname(current)===current)break;}
  const manifestBytes=await readFile(options.manifest);if(manifestBytes.length>1024*1024)throw new Error('Comparison manifest exceeds 1 MiB');
  const manifest=JSON.parse(manifestBytes);
  if(manifest.schema!=='rea-original-player-pairs-v1'||manifest.cameraBasis!=='inferred_alignment'||!Array.isArray(manifest.pairs)||manifest.pairs.length<1||manifest.pairs.length>8)throw new Error('Require explicit inferred camera basis and 1..8 original/player frame pairs');
  if(manifest.originalCameraVerified===true||manifest.pixelParityVerified===true)throw new Error('This comparison builder cannot establish camera or pixel parity');
  const model=manifest.model;if(!model||!path.isAbsolute(model.path)||sha(await readFile(model.path))!==model.sha256)throw new Error('Explicit model path and matching SHA-256 required');
  const pairs=[],inputs=[];
  for(const [ordinal,pair] of manifest.pairs.entries()){
    if(!Number.isInteger(pair.original?.vblank)||pair.original.vblank<0||!Number.isInteger(pair.player?.clipIndex)||!Number.isInteger(pair.player?.sourceFrame)||pair.player.sourceFrame<0||!path.isAbsolute(pair.player.statePath))throw new Error('Each pair needs original VBlank and explicit player clip/source frame/state proof');
    if(!['unconfirmed','source_controller_observed','gpu_submission_verified'].includes(pair.phaseStatus))throw new Error('Explicit phase-evidence status required');
    const original=await pngSource(pair.original.path,pair.original.crop),player=await pngSource(pair.player.path,pair.player.crop),stateBytes=await readFile(pair.player.statePath),state=JSON.parse(stateBytes);
    if(Math.abs(original.crop[2]/original.crop[3]-player.crop[2]/player.crop[3])>1e-12)throw new Error('Original and player crops must have the same aspect ratio; source images are never stretched');
    if(state.schema!=='rea-captured-source-frame-v1'||state.primaryClipIndex!==pair.player.clipIndex||state.requestedSourceFrame!==pair.player.sourceFrame||state.png.sha256!==player.sha256||!state.repeatedPngIdentical)throw new Error('Player frame must match its actual paused-source-frame capture proof');
    if(state.comparisonView?.originalCameraVerified===true)throw new Error('Unexpected verified camera claim in inferred comparison');
    pairs.push({...pair,ordinal:ordinal+1,original:{...pair.original,...original},player:{...pair.player,...player},statePath:pair.player.statePath,stateSha256:sha(stateBytes)});inputs.push(original,player);
  }
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const phaseText={unconfirmed:'위상 미확정 · 비교 후보',source_controller_observed:'원작 컨트롤러 관측값으로 선택',gpu_submission_verified:'원작 GPU 제출 프레임 관측'};
  const rows=pairs.map(pair=>`<section class="pair"><header><strong>FRAME PAIR ${String(pair.ordinal).padStart(2,'0')}</strong><span>${escape(phaseText[pair.phaseStatus])}</span></header><div class="frames"><article><h2>원작 실행 <small>VBlank ${pair.original.vblank}</small></h2><div class="image original">${picture(pair.original)}</div><p>${escape(pair.original.label??'원작 게임에서 실제 캡처한 화면')}</p></article><article><h2>전용 재생기 <small>클립 ${pair.player.clipIndex} · 소스 ${pair.player.sourceFrame}</small></h2><div class="image player">${picture(pair.player)}</div><p>${escape(pair.player.label??'동일 모델의 명시적 소스 프레임 캡처')}</p></article></div>${pair.note?`<p class="pair-note">${escape(pair.note)}</p>`:''}</section>`).join('\n');
  const html=`<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>원작 · 전용 재생기 실제 프레임 비교</title><style>*{box-sizing:border-box}body{margin:0;background:#eef1e9;color:#263c33;font-family:"Segoe UI Variable Text","Malgun Gothic",sans-serif;padding:34px 30px 30px}main{max-width:1040px;margin:auto}.eyebrow{font-size:10px;font-weight:750;letter-spacing:2px;color:#78916f;margin:0 0 10px}h1{font-size:27px;letter-spacing:-1px;margin:0 0 12px}.lead{font-size:12px;line-height:1.8;color:#788773;margin:0 0 20px}.boundary{background:#fff7e7;border:1px solid #e3d6bb;border-radius:10px;padding:13px 16px;color:#8d7751;font-size:11px;line-height:1.8;margin-bottom:24px}.boundary strong{color:#775c36}.pair{border:1px solid #d4dfcd;border-radius:12px;background:#fdfcf5;margin:0 0 18px;overflow:hidden}.pair>header{display:flex;justify-content:space-between;gap:12px;padding:13px 17px;border-bottom:1px solid #e3e8d9;font-size:10px;letter-spacing:1.4px}.pair>header span{letter-spacing:0;color:#8a9a7d}.frames{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:15px 17px 0}article{min-width:0}h2{font-size:14px;margin:0 0 10px;font-weight:650}h2 small{float:right;font-size:10px;font-weight:400;color:#8c9b80;margin-top:2px}.image{border:1px solid #d4ddca;background:#dbe2d1;line-height:0;border-radius:7px;overflow:hidden}.image.player{background-color:#e4e9dc;background-image:linear-gradient(45deg,#d7dfcf 25%,transparent 25%),linear-gradient(-45deg,#d7dfcf 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#d7dfcf 75%),linear-gradient(-45deg,transparent 75%,#d7dfcf 75%);background-size:18px 18px;background-position:0 0,0 9px,9px -9px,-9px 0}.capture{display:block;width:100%;height:auto}article p,.pair-note{font-size:10px;color:#7f8c74;line-height:1.75;margin:8px 0 13px}.pair-note{border-top:1px solid #e8ebdf;margin:0;padding:11px 17px}.foot{font-size:10px;line-height:1.8;color:#84937a;margin:20px 0 0}@media(max-width:650px){body{padding:20px 12px}.frames{grid-template-columns:1fr;padding:12px;gap:5px}h1{font-size:22px}.pair>header{font-size:9px;padding:12px}.boundary{font-size:10px}}</style></head><body><main><p class="eyebrow">ACTUAL CAPTURE / ORIGINAL & RECONSTRUCTION</p><h1>원작과 전용 재생기, 프레임을 나란히</h1><p class="lead">${escape(manifest.description??'원작 실행에서 얻은 이미지와 전용 재생기의 실제 소스 프레임 캡처를 함께 기록합니다.')}</p><div class="boundary"><strong>추정 카메라 · 픽셀 동일성 미판정.</strong> 카메라·조명·배경의 차이가 남아 있습니다. 각 프레임의 위상 근거는 아래에 별도로 표시합니다. 이 자료는 임의의 재현율이나 성공 점수를 부여하지 않습니다.</div>${rows}<p class="foot">모델 SHA-256 ${escape(model.sha256)}<br>원본 PNG는 변경하지 않았습니다. 표시한 자르기만 SVG viewBox로 적용했습니다. 실제 파일 해시·선택 프레임·카메라 근거는 comparison-receipt.json에 있습니다.</p></main></body></html>`;
  const htmlPath=path.join(output,'comparison.html');await writeFile(htmlPath,html,{flag:'wx'});
  const receipt={schema:'rea-original-player-comparison-v1',ok:false,manifest:{path:options.manifest,sha256:sha(manifestBytes)},model,output:{html:{path:htmlPath,sha256:sha(html)}},sourceSha256:sha(await readFile(path.join(PROJECT,'scripts/build-original-player-comparison.mjs'))),cameraBasis:'inferred_alignment',originalCameraVerified:false,pixelParityVerified:false,scoreAssigned:false,sourceImagesMutated:false,pairs:pairs.map(({original,player,...pair})=>({...pair,original:{...original,dataUrl:undefined},player:{...player,dataUrl:undefined}})),errors:[],blockedRequests:[]};
  let context;
  try{
    const {chromium}=await import(pathToFileURL(PLAYWRIGHT).href);context=await chromium.launchPersistentContext(path.join(output,'browser-profile'),{executablePath:CHROMIUM,headless:true,viewport:{width:1120,height:900},deviceScaleFactor:1,env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
    const page=context.pages()[0]??await context.newPage();await page.route('**/*',route=>{const url=route.request().url();if(url.startsWith('data:')||url==='about:blank')return route.continue();receipt.blockedRequests.push(url);return route.abort('blockedbyclient');});page.on('pageerror',error=>receipt.errors.push(error.message));
    await page.setContent(html,{waitUntil:'load'});await page.evaluate(()=>Promise.all([...document.querySelectorAll('image')].map(image=>new Promise(resolve=>{const img=new Image();img.onload=resolve;img.onerror=resolve;img.src=image.getAttribute('href');}))));
    const pngPath=path.join(output,'comparison.png'),png=await page.screenshot({path:pngPath,fullPage:true});receipt.output.png={path:pngPath,bytes:png.length,sha256:sha(png)};
    if(receipt.errors.length||receipt.blockedRequests.length)throw new Error('Unexpected page error or network request during artifact rendering');
    for(const input of inputs)if(sha(await readFile(input.path))!==input.sha256)throw new Error('A source PNG changed during comparison assembly');
    receipt.ok=true;
  }catch(error){receipt.failure={message:error.message,stack:error.stack};}
  finally{if(context)await context.close();await writeFile(path.join(output,'comparison-receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});}
  return receipt;
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){try{const o=args(process.argv.slice(2)),r=await buildComparison(o);console.log(JSON.stringify({ok:r.ok,pairs:r.pairs.length,html:r.output.html.path,png:r.output.png?.path,failure:r.failure?.message}));if(!r.ok)process.exitCode=1;}catch(e){console.error(e.message);process.exitCode=1;}}
