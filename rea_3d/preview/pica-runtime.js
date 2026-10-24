// Source-driven PICA material/animation playback. SPICA reference commit is in PROVENANCE.md.
// This module is shared by the browser player, CPU tests and GPU render-plan producer.
export function sampleCurve(curve, frame, fallback = 0) {
  if (!curve) return fallback;
  if (curve.kind === 'boolean') {
    const values = curve.values || [];
    return values.length ? Boolean(values[Math.max(0, Math.min(values.length - 1, Math.floor(frame - (curve.start_frame || 0))))]) : fallback;
  }
  const keys = curve.keys || [];
  if (!keys.length) return fallback;
  if (frame <= keys[0].frame) return keys[0].value;
  if (frame >= keys.at(-1).frame) return keys.at(-1).value;
  let lo = 0, hi = keys.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (keys[mid].frame <= frame) lo = mid; else hi = mid; }
  const a = keys[lo], b = keys[hi], span = b.frame - a.frame, t = (frame - a.frame) / span;
  if (curve.interpolation === 'step') return a.value;
  if (curve.interpolation === 'hermite') {
    return (2*t*t*t - 3*t*t + 1)*a.value + (t*t*t - 2*t*t + t)*span*(a.out_slope || 0)
      + (-2*t*t*t + 3*t*t)*b.value + (t*t*t - t*t)*span*(b.in_slope || 0);
  }
  return a.value + (b.value - a.value)*t;
}

export function textureMatrix(coord = {}) {
  const [sx, sy] = coord.scale || [1, 1], [tx, ty] = coord.translation || [0, 0];
  const r = coord.rotation || 0, c = Math.cos(r), s = Math.sin(r);
  let x, y;
  switch (coord.transform_type || 0) {
    case 0: x = sx*((.5*s - .5*c) + .5 - tx); y = sy*((-.5*s - .5*c) + .5 - ty); break;
    case 1: x = sx*(-c*tx - s*ty); y = sy*(s*tx - c*ty); break;
    case 2: x = sx*c*(-tx-.5) - sx*s*(ty-.5) + .5; y = sy*s*(-tx-.5) + sy*c*(ty-.5) + .5; break;
    default: throw new Error(`Unsupported texture transform type ${coord.transform_type}`);
  }
  // SPICA's row-vector Matrix3x4 expressed as a GLSL column-major mat3.
  return [sx*c, sy*s, 0, -sx*s, sy*c, 0, x, y, 1];
}

const colorTargets = {1:'constant0',2:'constant1',3:'constant2',4:'constant3',5:'constant4',6:'constant5',7:'emission',8:'ambient',9:'diffuse',10:'specular0',11:'specular1',18:'blend'};
// The optional PF bank is a source-extracted fixed-pose layer. Its value is not
// inferred from a primary animation name or advanced by the primary clock.
export function validateFacialValue(runtime, channel, value) {
  const groups=runtime.facial_animation?.groups||[];
  const group=groups.find(group=>group.channel===channel);
  if(!Number.isInteger(channel)||!group)throw new Error('Unknown source facial channel');
  if(group.frame_multiplier!==10||!Number.isFinite(group.source_end_frame)||group.source_end_frame<0)throw new Error('Invalid source facial frame bounds');
  // This is a bounded source-inspection range. It does not claim these values
  // are all selected by original gameplay, nor emulate the runtime 6/7 masks.
  if(!Number.isInteger(value)||value < -1||value>Math.floor(group.source_end_frame/group.frame_multiplier))throw new Error('Facial value is outside the bounded source-inspection range');
  return group;
}

