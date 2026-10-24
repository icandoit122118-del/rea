import {createPicaFragment,evaluateMaterialFrame,quaternionBasisGlsl,validateFacialValue} from './pica-runtime.js';

/** Translate decoded PICA blend state without Three's NormalBlending alpha override. */
export function blendMaterialParameters(config,THREE) {
  const blend=config.blend;
  if(!blend||typeof blend.enabled!=='boolean')throw new Error('Missing decoded PICA blend state');
  if(!blend.enabled)return {transparent:false,blending:THREE.NoBlending};
  const factors={Zero:THREE.ZeroFactor,One:THREE.OneFactor,SourceColor:THREE.SrcColorFactor,
    OneMinusSourceColor:THREE.OneMinusSrcColorFactor,DestinationColor:THREE.DstColorFactor,
    OneMinusDestinationColor:THREE.OneMinusDstColorFactor,SourceAlpha:THREE.SrcAlphaFactor,
    OneMinusSourceAlpha:THREE.OneMinusSrcAlphaFactor,DestinationAlpha:THREE.DstAlphaFactor,
    OneMinusDestinationAlpha:THREE.OneMinusDstAlphaFactor,ConstantColor:THREE.ConstantColorFactor,
    OneMinusConstantColor:THREE.OneMinusConstantColorFactor,ConstantAlpha:THREE.ConstantAlphaFactor,
    OneMinusConstantAlpha:THREE.OneMinusConstantAlphaFactor,SourceAlphaSaturate:THREE.SrcAlphaSaturateFactor};
  const equations={FuncAdd:THREE.AddEquation,FuncSubtract:THREE.SubtractEquation,
    FuncReverseSubtract:THREE.ReverseSubtractEquation,Min:THREE.MinEquation,Max:THREE.MaxEquation};
  const lookup=(table,key)=>{if(!Object.hasOwn(table,blend[key]))throw new Error(`Unsupported PICA blend ${key}: ${blend[key]}`);return table[blend[key]];};
  const parameters={transparent:true,blending:THREE.CustomBlending,
    blendSrc:lookup(factors,'color_src'),blendDst:lookup(factors,'color_dst'),
    blendSrcAlpha:lookup(factors,'alpha_src'),blendDstAlpha:lookup(factors,'alpha_dst'),
    blendEquation:lookup(equations,'color_equation'),blendEquationAlpha:lookup(equations,'alpha_equation')};
  // Source replacement blending is effect-equivalent to the opaque path.
  if(blend.color_equation==='FuncAdd'&&blend.alpha_equation==='FuncAdd'&&
    blend.color_src==='One'&&blend.alpha_src==='One'&&blend.color_dst==='Zero'&&blend.alpha_dst==='Zero')
    return {transparent:false,blending:THREE.NoBlending};
  if(['color_src','color_dst','alpha_src','alpha_dst'].some(key=>/Constant/.test(blend[key]))) {
    const rgba=config.colors?.blend;
    if(!Array.isArray(rgba)||rgba.length!==4||rgba.some(value=>!Number.isFinite(value)||value<0||value>1))
      throw new Error('Constant PICA blending requires a decoded RGBA blend color');
    parameters.blendColor=new THREE.Color(rgba[0],rgba[1],rgba[2]);parameters.blendAlpha=rgba[3];
  }
  return parameters;
}

function vertexShader(bones) {
  return `precision highp float;
in vec3 position,normal,picaTangent;in vec2 uv,uv1,uv2;in vec4 picaColor;
uniform mat4 modelMatrix,viewMatrix,projectionMatrix;
out vec2 vUv0,vUv1,vUv2;out vec3 vNormal,vTangent,vPosition;out vec4 vColor,vQuat;
${quaternionBasisGlsl}
${bones ? `in vec4 skinIndex,skinWeight;uniform mat4 uBindMatrix,uBindMatrixInverse;uniform mat4 uBoneMatrices[${bones}];` : ''}
void main(){vec4 p=vec4(position,1.0);vec3 n=normal;vec3 t=picaTangent;
${bones ? `mat4 skin=uBoneMatrices[int(skinIndex.x)]*skinWeight.x+uBoneMatrices[int(skinIndex.y)]*skinWeight.y+uBoneMatrices[int(skinIndex.z)]*skinWeight.z+uBoneMatrices[int(skinIndex.w)]*skinWeight.w;mat4 bind=uBindMatrixInverse*skin*uBindMatrix;p=bind*p;n=mat3(bind)*n;t=mat3(bind)*t;` : ''}
vec4 world=modelMatrix*p;vPosition=world.xyz;vNormal=mat3(modelMatrix)*n;vTangent=mat3(modelMatrix)*t;
vUv0=uv;vUv1=uv1;vUv2=uv2;vColor=picaColor;vQuat=basisQuat(vNormal,vTangent);gl_Position=projectionMatrix*viewMatrix*world;}`;
}

