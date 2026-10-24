import * as THREE from 'three';
import { GLTFLoader } from '/vendor/three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from '/vendor/three/examples/jsm/controls/OrbitControls.js';
import { attachPicaPlayer } from './pica-player.js';
import { createSynchronizedPlayer } from './synchronized-player.js';

const get = id => document.getElementById(id);
const viewport = get('viewport');
const status = get('status');
const clipSelect = get('clip');
const playback = get('playback');
let renderer;
let camera;
let controls;
let model;
let player;
let picaPlayer;
let clips = [];
let playing = false;
let comparisonView = null;
const originalWireframe = new Map();
const scene = new THREE.Scene();
const clock = new THREE.Clock();

function fail(error) {
  status.classList.add('error');
  status.textContent = `표시할 수 없습니다: ${error?.message || String(error)}`;
  playback.textContent = '모델이나 브라우저의 WebGL 지원을 확인해 주세요.';
}

function fitCamera() {
  if (!model || !camera || !controls) return;
  const box = new THREE.Box3().setFromObject(model);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, .01);
  const verticalFov = THREE.MathUtils.degToRad(camera.fov);
  const effectiveFov = Math.min(verticalFov, 2 * Math.atan(Math.tan(verticalFov / 2) * camera.aspect));
  const distance = radius / Math.sin(effectiveFov / 2) * 1.2;
  camera.position.copy(center).add(new THREE.Vector3(.55, .32, 1).normalize().multiplyScalar(distance));
  camera.near = Math.max(radius / 1000, .0001);
  camera.far = Math.max(distance + radius * 100, 100);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.minDistance = radius * .05;
  controls.maxDistance = radius * 30;
  controls.update();
}

function selectClip() {
  const clip = clips[Number(clipSelect.value)];
  if (!clip || !player) return;
  player.selectClip(Number(clipSelect.value));
  get('timeline').disabled=false;get('timeline').max=String(clip.duration);get('timeline').value='0';
  playback.textContent = `${playing ? '재생 중' : '일시 정지'} · ${clip.name || '이름 없는 클립'} · ${clip.duration.toFixed(2)}초`;
}

function addInfo(label, value) {
  const term = document.createElement('dt');
  const detail = document.createElement('dd');
  term.textContent = label;
  detail.textContent = String(value);
  get('model-info').append(term, detail);
}

function updateFaceControls(error) {
  const state=player?.snapshot();
  const active=state?.auxiliary_index!==null&&state?.auxiliary_index!==undefined;
  get('auxiliary').value=active?String(state.auxiliary_index):'';
  get('face-value').disabled=!active;
  if(state)get('face-value').value=String(state.configured_face_value);
  get('face-frame').textContent=active?`원본 프레임 ${state.auxiliary_source_frame} · 고정 포즈`:'보조 포즈 사용 안 함';
  const message=get('face-control-status');
  message.classList.toggle('error',Boolean(error));
  message.textContent=error?`포즈를 바꾸지 않았습니다: ${error.message}`:'';
}

function setupFacialControls(){
  const state=picaPlayer?.inspectFacial();
  if(!state?.groups.length)return;
  get('facial-controls').hidden=false;
  for(const group of state.groups){
    const label=document.createElement('label'),select=document.createElement('select');
    const id=`facial-channel-${group.channel}`;label.htmlFor=id;label.textContent=group.channel===0?'눈 표정':group.channel===3?'입 표정':`표정 채널 ${group.channel}`;select.id=id;
    for(let value=-1;value<=Math.floor(group.source_end_frame/group.frame_multiplier);value++){
      const option=document.createElement('option');option.value=String(value);option.textContent=value===-1?'사용 안 함':`${value} · 원본 프레임 ${value*group.frame_multiplier}`;select.append(option);
    }
    select.addEventListener('change',()=>{
      try{configureFacialOverlay({[group.channel]:Number(select.value)});}
      catch(error){select.value=String(picaPlayer.inspectFacial().values[group.channel]??-1);get('facial-status').textContent=`표정을 바꾸지 않았습니다: ${error.message}`;}
    });
    get('facial-values').append(label,select);
  }
}

