/** Actual draw-time WebGL state audit of one explicitly selected local GLB.
 * Requires the existing task-specific isolated Chromium authorization. This
 * never attaches to an existing browser, TUS or original-game process and never
 * changes source material/renderer state. Instrumentation only reads GL state.
 */
import path from 'node:path';
import {readFile,writeFile,mkdir,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {startPreview} from '../src/preview-server.mjs';

const project=fileURLToPath(new URL('../',import.meta.url));
const defaultInput='C:/rea3d-validation/baseline/model.glb';
const expectedBaselineHash='e8bd5ccb7524170842037ef5d67286d1f3e2c197896bad2812bde673c63a3b76';
const defaultPlaywright='C:/browser-qa/node_modules/playwright-core/index.mjs';
const defaultChromium='C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');

export function parseArgs(args){
  const options={input:defaultInput,playwright:defaultPlaywright,chromium:defaultChromium,authorized:false,preflight:false};
  for(let i=0;i<args.length;i++){
    const key=args[i];
    if(key==='--authorized-isolated-browser')options.authorized=true;
    else if(key==='--preflight')options.preflight=true;
    else if(['--input','--output-dir','--playwright','--chromium'].includes(key)){
      const value=args[++i];if(!value||value.startsWith('--'))throw new Error(`Missing ${key}`);
      options[{'--input':'input','--output-dir':'output','--playwright':'playwright','--chromium':'chromium'}[key]]=value;
    }else throw new Error(`Unknown argument ${key}`);
  }
  return options;
}

export function parseGlb(bytes){
  if(bytes.readUInt32LE(0)!==0x46546c67||bytes.readUInt32LE(4)!==2||bytes.readUInt32LE(8)!==bytes.length)throw new Error('Invalid GLB header');
  const size=bytes.readUInt32LE(12);if(bytes.readUInt32LE(16)!==0x4e4f534a||size>32*1024*1024||20+size>bytes.length)throw new Error('Invalid GLB JSON');
  const document=JSON.parse(bytes.subarray(20,20+size).toString('utf8'));
  const runtime=document.extras?.reaPicaRuntime;
  if(!runtime?.materials?.length)throw new Error('Selected GLB has no decoded PICA source states');
  return {document,runtime};
}

