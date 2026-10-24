import { promises as fs } from 'node:fs';
import path from 'node:path';
import { PYTHON, runProcess } from '../src/bridge.mjs';

// Our own tiny archive, carrying only our own synthetic model and motion bytes.
// It is a test input producer, not a game archive repacker.
export function makeSyntheticGarc(payloads) {
  const count = payloads.length;
  const fatoSize = 12 + count * 4, fatbSize = 12 + count * 16;
  const dataOffset = 28 + fatoSize + fatbSize + 12;
  const dataBytes = payloads.reduce((n, p) => n + Math.ceil(p.length / 4) * 4, 0);
  const out = Buffer.alloc(dataOffset + dataBytes);
  out.write('CRAG', 0); out.writeUInt32LE(28, 4); out.writeUInt16LE(0xfeff, 8);
  out.writeUInt16LE(0x0400, 10); out.writeUInt32LE(4, 12); out.writeUInt32LE(dataOffset, 16);
  out.writeUInt32LE(out.length, 20); out.writeUInt32LE(Math.max(...payloads.map(p => p.length)), 24);
  out.write('OTAF', 28); out.writeUInt32LE(fatoSize, 32); out.writeUInt16LE(count, 36); out.writeUInt16LE(0xffff, 38);
  const fatb = 28 + fatoSize;
  out.write('BTAF', fatb); out.writeUInt32LE(fatbSize, fatb + 4); out.writeUInt32LE(count, fatb + 8);
  const fimb = fatb + fatbSize;
  out.write('BMIF', fimb); out.writeUInt32LE(12, fimb + 4); out.writeUInt32LE(dataBytes, fimb + 8);
  let cursor = 0;
  payloads.forEach((payload, i) => {
    out.writeUInt32LE(i * 16, 40 + i * 4);
    const record = fatb + 12 + i * 16, padded = Math.ceil(payload.length / 4) * 4;
    out.writeUInt32LE(1, record); out.writeUInt32LE(cursor, record + 4);
    out.writeUInt32LE(cursor + padded, record + 8); out.writeUInt32LE(payload.length, record + 12);
    payload.copy(out, dataOffset + cursor); cursor += padded;
  });
  return out;
}

export async function createFixture(directory) {
  const run = await runProcess(PYTHON, ['-I', '-c', 'import json,sys; from test_fixture import make_fixture; print(json.dumps([str(p) for p in make_fixture(sys.argv[1])]))', directory]);
  if (run.code !== 0) throw new Error(run.stderr || run.stdout);
  const [model, motion] = JSON.parse(run.stdout);
  const archive = path.join(directory, 'synthetic.garc');
  await fs.writeFile(archive, makeSyntheticGarc([await fs.readFile(model), await fs.readFile(motion)]), { flag: 'wx' });
  return { model, motion, archive };
}
