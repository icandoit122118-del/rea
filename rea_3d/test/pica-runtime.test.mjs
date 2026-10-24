import test from 'node:test';
import assert from 'node:assert/strict';
import {sampleCurve,textureMatrix,evaluateMaterialFrame,createPicaFragment} from '../preview/pica-runtime.js';

test('Hermite preserves distinct incoming/outgoing slopes and discrete curves hold',()=>{
  const curve={interpolation:'hermite',keys:[{frame:0,value:0,out_slope:2},{frame:2,value:2,in_slope:0}]};
  assert.equal(sampleCurve(curve,1),1.5);
  assert.equal(sampleCurve({...curve,interpolation:'step'},1),0);
  assert.equal(sampleCurve({kind:'boolean',start_frame:0,values:[false,true,false]},1.9),true);
});
test('Maya transform retains source scale-dependent negative translation',()=>{
  assert.deepEqual(textureMatrix({scale:[2,1],rotation:0,translation:[1,.25],transform_type:0}),[2,0,0,-0,1,0,-2,-.25,1]);
});
test('Visual state updates source UV and node visibility without mutating static material',()=>{
  const constant=value=>({keys:[{frame:0,value}],interpolation:'step'});
  const runtime={materials:[{name:'Eye',texture_names:['open'],tex_coords:[{scale:[2,1],translation:[1,0],rotation:0}],colors:{constant0:[1,1,1,0]}}],mesh_nodes:[{name:'Vine',default_visible:true}],clips:[{material_animations:[{elements:[{name:'Eye',target_id:21,curves:[constant(1),constant(.5)]}]}],visibility_animations:[{elements:[{name:'Vine',target_id:47,curves:[{kind:'boolean',values:[false]}]}]}]}]};
  const state=evaluateMaterialFrame(runtime,0,0);
  assert.equal(state.visibility.Vine,false);assert.deepEqual(state.material_states.Eye.tex_coords[0].translation,[1,.5]);
  assert.deepEqual(runtime.materials[0].tex_coords[0].translation,[1,0]);
});
test('Source-dependent shader does not fabricate absent combiner or LUT',()=>{
  assert.throws(()=>createPicaFragment({texenv_stages:[]},{desktop:true}),/missing PICA combiner/);
  assert.throws(()=>createPicaFragment({lut_references:{reflect_r:{table:'unknown',sampler:'missing'}}},{desktop:true}),/Missing source LUT/);
});

test('WebGL alpha comparisons emit float references at both byte-range endpoints',()=>{
  const stage={constant_index:0,color_sources:['Texture0','Texture0','Texture0'],alpha_sources:['Texture0','Texture0','Texture0'],
    color_operands:['Color','Color','Color'],alpha_operands:['Alpha','Alpha','Alpha'],color_combiner:'Replace',alpha_combiner:'Replace'};
  for(const reference of [0,128,255])for(const function_code of [2,3,4,5,6,7]){
    const shader=createPicaFragment({texenv_stages:[stage],alpha_test:{enabled:true,function_code,reference}},{webgl2:true});
    const comparison=shader.match(/if\(!\((outputColor\.a[^)]+)\)\) discard;/)?.[1];assert.ok(comparison);
    assert.match(comparison,/\d+\.\d+$/);
  }
  assert.throws(()=>createPicaFragment({texenv_stages:[stage],alpha_test:{enabled:true,function_code:6,reference:NaN}},{webgl2:true}),/Invalid alpha/);
});
