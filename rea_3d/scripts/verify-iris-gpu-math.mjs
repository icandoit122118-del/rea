/** Synthetic RGBA/depth fixture through the real attachPicaPlayer on an owned GPU context. */
import path from 'node:path';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {startPreview} from '../src/preview-server.mjs';
import {parseGlb} from './audit-shader-runtime.mjs';
const input='C:/rea3d-validation/baseline/model.glb';
const source='C:/rea_3d/preview/pica-player.js';
const sha=b=>createHash('sha256').update(b).digest('hex');

async function run(){
  const args=process.argv.slice(2),i=args.indexOf('--output-dir'),out=i>=0?path.resolve(args[i+1]):null;
  if(!args.includes('--authorized-isolated-browser')||!out||!out.startsWith(path.resolve('C:/rea3d-iris-blend')+path.sep))throw new Error('Explicit authorization and new scoped output required');
  const bytes=await readFile(input),sourceBytes=await readFile(source),{runtime}=parseGlb(bytes);
  if(sha(bytes)!=='e8bd5ccb7524170842037ef5d67286d1f3e2c197896bad2812bde673c63a3b76'||sha(sourceBytes)!=='6f2b9621362d0deecc9ae88953dd30a33941c237043c9687a0da40156a87fbc7')throw new Error('Frozen source/input identity required');
  await mkdir(out,{recursive:false});await mkdir(path.join(out,'tmp'));
  const receipt={schema:'rea3d.iris-blend.synthetic-GPU-math.v1',ok:false,synthetic:true,original_game_pixel_evidence:false,input_sha256:sha(bytes),renderer_sha256:sha(sourceBytes),errors:[]};let preview,context;
  try{
    preview=await startPreview({input,port:0});const pw=await import(pathToFileURL('C:/browser-qa/node_modules/playwright-core/index.mjs').href);
    context=await pw.chromium.launchPersistentContext(path.join(out,'browser-profile'),{executablePath:'C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe',headless:true,viewport:{width:320,height:240},args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(out,'tmp'),TMP:path.join(out,'tmp')}});
    const page=context.pages()[0]??await context.newPage();await page.route('**/*',route=>{const url=route.request().url();if(!url.startsWith(preview.url))return route.abort();if(url===preview.url)return route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><title>Synthetic GPU blend fixture</title>'});return route.continue();});
    page.on('pageerror',error=>receipt.errors.push(error.message));await page.goto(preview.url);
    receipt.gpu=await page.evaluate(async original=>{
      const THREE=await import('/vendor/three/build/three.module.js'),{attachPicaPlayer}=await import('/pica-player.js');
      const stage={constant_index:0,color_sources:['Constant','Constant','Constant'],alpha_sources:['Constant','Constant','Constant'],color_operands:['Color','Color','Color'],alpha_operands:['Alpha','Alpha','Alpha'],color_combiner:'Replace',alpha_combiner:'Replace',color_scale:1,alpha_scale:1};
      const config=(source,name,rgba)=>({...structuredClone(source),name,texture_names:['','',''],colors:{...structuredClone(source.colors),constant0:rgba},texture_mappers:[{},{},{}],tex_coords:[0,1,2].map(()=>({scale:[1,1],translation:[0,0],rotation:0,transform_type:0})),texenv_stages:[stage],lut_references:{},lighting:{},alpha_test:{enabled:false}});
      const iris=config(original.find(m=>m.name==='LIris'),'LIris',[1,.2,0,.5]),occluder=config(original.find(m=>m.name==='BodyA'),'Occluder',[.1,.7,.3,1]),probe=config(original.find(m=>m.name==='BodyA'),'Probe',[1,1,0,1]);
      const scene=new THREE.Scene(),geometry=new THREE.PlaneGeometry(2,2),objects={};for(const c of[iris,occluder,probe]){const object=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({name:c.name}));object.name=c.name;object.userData.reaPicaNodeName=c.name;scene.add(object);objects[c.name]=object;}
      const runtime={materials:[iris,occluder,probe],texture_bindings:{},mesh_nodes:[iris,occluder,probe].map(c=>({name:c.name,default_visible:true})),clips:[],source_fps:30,luts:[]};
      const player=await attachPicaPlayer({scene,parser:{json:{extras:{reaPicaRuntime:runtime}}}},THREE),renderer=new THREE.WebGLRenderer({alpha:true,antialias:false,premultipliedAlpha:false});renderer.setSize(4,4);renderer.autoClear=false;renderer.toneMapping=THREE.NoToneMapping;
      const target=new THREE.WebGLRenderTarget(4,4,{depthBuffer:true,stencilBuffer:false}),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);camera.position.z=2;camera.lookAt(0,0,0);
      const gl=renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info'),draws=[];let current=null;
      window.__REA_PICA_DRAW_OBSERVER__=tag=>{current=tag.material;};
      for(const method of['drawElements','drawArrays']){const originalDraw=gl[method].bind(gl);gl[method]=(...args)=>{draws.push({material:current,blend:gl.isEnabled(gl.BLEND),srcRGB:gl.getParameter(gl.BLEND_SRC_RGB),dstRGB:gl.getParameter(gl.BLEND_DST_RGB),srcAlpha:gl.getParameter(gl.BLEND_SRC_ALPHA),dstAlpha:gl.getParameter(gl.BLEND_DST_ALPHA),equationRGB:gl.getParameter(gl.BLEND_EQUATION_RGB),equationAlpha:gl.getParameter(gl.BLEND_EQUATION_ALPHA),depthWrite:gl.getParameter(gl.DEPTH_WRITEMASK)});return originalDraw(...args);};}
      const pixels=()=>{const data=new Uint8Array(4);renderer.readRenderTargetPixels(target,1,1,1,1,data);return Array.from(data);};
      const reset=alpha=>{for(const object of Object.values(objects)){object.visible=false;object.rotation.set(0,0,0);object.position.set(0,0,0);}iris.colors.constant0=[1,.2,0,alpha];player.update(0,0);for(const object of Object.values(objects))object.visible=false;renderer.setRenderTarget(target);renderer.setClearColor(new THREE.Color(.2,.4,.6),.8);renderer.clear(true,true,true);return pixels();};
      const cases=[];
      for(const alpha of[.5,0,1]){const clear=reset(alpha);objects.LIris.visible=true;renderer.render(scene,camera);cases.push({name:`source-alpha-${alpha}`,source:[1,.2,0,alpha],destination:[.2,.4,.6,.8],destination_actual_rgba8:clear,actual:pixels(),expected:[1*alpha+.2*(1-alpha),.2*alpha+.4*(1-alpha),.6*(1-alpha),alpha*alpha+.8*(1-alpha)].map(v=>Math.round(v*255))});}
      reset(.5);objects.Occluder.visible=true;objects.Occluder.position.z=.2;objects.LIris.visible=true;objects.LIris.position.z=.1;renderer.render(scene,camera);cases.push({name:'opaque-occluder-blocks-behind-iris',actual:pixels(),expected:[26,179,77,255]});
      reset(.5);objects.LIris.visible=true;objects.LIris.rotation.y=Math.PI;renderer.render(scene,camera);cases.push({name:'backface-culling-preserved',actual:pixels(),expected:[51,102,153,204]});
      reset(.5);objects.LIris.visible=true;objects.LIris.position.z=.2;renderer.render(scene,camera);objects.LIris.visible=false;objects.Probe.visible=true;objects.Probe.position.z=.1;renderer.render(scene,camera);cases.push({name:'iris-does-not-write-depth-for-later-pass',actual:pixels(),expected:[255,255,0,255]});
      for(const item of cases){item.maximum_byte_error=Math.max(...item.actual.map((v,i)=>Math.abs(v-item.expected[i])));item.passed=item.maximum_byte_error<=1;}
      const result={renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),method:'actual attachPicaPlayer + RawShaderMaterial to RGBA8 linear render target; readRenderTargetPixels',fixture_context_premultiplied_alpha:gl.getContextAttributes().premultipliedAlpha,cases,draws,GL_constants:{SRC_ALPHA:gl.SRC_ALPHA,ONE_MINUS_SRC_ALPHA:gl.ONE_MINUS_SRC_ALPHA,FUNC_ADD:gl.FUNC_ADD},gl_error:gl.getError(),programs:renderer.info.programs.map(p=>({runnable:p.diagnostics?.runnable??null}))};
      target.dispose();geometry.dispose();scene.traverse(o=>o.material?.dispose());renderer.dispose();return result;
    },runtime.materials);
    receipt.ok=receipt.errors.length===0&&receipt.gpu.gl_error===0&&receipt.gpu.cases.every(c=>c.passed)&&receipt.gpu.draws.filter(d=>d.material==='LIris').every(d=>d.blend&&d.srcRGB===770&&d.dstRGB===771&&d.srcAlpha===770&&d.dstAlpha===771&&d.equationRGB===32774&&d.equationAlpha===32774&&!d.depthWrite);
  }catch(error){receipt.errors.push(String(error?.stack??error));}
  finally{if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}}
  receipt.input_unchanged=sha(await readFile(input))===receipt.input_sha256;receipt.renderer_unchanged=sha(await readFile(source))===receipt.renderer_sha256;receipt.ok=receipt.ok&&receipt.input_unchanged&&receipt.renderer_unchanged;
  const file=path.join(out,'gpu-math-receipt.json');await writeFile(file,JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({ok:receipt.ok,receipt:file,sha256:sha(await readFile(file)),cases:receipt.gpu?.cases,errors:receipt.errors},null,2));if(!receipt.ok)process.exitCode=1;
}
run().catch(error=>{console.error(error);process.exitCode=1;});
