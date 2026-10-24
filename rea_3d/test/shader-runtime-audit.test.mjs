import test from 'node:test';
import assert from 'node:assert/strict';
import {compareMaterial,parseArgs,verifyRetainedRawState} from '../scripts/audit-shader-runtime.mjs';

const source=(replace=false)=>({name:'Iris',blend:{enabled:true,color_equation:'FuncAdd',alpha_equation:'FuncAdd',color_src:replace?'One':'SourceAlpha',alpha_src:replace?'One':'SourceAlpha',color_dst:replace?'Zero':'OneMinusSourceAlpha',alpha_dst:replace?'Zero':'OneMinusSourceAlpha'},depth_color_mask:{enabled:true,depth_function:'Lequal',depth_write:false,color_write:[true,true,true,true]},face_culling:'BackFace',stencil_test_raw:0xFF40});
const draw=(patch={})=>({tag:{material:'Iris'},state:{blend_enabled:false,blend:{color_src:'SRC_ALPHA',alpha_src:'SRC_ALPHA',color_dst:'ONE_MINUS_SRC_ALPHA',alpha_dst:'ONE_MINUS_SRC_ALPHA',color_equation:'FUNC_ADD',alpha_equation:'FUNC_ADD'},depth_test:true,depth_function:'LEQUAL',depth_write:false,color_write:[true,true,true,true],cull_enabled:true,cull_face:'BACK',stencil_enabled:false,...patch}});

test('inactive required alpha blending is a semantic mismatch without claiming pixel impact',()=>{
  const result=compareMaterial(source(),[draw(),draw()]);
  assert.equal(result.blend_effect_classification,'required_source_blending_not_applied');
  assert.deepEqual(result.semantic_mismatch_fields,['blend_enabled']);
  assert.equal(result.pixel_impact_verified,false);
});
test('disabled One Zero additive blend is classified as replacement equivalent',()=>{
  const result=compareMaterial(source(true),[draw()]);
  assert.equal(result.blend_effect_classification,'disabled_but_equivalent_for_decoded_OneZero_FuncAdd');
  assert.equal(result.state_mismatch_count,1);assert.deepEqual(result.semantic_mismatch_fields,[]);
});
test('active alpha blend factors and equations are checked against decoded source',()=>{
  const ok=compareMaterial(source(),[draw({blend_enabled:true})]);assert.deepEqual(ok.semantic_mismatch_fields,[]);
  const bad=draw({blend_enabled:true});bad.state.blend.alpha_src='ONE';
  assert.deepEqual(compareMaterial(source(),[bad]).semantic_mismatch_fields,['blend.alpha_src']);
});
test('each depth mask, comparison, color mask and culling mismatch remains visible',()=>{
  const result=compareMaterial(source(),[draw({depth_function:'LESS',depth_write:true,color_write:[true,true,true,false],cull_face:'FRONT'})]);
  assert.deepEqual(result.semantic_mismatch_fields,['blend_enabled','depth_function','depth_write','color_write','cull_face']);
});
test('missing draws cannot pass parity checks and browser execution remains opt-in',()=>{
  const result=compareMaterial(source(),[]);assert.equal(result.sample_count,0);assert.ok(result.semantic_mismatch_fields.length>0);
  assert.equal(parseArgs(['--preflight']).authorized,false);assert.equal(parseArgs(['--authorized-isolated-browser']).authorized,true);
  assert.throws(()=>parseArgs(['--unknown']),/Unknown argument/);
});
test('retained raw partial command words independently establish blend and depth masks',()=>{
  const config=source();config.pica={fragment_commands:{writes:[
    {register:0x100,byte_mask:2,raw_value:0x100},
    {register:0x101,byte_mask:3,raw_value:0},
    {register:0x101,byte_mask:12,raw_value:0x76760000},
    {register:0x107,byte_mask:15,raw_value:0xf51},
    {register:0x40,byte_mask:1,raw_value:2},
    {register:0x105,byte_mask:1,raw_value:0x40},
  ]}};
  const report=verifyRetainedRawState(config);assert.equal(report.passed,true);assert.equal(report.raw.blend.color_src,'SourceAlpha');assert.equal(report.raw.depth.depth_write,false);
  config.blend.alpha_src='One';assert.equal(verifyRetainedRawState(config).passed,false);
});