/** Runs only inside the new owned browser page, before app module loading. */
export function installStateProbe(){
  let tag=null,enabled=true,serial=0;
  const draws=[],compile=[],links=[],errors=[],contexts=[];
  const programs=new WeakMap();let programId=0;
  const programRecord=(gl,program)=>{
    if(!program)return null;
    if(programs.has(program))return programs.get(program);
    const uniforms=[];
    for(let i=0,n=gl.getProgramParameter(program,gl.ACTIVE_UNIFORMS);i<n;i++)uniforms.push(gl.getActiveUniform(program,i)?.name);
    const uvLocations={};for(const name of uniforms)if(/^uUvMatrix[012]$/.test(name))uvLocations[name]=gl.getUniformLocation(program,name);
    const record={id:++programId,pica:uniforms.some(name=>/^uUvMatrix[012]$/.test(name))&&uniforms.some(name=>/^uTexture[012]$/.test(name)),uniforms,uvLocations};
    programs.set(program,record);return record;
  };
  const enumName=(gl,value)=>{
    const names=['ZERO','ONE','SRC_COLOR','ONE_MINUS_SRC_COLOR','DST_COLOR','ONE_MINUS_DST_COLOR','SRC_ALPHA','ONE_MINUS_SRC_ALPHA','DST_ALPHA','ONE_MINUS_DST_ALPHA','CONSTANT_COLOR','ONE_MINUS_CONSTANT_COLOR','CONSTANT_ALPHA','ONE_MINUS_CONSTANT_ALPHA','SRC_ALPHA_SATURATE','FUNC_ADD','FUNC_SUBTRACT','FUNC_REVERSE_SUBTRACT','MIN','MAX','NEVER','ALWAYS','EQUAL','NOTEQUAL','LESS','LEQUAL','GREATER','GEQUAL','FRONT','BACK','FRONT_AND_BACK','CW','CCW','KEEP','REPLACE','INCR','DECR','INVERT','INCR_WRAP','DECR_WRAP','VERTEX_SHADER','FRAGMENT_SHADER'];
    return names.find(name=>gl[name]===value)??`0x${Number(value).toString(16)}`;
  };
  const enabledCap=(gl,cap)=>gl.isEnabled(gl[cap]);
  const param=(gl,name)=>gl.getParameter(gl[name]);
  const enumParam=(gl,name)=>enumName(gl,param(gl,name));
  window.__REA_PICA_DRAW_OBSERVER__=value=>{tag={mesh:value.mesh,material:value.material,source_node:value.source_node,expected_uv:value.expected_uv.map(matrix=>[...matrix])};};
  const originalGetContext=HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext=function(...args){const gl=originalGetContext.apply(this,args);if(gl&&/^webgl/.test(args[0])&&!contexts.includes(gl))contexts.push(gl);return gl;};
  for(const proto of [window.WebGLRenderingContext?.prototype,window.WebGL2RenderingContext?.prototype].filter(Boolean)){
    const wrap=(name,before,after)=>{
      const original=proto[name];if(typeof original!=='function'||original.__REA_SHADER_AUDIT_WRAPPED__)return;
      const replacement=function(...args){
        if(before)try{before.call(this,args);}catch(error){errors.push({method:name,phase:'before',message:String(error)});}
        const value=original.apply(this,args);
        if(after)try{after.call(this,args,value);}catch(error){errors.push({method:name,phase:'after',message:String(error)});}
        return value;
      };
      Object.defineProperty(replacement,'__REA_SHADER_AUDIT_WRAPPED__',{value:true});proto[name]=replacement;
    };
    wrap('compileShader',null,function([shader]){compile.push({type:enumName(this,this.getShaderParameter(shader,this.SHADER_TYPE)),ok:this.getShaderParameter(shader,this.COMPILE_STATUS),log:this.getShaderInfoLog(shader),source:this.getShaderSource(shader)});});
    wrap('linkProgram',null,function([program]){links.push({ok:this.getProgramParameter(program,this.LINK_STATUS),log:this.getProgramInfoLog(program)});});
    for(const method of ['drawElements','drawArrays','drawElementsInstanced','drawArraysInstanced'])wrap(method,function(args){
      if(!enabled||!tag)return;
      const program=programRecord(this,this.getParameter(this.CURRENT_PROGRAM));if(!program?.pica)return;
      const currentTag=tag;tag=null;
      const state={blend_enabled:enabledCap(this,'BLEND'),blend:{
        color_src:enumParam(this,'BLEND_SRC_RGB'),color_dst:enumParam(this,'BLEND_DST_RGB'),alpha_src:enumParam(this,'BLEND_SRC_ALPHA'),alpha_dst:enumParam(this,'BLEND_DST_ALPHA'),color_equation:enumParam(this,'BLEND_EQUATION_RGB'),alpha_equation:enumParam(this,'BLEND_EQUATION_ALPHA'),constant_color:Array.from(param(this,'BLEND_COLOR'))},
        depth_test:enabledCap(this,'DEPTH_TEST'),depth_function:enumParam(this,'DEPTH_FUNC'),depth_write:param(this,'DEPTH_WRITEMASK'),color_write:Array.from(param(this,'COLOR_WRITEMASK')),
        cull_enabled:enabledCap(this,'CULL_FACE'),cull_face:enumParam(this,'CULL_FACE_MODE'),front_face:enumParam(this,'FRONT_FACE'),
        stencil_enabled:enabledCap(this,'STENCIL_TEST'),stencil_front:{function:enumParam(this,'STENCIL_FUNC'),reference:param(this,'STENCIL_REF'),value_mask:param(this,'STENCIL_VALUE_MASK'),write_mask:param(this,'STENCIL_WRITEMASK'),fail:enumParam(this,'STENCIL_FAIL'),depth_fail:enumParam(this,'STENCIL_PASS_DEPTH_FAIL'),pass:enumParam(this,'STENCIL_PASS_DEPTH_PASS')},
        stencil_back:{function:enumParam(this,'STENCIL_BACK_FUNC'),reference:param(this,'STENCIL_BACK_REF'),value_mask:param(this,'STENCIL_BACK_VALUE_MASK'),write_mask:param(this,'STENCIL_BACK_WRITEMASK'),fail:enumParam(this,'STENCIL_BACK_FAIL'),depth_fail:enumParam(this,'STENCIL_BACK_PASS_DEPTH_FAIL'),pass:enumParam(this,'STENCIL_BACK_PASS_DEPTH_PASS')},
        polygon_offset_enabled:enabledCap(this,'POLYGON_OFFSET_FILL'),polygon_offset_factor:param(this,'POLYGON_OFFSET_FACTOR'),polygon_offset_units:param(this,'POLYGON_OFFSET_UNITS'),
        default_framebuffer:this.getParameter(this.FRAMEBUFFER_BINDING)===null};
      const actualUv={};for(const [name,location] of Object.entries(program.uvLocations))actualUv[name]=Array.from(this.getUniform(this.getParameter(this.CURRENT_PROGRAM),location));
      draws.push({serial:++serial,timing:'immediately_before_actual_gl_draw',method,args:args.map(v=>typeof v==='number'?v:null),tag:currentTag,program_id:program.id,state,actual_uv_matrices:actualUv});
      if(draws.length>512)draws.shift();
    });
  }
  window.__REA_SHADER_STATE_AUDIT__={
    freeze(){enabled=false;return this.snapshot();},
    snapshot(){return {draws:[...draws],compile:[...compile],links:[...links],errors:[...errors],contexts:contexts.map(gl=>{const ext=gl.getExtension('WEBGL_debug_renderer_info');return {version:param(gl,'VERSION'),shading_language:param(gl,'SHADING_LANGUAGE_VERSION'),vendor:ext?gl.getParameter(ext.UNMASKED_VENDOR_WEBGL):param(gl,'VENDOR'),renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):param(gl,'RENDERER')};})};}
  };
}

