import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {createSynchronizedPlayer} from '../preview/synchronized-player.js';
import {evaluateMaterialFrame} from '../preview/pica-runtime.js';

const quaternion=angle=>new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),angle).toArray();
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
const angleNear=(scene,bone,angle)=>near(scene.getObjectByName(bone).quaternion.angleTo(new THREE.Quaternion().fromArray(quaternion(angle))),0);
function fixture({sourceFrames=71,duration=70/30,trackEnd=70/30,fps=30}={}){
  const scene=new THREE.Group();
  for(const name of ['Waist','Jaw','LFoot','RFoot','Feeler']){const bone=new THREE.Bone();bone.name=name;scene.add(bone);}
  const constant=(name,angle,end=2)=>new THREE.QuaternionKeyframeTrack(`${name}.quaternion`,[0,end],[...quaternion(angle),...quaternion(angle)]);
  const primary=new THREE.AnimationClip('primary',2,[new THREE.VectorKeyframeTrack('Waist.position',[0,2],[0,0,0,20,0,0]),
    constant('Jaw',.3),constant('LFoot',.1),constant('RFoot',.1),constant('Feeler',.1)]);
  const auxiliary=new THREE.AnimationClip('auxiliary',duration,[constant('LFoot',1.5,trackEnd),constant('RFoot',1.5,trackEnd),constant('Feeler',1.2,trackEnd),
    new THREE.QuaternionKeyframeTrack('Jaw.quaternion',[0,trackEnd],[...quaternion(0),...quaternion(1)]),
    new THREE.VectorKeyframeTrack('LFoot.position',[0,trackEnd],[999,0,0,999,0,0])]);
  const runtime={source_fps:fps,materials:[{name:'Eye',texture_names:[],tex_coords:[{scale:[1,1],translation:[0,0]}],colors:{}}],
    mesh_nodes:[{name:'Vine',default_visible:false}],clips:[{role:'primary',
      material_animations:[{end_frame:60,elements:[{name:'Eye',target_id:21,curves:[null,{keys:[{frame:0,value:0},{frame:60,value:.5}],interpolation:'linear'}]}]}],
      visibility_animations:[{end_frame:60,elements:[{name:'Vine',target_id:47,curves:[{kind:'boolean',values:Array.from({length:61},(_,i)=>i>=15)}]}]}]},
    {role:'auxiliary_pose',auxiliary_pose:{source_frame_count:sourceFrames,channel_mask:['LFoot','RFoot','Feeler','Jaw'].map(bone=>({bone,path:'rotation'}))}},
    {role:'primary'}]};
  const picaPlayer={runtime,update:(i,t)=>evaluateMaterialFrame(runtime,i,t*30)};
  const player=createSynchronizedPlayer({gltf:{scene,animations:[primary,auxiliary,primary.clone()]},THREE,picaPlayer});
  player.selectClip(0);
  return {scene,player,runtime,auxiliary};
}

test('face selects a fixed source frame while primary skeleton, material and visibility keep advancing',()=>{
  const {scene,player}=fixture();player.seek(.2);player.play();player.setSpeed(2);
  player.selectAuxiliary(1);player.setFaceValue(3);
  let state=player.snapshot();near(state.time_seconds,.2);assert.equal(state.playing,true);assert.equal(state.speed,2);
  assert.equal(state.face_value,3);assert.equal(state.auxiliary_source_frame,30);near(state.auxiliary_time_seconds,1);
  assert.equal(state.auxiliary_mode,'fixed_source_pose');assert.equal(player.getAuxiliarySourceFrame(),30);
  assert.equal(state.auxiliary_control_evidence.original_runtime_trigger_verified,false);
  angleNear(scene,'Jaw',3/7);angleNear(scene,'LFoot',1.5);near(scene.getObjectByName('LFoot').position.x,0);
  assert.equal(state.visual_state.visibility.Vine,false);
  for(let i=0;i<4;i++)player.advance(.05);
  state=player.snapshot();near(state.time_seconds,.6);near(scene.getObjectByName('Waist').position.x,6);
  near(state.visual_state.material_states.Eye.tex_coords[0].translation[1],.15);
  assert.equal(state.visual_state.visibility.Vine,true);near(state.visual_state.frame,18);
  assert.equal(state.auxiliary_source_frame,30);near(state.auxiliary_time_seconds,1);angleNear(scene,'Jaw',3/7);
});

