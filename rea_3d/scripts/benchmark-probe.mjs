// Isolated-page QA instrumentation. No application source or live server is modified.
export function installBenchmarkProbe(){
  const marks={},loads=[],contexts=[],shaders=[],links=[],frames=[],pending=[];
  const now=()=>performance.now();let measuring=false,start=0,end=0,active=null,lastDrawTimestamp=null,firstDraw=null,disjoint=false;
  let gl0=null,ext0=null,querySkipped=0;
  const rawRAF=window.requestAnimationFrame.bind(window);
  const poll=()=>{if(!gl0||!ext0)return;const gl=gl0,ext=ext0;
    if(gl.getParameter(ext.GPU_DISJOINT_EXT)){disjoint=true;for(const p of pending)gl.deleteQuery(p.query);pending.length=0;return;}
    for(let i=pending.length-1;i>=0;i--){const p=pending[i];if(gl.getQueryParameter(p.query,gl.QUERY_RESULT_AVAILABLE)){const ns=gl.getQueryParameter(p.query,gl.QUERY_RESULT);if(p.frame)p.frame.gpu_ms=ns/1e6;gl.deleteQuery(p.query);pending.splice(i,1);}}
  };
  window.requestAnimationFrame=callback=>rawRAF(timestamp=>{
    poll();let query=null;
    if(gl0&&ext0&&pending.length<24){query=gl0.createQuery();gl0.beginQuery(ext0.TIME_ELAPSED_EXT,query);}else if(measuring&&ext0)querySkipped++;
    const previous=active,state={draws:0,submission_ms:0,render_ms:0};active=state;const cpuStart=now();
    try{return callback(timestamp);}finally{
      const cpuEnd=now();active=previous;if(query)gl0.endQuery(ext0.TIME_ELAPSED_EXT);
      let frame=null;
      if(state.draws){if(firstDraw===null)firstDraw=cpuEnd;const interval=lastDrawTimestamp===null?null:timestamp-lastDrawTimestamp;lastDrawTimestamp=timestamp;
        if(measuring){frame={timestamp,at_ms:cpuEnd,interval_ms:interval,callback_cpu_ms:cpuEnd-cpuStart,renderer_cpu_ms:state.render_ms,draw_submission_cpu_ms:state.submission_ms,draw_calls:state.draws,gpu_ms:null};frames.push(frame);}}
      if(query)pending.push({query,frame});
    }
  });
  const rawGetContext=HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext=function(type,...args){const gl=rawGetContext.call(this,type,...args);
    if(!gl||!['webgl','webgl2','experimental-webgl'].includes(type)||gl.__reaBenchWrapped)return gl;
    Object.defineProperty(gl,'__reaBenchWrapped',{value:true});const dbg=gl.getExtension('WEBGL_debug_renderer_info');
    const ext=type==='webgl2'?gl.getExtension('EXT_disjoint_timer_query_webgl2'):null;
    contexts.push({type,vendor:dbg?gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL):gl.getParameter(gl.VENDOR),renderer:dbg?gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),version:gl.getParameter(gl.VERSION),shadingLanguage:gl.getParameter(gl.SHADING_LANGUAGE_VERSION),timerQuerySupported:Boolean(ext),attributes:gl.getContextAttributes()});
    if(!gl0){gl0=gl;ext0=ext;}
    for(const name of ['drawArrays','drawElements','drawArraysInstanced','drawElementsInstanced']){if(!gl[name])continue;const raw=gl[name].bind(gl);gl[name]=(...params)=>{const t=now();try{return raw(...params);}finally{if(active){active.draws++;active.submission_ms+=now()-t;}}};}
    for(const [name,array,status] of [['compileShader',shaders,gl.COMPILE_STATUS],['linkProgram',links,gl.LINK_STATUS]]){const raw=gl[name].bind(gl);gl[name]=object=>{const t=now();const value=raw(object);array.push({at_ms:t,cpu_ms:now()-t,ok:name==='compileShader'?gl.getShaderParameter(object,status):gl.getProgramParameter(object,status)});return value;};}
    return gl;
  };
  const rawFetch=window.fetch.bind(window);window.fetch=async(...args)=>{const url=String(args[0]?.url??args[0]),entry={url,start_ms:now()};loads.push(entry);try{const response=await rawFetch(...args);entry.headers_ms=now();entry.status=response.status;return response;}catch(error){entry.error=error.message;throw error;}};
  window.__REA_BENCH_PROBE__={mark:(key)=>{marks[key]=now();},measureRender:callback=>{const t=now();try{return callback();}finally{if(active)active.render_ms+=now()-t;}},beginMeasure:()=>{poll();frames.length=0;start=now();end=0;measuring=true;return start;},endMeasure:()=>{measuring=false;end=now();poll();return end;},snapshot:()=>{poll();return {marks:{...marks},fetch:loads.slice(),first_draw_ms:firstDraw,measurement:{start_ms:start,end_ms:end,elapsed_ms:end?end-start:0},frames:frames.map(f=>({...f})),contexts,shaderCompiles:shaders,programLinks:links,gpuDisjoint:disjoint,gpuPending:pending.length,gpuQuerySkipped:querySkipped,canvas:gl0?{width:gl0.drawingBufferWidth,height:gl0.drawingBufferHeight}:null,environment:{devicePixelRatio,visibility:document.visibilityState,userAgent:navigator.userAgent,webdriver:navigator.webdriver,hardwareConcurrency:navigator.hardwareConcurrency},jsHeap:performance.memory?{usedJSHeapSize:performance.memory.usedJSHeapSize,totalJSHeapSize:performance.memory.totalJSHeapSize,jsHeapSizeLimit:performance.memory.jsHeapSizeLimit,scope:'Chromium nonstandard performance.memory; may be coarse, not GPU memory'}:null};}};
}
