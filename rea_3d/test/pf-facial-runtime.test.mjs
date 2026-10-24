import test from 'node:test';
import assert from 'node:assert/strict';
import {evaluateMaterialFrame,validateFacialValue} from '../preview/pica-runtime.js';

const curve=(end,value)=>({keys:[{frame:0,value:0},{frame:end,value}],interpolation:'linear'});
const animation=(name,end,value)=>({end_frame:end,elements:[{name,target_id:21,curves:[curve(end,value),null]}]});
const runtime={materials:[{name:'Eye',texture_names:['eye'],tex_coords:[{translation:[0,0],scale:[1,1]}]},{name:'Mouth',texture_names:['mouth'],tex_coords:[{translation:[0,0],scale:[1,1]}]}],mesh_nodes:[{name:'MouthMesh',default_visible:false}],
  clips:[{material_animations:[animation('Eye',100,.1),animation('Mouth',100,.2)]}],
  facial_animation:{schema:'rea3d-pf-facial-runtime-v1',groups:[
    {channel:0,source_entries:[0],source_end_frame:80,frame_multiplier:10,material_animations:[animation('Eye',80,.8)]},
    {channel:3,source_entries:[9,10,11],source_end_frame:70,frame_multiplier:10,material_animations:[animation('Mouth',70,.7)],visibility_animations:[{end_frame:5,elements:[{name:'MouthMesh',target_id:47,curves:[{kind:'boolean',values:[false,false,false,false,false,true]}]}]}]},
  ]}};

test('PF facial layers use independently fixed index times ten and retain the primary motion clock',()=>{
  const source=JSON.stringify(runtime),first=evaluateMaterialFrame(runtime,0,10,{'0':2,'3':5}),later=evaluateMaterialFrame(runtime,0,60,{'0':2,'3':5});
  assert.equal(first.frame,10);assert.equal(later.frame,60);
  assert.equal(first.material_states.Eye.tex_coords[0].translation[0],.2);
  assert.equal(first.material_states.Mouth.tex_coords[0].translation[0],.5);
  assert.deepEqual(first.material_states,later.material_states);
  assert.equal(first.visibility.MouthMesh,true);
  assert.deepEqual(first.facial_overlays.map(x=>[x.channel,x.source_frame]),[[0,20],[3,50]]);
  assert.equal(first.facial_overlays[0].original_runtime_trigger_verified,false);
  assert.equal(JSON.stringify(runtime),source);
});

test('PF detach restores primary UV and visibility while another facial group remains fixed',()=>{
  const state=evaluateMaterialFrame(runtime,0,50,{'0':-1,'3':7});
  assert.equal(state.material_states.Eye.tex_coords[0].translation[0],.05);
  assert.equal(state.material_states.Mouth.tex_coords[0].translation[0],.7);
  assert.deepEqual(state.facial_overlays.map(x=>x.channel),[3]);
  const detached=evaluateMaterialFrame(runtime,0,50,{'0':-1,'3':-1});
  assert.equal(detached.visibility.MouthMesh,false);assert.equal(detached.material_states.Mouth.tex_coords[0].translation[0],.1);
  assert.equal(evaluateMaterialFrame(runtime,0,0,{'0':8}).material_states.Eye.tex_coords[0].translation[0],.8);
});

test('PF inspection accepts only existing integer channels and source-bounded integer values',()=>{
  for(const [channel,value] of [[0,9],[3,8],[3,-2],[3,1.5],[3,NaN],[3,'3'],[1,0],[.5,0]])assert.throws(()=>validateFacialValue(runtime,channel,value),/source facial|bounded source/);
  assert.throws(()=>evaluateMaterialFrame(runtime,0,0,{'00':2}),/canonical/);
  assert.throws(()=>evaluateMaterialFrame({...runtime,facial_animation:undefined},0,0,{'0':2}),/Unknown source facial/);
  assert.equal(evaluateMaterialFrame({...runtime,facial_animation:undefined},0,50).material_states.Eye.tex_coords[0].translation[0],.05);
});
