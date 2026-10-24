// Shared-runtime clone load harness; distinct from the existing app's steady-state measurements.
import * as THREE from 'three';
import {GLTFLoader} from '/vendor/three/examples/jsm/loaders/GLTFLoader.js';
import {clone} from '/vendor/three/examples/jsm/utils/SkeletonUtils.js';
import {attachPicaPlayer} from '/pica-player.js';
import {createSynchronizedPlayer} from '/synchronized-player.js';
const cfg=globalThis.__REA_BENCH_CONFIG__,probe=globalThis.__REA_BENCH_PROBE__;
let ready=false,renderer,players=[],failure=null;
export function inspectBenchmarkScene(){return {ready,failure,count:players.length,mode:cfg.mode,clip:0,playback:players[0]?.snapshot(),counters:renderer?{memory:{...renderer.info.memory},render:{...renderer.info.render},programCount:renderer.info.programs.length}:null,glError:renderer?.getContext().getError()};}
async function boot(){
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(1);renderer.setSize(cfg.width,cfg.height);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;document.body.append(renderer.domElement);
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,cfg.width/cfg.height,.05,7000);
  camera.position.set(0,35,500);camera.lookAt(0,35,0);scene.add(new THREE.HemisphereLight(0xdcecff,0x536170,2.5));
  for(const [color,intensity,position] of [[0xffffff,3,[3,6,5]],[0xa1bfff,1,[-4,2,-3]]]){const light=new THREE.DirectionalLight(color,intensity);light.position.fromArray(position);scene.add(light);}
  const response=await fetch('/model.glb'),buffer=await response.arrayBuffer();probe.mark('buffer_ready');probe.mark('parse_started');
  const original=await new Promise((resolve,reject)=>new GLTFLoader().parse(buffer,'',resolve,reject));probe.mark('parse_completed');probe.mark('pica_attach_started');
  const columns=cfg.count===6?3:4,rows=Math.ceil(cfg.count/columns);
  for(let i=0;i<cfg.count;i++){
    const model=clone(original.scene),gltf={...original,scene:model};model.position.set((i%columns-(columns-1)/2)*150,((rows-1)/2-Math.floor(i/columns))*120,0);
    model.traverse(object=>{if(object.isMesh)object.frustumCulled=false;});scene.add(model);
    const pica=await attachPicaPlayer(gltf,THREE);pica.setRenderMode(cfg.mode);const player=createSynchronizedPlayer({gltf,THREE,picaPlayer:pica});player.selectClip(0);player.play();players.push(player);
  }
  probe.mark('pica_attach_completed');const clock=new THREE.Clock();ready=true;
  renderer.setAnimationLoop(()=>{const dt=Math.min(clock.getDelta(),.1);for(const player of players)player.advance(dt);probe.measureRender(()=>renderer.render(scene,camera));});
}
boot().catch(error=>{failure=error.message;console.error(error);});
