import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import * as fs from 'node:fs/promises';
import path from 'node:path';

// Format structure independently implemented after examining the pinned upstream
// sources listed in research/garc-sources/SOURCES.json. No payload decompression.
export const GARC_LIMITS = Object.freeze({
  maxArchiveBytes: 512 * 1024 * 1024,
  maxEntries: 10_000,
  maxTotalBytes: 1024 * 1024 * 1024,
});

export class GarcError extends Error {
  constructor(message) { super(message); this.name = 'GarcError'; }
}

function fail(message) { throw new GarcError(message); }
function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function limitsFor(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) fail('Options must be an object');
  const result = { ...GARC_LIMITS };
  for (const [key, value] of Object.entries(options)) {
    if (!Object.hasOwn(GARC_LIMITS, key)) fail(`Unsupported option: ${key}`);
    if (!Number.isSafeInteger(value) || value < 1 || value > GARC_LIMITS[key]) fail(`Invalid bounded limit: ${key}`);
    result[key] = value;
  }
  return result;
}

function inspect(bytes, limits) {
  if (!(bytes instanceof Uint8Array)) fail('Archive input must be a Buffer or Uint8Array');
  const data = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (data.length > limits.maxArchiveBytes) fail('Archive exceeds maxArchiveBytes');
  function range(offset, size, label, end = data.length) {
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(size) || offset < 0 || size < 0 || offset > end || size > end - offset) {
      fail(`Truncated or out-of-bounds ${label}`);
    }
  }
  range(0, 28, 'GARC header');
  if (data.toString('ascii', 0, 4) !== 'CRAG') fail('Invalid GARC magic (expected on-disk CRAG)');
  const marker = data.readUInt16LE(8);
  if (marker !== 0xfeff && marker !== 0xfffe) fail('Invalid GARC byte-order marker');
  const little = marker === 0xfeff;
  const u16 = offset => little ? data.readUInt16LE(offset) : data.readUInt16BE(offset);
  const u32 = offset => little ? data.readUInt32LE(offset) : data.readUInt32BE(offset);
  const rawVersion = u16(10);
  const version = rawVersion === 0x0400 ? 4 : rawVersion === 0x0600 ? 6 : 0;
  if (!version) fail(`Unsupported GARC version: 0x${rawVersion.toString(16)}`);
  const headerSize = u32(4);
  if (headerSize !== (version === 4 ? 28 : 36)) fail('Invalid GARC header size for version');
  range(0, headerSize, 'versioned GARC header');
  if (u32(12) !== 4) fail('Invalid GARC section count');
  const dataOffset = u32(16);
  const fileSize = u32(20);
  if (fileSize !== data.length) fail('GARC declared file size does not match input');
  const largestUnpadded = u32(version === 4 ? 24 : 28);
  const largestPadded = version === 6 ? u32(24) : null;
  const alignment = version === 6 ? u32(32) : 4;
  if (alignment < 1 || alignment > 1024 * 1024 || (alignment & (alignment - 1)) !== 0) fail('Invalid GARC alignment');

  function section(offset, magic, minimumSize) {
    range(offset, minimumSize, `${magic} header`);
    if (data.toString('ascii', offset, offset + 4) !== magic) fail(`Invalid section magic: expected ${magic}`);
    const size = u32(offset + 4);
    if (size < minimumSize) fail(`Invalid ${magic} section size`);
    range(offset, size, `${magic} section`);
    return size;
  }
  const fatoOffset = headerSize;
  const fatoSize = section(fatoOffset, 'OTAF', 12);
  const groupCount = u16(fatoOffset + 8);
  if (groupCount > limits.maxEntries) fail('GARC group count exceeds maxEntries');
  if (fatoSize !== 12 + groupCount * 4) fail('FATO size does not match group count');
  if (u16(fatoOffset + 10) !== 0xffff) fail('Invalid FATO reserved padding');
  const fatbOffset = fatoOffset + fatoSize;
  const fatbSize = section(fatbOffset, 'BTAF', 12);
  const fileCount = u32(fatbOffset + 8);
  if (fileCount > limits.maxEntries) fail('GARC file count exceeds maxEntries');
  const fatbBody = fatbOffset + 12;
  const fatbEnd = fatbOffset + fatbSize;
  const fimbOffset = fatbEnd;
  const fimbHeaderSize = section(fimbOffset, 'BMIF', 12);
  if (fimbHeaderSize !== 12) fail('Invalid FIMB header size');
  if (dataOffset !== fimbOffset + fimbHeaderSize) fail('GARC data offset does not match FIMB');
  const dataSize = u32(fimbOffset + 8);
  if (dataSize !== data.length - dataOffset) fail('FIMB data size does not match archive extent');
  const entries = [];
  const tableRanges = [];
  const payloadRanges = [];
  let totalBytes = 0;
  let observedLargest = 0;
  let observedLargestPadded = 0;
  for (let index = 0; index < groupCount; index++) {
    const relativeTableOffset = u32(fatoOffset + 12 + index * 4);
    if (relativeTableOffset % 4 !== 0) fail('Unaligned FATO record offset');
    const recordStart = fatbBody + relativeTableOffset;
    range(recordStart, 4, 'FATB bitmask', fatbEnd);
    const mask = u32(recordStart);
    let cursor = recordStart + 4;
    for (let subindex = 0; subindex < 32; subindex++) {
      if (((mask >>> subindex) & 1) === 0) continue;
      range(cursor, 12, 'FATB subentry', fatbEnd);
      const start = u32(cursor);
      const end = u32(cursor + 4);
      const size = u32(cursor + 8);
      cursor += 12;
      if (end < start || size > end - start) fail('Invalid FATB start/end/length relationship');
      range(start, end - start, 'FIMB member', dataSize);
      totalBytes += size;
      if (totalBytes > limits.maxTotalBytes) fail('GARC extracted bytes exceed maxTotalBytes');
      if (entries.length >= limits.maxEntries) fail('GARC entries exceed maxEntries');
      const offset = dataOffset + start;
      entries.push({ index, subindex, offset, size, paddedSize: end - start, dataRelativeOffset: start });
      if (end > start) payloadRanges.push([start, end]);
      observedLargest = Math.max(observedLargest, size);
      observedLargestPadded = Math.max(observedLargestPadded, end - start);
    }
    tableRanges.push([recordStart, cursor]);
  }
  if (entries.length !== fileCount) fail('FATB file count does not match populated subentries');
  tableRanges.sort((a, b) => a[0] - b[0]);
  let tableCursor = fatbBody;
  for (const [start, end] of tableRanges) {
    if (start !== tableCursor) fail('Overlapping or unreferenced FATB table records');
    tableCursor = end;
  }
  if (tableCursor !== fatbEnd) fail('Unreferenced FATB table bytes');
  payloadRanges.sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < payloadRanges.length; i++) {
    if (payloadRanges[i][0] < payloadRanges[i - 1][1]) fail('Overlapping GARC member payloads are unsupported');
  }
  if (largestUnpadded < observedLargest || largestUnpadded > dataSize) fail('Invalid largest unpadded member size');
  if (version === 6 && (largestPadded < observedLargestPadded || largestPadded > dataSize || largestPadded < largestUnpadded)) fail('Invalid largest padded member size');
  return {
    format: 'GARC', version, rawVersion, byteOrder: little ? 'little' : 'big', headerSize, fileSize,
    dataOffset, dataSize, groupCount, fileCount, alignment, largestUnpadded, largestPadded,
    totalBytes, entries,
    scope: 'archive-members-only',
    decodedModels: false, decodedTextures: false, decodedAnimations: false,
    decompressed: false,
  };
}