const factors={Zero:'ZERO',One:'ONE',SourceColor:'SRC_COLOR',OneMinusSourceColor:'ONE_MINUS_SRC_COLOR',DestinationColor:'DST_COLOR',OneMinusDestinationColor:'ONE_MINUS_DST_COLOR',SourceAlpha:'SRC_ALPHA',OneMinusSourceAlpha:'ONE_MINUS_SRC_ALPHA',DestinationAlpha:'DST_ALPHA',OneMinusDestinationAlpha:'ONE_MINUS_DST_ALPHA',ConstantColor:'CONSTANT_COLOR',OneMinusConstantColor:'ONE_MINUS_CONSTANT_COLOR',ConstantAlpha:'CONSTANT_ALPHA',OneMinusConstantAlpha:'ONE_MINUS_CONSTANT_ALPHA',SourceAlphaSaturate:'SRC_ALPHA_SATURATE'};
const equations={FuncAdd:'FUNC_ADD',FuncSubtract:'FUNC_SUBTRACT',FuncReverseSubtract:'FUNC_REVERSE_SUBTRACT',Min:'MIN',Max:'MAX'};
const depthFunctions={Never:'NEVER',Always:'ALWAYS',Equal:'EQUAL',Notequal:'NOTEQUAL',Less:'LESS',Lequal:'LEQUAL',Greater:'GREATER',Gequal:'GEQUAL'};
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);

