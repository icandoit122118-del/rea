import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { GarcError, GARC_LIMITS, inspectGarc, extractGarc } from '../src/garc.mjs';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');

// Entirely synthetic format fixture, with no Nintendo model or game payload.
function fixture({ version = 4, big = false, reordered = false, empty = false } = {}) {
  const groups = empty ? [] : [
    [{ subindex: 0, bytes: Buffer.from('abc') }],
    [{ subindex: 2, bytes: Buffer.from([0x11, 5, 0, 0, 9]) }, { subindex: 31, bytes: Buffer.from('Z') }],
  ];
  const headerSize = version === 4 ? 28 : 36;
  const fatoSize = 12 + groups.length * 4;
  const fatbSize = 12 + groups.reduce((size, group) => size + 4 + group.length * 12, 0);
  const dataOffset = headerSize + fatoSize + fatbSize + 12;
  const dataSize = groups.flat().reduce((size, member) => size + Math.ceil(member.bytes.length / 4) * 4, 0);
  const data = Buffer.alloc(dataOffset + dataSize, 0);
  const u16 = (offset, value) => big ? data.writeUInt16BE(value, offset) : data.writeUInt16LE(value, offset);
  const u32 = (offset, value) => big ? data.writeUInt32BE(value, offset) : data.writeUInt32LE(value, offset);
  data.write('CRAG', 0, 'ascii');
  u32(4, headerSize); u16(8, 0xfeff); u16(10, version * 0x100);
  u32(12, 4); u32(16, dataOffset); u32(20, data.length);
  const largest = empty ? 0 : 5;
  u32(24, version === 4 ? largest : Math.ceil(largest / 4) * 4);
  if (version === 6) { u32(28, largest); u32(32, 4); }
  const fatoOffset = headerSize;
  data.write('OTAF', fatoOffset); u32(fatoOffset + 4, fatoSize);
  u16(fatoOffset + 8, groups.length); u16(fatoOffset + 10, 0xffff);
  const fatbOffset = headerSize + fatoSize;
  data.write('BTAF', fatbOffset); u32(fatbOffset + 4, fatbSize); u32(fatbOffset + 8, groups.flat().length);
  const fatbBody = fatbOffset + 12;
  let tableCursor = fatbBody;
  let payloadCursor = 0;
  const physicalOrder = reordered ? [1, 0] : groups.map((_, index) => index);
  for (const index of physicalOrder) {
    const group = groups[index];
    u32(fatoOffset + 12 + index * 4, tableCursor - fatbBody);
    const mask = group.reduce((bits, member) => (bits | (1 << member.subindex)) >>> 0, 0);
    u32(tableCursor, mask); tableCursor += 4;
    for (const member of group) {
      const padded = Math.ceil(member.bytes.length / 4) * 4;
      u32(tableCursor, payloadCursor); u32(tableCursor + 4, payloadCursor + padded); u32(tableCursor + 8, member.bytes.length);
      tableCursor += 12;
      member.bytes.copy(data, dataOffset + payloadCursor);
      payloadCursor += padded;
    }
  }
  const fimbOffset = fatbOffset + fatbSize;
  data.write('BMIF', fimbOffset); u32(fimbOffset + 4, 12); u32(fimbOffset + 8, dataSize);
  return { data, headerSize, fatoOffset, fatbOffset, fatbBody, fimbOffset, dataOffset };
}

async function withTemp(t) {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'rea-garc-test-'));
  t.after(async () => {
    const checked = path.resolve(temporary);
    assert.equal(path.dirname(checked).toLowerCase(), path.resolve(os.tmpdir()).toLowerCase());
    assert.ok(path.basename(checked).startsWith('rea-garc-test-'));
    assert.equal((await fs.lstat(checked)).isSymbolicLink(), false);
    await fs.rm(checked, { recursive: true, force: true });
  });
  return temporary;
}

for (const version of [4, 6]) {
  for (const big of [false, true]) {
    test(`GARC v${version} ${big ? 'big' : 'little'} endian parses sparse 32-bit subentries`, () => {
      const { data, dataOffset } = fixture({ version, big });
      const report = inspectGarc(data);
      assert.equal(report.format, 'GARC'); assert.equal(report.version, version);
      assert.equal(report.rawVersion, version * 0x100); assert.equal(report.byteOrder, big ? 'big' : 'little');
      assert.equal(report.fileCount, 3); assert.equal(report.groupCount, 2); assert.equal(report.totalBytes, 9);
      assert.deepEqual(report.entries.map(entry => [entry.index, entry.subindex, entry.size]), [[0, 0, 3], [1, 2, 5], [1, 31, 1]]);
      assert.equal(report.entries[0].offset, dataOffset);
      assert.equal(report.decompressed, false); assert.equal(report.decodedModels, false);
      assert.equal(report.decodedAnimations, false); assert.equal(report.decodedTextures, false);
    });
  }
}

