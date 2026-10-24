/** CPU-only field inventory of the recovered exact-hash native-source GLB. */
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {parseGlb,verifyRetainedRawState} from './audit-shader-runtime.mjs';

const input='C:/rea3d-validation/baseline/model.glb';
const output='C:/rea3d-validation/shader/baseline-field-audit.json';
const bytes=await readFile(input),hash=createHash('sha256').update(bytes).digest('hex');
if(hash!=='e8bd5ccb7524170842037ef5d67286d1f3e2c197896bad2812bde673c63a3b76')throw new Error('Exact recovered baseline input required');
const {runtime}=parseGlb(bytes);
const materials=runtime.materials.map(m=>({
  name:m.name,color_scale:m.color_scale,depth_color_mask:m.depth_color_mask,
  polygon_offset_enabled:m.lighting?.polygon_offset_enabled,polygon_offset_unit:m.polygon_offset_unit,
  stencil_test_raw:m.stencil_test_raw,stencil_enabled:Boolean(m.stencil_test_raw&1),stencil_operation_raw:m.stencil_operation_raw,
  fog_enabled:m.lighting?.fog_enabled,fog_index:m.lighting?.fog_index,
  lighting:m.lighting,enabled_textures:m.enabled_textures,texture_sources:m.texture_sources,
  texture3_stage_references:m.texenv_stages.flatMap((stage,index)=>[
    ...(stage.color_sources??[]).filter(value=>value==='Texture3').map(()=>({stage:index,component:'color'})),
    ...(stage.alpha_sources??[]).filter(value=>value==='Texture3').map(()=>({stage:index,component:'alpha'})),
  ]),texture_mappers:m.texture_mappers,shader_reference:m.shader_reference,
  layer_config:m.layer_config,translucency_kind:m.translucency_kind,
  blend:m.blend,raw_command_redecode:verifyRetainedRawState(m),source_offsets:m.source_offsets,
}));
const all=predicate=>materials.every(predicate);
const result={schema:'rea3d.actual-source-glb.shader-fields.v1',measured_at:new Date().toISOString(),
  execution:'CPU_read_only_existing_GLB_extras_no_GPU_no_native_process',input:{path:input,bytes:bytes.length,sha256:hash},
  material_count:materials.length,materials,
  common_values:{color_scale_all_one:all(m=>m.color_scale===1),depth_function_all_Lequal:all(m=>m.depth_color_mask.depth_function==='Lequal'),
    RGBA_write_all_true:all(m=>m.depth_color_mask.color_write.every(Boolean)),polygon_offset_disabled_and_zero:all(m=>m.polygon_offset_enabled===false&&m.polygon_offset_unit===0),
    stencil_disabled_and_keep_operations:all(m=>!m.stencil_enabled&&m.stencil_operation_raw===0),fog_disabled_index_zero:all(m=>m.fog_enabled===false&&m.fog_index===0),
    texture3_disabled_and_unreferenced:all(m=>m.enabled_textures[3]===false&&m.texture3_stage_references.length===0),
    sampler_min_lod_bias_all_zero:all(m=>m.texture_mappers.every(mapper=>mapper.min_lod===0&&mapper.lod_bias===0)),
    external_shader_reference:[...new Set(materials.map(m=>m.shader_reference))],
    actually_enabled_source_features:['normal bump texture2','reflection LUT','fresnel selector2'],
    raw_command_redecode_all_pass:all(m=>m.raw_command_redecode.passed)},
  pending_draw_state_differences:materials.filter(m=>m.blend.color_src==='SourceAlpha').map(m=>({material:m.name,expected_blend:m.blend,expected_depth_write:m.depth_color_mask.depth_write,draw_observed_yet:false,pixel_impact_verified:false})),
  conclusion:'General renderer omissions do not prove mismatches on this sample when its source state uses the same default/off/identity value. Iris alpha blending is a source requirement pending actual draw verification.',
  boundaries:{BCH_source_reopened:false,original_shader_program_resolved:false,original_game_renderpass_semantics_newly_verified:false,original_light_camera_parity:false,pixel_impact_verified:false}};
await writeFile(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({output,sha256:createHash('sha256').update(await readFile(output)).digest('hex'),material_count:materials.length,common_values:result.common_values},null,2));
