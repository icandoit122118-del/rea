import http from 'node:http';
import path from 'node:path';
import { readFile, realpath, open } from 'node:fs/promises';
import { createHash, randomBytes } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));
const MAX_MODEL_BYTES = 256 * 1024 * 1024;
const MAX_JSON_BYTES = 32 * 1024 * 1024;
const staticFiles = new Map([
  ['/', ['preview/index.html', 'text/html; charset=utf-8']],
  ['/fan', ['preview/fan.html', 'text/html; charset=utf-8']],
  ['/fan.js', ['preview/fan.js', 'text/javascript; charset=utf-8']],
  ['/fan-state.js', ['preview/fan-state.js', 'text/javascript; charset=utf-8']],
  ['/fan.css', ['preview/fan.css', 'text/css; charset=utf-8']],
  ['/app.js', ['preview/app.js', 'text/javascript; charset=utf-8']],
  ['/pica-player.js', ['preview/pica-player.js', 'text/javascript; charset=utf-8']],
  ['/pica-runtime.js', ['preview/pica-runtime.js', 'text/javascript; charset=utf-8']],
  ['/synchronized-player.js', ['preview/synchronized-player.js', 'text/javascript; charset=utf-8']],
  ['/style.css', ['preview/style.css', 'text/css; charset=utf-8']],
  ['/vendor/three/build/three.module.js', ['node_modules/three/build/three.module.js', 'text/javascript; charset=utf-8']],
  ['/vendor/three/build/three.core.js', ['node_modules/three/build/three.core.js', 'text/javascript; charset=utf-8']],
  ['/vendor/three/examples/jsm/loaders/GLTFLoader.js', ['node_modules/three/examples/jsm/loaders/GLTFLoader.js', 'text/javascript; charset=utf-8']],
  ['/vendor/three/examples/jsm/controls/OrbitControls.js', ['node_modules/three/examples/jsm/controls/OrbitControls.js', 'text/javascript; charset=utf-8']],
  ['/vendor/three/examples/jsm/utils/BufferGeometryUtils.js', ['node_modules/three/examples/jsm/utils/BufferGeometryUtils.js', 'text/javascript; charset=utf-8']],
  ['/vendor/three/examples/jsm/utils/SkeletonUtils.js', ['node_modules/three/examples/jsm/utils/SkeletonUtils.js', 'text/javascript; charset=utf-8']],
]);

function inspectGlb(data) {
  if (data.length < 20 || data.readUInt32LE(0) !== 0x46546c67) throw new Error('GLB magic header is missing.');
  if (data.readUInt32LE(4) !== 2) throw new Error('Only GLB version 2 is supported.');
  if (data.readUInt32LE(8) !== data.length) throw new Error('GLB declared length does not match its file size.');
  let offset = 12;
  let json;
  let chunks = 0;
  let sawBin = false;
  while (offset < data.length) {
    if (offset + 8 > data.length) throw new Error('GLB chunk header is truncated.');
    const length = data.readUInt32LE(offset);
    const type = data.readUInt32LE(offset + 4);
    offset += 8;
    if (length % 4 !== 0 || offset + length > data.length) throw new Error('GLB chunk length is invalid.');
    if (chunks === 0 && type !== 0x4e4f534a) throw new Error('The first GLB chunk must be JSON.');
    if (type === 0x4e4f534a) {
      if (json !== undefined || length > MAX_JSON_BYTES) throw new Error('GLB JSON chunk is duplicate or too large.');
      try { json = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data.subarray(offset, offset + length))); }
      catch { throw new Error('GLB JSON chunk is invalid.'); }
    } else if (type === 0x004e4942) {
      if (sawBin) throw new Error('GLB binary chunk is duplicate.');
      sawBin = true;
    } else {
      throw new Error('Unknown GLB chunks are not supported by this bounded viewer.');
    }
    offset += length;
    chunks += 1;
  }
  if (!json || json.asset?.version !== '2.0') throw new Error('GLB glTF asset version must be 2.0.');
  // Refuse dependent files and remote URLs, including extension-defined resources.
  const stack = [json];
  while (stack.length) {
    const value = stack.pop();
    if (!value || typeof value !== 'object') continue;
    for (const [key, child] of Object.entries(value)) {
      if (key === 'uri' && (typeof child !== 'string' || !child.startsWith('data:'))) {
        throw new Error('External resource URIs are not supported. Supply a self-contained GLB.');
      }
      if (child && typeof child === 'object') stack.push(child);
    }
  }
  return {
    scenes: Array.isArray(json.scenes) ? json.scenes.length : 0,
    meshes: Array.isArray(json.meshes) ? json.meshes.length : 0,
    materials: Array.isArray(json.materials) ? json.materials.length : 0,
    skins: Array.isArray(json.skins) ? json.skins.length : 0,
    animationCount: Array.isArray(json.animations) ? json.animations.length : 0,
    animationNames: Array.isArray(json.animations) ? json.animations.map((clip, index) => clip?.name || `클립 ${index + 1}`) : [],
  };
}