export function evaluateMaterialFrame(runtime, clipIndex, frame, facialValues = {}) {
  const states = Object.fromEntries((runtime.materials || []).map(config => [config.name, {
    ...config, texture_names:[...(config.texture_names || [])],
    tex_coords:(config.tex_coords || []).map(c => ({...c,scale:[...(c.scale || [1,1])],translation:[...(c.translation || [0,0])]})),
    colors:Object.fromEntries(Object.entries(config.colors || {}).map(([key, value]) => [key,[...value]])),
  }]));
  const visibility = Object.fromEntries((runtime.mesh_nodes || []).map(n => [n.name, n.default_visible !== false]));
  let modelVisible = true;
  const binding = (runtime.clips || [])[clipIndex];
  const overlays=[];
  const apply=(animations,sourceFrame)=>{
   for (const animation of animations) {
    const f = Math.max(0, Math.min(sourceFrame, animation.end_frame ?? sourceFrame));
    for (const element of animation.elements || []) {
      const id = element.target_id;
      if (id === 47) { visibility[element.name] = sampleCurve(element.curves?.[0], f, true); continue; }
      if (id === 48) { modelVisible = sampleCurve(element.curves?.[0], f, true); continue; }
      const state = states[element.name];
      if (!state) continue;
      if (id >= 19 && id <= 27) {
        const unit = Math.floor((id - 19)/3), kind = (id - 19)%3;
        state.tex_coords ??= [];
        const coord = state.tex_coords[unit] ??= {scale:[1,1],rotation:0,translation:[0,0],transform_type:0};
        if (kind === 1) coord.rotation = sampleCurve(element.curves?.[0], f, coord.rotation || 0);
        else {
          const prop = kind === 0 ? 'scale' : 'translation';
          coord[prop] = [0,1].map(i => sampleCurve(element.curves?.[i], f, coord[prop]?.[i] ?? (kind === 0 ? 1 : 0)));
        }
      } else if ([13,15,17].includes(id)) {
        const unit = (id - 13)/2, value = Math.trunc(sampleCurve(element.curves?.[0], f, 0));
        const name = animation.texture_names?.[value];
        if (name) state.texture_names[unit] = name;
      } else if (colorTargets[id]) {
        state.colors ??= {};
        const key = colorTargets[id], prior = state.colors[key] || [1,1,1,1];
        state.colors[key] = [0,1,2,3].map(i => sampleCurve(element.curves?.[i], f, prior[i]));
      }
    }
   }
  };
  apply([...(binding?.material_animations || []), ...(binding?.visibility_animations || [])],frame);
  for(const [key,value] of Object.entries(facialValues)){
    const channel=Number(key),group=validateFacialValue(runtime,channel,value);
    if(String(channel)!==key)throw new Error('Facial channel keys must be canonical integers');
    if(value===-1)continue;
    const sourceFrame=Math.min(group.source_end_frame,value*group.frame_multiplier);
    apply([...(group.material_animations||[]),...(group.visibility_animations||[])],sourceFrame);
    overlays.push({channel,value,source_frame:sourceFrame,source_entries:group.source_entries??[],manual_source_selection:true,original_runtime_trigger_verified:false});
  }
  for (const state of Object.values(states)) state.uv_matrices = [0,1,2].map(i => textureMatrix(state.tex_coords?.[i]));
  return {material_states:states, visibility, model_visible:modelVisible, clip_name:binding?.source_name || binding?.name || null, frame,facial_overlays:overlays};
}

