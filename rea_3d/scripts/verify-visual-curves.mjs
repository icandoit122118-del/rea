import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {sampleCurve} from '../preview/pica-runtime.js';
const [source,output]=process.argv.slice(2);
const data=await fs.readFile(source),expected=JSON.parse(data);
let tested=0,curves=0,maxError=0;const failures=[];
for(const pack of expected.packages)for(const entry of pack.curves){
  curves++;
  for(let frame=0;frame<entry.samples.length;frame++){
    const wanted=entry.samples[frame],actual=sampleCurve(entry.curve,frame,null);tested++;
    const equal=typeof wanted==='boolean'?actual===wanted:wanted===null?actual===null:typeof actual==='number'&&Math.abs(wanted-actual)<=1e-7;
    if(typeof actual==='number'&&typeof wanted==='number')maxError=Math.max(maxError,Math.abs(wanted-actual));
    if(!equal&&failures.length<10)failures.push({pack:pack.kind,entry:entry.entry,element:entry.name,frame,wanted,actual});
  }
}
const report={schema:'rea.pica.curve-crosscheck.v1',ok:failures.length===0,expectation_sha256:createHash('sha256').update(data).digest('hex'),curves,samples:tested,max_numeric_error:maxError,failures};
await fs.writeFile(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(report,null,2));
if(!report.ok)process.exitCode=1;