async function snapshotModel(input) {
  if (typeof input !== 'string' || !path.isAbsolute(input)) throw new Error('input must be an absolute GLB file path.');
  if (path.extname(input).toLowerCase() !== '.glb') throw new Error('input must have the .glb extension.');
  const resolved = await realpath(input);
  const handle = await open(resolved, 'r');
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size < 20 || before.size > MAX_MODEL_BYTES) throw new Error('GLB must be a regular file between 20 bytes and 256 MiB.');
    const data = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < data.length) {
      const result = await handle.read(data, offset, data.length - offset, offset);
      if (result.bytesRead === 0) throw new Error('GLB changed or was truncated while reading.');
      offset += result.bytesRead;
    }
    const after = await handle.stat();
    if (after.size !== before.size || after.mtimeMs !== before.mtimeMs) throw new Error('GLB changed while reading. Retry with a stable file.');
    return { data, input: resolved, sha256: createHash('sha256').update(data).digest('hex'), metadata: inspectGlb(data) };
  } finally {
    await handle.close();
  }
}

/** Serve a read-only snapshot of one GLB and a fixed set of offline viewer assets. */
export async function startPreview({ input, port = 0 } = {}) {
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('port must be an integer between 0 and 65535.');
  const model = await snapshotModel(input);
  const cachedFiles = new Map();
  // Resolve and cache explicit files before listening. No URL becomes a filesystem path.
  for (const [route, [relativePath, type]] of staticFiles) {
    const absolute = path.resolve(projectRoot, relativePath);
    const resolved = await realpath(absolute);
    if (resolved !== absolute) throw new Error(`Viewer asset symlinks are not permitted: ${relativePath}`);
    cachedFiles.set(route, { data: await readFile(resolved), type });
  }
  const metadata = { name: path.basename(model.input), bytes: model.data.length, ...model.metadata };
  let expectedHost;
  const server = http.createServer((request, response) => {
    const nonce = randomBytes(18).toString('base64');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
    response.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'self' 'nonce-${nonce}'; style-src 'self'; connect-src 'self' blob:; img-src 'self' data: blob:; worker-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`);
    const send = (code, data, type = 'text/plain; charset=utf-8') => {
      response.writeHead(code, { 'Content-Type': type, 'Content-Length': Buffer.byteLength(data) });
      response.end(request.method === 'HEAD' ? undefined : data);
    };
    if (request.headers.host !== expectedHost) return send(403, 'Invalid local host.');
    if (request.headers.origin && request.headers.origin !== `http://${expectedHost}`) return send(403, 'Cross-origin requests are not permitted.');
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.setHeader('Allow', 'GET, HEAD');
      return send(405, 'Method not allowed.');
    }
    // Deliberately match the raw URL. Encoded traversal, queries and directory paths never resolve.
    const route = request.url;
    if (route === '/model.glb') return send(200, model.data, 'model/gltf-binary');
    if (route === '/api/model') return send(200, JSON.stringify(metadata), 'application/json; charset=utf-8');
    if (route === '/favicon.ico') return send(204, '');
    const asset = cachedFiles.get(route);
    if (!asset) return send(404, 'Not found.');
    const data = route === '/' || route === '/fan' ? asset.data.toString('utf8').replaceAll('__CSP_NONCE__', nonce) : asset.data;
    send(200, data, asset.type);
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 1_000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      server.removeListener('error', reject);
      expectedHost = `127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
  const actualPort = server.address().port;
  return { url: `http://127.0.0.1:${actualPort}/`, server, port: actualPort, input: model.input, sha256: model.sha256, bytes: model.data.length, metadata };
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 1 && (args[0] === '--help' || args[0] === '-h')) {
    console.log('Usage: node src/preview-server.mjs --input ABSOLUTE_MODEL.glb [--port 0]');
    return;
  }
  let input;
  let port = 0;
  for (let index = 0; index < args.length; index += 2) {
    const [key, value] = [args[index], args[index + 1]];
    if (value === undefined || !['--input', '--port'].includes(key)) throw new Error('Use --input ABSOLUTE_MODEL.glb [--port 0].');
    if (key === '--input') input = value;
    else if (!/^\d+$/.test(value)) throw new Error('port must be a non-negative integer.');
    else port = Number(value);
  }
  const preview = await startPreview({ input, port });
  console.log(JSON.stringify({ ok: true, url: preview.url, pid: process.pid, input: preview.input, bytes: preview.bytes, sha256: preview.sha256, metadata: preview.metadata, evidence: 'HTTP server startup only; browser rendering and original-game comparison are unverified.' }));
  const stop = () => { preview.server.close(); preview.server.closeAllConnections(); };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main().catch(error => {
    console.error(JSON.stringify({ ok: false, error: error.message }));
    process.exitCode = 1;
  });
}