test('FATO offsets select actual FATB records rather than sequential assumptions', () => {
  const { data } = fixture({ reordered: true });
  const result = inspectGarc(data);
  const values = result.entries.map(entry => data.subarray(entry.offset, entry.offset + entry.size));
  assert.deepEqual(values, [Buffer.from('abc'), Buffer.from([0x11, 5, 0, 0, 9]), Buffer.from('Z')]);
});

test('empty v4 and v6 archives remain valid and bounded', () => {
  for (const version of [4, 6]) {
    const report = inspectGarc(fixture({ version, empty: true }).data);
    assert.deepEqual(report.entries, []); assert.equal(report.totalBytes, 0);
  }
});

test('rejects all truncated prefixes of a valid archive', () => {
  const { data } = fixture();
  for (let length = 0; length < data.length; length++) assert.throws(() => inspectGarc(data.subarray(0, length)), GarcError);
});

test('rejects malformed magic, endian, version, sizes, tables, and payload extents', async t => {
  const cases = [
    ['magic', value => value.data.write('GARC', 0)],
    ['BOM', value => value.data.writeUInt16LE(0, 8)],
    ['version', value => value.data.writeUInt16LE(0x0500, 10)],
    ['header size', value => value.data.writeUInt32LE(32, 4)],
    ['section count', value => value.data.writeUInt32LE(3, 12)],
    ['file size', value => value.data.writeUInt32LE(value.data.length + 1, 20)],
    ['data offset', value => value.data.writeUInt32LE(value.dataOffset - 1, 16)],
    ['largest unpadded', value => value.data.writeUInt32LE(1, 24)],
    ['FATO magic', value => value.data.write('FATO', value.fatoOffset)],
    ['FATO size', value => value.data.writeUInt32LE(12, value.fatoOffset + 4)],
    ['FATO count limit', value => value.data.writeUInt16LE(10_001, value.fatoOffset + 8)],
    ['FATO padding', value => value.data.writeUInt16LE(0, value.fatoOffset + 10)],
    ['FATO offset bounds', value => value.data.writeUInt32LE(0xfffffff0, value.fatoOffset + 12)],
    ['FATO offset alignment', value => value.data.writeUInt32LE(1, value.fatoOffset + 12)],
    ['FATO duplicate pointer', value => value.data.writeUInt32LE(0, value.fatoOffset + 16)],
    ['FATB magic', value => value.data.write('FATB', value.fatbOffset)],
    ['FATB section bounds', value => value.data.writeUInt32LE(0xffffffff, value.fatbOffset + 4)],
    ['FATB file count', value => value.data.writeUInt32LE(4, value.fatbOffset + 8)],
    ['FATB file count limit', value => value.data.writeUInt32LE(10_001, value.fatbOffset + 8)],
    ['FATB length greater than extent', value => value.data.writeUInt32LE(10, value.fatbBody + 12)],
    ['FATB end before start', value => value.data.writeUInt32LE(8, value.fatbBody + 4)],
    ['FATB payload outside FIMB', value => value.data.writeUInt32LE(0x10000, value.fatbBody + 8)],
    ['FATB mask beyond record', value => value.data.writeUInt32LE(0xffffffff, value.fatbBody)],
    ['overlapping payload', value => value.data.writeUInt32LE(0, value.fatbBody + 20)],
    ['FIMB magic', value => value.data.write('FIMB', value.fimbOffset)],
    ['FIMB header size', value => value.data.writeUInt32LE(16, value.fimbOffset + 4)],
    ['FIMB data size', value => value.data.writeUInt32LE(0xffffffff, value.fimbOffset + 8)],
  ];
  for (const [label, mutate] of cases) await t.test(label, () => {
    const value = fixture(); mutate(value); assert.throws(() => inspectGarc(value.data), GarcError);
  });
});