test('pause, primary seek/change/loop and stop preserve the fixed face without drift; -1 detaches',()=>{
  const {scene,player}=fixture();player.selectAuxiliary(1);player.setFaceValue(5);player.seek(.7);player.pause();
  for(let i=0;i<20;i++)player.advance(.1);
  near(player.snapshot().time_seconds,.7);near(player.snapshot().auxiliary_source_frame,50);angleNear(scene,'Jaw',5/7);
  player.seek(1.8);player.play();player.advance(.5);
  near(player.snapshot().time_seconds,.3);angleNear(scene,'Jaw',5/7);
  player.selectClip(2);assert.equal(player.snapshot().clip_index,2);angleNear(scene,'Jaw',5/7);
  player.stop();near(player.snapshot().time_seconds,0);assert.equal(player.snapshot().playing,false);angleNear(scene,'Jaw',5/7);
  player.setFaceValue(-1);assert.equal(player.snapshot().auxiliary_index,null);assert.equal(player.snapshot().face_value,-1);
  assert.equal(player.snapshot().auxiliary_source_frame,null);assert.equal(player.getAuxiliarySourceFrame(),null);
  angleNear(scene,'Jaw',.3);angleNear(scene,'LFoot',.1);
  player.selectAuxiliary(1);assert.equal(player.snapshot().face_value,5);angleNear(scene,'Jaw',5/7);
});

test('all eight bounded face values include the real source endpoint without looping',()=>{
  const {scene,player}=fixture();player.selectAuxiliary(1);
  for(let value=0;value<=7;value++){
    player.setFaceValue(value);assert.equal(player.snapshot().auxiliary_source_frame,value*10);
    angleNear(scene,'Jaw',value/7);
  }
  player.play();for(let i=0;i<40;i++)player.advance(.1);
  assert.equal(player.snapshot().auxiliary_source_frame,70);near(player.snapshot().auxiliary_time_seconds,70/30);angleNear(scene,'Jaw',1);
});

test('out-of-bounds source counts, clip duration and track times reject frame 70 without mutating state',()=>{
  for(const options of [{sourceFrames:70},{duration:2},{trackEnd:2}]){
    const {scene,player}=fixture(options);player.selectAuxiliary(1);player.setFaceValue(3);player.seek(.4);player.play();
    const before=player.snapshot(),jaw=scene.getObjectByName('Jaw').quaternion.toArray();
    assert.throws(()=>player.setFaceValue(7),/outside/);
    assert.deepEqual(player.snapshot(),before);assert.deepEqual(scene.getObjectByName('Jaw').quaternion.toArray(),jaw);
    player.selectAuxiliary(null);player.setFaceValue(7);
    assert.throws(()=>player.selectAuxiliary(1),/outside/);assert.equal(player.snapshot().auxiliary_index,null);
  }
  for(const options of [{sourceFrames:undefined},{sourceFrames:0},{fps:0},{fps:NaN}]){
    const {player,runtime}=fixture(options);if(options.sourceFrames===undefined&&'sourceFrames'in options)delete runtime.clips[1].auxiliary_pose.source_frame_count;
    assert.throws(()=>player.selectAuxiliary(1),/bounds and source fps/);
  }
});

test('invalid face values and unverified/duplicate source masks fail without changing the selected pose',()=>{
  const {player,runtime}=fixture();player.selectAuxiliary(1);player.setFaceValue(2);const before=player.snapshot();
  for(const value of [-2,8,1.5,NaN,'3',null])assert.throws(()=>player.setFaceValue(value),/integer/);
  assert.deepEqual(player.snapshot(),before);
  assert.throws(()=>player.selectAuxiliary(0),/not verified/);assert.throws(()=>player.selectAuxiliary(.5),/Unknown/);
  assert.throws(()=>player.selectClip(1),/layer over a primary/);assert.throws(()=>player.seek(NaN),/finite/);
  assert.throws(()=>player.setSpeed(5),/outside/);
  runtime.clips[1].auxiliary_pose.channel_mask.push({bone:'Jaw',path:'rotation'});
  assert.throws(()=>player.selectAuxiliary(1),/Duplicate/);
  assert.equal(player.snapshot().auxiliary_source_frame,20);
});
