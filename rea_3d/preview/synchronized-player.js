// Primary skeleton/material/visibility use action time; auxiliary poses use face_value * 10.
// Auxiliary masks come from verified source metadata, never from clip names.
export function createSynchronizedPlayer({gltf,THREE,picaPlayer}) {
  const clips=gltf.animations||[],runtime=picaPlayer?.runtime;
  const mixer=new THREE.AnimationMixer(gltf.scene);
  let action,clipIndex=-1,auxiliaryIndex=null,playing=false,speed=1,lastState=null;
  let overrides=[],restore=[],faceValue=0,auxiliaryTime=null,auxiliaryFrame=null;
  // Float32 GLB timestamps can round the exact source endpoint by a few ulps.
  const timeEpsilon=1e-6;
  const faceTime=(index,value,tracks)=>{
    const metadata=runtime.clips[index].auxiliary_pose,frame=value*10,fps=runtime.source_fps;
    if(!Number.isInteger(metadata.source_frame_count)||metadata.source_frame_count<1||!Number.isFinite(fps)||fps<=0)
      throw new Error('Auxiliary source frame bounds and source fps are required');
    if(frame>=metadata.source_frame_count)throw new Error(`Face source frame ${frame} is outside the verified source bounds`);
    const time=frame/fps,duration=clips[index].duration;
    if(!Number.isFinite(duration)||duration<0||time>duration+timeEpsilon)
      throw new Error(`Face source frame ${frame} is outside the skeletal clip bounds`);
    for(const {track} of tracks){
      const start=track.times[0],end=track.times[track.times.length-1];
      if(!Number.isFinite(start)||!Number.isFinite(end)||start>time+timeEpsilon||end<time-timeEpsilon)
        throw new Error(`Face source frame ${frame} is outside an auxiliary track's bounds`);
    }
    return {frame,time};
  };
  const restoreBase=()=>{for(const item of restore)item.object[item.property].fromArray(item.values);restore=[];};
  const sample=()=>{
    if(!action)return null;
    const time=action.time;
    for(const item of overrides){
      restore.push({object:item.object,property:item.property,values:item.object[item.property].toArray()});
      item.object[item.property].fromArray(item.interpolant.evaluate(auxiliaryTime));
      if(item.property==='quaternion')item.object.quaternion.normalize();
    }
    lastState=picaPlayer?.update(clipIndex,time)??null;
    gltf.scene.updateMatrixWorld(true);
    return lastState;
  };
  const selectClip=index=>{
    if(!Number.isInteger(index)||!clips[index])throw new Error('Unknown skeletal clip');
    if(runtime?.clips[index]?.role==='auxiliary_pose')throw new Error('Select auxiliary poses as a layer over a primary clip');
    restoreBase();mixer.stopAllAction();clipIndex=index;
    action=mixer.clipAction(clips[index]);action.reset().play();action.paused=!playing;
    mixer.update(0);sample();
  };
  const selectAuxiliary=index=>{
    if(index===null){restoreBase();overrides=[];auxiliaryIndex=null;auxiliaryTime=null;auxiliaryFrame=null;mixer.update(0);return sample();}
    if(!Number.isInteger(index))throw new Error('Unknown auxiliary clip');
    const binding=runtime?.clips[index],metadata=binding?.auxiliary_pose;
    if(binding?.role!=='auxiliary_pose'||!metadata?.channel_mask?.length)throw new Error('Auxiliary source mask is not verified');
    const clip=clips[index];if(!clip)throw new Error('Auxiliary skeletal clip is missing');
    const next=[];
    for(const target of metadata.channel_mask){
      const property={rotation:'quaternion',translation:'position',scale:'scale'}[target.path];
      if(!property)throw new Error('Unknown auxiliary source channel');
      const tracks=clip.tracks.filter(track=>{const b=THREE.PropertyBinding.parseTrackName(track.name);return b.nodeName===target.bone&&b.propertyName===property;});
      const object=gltf.scene.getObjectByName(target.bone);
      if(tracks.length!==1||!object?.[property])throw new Error(`Auxiliary mask did not bind: ${target.bone}.${property}`);
      if(next.some(item=>item.object===object&&item.property===property))throw new Error('Duplicate auxiliary source channel');
      next.push({object,property,interpolant:tracks[0].createInterpolant(),track:tracks[0]});
    }
    const fixed=faceTime(index,faceValue,next);
    restoreBase();overrides=next;auxiliaryIndex=index;auxiliaryTime=fixed.time;auxiliaryFrame=fixed.frame;
    mixer.update(0);return sample();
  };
  const setFaceValue=value=>{
    if(value===-1)return selectAuxiliary(null);
    if(!Number.isInteger(value)||value<0||value>7)throw new Error('Face value must be an integer from 0 to 7, or -1 to detach');
    const fixed=auxiliaryIndex===null?null:faceTime(auxiliaryIndex,value,overrides);
    restoreBase();faceValue=value;
    if(fixed){auxiliaryTime=fixed.time;auxiliaryFrame=fixed.frame;}
    mixer.update(0);return sample();
  };
  const seek=seconds=>{
    if(!action||!Number.isFinite(seconds))throw new Error('Select a clip and a finite playback time');
    restoreBase();action.time=Math.max(0,Math.min(clips[clipIndex].duration,seconds));mixer.update(0);return sample();
  };
  const advance=delta=>{
    if(!Number.isFinite(delta)||delta<0)throw new Error('Playback delta must be nonnegative');
    restoreBase();mixer.update(playing?delta:0);return sample();
  };
  const setPlaying=value=>{playing=Boolean(value);if(action)action.paused=!playing;};
  return {clips,runtime,selectClip,selectAuxiliary,setFaceValue,seek,advance,
    getAuxiliarySourceFrame:()=>auxiliaryFrame,
    play:()=>setPlaying(true),pause:()=>setPlaying(false),stop:()=>{setPlaying(false);if(action)seek(0);},
    setSpeed:value=>{if(!Number.isFinite(value)||value<.25||value>2)throw new Error('Playback speed outside .25–2');speed=value;mixer.timeScale=value;},
    snapshot:()=>({clip_index:clipIndex,time_seconds:action?.time??0,frame:(action?.time??0)*(runtime?.source_fps??30),
      playing,speed,auxiliary_index:auxiliaryIndex,
      face_value:auxiliaryIndex===null?-1:faceValue,configured_face_value:faceValue,
      auxiliary_source_frame:auxiliaryFrame,auxiliary_time_seconds:auxiliaryTime,
      auxiliary_mode:auxiliaryIndex===null?'detached':'fixed_source_pose',
      auxiliary_control_evidence:{scope:'selected_ORAS_PB25_PK33',native_static_mapping:'face_value * 10 source frames; channel 3 selects PB25/PK33',
        original_runtime_trigger_verified:false,manual_source_pose_selection:true,
        raw_lua_command_emulation:false,face_value_0_is_attached_source_pose:true,
        original_face_6_7_may_remap_to_0:true},
      auxiliary_mask:auxiliaryIndex===null?[]:runtime.clips[auxiliaryIndex].auxiliary_pose.channel_mask,
      duration:clips[clipIndex]?.duration??0,visual_state:lastState}),
  };
}