function attributes(geometry,THREE) {
  const count=geometry.attributes.position.count;
  const fill=(name,width,values)=>{if(!geometry.getAttribute(name)){const data=new Float32Array(count*width);for(let i=0;i<count;i++)data.set(values,i*width);geometry.setAttribute(name,new THREE.BufferAttribute(data,width));}};
  fill('normal',3,[0,1,0]);fill('uv',2,[0,0]);
  for(const name of ['uv1','uv2'])if(!geometry.getAttribute(name))geometry.setAttribute(name,geometry.getAttribute('uv'));
  if(geometry.attributes._pica_tangent)geometry.setAttribute('picaTangent',geometry.attributes._pica_tangent);
  fill('picaTangent',3,[1,0,0]);
  if(geometry.attributes.color)geometry.setAttribute('picaColor',geometry.attributes.color);
  fill('picaColor',4,[1,1,1,1]);
}

/** Attach decoded source states. Standard glTF remains a fallback for other viewers. */
export async function attachPicaPlayer(gltf,THREE) {
  const runtime=gltf.parser.json.extras?.reaPicaRuntime;
  if(!runtime?.materials?.length)return null;
  const images=new Map();
  for(const [name,index] of Object.entries(runtime.texture_bindings)) images.set(name,await gltf.parser.getDependency('texture',index));
  const white=new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1);white.needsUpdate=true;
  const configs=new Map(runtime.materials.map(m=>[m.name,m]));
  const objects=[];
  gltf.scene.traverse(object=>{if(object.isMesh)objects.push(object);});
  const records=[];
  const min=[THREE.NearestFilter,THREE.NearestMipmapNearestFilter,THREE.NearestMipmapLinearFilter,THREE.LinearFilter,THREE.LinearMipmapNearestFilter,THREE.LinearMipmapLinearFilter];
  const wrap=[THREE.ClampToEdgeWrapping,THREE.ClampToEdgeWrapping,THREE.RepeatWrapping,THREE.MirroredRepeatWrapping];
  for(const object of objects) {
    const name=object.material.name,config=configs.get(name);
    if(!config)continue;
    attributes(object.geometry,THREE);
    const uniforms={uSceneAmbient:{value:new THREE.Vector3(.35,.35,.35)},uLightDirection:{value:new THREE.Vector3(.35,.6,1)},uLightColor:{value:new THREE.Vector3(1,1,1)},uCameraPosition:{value:new THREE.Vector3()}};
    const textures=new Map();
    for(let unit=0;unit<3;unit++) {
      uniforms[`uTexture${unit}`]={value:white};uniforms[`uUvMatrix${unit}`]={value:new THREE.Matrix3()};
      const mapper=config.texture_mappers[unit]||{};
      for(const [textureName,image] of images) {
        const texture=image.clone();texture.flipY=false;texture.colorSpace=THREE.NoColorSpace;
        texture.wrapS=wrap[mapper.wrap_u]??THREE.RepeatWrapping;texture.wrapT=wrap[mapper.wrap_v]??THREE.RepeatWrapping;
        texture.magFilter=mapper.mag_filter===0?THREE.NearestFilter:THREE.LinearFilter;
        texture.minFilter=min[mapper.min_filter]??THREE.LinearFilter;texture.generateMipmaps=true;texture.needsUpdate=true;
        textures.set(`${unit}:${textureName}`,texture);
      }
    }
    for(const key of ['Emission','Ambient','Diffuse','Specular0','Specular1',...Array.from({length:6},(_,i)=>`Constant${i}`),'BufferColor'])uniforms[`u${key}`]={value:new THREE.Vector4(1,1,1,1)};
    const bones=object.isSkinnedMesh?object.skeleton.bones.length:0;
    if(bones){uniforms.uBindMatrix={value:object.bindMatrix};uniforms.uBindMatrixInverse={value:object.bindMatrixInverse};uniforms.uBoneMatrices={value:object.skeleton.boneMatrices};}
    const material=new THREE.RawShaderMaterial({name,glslVersion:THREE.GLSL3,vertexShader:vertexShader(bones),
      fragmentShader:createPicaFragment(config,{webgl2:true,luts:runtime.luts}),uniforms,
      side:config.face_culling==='BackFace'?THREE.FrontSide:config.face_culling==='FrontFace'?THREE.BackSide:THREE.DoubleSide,
      depthTest:config.depth_color_mask.enabled,depthWrite:config.depth_color_mask.depth_write,
      ...blendMaterialParameters(config,THREE),toneMapped:false});
    const fallbackMaterial=object.material;
    object.material=material;
    object.onBeforeRender=(renderer,scene,camera)=>{
      uniforms.uCameraPosition.value.setFromMatrixPosition(camera.matrixWorld);
      if(bones){object.skeleton.update();uniforms.uBoneMatrices.value=object.skeleton.boneMatrices.subarray(0,bones*16);}
      if(typeof globalThis.__REA_PICA_DRAW_OBSERVER__==='function')globalThis.__REA_PICA_DRAW_OBSERVER__({
        mesh:object.name,material:config.name,source_node:object.userData.reaPicaNodeName,
        expected_uv:[0,1,2].map(i=>uniforms[`uUvMatrix${i}`].value.toArray()),
      });
    };
    records.push({object,config,uniforms,textures,material,fallbackMaterial});
  }
  const facialValues={};
  const update=(clipIndex,seconds)=>{
    const state=evaluateMaterialFrame(runtime,clipIndex,seconds*runtime.source_fps,facialValues);
    for(const record of records){const {object,config,uniforms,textures}=record,current=state.material_states[config.name];
      const nodeName=object.userData.reaPicaNodeName;
      object.visible=state.model_visible && (state.visibility[nodeName]??true);
      for(let i=0;i<3;i++){uniforms[`uUvMatrix${i}`].value.fromArray(current.uv_matrices[i]);uniforms[`uTexture${i}`].value=textures.get(`${i}:${current.texture_names[i]}`)||white;}
      for(const [key,values] of Object.entries(current.colors||{})){const uniform=uniforms[`u${key[0].toUpperCase()+key.slice(1)}`];if(uniform)uniform.value.fromArray(values);}
      if(current.colors?.blend&&record.material.blending===THREE.CustomBlending){
        record.material.blendColor.setRGB(...current.colors.blend.slice(0,3));record.material.blendAlpha=current.colors.blend[3];
      }
      uniforms.uBufferColor.value.fromArray(config.texenv_buffer_color||[0,0,0,0]);
    }
    return state;
  };
  let renderMode='source';
  return {runtime,update,materialCount:records.length,
    setFacialValues:values=>{
      if(!values||typeof values!=='object'||Array.isArray(values))throw new Error('Facial values must be an object');
      for(const [key,value] of Object.entries(values)){
        const channel=Number(key);if(String(channel)!==key)throw new Error('Facial channel keys must be canonical integers');
        validateFacialValue(runtime,channel,value);
      }
      for(const [key,value] of Object.entries(values))facialValues[key]=value;
    },
    inspectFacial:()=>({values:{...facialValues},groups:(runtime.facial_animation?.groups||[]).map(group=>({channel:group.channel,source_end_frame:group.source_end_frame,frame_multiplier:group.frame_multiplier,source_entries:group.source_entries})),manual_source_selection:true,automatic_blink_enabled:false,original_runtime_trigger_verified:false,runtime_masks_6_7_emulated:false}),
    setRenderMode:mode=>{
      if(!['source','fallback'].includes(mode))throw new Error('Unknown material mode');
      renderMode=mode;for(const record of records)record.object.material=mode==='source'?record.material:record.fallbackMaterial;
    },
    inspect:()=>({mode:renderMode,meshes:records.map(({object,config,uniforms})=>({
      name:object.name,source_node:object.userData.reaPicaNodeName,material:config.name,visible:object.visible,
      source_shader_active:object.material.isRawShaderMaterial===true,
      uv_matrices:[0,1,2].map(i=>uniforms[`uUvMatrix${i}`].value.toArray()),
      colors:Object.fromEntries(Object.entries(uniforms).filter(([key,value])=>value.value?.isVector4).map(([key,value])=>[key,value.value.toArray()])),
      bone_matrix_uniform_length:uniforms.uBoneMatrices?.value?.length??0,
    }))}),
  };
}