export function inspectGarc(buffer) { return inspect(buffer, GARC_LIMITS); }

function absolutePath(value, label) {
  if (typeof value !== 'string' || !value || value.includes('\0') || !path.isAbsolute(value)) fail(`${label} must be an absolute local path`);
  if (value.split(/[\\/]/).includes('..')) fail(`${label} must not contain traversal segments`);
  // UNC/device paths and alternate data streams are intentionally outside this local profile.
  if (process.platform === 'win32' && (/^[\\/]{2}/.test(value) || value.slice(2).includes(':'))) fail(`${label} must not be a UNC, device, or alternate-stream path`);
  return path.resolve(value);
}

async function checkPathChain(target, expectedKind) {
  const root = path.parse(target).root;
  const parts = target.slice(root.length).split(path.sep).filter(Boolean);
  let current = root;
  for (let i = -1; i < parts.length; i++) {
    if (i >= 0) current = path.join(current, parts[i]);
    const stat = await fs.lstat(current);
    if (stat.isSymbolicLink()) fail('Symbolic links and junction/reparse redirects are not permitted');
    const final = i === parts.length - 1;
    if (!final && !stat.isDirectory()) fail('Path ancestor is not a directory');
    if (final && expectedKind === 'file' && !stat.isFile()) fail('Archive input must be a regular file');
    if (final && expectedKind === 'directory' && !stat.isDirectory()) fail('Output parent must be a directory');
    const actual = await fs.realpath(current);
    const canonical = process.platform === 'win32' ? actual.toLowerCase() : actual;
    const requested = process.platform === 'win32' ? current.toLowerCase() : current;
    if (canonical !== requested) fail('Canonical path differs from requested path; redirects are not permitted');
  }
}

