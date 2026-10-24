import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {runProcess,ROOT} from '../src/bridge.mjs';

const [nativeFile,work]=process.argv.slice(2);
if(![nativeFile,work].every(p=>p&&path.isAbsolute(p)))throw new Error('Usage: node verify-mcp-cli-equivalence.mjs ABS_NATIVE_JSON NEW_WORK_DIRECTORY');
const hash=buffer=>createHash('sha256').update(buffer).digest('hex');
const native=JSON.parse(await fs.readFile(nativeFile,'utf8'));
assert.equal(native.ok,true);
const operation=JSON.parse(await fs.readFile(native.receipt.path,'utf8'));
assert.equal(hash(await fs.readFile(native.receipt.path)),native.receipt.sha256);
await fs.mkdir(work);
const args={...operation.request,output_dir:path.join(work,'cli-export')};
const request=path.join(work,'request.json');
await fs.writeFile(request,JSON.stringify({version:1,method:'export',args},null,2),{flag:'wx'});
const inputs=[args.path,...args.motion_paths,...args.texture_paths];
const before=await Promise.all(inputs.map(async p=>({path:p,sha256:hash(await fs.readFile(p))})));
const execution=await runProcess(process.execPath,[path.join(ROOT,'src/cli.mjs'),'--file',request],{timeout:90000});
await fs.writeFile(path.join(work,'cli-stdout.json'),execution.stdout,{flag:'wx'});
await fs.writeFile(path.join(work,'cli-stderr.txt'),execution.stderr,{flag:'wx'});
assert.equal(execution.code,0);const cli=JSON.parse(execution.stdout);assert.equal(cli.ok,true);
const nativeBytes=await fs.readFile(native.result.glb_path),cliBytes=await fs.readFile(cli.result.glb_path);
assert.deepEqual(cliBytes,nativeBytes);
for(const file of before)assert.equal(hash(await fs.readFile(file.path)),file.sha256);
const output={schema:'rea.mcp-cli-quality-equivalence.v1',ok:true,measured_at:new Date().toISOString(),
  comparison:'Native MCP and ordinary JSON CLI invoke the same asset decoder/exporter with identical inputs and options.',
  native:{receipt:native.receipt,glb:native.result.glb_path,sha256:hash(nativeBytes)},
  cli:{receipt:cli.receipt,glb:cli.result.glb_path,sha256:hash(cliBytes)},
  byte_identical_glb:true,source_inputs_unchanged:before,
  implication:'MCP transport alone provides no visual-quality advantage. Correct source-data interpretation and rendering determine fidelity.',
  boundary:'This does not compare every third-party converter or quantify original-game visual parity.'};
const report=path.join(work,'equivalence.json');await fs.writeFile(report,JSON.stringify(output,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({ok:true,report,byte_identical_glb:true,sha256:output.native.sha256}));
