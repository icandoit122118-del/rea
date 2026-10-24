import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,lstat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {parseCaptureArgs,validateCaptureRequest,capturePlayerFrames,OUTPUT_ROOT} from '../scripts/capture-player-frames.mjs';

const oracle={document:{animations:[{name:'primary'},{name:'aux'}],extras:{reaPicaRuntime:{source_fps:30,clips:[{role:'primary'},{role:'auxiliary_pose',auxiliary_pose:{source_frame_count:71,channel_mask:[{bone:'Jaw',path:'rotation'}]}}]}}},duration:()=>70/30};
const valid={clipIndex:0,frames:[0,10,70],width:400,height:240};

test('source-frame requests allow exact endpoint and refuse invalid, duplicated or auxiliary requests before browser launch',()=>{
  assert.deepEqual(validateCaptureRequest(valid,oracle).crop,[0,0,400,240]);
  assert.deepEqual(validateCaptureRequest({...valid,crop:[10,20,300,180]},oracle).crop,[10,20,300,180]);
  for(const frames of [[],[0,0],[71],[-1],[.5],Array.from({length:65},(_,i)=>i)])assert.throws(()=>validateCaptureRequest({...valid,frames},oracle),/frame|bounds/);
  assert.throws(()=>validateCaptureRequest({...valid,clipIndex:1},oracle),/primary/);
  assert.throws(()=>validateCaptureRequest({...valid,clipIndex:5},oracle),/index/);
  assert.throws(()=>validateCaptureRequest({...valid,width:4096},oracle),/16..2048/);
  assert.throws(()=>validateCaptureRequest({...valid,crop:[390,0,20,240]},oracle),/crop/);
});

test('explicit manual face capture requires both bounded arguments and preserves fixed-source semantics',()=>{
  assert.equal(validateCaptureRequest(valid,oracle).face,null);
  assert.deepEqual(validateCaptureRequest({...valid,auxiliaryIndex:1,faceValue:7},oracle).face,
    {auxiliaryIndex:1,value:7,sourceFrame:70,sourceTimeSeconds:70/30,manualSourcePose:true,originalRuntimeTriggerVerified:false});
  for(const spec of [{auxiliaryIndex:1},{faceValue:3},{auxiliaryIndex:0,faceValue:3},{auxiliaryIndex:1.5,faceValue:3},{auxiliaryIndex:1,faceValue:8},{auxiliaryIndex:1,faceValue:NaN},{auxiliaryIndex:1,faceValue:-1}])assert.throws(()=>validateCaptureRequest({...valid,...spec},oracle),/auxiliary|manual face/);
  const badCount=structuredClone(oracle.document);badCount.extras.reaPicaRuntime.clips[1].auxiliary_pose.source_frame_count=70;
  assert.throws(()=>validateCaptureRequest({...valid,auxiliaryIndex:1,faceValue:7},{...oracle,document:badCount}),/source bounds/);
  assert.throws(()=>validateCaptureRequest({...valid,auxiliaryIndex:1,faceValue:7},{...oracle,duration:index=>index===1?2:70/30}),/source bounds/);
  assert.deepEqual(parseCaptureArgs(['--auxiliary-index','9','--face-value','3']),{width:400,height:240,auxiliaryIndex:9,faceValue:3});
});

test('PF source values are explicit, bounded and require a source-bearing runtime',()=>{
  const document=structuredClone(oracle.document);document.extras.reaPicaRuntime.facial_animation={groups:[{channel:0,frame_multiplier:10,source_end_frame:80},{channel:3,frame_multiplier:10,source_end_frame:70}]};
  const source={...oracle,document};
  assert.deepEqual(validateCaptureRequest({...valid,facialValues:{'0':2,'3':3}},source).facialValues,{'0':2,'3':3});
  for(const facialValues of [null,[],{'0':9},{'3':8},{'1':0},{'00':2},{'0':1.2}])assert.throws(()=>validateCaptureRequest({...valid,facialValues},source),/Facial|source facial/);
  assert.throws(()=>validateCaptureRequest({...valid,facialValues:{'0':2}},oracle),/Unknown source facial/);
  assert.deepEqual(parseCaptureArgs(['--facial-values','{"0":2}']).facialValues,{'0':2});
});

test('CLI requires explicit values, rejects duplicates and parses bounded camera/crop inputs without starting a browser',()=>{
  const parsed=parseCaptureArgs(['--input','C:/a.glb','--clip-index','0','--frames','[0,10]','--output-dir','D:/new','--width','400','--height','240','--crop','[0,0,100,100]','--view-config','D:/view.json']);
  assert.equal(parsed.authorized,undefined);assert.equal(parsed.clipIndex,0);assert.deepEqual(parsed.frames,[0,10]);assert.equal(parsed.viewConfig,'D:/view.json');
  assert.throws(()=>parseCaptureArgs(['--frames','[0]','--frames','[1]']),/Duplicate/);
  assert.throws(()=>parseCaptureArgs(['--frames','no']),/JSON/);
  assert.throws(()=>parseCaptureArgs(['--input']),/Missing/);
  assert.throws(()=>parseCaptureArgs(['--camera','any']),/Unsupported/);
});

test('authorization and output boundary failures leave caller files untouched and never launch Chromium',async t=>{
  const directory=await mkdtemp(path.join(tmpdir(),'rea3d-frame-guard-'));
  t.after(async()=>{const checked=path.resolve(directory);assert.equal(path.dirname(checked).toLowerCase(),path.resolve(tmpdir()).toLowerCase());assert.ok(path.basename(checked).startsWith('rea3d-frame-guard-'));assert.equal((await lstat(checked)).isSymbolicLink(),false);await rm(checked,{recursive:true,force:true});});
  const marker=path.join(directory,'preserve.txt');await writeFile(marker,'preserve');
  await assert.rejects(capturePlayerFrames({input:'C:/a.glb',output:directory}),/authorization/);
  await assert.rejects(capturePlayerFrames({authorized:true,input:'C:/a.glb',output:directory}),/dedicated frame-comparison/);
  await assert.rejects(capturePlayerFrames({authorized:true,input:'relative.glb',output:OUTPUT_ROOT}),/Absolute/);
  assert.equal(await readFile(marker,'utf8'),'preserve');
});