const sourceCodes = {PrimaryColor:0,FragmentPrimaryColor:1,FragmentSecondaryColor:2,Texture0:3,Texture1:4,Texture2:5,PreviousBuffer:13,Constant:14,Previous:15};
const colorOps = {Color:0,OneMinusColor:1,Alpha:2,OneMinusAlpha:3,Red:4,OneMinusRed:5,Green:8,OneMinusGreen:9,Blue:12,OneMinusBlue:13};
const alphaOps = {Alpha:0,OneMinusAlpha:1,Red:2,OneMinusRed:3,Green:4,OneMinusGreen:5,Blue:6,OneMinusBlue:7};
const combineCodes = {Replace:0,Modulate:1,Add:2,AddSigned:3,Interpolate:4,Subtract:5,DotProduct3Rgb:6,DotProduct3Rgba:7,MultAdd:8,AddMult:9};
function code(value, dictionary) { if (typeof value === 'number') return value; if (value in dictionary) return dictionary[value]; throw new Error(`Unsupported PICA enum ${value}`); }
function sourceExpression(value, constantIndex) {
  const names = {0:'vColor',1:'fragPrimary',2:'fragSecondary',3:'tex0',4:'tex1',5:'tex2',13:'combBuffer',14:`uConstant${constantIndex}`,15:'previous'};
  const result = names[code(value, sourceCodes)];
  if (!result) throw new Error(`Unsupported PICA combiner source ${value}`);
  return result;
}
function operandExpression(source, value, alpha) {
  const op = code(value, alpha ? alphaOps : colorOps), base = op & ~1;
  const swizzle = alpha ? {0:'a',2:'r',4:'g',6:'b'}[base] : {0:'rgb',2:'aaa',4:'rrr',8:'ggg',12:'bbb'}[base];
  if (!swizzle) throw new Error(`Unsupported PICA operand ${op}`);
  const expression = `${source}.${swizzle}`;
  return op & 1 ? `(${alpha ? '1.0' : 'vec3(1.0)'} - ${expression})` : expression;
}
function combineExpression(mode, args, alpha = false) {
  const [a,b,c] = args, n = code(mode, combineCodes), zero = alpha ? '0.0':'vec3(0.0)', one = alpha ? '1.0':'vec3(1.0)';
  if (n === 0) return a;
  if (n === 1) return `(${a}*${b})`;
  if (n === 2) return `min(${a}+${b},${one})`;
  if (n === 3) return `clamp(${a}+${b}-${alpha ? '.5':'vec3(.5)'},${zero},${one})`;
  if (n === 4) return `mix(${b},${a},${c})`;
  if (n === 5) return `max(${a}-${b},${zero})`;
  if (n === 6 || n === 7) return alpha ? `min(${n===6?'3.0':'4.0'}*${a}*${b},1.0)` : `vec3(min(dot(${a},${b}),1.0))`;
  if (n === 8) return `min(${a}*${b}+${c},${one})`;
  if (n === 9) return `(min(${a}+${b},${one})*${c})`;
  throw new Error(`Unsupported PICA combiner mode ${mode}`);
}

export const quaternionBasisGlsl = `vec4 basisQuat(vec3 normal,vec3 tangent){
vec3 n=normalize(normal);vec3 b=normalize(cross(n,tangent));vec3 t=cross(b,n);mat3 m=mat3(t,b,n);float tr=m[0][0]+m[1][1]+m[2][2];vec4 q;
if(1.0+tr>0.00390625){q=vec4(m[1][2]-m[2][1],m[2][0]-m[0][2],m[0][1]-m[1][0],1.0+tr);}
else if(m[0][0]>m[1][1]&&m[0][0]>m[2][2]){q=vec4(1.0+m[0][0]-m[1][1]-m[2][2],m[1][0]+m[0][1],m[2][0]+m[0][2],m[1][2]-m[2][1]);}
else if(m[1][1]>m[2][2]){q=vec4(m[1][0]+m[0][1],1.0+m[1][1]-m[0][0]-m[2][2],m[2][1]+m[1][2],m[2][0]-m[0][2]);}
else{q=vec4(m[2][0]+m[0][2],m[2][1]+m[1][2],1.0+m[2][2]-m[0][0]-m[1][1],m[0][1]-m[1][0]);}return normalize(q);}`;

export const desktopVertexShader = `#version 330
in vec3 in_position; in vec3 in_normal; in vec3 in_tangent;
in vec2 in_uv0; in vec2 in_uv1; in vec2 in_uv2; in vec4 in_color;
uniform mat4 uMvp;
out vec2 vUv0; out vec2 vUv1; out vec2 vUv2;
out vec3 vNormal; out vec3 vTangent; out vec3 vPosition; out vec4 vColor;out vec4 vQuat;
${quaternionBasisGlsl}
void main(){vUv0=in_uv0;vUv1=in_uv1;vUv2=in_uv2;vNormal=in_normal;vTangent=in_tangent;vPosition=in_position;vColor=in_color;vQuat=basisQuat(in_normal,in_tangent);gl_Position=uMvp*vec4(in_position,1.0);}`;

