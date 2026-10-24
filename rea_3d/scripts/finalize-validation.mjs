import path from 'node:path';
import {readFile,writeFile,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const root='C:/rea3d-validation';
const read=async relative=>JSON.parse((await readFile(path.join(root,relative),'utf8')).replace(/^\uFEFF/,''));
const report=await readFile(path.join(root,'VALIDATION.ko.md'),'utf8');
const links=[...report.matchAll(/\]\(([^)]+)\)/g)].map(match=>match[1]).filter(target=>!/^https?:/.test(target));
for(const target of links)if(!(await stat(path.join(root,target))).isFile())throw new Error(`Missing report link: ${target}`);
const summary=await read('performance/run-001/summary.json');
const crosschecks=[];
for(const id of ['player-source-400x240','scaling-12-fallback-1280x720']){
  const item=await read(`performance/run-001/${id}-run-1.json`),frames=item.actual.probe.frames;
  const intervals=frames.map(frame=>frame.interval_ms).filter(value=>Number.isFinite(value)&&value>0);
  const elapsed=item.actual.probe.measurement.end_ms-item.actual.probe.measurement.start_ms;
  const fps=1000*frames.length/elapsed;
  const intervalRate=1000*intervals.length/intervals.reduce((sum,value)=>sum+value,0);
  const expected=summary.cases.find(item=>item.id===id).runs[0].fps;
  crosschecks.push({id,frames:frames.length,measurement_ms_from_end_minus_start:elapsed,independent_window_fps:fps,summary_fps:expected,error:Math.abs(fps-expected),raf_interval_rate:intervalRate,rate_scope_note:'Window callback count and reciprocal RAF interval use different boundary samples; only the matching window estimator is compared for exact arithmetic equality.'});
  if(Math.abs(fps-expected)>1e-9)throw new Error(`FPS crosscheck failed: ${id}`);
}
const shader=await read('shader/runtime-001/shader-runtime-receipt.json');
if(!summary.ok||summary.cases.length!==13||!shader.verification_ok||shader.decoded_state_parity!==false||shader.findings.length!==2)throw new Error('Unexpected verification result');
const evidence=['VALIDATION.ko.md','baseline/model.glb','baseline/recovery-receipt.json','machine-environment.json','generality/REPORT.md','generality/original-zip-read-audit.json','generality-runtime-001/REPORT.md','generality-runtime-001/input-manifest.json','generality-runtime-001/structure-validation.json','generality-runtime-001/browser-receipt-crosscheck.json','generality-browser-001/REPORT.md','generality-browser-001/generality-browser-receipt.json','generality-browser-001/contact-sheet.png','shader/REPORT.md','shader/baseline-field-audit.json','shader/runtime-001/shader-runtime-receipt.json','shader/runtime-001/baseline-actual-player.png','performance/run-001/REPORT.md','performance/run-001/summary.json','performance/run-001/summary.csv','performance/run-001/frame-costs.svg'];
const manifest=[];
for(const relative of evidence){const bytes=await readFile(path.join(root,relative));manifest.push({path:relative,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});}
const receipt={schema:'rea3d.validation-final-crosscheck.v1',at:new Date().toISOString(),report_links_checked:links.length,report_links_all_exist:true,independent_fps_crosschecks:crosschecks,performance_cases:13,performance_runs:summary.cases.reduce((sum,item)=>sum+item.runs.length,0),shader_audit_executed:true,shader_decoded_state_parity:false,shader_unresolved_findings:shader.findings.map(item=>item.material),original_additional_species_conversion_verified:false,manifest};
await writeFile(path.join(root,'validation-index.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({ok:true,links_checked:links.length,fps_crosschecks:crosschecks,evidence_files:manifest.length},null,2));