async function load() {
  const [infoResponse, response] = await Promise.all([fetch('/api/model'), fetch('/model.glb')]);
  if (!infoResponse.ok || !response.ok) throw new Error('로컬 모델을 읽지 못했습니다.');
  const info = await infoResponse.json();
  get('filename').textContent = info.name;
  const data = await response.arrayBuffer();
  const loader = new GLTFLoader();
  const gltf = await new Promise((resolve, reject) => loader.parse(data, '', resolve, reject));
  model = gltf.scene;
  if (!model) throw new Error('기본 장면이 없습니다.');
  scene.add(model);
  clips = gltf.animations || [];
  picaPlayer = await attachPicaPlayer(gltf, THREE);
  player=createSynchronizedPlayer({gltf,THREE,picaPlayer});
  setupFacialControls();
  let meshCount = 0;
  let boneCount = 0;
  let triangles = 0;
  const materials = new Set();
  model.traverse(object => {
    if (object.isBone) boneCount += 1;
    if (!object.isMesh) return;
    meshCount += 1;
    const geometry = object.geometry;
    triangles += (geometry.index?.count ?? geometry.attributes.position?.count ?? 0) / 3;
    for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
      if (!material) continue;
      materials.add(material);
      originalWireframe.set(material, Boolean(material.wireframe));
    }
  });
  get('model-info').replaceChildren();
  addInfo('파일 크기', `${(info.bytes / 1024).toFixed(1)} KiB`);
  addInfo('장면 내 메시', meshCount);
  addInfo('삼각형', Math.floor(triangles).toLocaleString('ko-KR'));
  addInfo('재질', materials.size);
  addInfo('뼈대 관절', boneCount);
  addInfo('로드한 모션 클립', clips.length);
  if(picaPlayer)addInfo('재질·표정·가시성 모션', '원본 트랙 적용');
  clipSelect.replaceChildren();
  if (clips.length) {
    clips.forEach((clip, index) => {
      if(picaPlayer?.runtime.clips[index]?.role==='auxiliary_pose')return;
      const option = document.createElement('option');
      option.value = String(index);
      const sourceName=picaPlayer?.runtime.clips[index]?.source_name;
      option.textContent = `${index + 1}. ${sourceName?.replace(/^pm\d+_\d+_/, '') || clip.name || '이름 없는 클립'} (${clip.duration.toFixed(2)}초)`;
      clipSelect.append(option);
    });
    const auxiliary=get('auxiliary');
    for(const [index,binding] of (picaPlayer?.runtime.clips||[]).entries()){
      if(binding.role!=='auxiliary_pose')continue;
      const option=document.createElement('option');option.value=String(index);
      option.textContent=`${binding.source_package||'원본'} 턱·자세 포즈`;
      auxiliary.append(option);
    }
    auxiliary.disabled=auxiliary.options.length===1;
    updateFaceControls();
    get('material-mode').disabled=!picaPlayer;
    playing = true;
    player.play();
    clipSelect.disabled = false;
    for (const id of ['play', 'pause', 'stop']) get(id).disabled = false;
    selectClip();
  } else {
    const option = document.createElement('option');
    option.textContent = '포함된 모션 없음';
    clipSelect.append(option);
    playback.textContent = '이 GLB에는 애니메이션 클립이 없습니다.';
  }
  fitCamera();
  status.textContent = '';
}

try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  viewport.append(renderer.domElement);
  camera = new THREE.PerspectiveCamera(40, 1, .01, 1000);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xdcecff, 0x536170, 2.5));
  const key = new THREE.DirectionalLight(0xffffff, 3);
  key.position.set(3, 6, 5);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xa1bfff, 1);
  fill.position.set(-4, 2, -3);
  scene.add(fill);
  const resize = () => {
    const { width, height } = viewport.getBoundingClientRect();
    renderer.setSize(Math.max(width, 1), Math.max(height, 1));
    camera.aspect = comparisonView?.aspect ?? width / Math.max(height, 1);
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(viewport);
  resize();
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1);
    player?.advance(delta);
    if(player){const state=player.snapshot();get('timeline').value=String(state.time_seconds);get('time-value').textContent=`${state.time_seconds.toFixed(2)}초`;}
    if(!comparisonView)controls.update();
    renderer.render(scene, camera);
  });
  load().catch(fail);
} catch (error) { fail(error); }

