import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {blendMaterialParameters} from '../preview/pica-player.js';

const iris={enabled:true,color_equation:'FuncAdd',alpha_equation:'FuncAdd',color_src:'SourceAlpha',color_dst:'OneMinusSourceAlpha',alpha_src:'SourceAlpha',alpha_dst:'OneMinusSourceAlpha'};

test('source alpha factors are explicit for RGB and alpha, unlike NormalBlending',()=>{
  const parameters=blendMaterialParameters({name:'arbitrary material',blend:iris},THREE);
  const material=new THREE.RawShaderMaterial(parameters);
  assert.equal(material.transparent,true);assert.equal(material.blending,THREE.CustomBlending);
  assert.equal(material.blendSrc,THREE.SrcAlphaFactor);assert.equal(material.blendDst,THREE.OneMinusSrcAlphaFactor);
  assert.equal(material.blendSrcAlpha,THREE.SrcAlphaFactor);assert.equal(material.blendDstAlpha,THREE.OneMinusSrcAlphaFactor);
  assert.equal(material.blendEquation,THREE.AddEquation);assert.equal(material.blendEquationAlpha,THREE.AddEquation);
  const sourceAlpha=.5,destinationAlpha=.8;
  assert.equal(sourceAlpha*sourceAlpha+destinationAlpha*(1-sourceAlpha),.65);
});
test('disabled and source One/Zero replacement blending preserve opaque classification',()=>{
  assert.deepEqual(blendMaterialParameters({blend:{enabled:false}},THREE),{transparent:false,blending:THREE.NoBlending});
  assert.deepEqual(blendMaterialParameters({blend:{...iris,color_src:'One',alpha_src:'One',color_dst:'Zero',alpha_dst:'Zero'}},THREE),{transparent:false,blending:THREE.NoBlending});
});
test('separate source color and alpha equations/factors remain separate',()=>{
  const parameters=blendMaterialParameters({blend:{...iris,color_equation:'FuncSubtract',alpha_equation:'Max',alpha_src:'One',alpha_dst:'Zero'}},THREE);
  assert.equal(parameters.blendEquation,THREE.SubtractEquation);assert.equal(parameters.blendEquationAlpha,THREE.MaxEquation);
  assert.equal(parameters.blendSrcAlpha,THREE.OneFactor);assert.equal(parameters.blendDstAlpha,THREE.ZeroFactor);
});
test('constant blending requires the decoded color rather than an invented default',()=>{
  const config={blend:{...iris,color_src:'ConstantColor',alpha_src:'ConstantAlpha'}};
  assert.throws(()=>blendMaterialParameters(config,THREE),/decoded RGBA/);
  const parameters=blendMaterialParameters({...config,colors:{blend:[.2,.4,.6,.8]}},THREE);
  assert.equal(parameters.blendSrc,THREE.ConstantColorFactor);assert.equal(parameters.blendSrcAlpha,THREE.ConstantAlphaFactor);
  assert.deepEqual(parameters.blendColor.toArray(),[.2,.4,.6]);assert.equal(parameters.blendAlpha,.8);
});
test('unknown enabled blend fields fail explicitly instead of rendering without blending',()=>{
  assert.throws(()=>blendMaterialParameters({},THREE),/Missing decoded/);
  assert.throws(()=>blendMaterialParameters({blend:{...iris,alpha_src:'Unknown'}},THREE),/alpha_src/);
  assert.throws(()=>blendMaterialParameters({blend:{...iris,alpha_src:'constructor'}},THREE),/alpha_src/);
  assert.throws(()=>blendMaterialParameters({blend:{...iris,color_equation:'Unknown'}},THREE),/color_equation/);
});