export function verifyRetainedRawState(config){
  const registers=new Map();
  for(const write of config.pica?.fragment_commands?.writes??[]){
    const mask=[0,1,2,3].reduce((bits,index)=>bits|((write.byte_mask>>index&1)?0xff<<(index*8):0),0)>>>0;
    const old=registers.get(write.register)??{value:0,known:0};
    registers.set(write.register,{value:((old.value&~mask)|(write.raw_value&mask))>>>0,known:(old.known|mask)>>>0});
  }
  const known=(register,mask)=>{const item=registers.get(register);return item&&((item.known&mask)>>>0)===(mask>>>0)?item.value:null;};
  const operation=known(0x100,0x100),blend=known(0x101,0xffff0707),depth=known(0x107,0x1f71),cull=known(0x40,3),stencil=known(0x105,1);
  const factorNames=['Zero','One','SourceColor','OneMinusSourceColor','DestinationColor','OneMinusDestinationColor','SourceAlpha','OneMinusSourceAlpha','DestinationAlpha','OneMinusDestinationAlpha','ConstantColor','OneMinusConstantColor','ConstantAlpha','OneMinusConstantAlpha','SourceAlphaSaturate'];
  const equationNames=['FuncAdd','FuncSubtract','FuncReverseSubtract','Min','Max'];
  const testNames=['Never','Always','Equal','Notequal','Less','Lequal','Greater','Gequal'];
  const raw={blend_enabled:operation===null?null:Boolean(operation&0x100),blend:blend===null?null:{color_equation:equationNames[blend&7],alpha_equation:equationNames[blend>>>8&7],color_src:factorNames[blend>>>16&15],color_dst:factorNames[blend>>>20&15],alpha_src:factorNames[blend>>>24&15],alpha_dst:factorNames[blend>>>28&15]},depth:depth===null?null:{enabled:Boolean(depth&1),depth_function:testNames[depth>>>4&7],depth_write:Boolean(depth&0x1000),color_write:[8,9,10,11].map(bit=>Boolean(depth&(1<<bit)))},face_culling:cull===null?null:['Never','FrontFace','BackFace'][cull&3],stencil_enabled:stencil===null?null:Boolean(stencil&1)};
  const checks=[{field:'blend_enabled',passed:raw.blend_enabled!==null&&raw.blend_enabled===config.blend?.enabled},{field:'blend_factors_equations',passed:raw.blend!==null&&Object.entries(raw.blend).every(([name,value])=>value!==undefined&&value===config.blend?.[name])},{field:'depth_and_color_mask',passed:raw.depth!==null&&Object.entries(raw.depth).every(([name,value])=>equal(value,config.depth_color_mask?.[name]))},{field:'face_culling',passed:raw.face_culling!==null&&raw.face_culling===config.face_culling},{field:'stencil_enabled',passed:raw.stencil_enabled!==null&&raw.stencil_enabled===Boolean(config.stencil_test_raw&1)}];
  return {method:'independently_recombine_retained_raw_command_words_and_decode_selected_bitfields',original_BCH_bytes_reopened:false,raw,checks,passed:checks.every(check=>check.passed)};
}

export function compareMaterial(config,draws){
  const samples=draws.filter(draw=>draw.tag.material===config.name),checks=[];
  const check=(field,expected,read)=>{
    if(expected===null||expected===undefined){checks.push({field,status:'source_unknown',expected});return;}
    const actual=[...new Map(samples.map(draw=>[JSON.stringify(read(draw.state)),read(draw.state)])).values()];
    checks.push({field,expected,actual,passed:samples.length>0&&actual.every(value=>equal(value,expected))});
  };
  const blend=config.blend??{},depth=config.depth_color_mask??{};
  const oneZeroReplace=blend.color_equation==='FuncAdd'&&blend.alpha_equation==='FuncAdd'&&blend.color_src==='One'&&blend.alpha_src==='One'&&blend.color_dst==='Zero'&&blend.alpha_dst==='Zero';
  check('blend_enabled',blend.enabled,s=>s.blend_enabled);
  check('depth_test',depth.enabled,s=>s.depth_test);check('depth_function',depthFunctions[depth.depth_function],s=>s.depth_function);check('depth_write',depth.depth_write,s=>s.depth_write);check('color_write',depth.color_write,s=>s.color_write);
  check('cull_enabled',config.face_culling==='Never'?false:['BackFace','FrontFace'].includes(config.face_culling)?true:null,s=>s.cull_enabled);
  if(config.face_culling!=='Never')check('cull_face',{BackFace:'BACK',FrontFace:'FRONT'}[config.face_culling],s=>s.cull_face);
  check('stencil_enabled',Number.isInteger(config.stencil_test_raw)?Boolean(config.stencil_test_raw&1):null,s=>s.stencil_enabled);
  const activeBlendSamples=samples.filter(draw=>draw.state.blend_enabled);
  const factorsChecked=blend.enabled&&activeBlendSamples.length>0;
  if(factorsChecked){
    for(const field of ['color_src','color_dst','alpha_src','alpha_dst'])check(`blend.${field}`,factors[blend[field]],s=>s.blend[field]);
    for(const field of ['color_equation','alpha_equation'])check(`blend.${field}`,equations[blend[field]],s=>s.blend[field]);
  }
  const allBlendDisabled=samples.length>0&&samples.every(draw=>!draw.state.blend_enabled);
  const blendEffect=allBlendDisabled&&blend.enabled?(oneZeroReplace?'disabled_but_equivalent_for_decoded_OneZero_FuncAdd':'required_source_blending_not_applied'):'see_draw_state';
  const mismatches=checks.filter(item=>item.passed===false);
  return {material:config.name,sample_count:samples.length,source:{blend,depth_color_mask:depth,face_culling:config.face_culling,stencil_test_raw:config.stencil_test_raw,stencil_operation_raw:config.stencil_operation_raw,shader_reference:config.shader_reference,pica:config.pica,source_offsets:config.source_offsets},
    retained_raw_state_check:verifyRetainedRawState(config),checks,blend_effect_classification:blendEffect,blend_factors_checked_while_active:Boolean(factorsChecked),state_mismatch_count:mismatches.length,
    semantic_mismatch_fields:mismatches.filter(item=>!(item.field==='blend_enabled'&&blendEffect==='disabled_but_equivalent_for_decoded_OneZero_FuncAdd')).map(item=>item.field),
    pixel_impact_verified:false};
}