get('reset').addEventListener('click', fitCamera);
get('rotate').addEventListener('change', event => { if (controls) controls.autoRotate = event.target.checked; });
get('wireframe').addEventListener('change', event => {
  for (const [material, original] of originalWireframe) {
    material.wireframe = event.target.checked || original;
    material.needsUpdate = true;
  }
});
clipSelect.addEventListener('change', selectClip);
get('play').addEventListener('click', () => {
  playing = true;
  player?.play();
  const clip = clips[Number(clipSelect.value)];
  if (clip) playback.textContent = `재생 중 · ${clip.name || '이름 없는 클립'} · ${clip.duration.toFixed(2)}초`;
});
get('pause').addEventListener('click', () => {
  playing = false;
  player?.pause();
  playback.textContent = '일시 정지';
});
get('stop').addEventListener('click', () => {
  playing = false;
  player?.stop();
  playback.textContent = '정지 · 클립 시작 위치';
});
get('speed').addEventListener('input', event => {
  const speed = Number(event.target.value);
  player?.setSpeed(speed);
  get('speed-value').textContent = `${speed.toFixed(2)}×`;
});
get('timeline').addEventListener('input',event=>{
  if(!player)return;
  playing=false;player.pause();player.seek(Number(event.target.value));playback.textContent='일시 정지 · 선택한 위치';
});
get('auxiliary').addEventListener('change',event=>{
  try{if(player){if(event.target.value==='')player.setFaceValue(-1);else player.selectAuxiliary(Number(event.target.value));}updateFaceControls();}
  catch(error){updateFaceControls(error);}
});
get('face-value').addEventListener('change',event=>{
  try{player?.setFaceValue(Number(event.target.value));updateFaceControls();}
  catch(error){updateFaceControls(error);}
});
get('material-mode').addEventListener('change',event=>picaPlayer?.setRenderMode(event.target.value));

// Read-only diagnostics for explicit local playback verification.
export function inspectPlayback(){
  const gl=renderer?.getContext(),debug=gl?.getExtension('WEBGL_debug_renderer_info');
  const bones=[];model?.traverse(object=>{if(object.isBone)bones.push({name:object.name,translation:object.position.toArray(),rotation:object.quaternion.toArray(),scale:object.scale.toArray()});});
  return {ready:Boolean(player&&status.textContent===''),playback:player?.snapshot()??null,
    actual_materials:picaPlayer?.inspect()??null,bones,render_frame:renderer?.info.render.frame??0,
    draw_calls:renderer?.info.render.calls??0,triangles:renderer?.info.render.triangles??0,
    programs:(renderer?.info.programs||[]).map(program=>({runnable:program.diagnostics?.runnable??null,used_times:program.usedTimes})),
    gl_error:gl?.getError(),webgl_renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl?.getParameter(gl.RENDERER)};
}

// Exact fixed source frame for QA; reading this never advances or changes playback.
export function inspectFaceControl(){
  const state=player?.snapshot();
  return state?{auxiliary_index:state.auxiliary_index,face_value:state.face_value,
    source_frame:player.getAuxiliarySourceFrame(),source_time_seconds:state.auxiliary_time_seconds,
    mode:state.auxiliary_mode,evidence:state.auxiliary_control_evidence}:null;
}

// Explicit source-state replay; no primary-clock/RNG inference or guest-memory writes.
export function configureFacialOverlay(values){
  if(!picaPlayer||!player)throw new Error('The model must be ready');
  picaPlayer.setFacialValues(values);
  player.seek(player.snapshot().time_seconds);
  const state=picaPlayer.inspectFacial();
  for(const group of state.groups){const control=get(`facial-channel-${group.channel}`);if(control)control.value=String(state.values[group.channel]??-1);}
  get('facial-status').textContent=Object.entries(state.values).filter(([,value])=>value>=0).map(([channel,value])=>`${Number(channel)===0?'눈':Number(channel)===3?'입':`채널 ${channel}`} 원본 프레임 ${value*10}`).join(' · ');
  return state;
}

export function inspectFacialOverlay(){return picaPlayer?.inspectFacial()??null;}