function lutFunction(config, name, luts) {
  const ref = config.lut_references?.[name];
  if (!ref?.sampler) return `float lut_${name}(float x){return 1.0;}`;
  const table = luts.find(t => t.name === ref.table)?.samplers?.find(s => s.name === ref.sampler);
  if (!table || table.decoded?.length !== 256 || table.decoded.some(v => !Number.isFinite(v))) throw new Error(`Missing source LUT ${name}/${ref.sampler}`);
  const values = table.decoded.map(n => Number(n).toFixed(9)).join(',');
  const index = table.absolute ? 'max(i-256,0)' : '(i<256 ? i/2+128 : (i-256)/2)';
  return `float lut_${name}_texel(int i){const float data[256]=float[256](${values}); i=clamp(i,0,511);return data[${index}];}
float lut_${name}(float x){float p=clamp((x+1.0)*.5,0.0,1.0)*512.0-.5;int i=int(floor(p));return mix(lut_${name}_texel(i),lut_${name}_texel(i+1),fract(p));}`;
}

export function createPicaFragment(config, {desktop = false, webgl2 = false, luts = []} = {}) {
  const modern = desktop || webgl2;
  const lighting = config.lighting || {};
  const lutKeys = ['dist0','dist1','fresnel','reflect_r','reflect_g','reflect_b'];
  const lutInputs = ['dot(n,h)','dot(eye,h)','dot(n,eye)','dot(n,l)','dot(l,-l)','dot(h-n*dot(n,h),t)'];
  const lut = key => `min(lut_${key}(${lutInputs[config.lut_config?.[key]?.input_code ?? 0]})*${Number(config.lut_config?.[key]?.scale ?? 1).toFixed(5)},1.0)`;
  const sources = config.texture_sources || [0,0,0];
  const coords = [0,1,2].map(i => `vUv${Math.max(0,Math.min(2,Math.trunc(sources[i] ?? 0)))}`);
  if ([1,2,3].includes(config.tex_coord_config)) coords[2] = coords[1];
  const uniforms = ['Emission','Ambient','Diffuse','Specular0','Specular1',...Array.from({length:6},(_,i)=>`Constant${i}`),'BufferColor'];
  const lines = [desktop ? '#version 330' : 'precision highp float;',
    'uniform sampler2D uTexture0,uTexture1,uTexture2;',
    'uniform mat3 uUvMatrix0,uUvMatrix1,uUvMatrix2;',
    ...uniforms.map(n=>`uniform vec4 u${n};`),
    'uniform vec3 uSceneAmbient,uLightDirection,uLightColor,uCameraPosition;',
    `${modern?'in':'varying'} vec2 vUv0,vUv1,vUv2;`, `${modern?'in':'varying'} vec3 vNormal,vTangent,vPosition;`, `${modern?'in':'varying'} vec4 vColor,vQuat;`,
    ...(modern ? ['out vec4 fragColor;'] : []),
    ...lutKeys.map(key => lutFunction(config,key,luts)),
    // Undo the GLB V conversion, apply the original source transform, then return
    // to the GLB/unchanged-PNG convention used by both tested renderers.
    'vec2 mapUv(vec2 uv,mat3 m){vec3 p=m*vec3(uv.x,1.0-uv.y,1.0);return vec2(p.x,1.0-p.y);}',
    'vec3 quatRotate(vec4 q,vec3 v){return v+2.0*cross(q.xyz,cross(q.xyz,v)+q.w*v);}',
    'void main(){',
    ...[0,1,2].flatMap(i=>{
      const mapper=config.texture_mappers?.[i]||{},m=(i===2&&[1,2,3].includes(config.tex_coord_config))?1:i;
      return [`vec2 tc${i}=mapUv(${coords[i]},uUvMatrix${m});`,
        `vec4 tex${i}=${modern?'texture':'texture2D'}(uTexture${i},tc${i});`];
    }),
    'vec4 nq=normalize(vQuat);vec3 n=quatRotate(nq,vec3(0,0,1));vec3 t=quatRotate(nq,vec3(1,0,0));',
    ...([1,2].includes(lighting.bump_mode) ? [
      `vec3 bump=tex${lighting.bump_texture ?? 2}.rgb*2.0-1.0;`,
      ...(lighting.bump_renormalize ? ['bump.z=sqrt(max(1.0-dot(bump.xy,bump.xy),0.0));'] : []),
      lighting.bump_mode === 1 ? 'n=quatRotate(nq,bump);' : 't=quatRotate(nq,bump);',
    ] : []),
    'vec3 l=normalize(uLightDirection);vec3 eye=normalize(uCameraPosition-vPosition);vec3 h=normalize(eye+l);',
    'float ndl=max(dot(n,l),0.0);',
    'vec4 fragPrimary=vec4(clamp(uEmission.rgb+uAmbient.rgb*uSceneAmbient+uDiffuse.rgb*uLightColor*ndl,0.0,1.0),1.0);',
    `vec3 spec0=uSpecular0.rgb${lighting.lut_dist0_enabled ? `*${lut('dist0')}`:''};`,
    `vec3 spec1=${lighting.lut_reflection_enabled ? `vec3(${lut('reflect_r')},${lut('reflect_g')},${lut('reflect_b')})`:'uSpecular1.rgb'}${lighting.lut_dist1_enabled ? `*${lut('dist1')}`:''};`,
    ...(lighting.geometry_factor0_enabled ? ['spec0*=ndl;'] : []),
    ...(lighting.geometry_factor1_enabled ? ['spec1*=ndl;'] : []),
    `vec4 fragSecondary=vec4(clamp((spec0+spec1)*uLightColor${lighting.clamp_highlight ? '*step(0.0,dot(n,l))':''},0.0,1.0),1.0);`,
    ...((lighting.fresnel_selector & 1) ? [`fragPrimary.a=${lut('fresnel')};`] : []),
    ...((lighting.fresnel_selector & 2) ? [`fragSecondary.a=${lut('fresnel')};`] : []),
    'vec4 previous=vec4(1.0);vec4 outputColor=previous;vec4 combBuffer=uBufferColor;'];
  for (const [i, stage] of (config.texenv_stages || []).entries()) {
    const constant = stage.constant_index ?? i;
    if (!Number.isInteger(constant) || constant < 0 || constant > 5) throw new Error('Invalid PICA constant assignment');
    const color = (stage.color_sources || []).map((v,j)=>operandExpression(sourceExpression(v,constant),stage.color_operands[j],false));
    const alpha = (stage.alpha_sources || []).map((v,j)=>operandExpression(sourceExpression(v,constant),stage.alpha_operands[j],true));
    if (color.length !== 3 || alpha.length !== 3) throw new Error('PICA stage must contain three operands');
    lines.push(`// decoded PICA texture combiner stage ${i}`);
    lines.push(`outputColor.rgb=clamp(${combineExpression(stage.color_combiner,color)}*${Number(stage.color_scale ?? 1).toFixed(1)},0.0,1.0);`);
    lines.push(`outputColor.a=clamp(${combineExpression(stage.alpha_combiner,alpha,true)}*${Number(stage.alpha_scale ?? 1).toFixed(1)},0.0,1.0);`);
    if (stage.update_color_buffer) lines.push('combBuffer.rgb=previous.rgb;');
    if (stage.update_alpha_buffer) lines.push('combBuffer.a=previous.a;');
    lines.push('previous=outputColor;');
  }
  if (!(config.texenv_stages || []).length) throw new Error('Refusing to fabricate a missing PICA combiner');
  const test = config.alpha_test;
  if (test?.enabled) {
    const f = test.function_code ?? test.function, value = Number(test.reference ?? 0)/255;
    if(!Number.isFinite(value)||value<0||value>1)throw new Error('Invalid alpha-test reference');
    // GLSL ES does not allow comparisons between float and integer literals.
    const ref=Number.isInteger(value)?value.toFixed(1):String(value);
    const pass = typeof f === 'number' ? ['false','true',`outputColor.a==${ref}`,`outputColor.a!=${ref}`,`outputColor.a<${ref}`,`outputColor.a<=${ref}`,`outputColor.a>${ref}`,`outputColor.a>=${ref}`][f] : null;
    if (pass === null || pass === undefined) throw new Error('Unsupported alpha-test function');
    lines.push(`if(!(${pass})) discard;`);
  }
  lines.push(`${modern?'fragColor':'gl_FragColor'}=outputColor;`, '}');
  return lines.join('\n');
}
