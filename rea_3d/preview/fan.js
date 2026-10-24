import * as THREE from 'three';
import {GLTFLoader} from '/vendor/three/examples/jsm/loaders/GLTFLoader.js';
import {OrbitControls} from '/vendor/three/examples/jsm/controls/OrbitControls.js';
import {attachPicaPlayer} from './pica-player.js';
import {createSynchronizedPlayer} from './synchronized-player.js';
import {SAVE_KEY,DEMO_PARTY,defaultState,validateSave} from './fan-state.js';

const $=id=>document.getElementById(id),viewport=$('fan-viewport');
const menus=[['party','포켓몬','My little team','M8 4h8l4 5v7l-4 4H8l-4-4V9z M4 12h16 M9 12a3 3 0 0 0 6 0a3 3 0 0 0-6 0'],['bag','가방','Ready for the road','M8 7V5a4 4 0 0 1 8 0v2 M6 7h12l2 13H4z M9 12h6v5H9z'],['dex','도감','A growing collection','M5 3h12a2 2 0 0 1 2 2v16H7a3 3 0 0 1-3-3V5a2 2 0 0 1 1-2z M8 3v18 M12 8h4 M12 12h4'],['trainer','트레이너','Hello, adventurer','M4 5h16v14H4z M9 12a2 2 0 1 0 0-4a2 2 0 1 0 0 4 M6 16c0-3 6-3 6 0 M15 9h2 M15 13h2'],['save','저장','Keep this moment','M5 3h12l3 3v15H4V3z M8 3v6h8V3 M8 21v-7h8v7'],['settings','설정','Make yourself at home','M10 3h4l1 3l3 1l2 4l-2 3v4l-4 3l-3-2H7l-3-4l2-3V8z M9 12a3 3 0 1 0 6 0a3 3 0 1 0-6 0']];
const titles={party:['MY LITTLE TEAM','함께하는 포켓몬'],bag:['READY FOR THE ROAD','가방을 열어볼까요?'],dex:['A GROWING COLLECTION','작은 발견의 기록'],trainer:['HELLO, ADVENTURER','나의 트레이너'],save:['KEEP THIS MOMENT','지금의 새잎을 저장해요'],settings:['MAKE YOURSELF AT HOME','편한 모습으로 설정해요']};
let state=defaultState(),currentMenu='home',ready=false,primaryIndices=[],metadata=null,storageAvailable=true;
let renderer,scene,camera,controls,model,picaPlayer,player,toastTimer;
const clock=new THREE.Clock(),menuButtons=[];
const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
function toast(message){clearTimeout(toastTimer);$('fan-toast').textContent=message;$('fan-toast').classList.add('visible');toastTimer=setTimeout(()=>$('fan-toast').classList.remove('visible'),2600);}
function setSaveIndicator(){const node=$('save-indicator');node.replaceChildren(el('span','status-dot'));node.append(document.createTextNode(!storageAvailable?'이 브라우저에서 저장을 사용할 수 없어요':state.savedAt?`${new Date(state.savedAt).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})}에 이 기기에 저장했어요`:'이 기기에 저장할 수 있어요'));}
function navigate(menu,focus=true){
  if(menu!=='home'&&!titles[menu])throw new Error('Unknown menu');
  currentMenu=menu;const home=menu==='home';$('home-menu').hidden=!home;$('menu-content').hidden=home;$('menu-back').hidden=home;$('home-count').hidden=!home;
  $('section-overline').textContent=home?'YOUR NEXT LITTLE STEP':titles[menu][0];$('section-title').textContent=home?'어디로 갈까요?':titles[menu][1];
  if(controls)controls.enabled=home;
  $('scene-hint').textContent=home?'드래그해서 둘러보세요':'메뉴를 닫으면 시점을 움직일 수 있어요';
  if(!home)renderPanel(menu);
  if(focus){if(home)(menuButtons.find(button=>button.dataset.menu===navigate.lastMenu)||menuButtons[0]).focus();else $('menu-back').focus();}
  if(!home)navigate.lastMenu=menu;
}
for(const [id,name,subtitle,iconPath] of menus){
  const button=el('button','menu-button');button.type='button';button.dataset.menu=id;button.setAttribute('aria-label',name);
  const icon=el('span','menu-icon');icon.setAttribute('aria-hidden','true');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');const shape=document.createElementNS(svg.namespaceURI,'path');shape.setAttribute('d',iconPath);svg.append(shape);icon.append(svg);
  const label=el('span');label.append(el('strong','',name),el('small','',subtitle));button.append(icon,label,el('span','arrow','›'));button.addEventListener('click',()=>navigate(id));$('home-menu').append(button);menuButtons.push(button);
}
$('home-menu').addEventListener('keydown',event=>{
  const index=menuButtons.indexOf(document.activeElement);if(index<0)return;
  const columns=getComputedStyle($('home-menu')).gridTemplateColumns.split(/\s+/).length;
  const offset={ArrowRight:1,ArrowLeft:-1,ArrowDown:columns,ArrowUp:-columns}[event.key];if(offset===undefined)return;
  event.preventDefault();menuButtons[(index+offset+menuButtons.length)%menuButtons.length].focus();
});
document.addEventListener('keydown',event=>{if(currentMenu!=='home'&&(event.key==='Escape'||event.key==='Backspace'&&!['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName))){event.preventDefault();navigate('home');}});
$('menu-back').addEventListener('click',()=>navigate('home'));
function note(text){return el('p','section-note',text);}
function action(text,id,handler,secondary=false){const button=el('button',`action-button${secondary?' secondary':''}`,text);button.type='button';button.id=id;button.addEventListener('click',handler);return button;}
function renderPanel(menu){
  const panel=el('div','panel');panel.dataset.panel=menu;$('menu-content').replaceChildren(panel);
  if(menu==='party'){
    const card=el('div','party-card'),avatar=el('div','party-avatar','❧');avatar.setAttribute('aria-hidden','true');
    const details=el('div','party-details'),title=el('h3','',DEMO_PARTY.name);title.append(el('span','level-label',`Lv.${DEMO_PARTY.level}`));details.append(title,el('span','type-chip','풀'),el('span','type-chip poison','독'));
    const hp=el('div','hp-info');hp.append(el('span','',`HP  `),el('strong','',`${DEMO_PARTY.hp} / ${DEMO_PARTY.maxHp}`),el('div','hp-bar'));card.append(avatar,details,hp);
    const moves=el('div','moves');for(const move of DEMO_PARTY.moves){const chip=el('span','move-chip',move.name);chip.title=move.original;moves.append(chip);}
    panel.append(note('오늘의 데모 파티 · 원작 테스트 세이브에서 확인한 1마리예요.'),card,moves);
  }else if(menu==='bag'){
    const empty=el('div','empty-state'),copy=el('div');copy.append(el('h3','','아직 아이템이 없어요'),el('p','','이 데모에서는 메뉴와 포켓몬을 살펴볼 수 있어요.\n아이템 획득과 사용은 다음 여정에서 만나요.'));empty.append(el('span','empty-symbol','◇'),copy);panel.append(empty);
  }else if(menu==='dex'){
    const card=el('article','dex-entry'),number=el('div','dex-number');number.append(el('small','','POKÉDEX No.'),document.createTextNode('001'));
    const copy=el('div');copy.append(el('h3','','이상해씨'),el('span','type-chip','풀'),el('span','type-chip poison','독'),el('p','','등에 씨앗을 지닌 포켓몬. 위의 3D 화면에서 모습을 둘러볼 수 있어요. 이 데모에 준비된 도감 항목은 이상해씨 1종이에요.'));card.append(number,copy);panel.append(card);
  }else if(menu==='trainer'){
    const form=el('form','trainer-form'),label=el('label','','이 데모에서 사용할 이름'),input=el('input');input.id='trainer-name';input.name='trainerName';input.maxLength=12;input.autocomplete='off';input.value=state.trainerName;label.htmlFor=input.id;label.append(input);
    const button=el('button','action-button','이름 적용');button.type='submit';form.append(label,button);form.addEventListener('submit',event=>{event.preventDefault();const name=input.value.trim();if(!name){input.setCustomValidity('이름을 적어주세요.');input.reportValidity();return;}input.setCustomValidity('');state.trainerName=name;toast(`${name} 님, 반가워요! 저장 메뉴에서 기억해 둘 수 있어요.`);});input.addEventListener('input',()=>input.setCustomValidity(''));
    panel.append(note('이름과 화면 설정은 이 팬게임 데모 안에서만 사용해요.'),form,note('데모 파티 1마리 · 원작 세이브와 연결되지 않아요.'));
  }else if(menu==='save'){
    const box=el('div','save-panel'),copy=el('div');copy.append(el('strong','',`${state.trainerName}의 작은 기록`),el('p','',state.savedAt?`마지막 저장 ${new Date(state.savedAt).toLocaleString('ko-KR')}\n이름과 화면 설정을 이 브라우저에 저장해요.`:'아직 저장한 기록이 없어요.\n이름과 화면 설정을 이 브라우저에 저장해요.'));
    const buttons=el('div','save-actions');buttons.append(action('지금 저장','save-now',saveState),action('기록 불러오기','load-save',()=>loadState(true),true));box.append(copy,buttons);panel.append(box,note('저장 범위: 트레이너 이름·동작·눈 표정·화면 회전. 데모 파티와 원작 게임 파일은 바뀌지 않아요.'));
  }else if(menu==='settings'){
    const rows=el('div','settings-list');
    for(const [id,label,key] of [['setting-animation','동작 재생','animation'],['setting-rotate','화면 자동 회전','autoRotate']]){
      const row=el('label','setting-row',label),input=el('input');input.id=id;input.type='checkbox';input.checked=state.settings[key];input.disabled=!ready;input.addEventListener('change',()=>{state.settings[key]=input.checked;applySettings();});row.append(input);rows.append(row);
    }
    const clipRow=el('label','setting-row','재생할 동작'),select=el('select');select.id='setting-clip';select.disabled=!ready;
    for(const [ordinal,index] of primaryIndices.entries()){const option=el('option','',`동작 ${String(ordinal+1).padStart(2,'0')}`);option.value=String(index);select.append(option);}select.value=String(state.settings.clipIndex);select.addEventListener('change',()=>{state.settings.clipIndex=Number(select.value);applySettings();});clipRow.append(select);rows.append(clipRow);
    const eyeRow=el('label','setting-row','눈 표정'),eye=el('select');eye.id='setting-eye';eye.disabled=!ready||!picaPlayer?.runtime.facial_animation;for(const [value,label] of [[0,'기본 표정'],[1,'반쯤 감은 눈'],[2,'감은 눈']]){const option=el('option','',label);option.value=String(value);eye.append(option);}eye.value=String(state.settings.eyeValue);eye.addEventListener('change',()=>{state.settings.eyeValue=Number(eye.value);applySettings();});eyeRow.append(eye);rows.append(eyeRow);
    panel.append(rows,el('p','setting-help','설정은 위의 3D 화면에 바로 적용돼요. 이 데모에는 소리가 없어요. 저장 메뉴에서 설정을 기억할 수 있어요.'));
  }
}
function saveState(){
  if(!ready){toast('모델을 불러온 다음 저장할 수 있어요.');return;}
  const saved={...state,settings:{...state.settings},savedAt:new Date().toISOString()};
  try{const checked=validateSave(saved,primaryIndices);localStorage.setItem(SAVE_KEY,JSON.stringify(checked));const readback=validateSave(JSON.parse(localStorage.getItem(SAVE_KEY)),primaryIndices);state=readback;storageAvailable=true;setSaveIndicator();renderPanel('save');toast('이름과 화면 설정을 이 기기에 저장했어요.');}
  catch{storageAvailable=false;setSaveIndicator();toast('저장할 수 없어요. 브라우저의 저장 공간 설정을 확인해 주세요.');}
}
function loadState(notify=false){
  try{const raw=localStorage.getItem(SAVE_KEY);if(!raw){if(notify)toast('아직 저장된 기록이 없어요. 먼저 지금 저장을 눌러주세요.');return false;}
    state=validateSave(JSON.parse(raw),primaryIndices);storageAvailable=true;applySettings();setSaveIndicator();if(notify){navigate('home');toast(`${state.trainerName}의 기록을 불러왔어요.`);}return true;
  }catch{if(notify)toast('저장 기록을 읽을 수 없어요. 현재 화면 설정을 유지했어요.');return false;}
}
function applySettings(){
  if(!ready)return;
  if(player.snapshot().clip_index!==state.settings.clipIndex)player.selectClip(state.settings.clipIndex);
  if(state.settings.animation)player.play();else player.pause();
  controls.autoRotate=state.settings.autoRotate;controls.enabled=currentMenu==='home';
  if(picaPlayer?.runtime.facial_animation){picaPlayer.setFacialValues({'0':state.settings.eyeValue,'3':-1});player.seek(player.snapshot().time_seconds);}
  $('scene-play').textContent=state.settings.animation?'Ⅱ':'▷';$('scene-play').setAttribute('aria-label',state.settings.animation?'동작 일시 정지':'동작 재생');$('scene-play').title=state.settings.animation?'동작 일시 정지':'동작 재생';
}
function fitCamera(){
  if(!model)return;
  model.updateMatrixWorld(true);model.traverse(object=>{if(object.isSkinnedMesh){object.skeleton.update();object.computeBoundingBox();}});
  const bounds=new THREE.Box3().setFromObject(model),center=bounds.getCenter(new THREE.Vector3()),radius=bounds.getSize(new THREE.Vector3()).length()/2;
  const halfFov=Math.min(THREE.MathUtils.degToRad(camera.fov)/2,Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)*camera.aspect));
  const distance=radius/Math.sin(halfFov)*1.06;camera.position.copy(center).add(new THREE.Vector3(.58,.25,1).normalize().multiplyScalar(distance));camera.near=Math.max(radius/1000,.001);camera.far=radius*150;camera.updateProjectionMatrix();controls.target.copy(center);controls.minDistance=radius*1.4;controls.maxDistance=radius*8;controls.update();
}
$('scene-play').addEventListener('click',()=>{state.settings.animation=!state.settings.animation;applySettings();if(currentMenu==='settings')renderPanel('settings');});
$('scene-reset').addEventListener('click',()=>{fitCamera();toast('이상해씨가 잘 보이는 시점으로 돌아왔어요.');});
async function boot(){
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.setClearColor(0,0);viewport.append(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(36,1,.01,10000);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.enablePan=false;controls.maxPolarAngle=Math.PI*.57;controls.autoRotateSpeed=.65;
  const resize=()=>{const rect=viewport.getBoundingClientRect();renderer.setSize(rect.width,rect.height);camera.aspect=rect.width/Math.max(1,rect.height);camera.updateProjectionMatrix();if(model)fitCamera();};new ResizeObserver(resize).observe(viewport);resize();
  const [response,info]=await Promise.all([fetch('/model.glb'),fetch('/api/model')]);if(!response.ok||!info.ok)throw new Error('모델을 읽지 못했어요.');metadata=await info.json();
  // Parse the fetched self-contained buffer once; no texture or model leaves loopback.
  const buffer=await response.arrayBuffer();const loaded=await new Promise((resolve,reject)=>new GLTFLoader().parse(buffer,'',resolve,reject));
  model=loaded.scene;scene.add(model);picaPlayer=await attachPicaPlayer(loaded,THREE);player=createSynchronizedPlayer({gltf:loaded,THREE,picaPlayer});
  primaryIndices=loaded.animations.map((_,index)=>index).filter(index=>picaPlayer?.runtime.clips[index]?.role!=='auxiliary_pose');if(!primaryIndices.length)throw new Error('재생할 동작이 없어요.');
  state.settings.clipIndex=primaryIndices[0];player.selectClip(primaryIndices[0]);ready=true;loadState(false);applySettings();fitCamera();$('fan-loading').hidden=true;$('scene-play').disabled=false;$('scene-reset').disabled=false;setSaveIndicator();
  const bounds=new THREE.Box3().setFromObject(model),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
  const shadowCanvas=document.createElement('canvas');shadowCanvas.width=128;shadowCanvas.height=128;const shadowContext=shadowCanvas.getContext('2d');
  const gradient=shadowContext.createRadialGradient(64,64,5,64,64,63);gradient.addColorStop(0,'rgba(69,95,52,.18)');gradient.addColorStop(.5,'rgba(69,95,52,.09)');gradient.addColorStop(1,'rgba(69,95,52,0)');shadowContext.fillStyle=gradient;shadowContext.fillRect(0,0,128,128);
  const shadow=new THREE.Mesh(new THREE.PlaneGeometry(size.x*1.6,size.z*1.6),new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(shadowCanvas),transparent:true,depthWrite:false,toneMapped:false}));shadow.rotation.x=-Math.PI/2;shadow.position.set(center.x,bounds.min.y-.3,center.z);scene.add(shadow);
  renderer.setAnimationLoop(()=>{player.advance(Math.min(clock.getDelta(),.1));if(currentMenu==='home')controls.update();renderer.render(scene,camera);});
  if(currentMenu!=='home')renderPanel(currentMenu);
}
boot().catch(error=>{$('fan-loading').textContent=`불러오지 못했어요: ${error.message}`;$('fan-loading').classList.add('error');console.error(error);});

export function inspectFan(){
  const gl=renderer?.getContext(),ext=gl?.getExtension('WEBGL_debug_renderer_info');
  return {ready,currentMenu,state:structuredClone(state),party:DEMO_PARTY,primaryIndices:[...primaryIndices],metadata,playback:player?.snapshot()??null,facial:picaPlayer?.inspectFacial()??null,
    cameraInputEnabled:controls?.enabled??false,autoRotate:controls?.autoRotate??false,cameraPosition:camera?.position.toArray(),renderFrame:renderer?.info.render.frame??0,drawCalls:renderer?.info.render.calls??0,glError:gl?.getError(),gpu:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null,
    source:'local demo save based on original test fixture',originalSaveLinked:false,battleImplemented:false,bagItemUseImplemented:false};
}