/** Extract original bytes without LZ decoding. outputDir must not exist; its parent must exist. */
export async function extractGarc(inputPath, outputDir, options = {}) {
  const limits = limitsFor(options);
  const sourcePath = absolutePath(inputPath, 'inputPath');
  const destination = absolutePath(outputDir, 'outputDir');
  await checkPathChain(sourcePath, 'file');
  const parent = path.dirname(destination);
  await checkPathChain(parent, 'directory');
  try { await fs.lstat(destination); fail('Output directory already exists; no overwrite is permitted'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const sourceHandle = await fs.open(sourcePath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  let data;
  try {
    const before = await sourceHandle.stat();
    if (!before.isFile()) fail('Archive input must be a regular file');
    if (before.size > limits.maxArchiveBytes) fail('Archive exceeds maxArchiveBytes');
    // Read a bounded buffer, even if another process grows the source after stat.
    data = Buffer.alloc(before.size);
    let read = 0;
    while (read < data.length) {
      const result = await sourceHandle.read(data, read, data.length - read, read);
      if (!result.bytesRead) fail('Archive changed or was truncated while reading');
      read += result.bytesRead;
    }
    const after = await sourceHandle.stat();
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) fail('Archive changed while reading');
  } finally { await sourceHandle.close(); }
  const inventory = inspect(data, limits);
  const manifest = {
    schemaVersion: 1, ...inventory,
    source: { path: sourcePath, size: data.length, sha256: sha256(data) },
    outputDirectory: destination,
    entries: inventory.entries.map(entry => {
      const relativePath = `${String(entry.index).padStart(5, '0')}_${String(entry.subindex).padStart(2, '0')}.bin`;
      return { ...entry, relativePath, sha256: sha256(data.subarray(entry.offset, entry.offset + entry.size)) };
    }),
  };
  // Validate fully before creating output. Exclusive directory and file creation
  // prevent accidental overwrites; failed writes leave a bounded partial result.
  await checkPathChain(parent, 'directory');
  await fs.mkdir(destination);
  for (const entry of manifest.entries) {
    await checkPathChain(destination, 'directory');
    const memberPath = path.join(destination, entry.relativePath);
    if (path.dirname(memberPath) !== destination) fail('Generated member path escaped output directory');
    await fs.writeFile(memberPath, data.subarray(entry.offset, entry.offset + entry.size), { flag: 'wx', mode: 0o600 });
    const persisted = await fs.readFile(memberPath);
    if (sha256(persisted) !== entry.sha256) fail('Extracted member readback hash mismatch');
  }
  await checkPathChain(destination, 'directory');
  const manifestPath = path.join(destination, 'manifest.json');
  await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
  const savedManifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  if (JSON.stringify(savedManifest) !== JSON.stringify(manifest)) fail('Saved manifest readback mismatch');
  return manifest;
}