test('v6 rejects invalid alignment and padded maximum', () => {
  for (const [offset, number] of [[32, 0], [32, 3], [32, 0x80000000], [24, 2]]) {
    const { data } = fixture({ version: 6 }); data.writeUInt32LE(number, offset);
    assert.throws(() => inspectGarc(data), GarcError);
  }
});

test('extracts exact original bytes and persists hashes and manifest readback', async t => {
  const temporary = await withTemp(t);
  const source = path.join(temporary, 'synthetic.garc'); const output = path.join(temporary, 'new-output');
  const { data } = fixture({ version: 6 }); await fs.writeFile(source, data);
  const result = await extractGarc(source, output);
  assert.equal(result.source.sha256, digest(data)); assert.equal(result.source.size, data.length);
  assert.equal(result.scope, 'archive-members-only'); assert.equal(result.decompressed, false);
  assert.deepEqual((await fs.readdir(output)).sort(), ['00000_00.bin', '00001_02.bin', '00001_31.bin', 'manifest.json']);
  for (const member of result.entries) {
    const saved = await fs.readFile(path.join(output, member.relativePath));
    assert.deepEqual(saved, data.subarray(member.offset, member.offset + member.size));
    assert.equal(digest(saved), member.sha256);
  }
  const compressedLooking = await fs.readFile(path.join(output, '00001_02.bin'));
  assert.equal(compressedLooking[0], 0x11); assert.equal(compressedLooking.length, 5);
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(output, 'manifest.json'), 'utf8')), result);
});

test('refuses an existing output without changing its contents', async t => {
  const temporary = await withTemp(t); const source = path.join(temporary, 'archive'); const output = path.join(temporary, 'output');
  await fs.writeFile(source, fixture().data); await fs.mkdir(output); await fs.writeFile(path.join(output, 'keep'), 'untouched');
  await assert.rejects(extractGarc(source, output), /already exists/);
  assert.equal(await fs.readFile(path.join(output, 'keep'), 'utf8'), 'untouched');
  assert.deepEqual(await fs.readdir(output), ['keep']);
});

test('validates archive and bounded options before creating any output', async t => {
  const temporary = await withTemp(t); const source = path.join(temporary, 'archive');
  await fs.writeFile(source, fixture().data);
  const options = [{ maxArchiveBytes: 1 }, { maxEntries: 2 }, { maxTotalBytes: 8 }, { maxArchiveBytes: GARC_LIMITS.maxArchiveBytes + 1 }, { decompress: true }, { toString: 1 }, { maxEntries: -1 }];
  for (let index = 0; index < options.length; index++) {
    const output = path.join(temporary, `output-${index}`);
    await assert.rejects(extractGarc(source, output, options[index]));
    await assert.rejects(fs.stat(output), { code: 'ENOENT' });
  }
  await fs.writeFile(source, Buffer.from('invalid'));
  const corruptOutput = path.join(temporary, 'corrupt-output');
  await assert.rejects(extractGarc(source, corruptOutput), GarcError);
  await assert.rejects(fs.stat(corruptOutput), { code: 'ENOENT' });
});

test('rejects relative paths, traversal, and a missing output parent', async t => {
  const temporary = await withTemp(t); const source = path.join(temporary, 'archive'); await fs.writeFile(source, fixture().data);
  await assert.rejects(extractGarc('archive', path.join(temporary, 'out')), /absolute/);
  await assert.rejects(extractGarc(source, 'out'), /absolute/);
  await assert.rejects(extractGarc(source, `${temporary}${path.sep}..${path.sep}out`), /traversal/);
  await assert.rejects(extractGarc(source, path.join(temporary, 'absent-parent', 'out')), { code: 'ENOENT' });
});

test('rejects a symlink/junction output ancestor and source ancestor', async t => {
  const temporary = await withTemp(t); const real = path.join(temporary, 'real'); const redirect = path.join(temporary, 'redirect');
  await fs.mkdir(real);
  try { await fs.symlink(real, redirect, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) return t.skip(`Host cannot create link fixture: ${error.code}`); throw error; }
  const source = path.join(real, 'archive'); await fs.writeFile(source, fixture().data);
  await assert.rejects(extractGarc(source, path.join(redirect, 'out')), /links|redirect/);
  await assert.rejects(extractGarc(path.join(redirect, 'archive'), path.join(temporary, 'out')), /links|redirect/);
  await assert.rejects(fs.stat(path.join(real, 'out')), { code: 'ENOENT' });
});