// Explicit local QA only. Normal loading, camera fitting and UI retain their defaults.
export function configureComparisonView(spec){
  if(!camera||!controls||!renderer||!model)throw new Error('The model and camera must be ready');
  if(!spec||typeof spec!=='object'||Array.isArray(spec))throw new Error('Comparison view must be an object');
  const keys=new Set(['position','target','fov','near','far','aspect','canvas_size']);
  if(Object.keys(spec).some(key=>!keys.has(key)))throw new Error('Unknown comparison view field');
  const vector=(value,name,fallback)=>{
    if(value===undefined)return fallback;
    if(!Array.isArray(value)||value.length!==3||value.some(n=>!Number.isFinite(n)||Math.abs(n)>1000000))throw new Error(`Invalid comparison ${name}`);
    return value.slice();
  };
  const next={position:vector(spec.position,'position',camera.position.toArray()),target:vector(spec.target,'target',controls.target.toArray()),
    fov:spec.fov===undefined?camera.fov:spec.fov,near:spec.near===undefined?camera.near:spec.near,
    far:spec.far===undefined?camera.far:spec.far,aspect:spec.aspect===undefined?(comparisonView?.aspect??camera.aspect):spec.aspect};
  if(!Number.isFinite(next.fov)||next.fov<5||next.fov>150||!Number.isFinite(next.near)||next.near<.000001||
    !Number.isFinite(next.far)||next.far<=next.near||next.far>100000000||!Number.isFinite(next.aspect)||next.aspect<.03125||next.aspect>32)
    throw new Error('Comparison projection is outside finite bounds');
  if(new THREE.Vector3().fromArray(next.position).distanceToSquared(new THREE.Vector3().fromArray(next.target))<1e-12)throw new Error('Comparison camera position and target must differ');
  if(spec.canvas_size!==undefined){
    const size=spec.canvas_size;
    if(!Array.isArray(size)||size.length!==2||size.some(n=>!Number.isInteger(n)||n<16||n>2048))throw new Error('Comparison canvas size must be within 16..2048');
    if(spec.aspect===undefined)next.aspect=size[0]/size[1];
  }
  if(next.aspect<.03125||next.aspect>32)throw new Error('Comparison canvas aspect is outside finite bounds');
  // Validate everything above before changing renderer, controls or camera state.
  controls.autoRotate=false;controls.enableDamping=false;controls.update();
  if(spec.canvas_size){
    const [width,height]=spec.canvas_size;
    for(const [name,value] of Object.entries({width:`${width}px`,height:`${height}px`,'min-height':'0','min-width':'0'}))viewport.style.setProperty(name,value,'important');
    const main=viewport.parentElement;main.style.setProperty('height','auto','important');main.style.setProperty('min-height','0','important');main.style.setProperty('grid-template-columns',`${width}px 290px`,'important');
    renderer.setPixelRatio(1);renderer.setSize(width,height);
  }
  camera.position.fromArray(next.position);controls.target.fromArray(next.target);
  camera.fov=next.fov;camera.near=next.near;camera.far=next.far;camera.aspect=next.aspect;
  comparisonView={aspect:next.aspect,explicitQaConfiguration:true};
  camera.updateProjectionMatrix();camera.lookAt(controls.target);controls.enabled=false;camera.updateMatrixWorld(true);
  return inspectComparisonView();
}

export function inspectComparisonView(){
  if(!camera||!controls||!renderer||!model)return null;
  camera.updateMatrixWorld(true);model.updateMatrixWorld(true);
  const bounds=new THREE.Box3().setFromObject(model),size=renderer.getDrawingBufferSize(new THREE.Vector2());
  return {explicitQaConfiguration:Boolean(comparisonView),camera:{position:camera.position.toArray(),quaternion:camera.quaternion.toArray(),
    up:camera.up.toArray(),target:controls.target.toArray(),fov:camera.fov,near:camera.near,far:camera.far,aspect:camera.aspect,
    projectionMatrix:camera.projectionMatrix.toArray(),viewMatrix:camera.matrixWorldInverse.toArray()},
    controls:{autoRotate:controls.autoRotate,damping:controls.enableDamping,enabled:controls.enabled},
    canvas:{width:size.x,height:size.y},model:{position:model.position.toArray(),quaternion:model.quaternion.toArray(),scale:model.scale.toArray(),matrixWorld:model.matrixWorld.toArray(),
      bounds:{min:bounds.min.toArray(),max:bounds.max.toArray(),method:'Three.Box3.setFromObject at captured pose'}},originalCameraVerified:false};
}
