// Diagnostic only: compare coordinate conventions without changing the source GLB.
import fs from 'node:fs/promises';
const [source, output] = process.argv.slice(2);
const bytes = await fs.readFile(source);
const length = bytes.readUInt32LE(12);
const doc = JSON.parse(bytes.subarray(20, 20 + length).toString());
const binStart = 20 + length + 8;
const seen = new Set();
for (const mesh of doc.meshes) for (const primitive of mesh.primitives) for (const [name, index] of Object.entries(primitive.attributes)) {
  if (!name.startsWith('TEXCOORD_') || seen.has(index)) continue;
  seen.add(index);
  const acc = doc.accessors[index], view = doc.bufferViews[acc.bufferView];
  if (acc.type !== 'VEC2' || acc.componentType !== 5126) throw new Error('Unexpected diagnostic UV accessor');
  const stride = view.byteStride ?? 8;
  const start = binStart + (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
  if (start + stride * (acc.count-1) + 8 > bytes.length) throw new Error('UV accessor outside GLB');
  for (let i=0; i<acc.count; i++) bytes.writeFloatLE(1-bytes.readFloatLE(start+i*stride+4), start+i*stride+4);
}
await fs.writeFile(output, bytes, {flag:'wx'});
process.stdout.write(JSON.stringify({output, diagnostic_only:true, flipped_uv_accessors:seen.size})+'\n');