export async function run(options){
  if(!path.isAbsolute(options.input))throw new Error('Absolute input required');
  const input=await readFile(options.input),{runtime}=parseGlb(input);
  const inputHash=sha(input);
  if(inputHash!==expectedBaselineHash)throw new Error('This bounded baseline audit requires the recovered exact-source GLB hash');
  const source={};for(const name of ['scripts/verify-iris-blending.mjs','preview/app.js','preview/pica-player.js','preview/pica-runtime.js','src/preview-server.mjs'])source[name]=sha(await readFile(path.join(project,name)));
  const preflight={input:{path:options.input,bytes:input.length,sha256:inputHash},materials:runtime.materials.map(m=>m.name),source,execution:'filesystem_and_node_import_only',browser_launched:false};
  for(const name of ['chromium','playwright']){const s=await stat(options[name]);if(!s.isFile())throw new Error(`Missing ${name}`);}
  const playwright=await import(pathToFileURL(options.playwright).href);
  if(options.preflight)return preflight;
  if(!options.authorized)throw new Error('Explicit task-specific isolated Chromium flag required');
  if(!options.output||!path.isAbsolute(options.output))throw new Error('New absolute output directory required');
  const output=path.resolve(options.output),allowed=path.resolve('C:/rea3d-iris-blend');
  if(!output.startsWith(allowed+path.sep))throw new Error('Output must stay in the new C: validation/shader scope');
  await mkdir(output,{recursive:false});await mkdir(path.join(output,'tmp'));
  const receipt={schema:'rea3d.actual-draw-shader-state-audit.v1',verification_ok:false,execution:'actual_isolated_headless_Chromium_current_Three_PICA_player',input:preflight.input,source,started_at:new Date().toISOString(),errors:[],console:[],blocked_requests:[],boundaries:{native_game_process_access:false,original_shader_bytecode_executed:false,external_PokePack_program_resolved:false,original_camera_light_verified:false,original_pixel_parity:false,source_renderer_mutated:false},browser_profile:path.join(output,'browser-profile')};
  let preview,context;
  try{
    preview=await startPreview({input:options.input,port:0});receipt.url=preview.url;
    context=await playwright.chromium.launchPersistentContext(receipt.browser_profile,{executablePath:options.chromium,headless:true,viewport:{width:1000,height:800},deviceScaleFactor:1,args:['--use-angle=d3d11','--enable-webgl','--ignore-gpu-blocklist'],env:{...process.env,TEMP:path.join(output,'tmp'),TMP:path.join(output,'tmp')}});
    const page=context.pages()[0]??await context.newPage();
    await page.route('**/*',route=>{const url=route.request().url();if(url.startsWith(preview.url)||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();receipt.blocked_requests.push(url);return route.abort('blockedbyclient');});
    page.on('pageerror',error=>receipt.errors.push(error.message));page.on('console',message=>{if(['warning','error'].includes(message.type()))receipt.console.push({type:message.type(),text:message.text()});});
    await page.addInitScript(installStateProbe);await page.goto(preview.url,{waitUntil:'networkidle',timeout:30000});
    await page.waitForFunction(()=>document.getElementById('clip')?.disabled===false||document.getElementById('status')?.classList.contains('error'),null,{timeout:30000});
    const status=await page.locator('#status').textContent();if(status)throw new Error(`Player failed: ${status}`);
    await page.locator('#pause').click();
    await page.waitForFunction(names=>{const d=window.__REA_SHADER_STATE_AUDIT__.snapshot().draws;return names.every(name=>d.filter(x=>x.tag.material===name).length>=2);},runtime.materials.map(m=>m.name),{timeout:15000});
    receipt.probe=await page.evaluate(()=>window.__REA_SHADER_STATE_AUDIT__.freeze());
    receipt.playback=await page.evaluate(async()=>{const app=await import('/app.js');return app.inspectPlayback();});
    const pngPath=path.join(output,'baseline-actual-player.png'),png=await page.locator('#viewport canvas').screenshot({path:pngPath});receipt.capture={path:pngPath,bytes:png.length,sha256:sha(png)};
    receipt.compiled_shader_sources=[];
    for(const [index,event]of receipt.probe.compile.entries()){
      const sourcePath=path.join(output,`compiled-${String(index).padStart(2,'0')}-${event.type}.glsl`),sourceBytes=Buffer.from(event.source??'','utf8');
      await writeFile(sourcePath,sourceBytes,{flag:'wx'});delete event.source;
      event.source_path=sourcePath;event.source_sha256=sha(sourceBytes);event.source_bytes=sourceBytes.length;receipt.compiled_shader_sources.push({type:event.type,path:sourcePath,sha256:event.source_sha256,bytes:sourceBytes.length});
    }
    receipt.uv_uniform_checks=receipt.probe.draws.map(draw=>({serial:draw.serial,material:draw.tag.material,matrices:Object.entries(draw.actual_uv_matrices).map(([name,actual])=>{const expected=draw.tag.expected_uv[Number(name.at(-1))],error=Math.max(...actual.map((v,i)=>Math.abs(v-expected[i])));return {name,maximum_component_error:error,passed:error<2e-6};})}));
    receipt.materials=runtime.materials.map(material=>compareMaterial(material,receipt.probe.draws));
    receipt.decoded_state_parity=receipt.materials.every(material=>material.semantic_mismatch_fields.length===0);
    receipt.findings=receipt.materials.filter(m=>m.blend_effect_classification==='required_source_blending_not_applied').map(m=>({priority:'P2',material:m.material,issue:'decoded source alpha blending is disabled at actual draw',expected:m.source.blend,actual:'BLEND=false',pixel_impact_verified:false}));
    receipt.verification_ok=receipt.materials.every(m=>m.sample_count>=2&&m.retained_raw_state_check.passed)&&receipt.uv_uniform_checks.length>0&&receipt.uv_uniform_checks.every(draw=>draw.matrices.length>0&&draw.matrices.every(matrix=>matrix.passed))&&receipt.errors.length===0&&receipt.probe.errors.length===0&&receipt.probe.compile.length>0&&receipt.probe.compile.every(x=>x.ok)&&receipt.probe.links.length>0&&receipt.probe.links.every(x=>x.ok)&&receipt.playback.gl_error===0;
  }catch(error){receipt.errors.push(String(error?.stack??error));}
  finally{if(context)await context.close();if(preview){preview.server.closeAllConnections();await new Promise(resolve=>preview.server.close(resolve));}}
  receipt.finished_at=new Date().toISOString();receipt.input.sha256_after=sha(await readFile(options.input));receipt.input_unchanged=receipt.input.sha256_after===inputHash;
  const outputPath=path.join(output,'shader-runtime-receipt.json');await writeFile(outputPath,JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  return {receipt:outputPath,sha256:sha(await readFile(outputPath)),verification_ok:receipt.verification_ok,decoded_state_parity:receipt.decoded_state_parity,findings:receipt.findings,errors:receipt.errors};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))run(parseArgs(process.argv.slice(2))).then(result=>{console.log(JSON.stringify(result,null,2));if(result.verification_ok===false)process.exitCode=1;}).catch(error=>{console.error(String(error?.stack??error));process.exitCode=1;});
