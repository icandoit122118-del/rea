import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { startPreview } from '../src/preview-server.mjs';

async function removeOwnTemporary(directory) {
  const checked = path.resolve(directory);
  assert.equal(path.dirname(checked).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
  assert.ok(path.basename(checked).startsWith('rea3d-preview-'));
  assert.equal((await lstat(checked)).isSymbolicLink(), false);
  await rm(checked, { recursive: true, force: true });
}

function makeGlb(document = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [] }], nodes: [] }) {
  const json = Buffer.from(JSON.stringify(document));
  const padded = Buffer.alloc(Math.ceil(json.length / 4) * 4, 0x20);
  json.copy(padded);
  const data = Buffer.alloc(20 + padded.length);
  data.writeUInt32LE(0x46546c67, 0);
  data.writeUInt32LE(2, 4);
  data.writeUInt32LE(data.length, 8);
  data.writeUInt32LE(padded.length, 12);
  data.writeUInt32LE(0x4e4f534a, 16);
  padded.copy(data, 20);
  return data;
}

function request(preview, route, { method = 'GET', headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port: preview.port, path: route, method, headers }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, data: Buffer.concat(chunks) }));
      response.on('error', reject);
    });
    req.on('error', reject);
    req.end();
  });
}

test('offline preview serves one immutable GLB and explicit local assets only', async t => {
  const directory = await mkdtemp(path.join(tmpdir(), 'rea3d-preview-'));
  const input = path.join(directory, 'sample.glb');
  const original = makeGlb();
  await writeFile(input, original);
  let preview;
  t.after(async () => {
    if (preview) {
      preview.server.closeAllConnections();
      await new Promise(resolve => preview.server.close(resolve));
    }
    await removeOwnTemporary(directory);
  });
  preview = await startPreview({ input, port: 0 });

  await t.test('binds IPv4 loopback and reports the exact model snapshot', async () => {
    assert.equal(preview.server.address().address, '127.0.0.1');
    assert.equal(preview.url, `http://127.0.0.1:${preview.port}/`);
    assert.equal(preview.bytes, original.length);
    assert.equal(preview.sha256, createHash('sha256').update(original).digest('hex'));
    const result = await request(preview, '/model.glb');
    assert.equal(result.status, 200);
    assert.equal(result.headers['content-type'], 'model/gltf-binary');
    assert.deepEqual(result.data, original);
    assert.deepEqual(await readFile(input), original, 'source file remains byte-for-byte unchanged');
  });

  await t.test('provides HTML, Korean controls, metadata and locally installed Three modules', async () => {
    const html = await request(preview, '/');
    assert.equal(html.status, 200);
    assert.match(html.data.toString(), /애니메이션/);
    assert.match(html.data.toString(), /원작 게임 화면과의 동일성은 별도 검증 대상/);
    assert.doesNotMatch(html.data.toString(), /https?:\/\//);
    assert.doesNotMatch(html.data.toString(), /__CSP_NONCE__/);
    const nonce = html.data.toString().match(/nonce="([^"]+)"/)[1];
    assert.ok(html.headers['content-security-policy'].includes(`'nonce-${nonce}'`));
    assert.ok(html.headers['content-security-policy'].includes("connect-src 'self' blob:"));
    assert.ok(html.headers['content-security-policy'].includes("frame-ancestors 'none'"));
    const metadata = await request(preview, '/api/model');
    assert.deepEqual(JSON.parse(metadata.data), { name: 'sample.glb', bytes: original.length, scenes: 1, meshes: 0, materials: 0, skins: 0, animationCount: 0, animationNames: [] });
    const assets = [
      '/app.js', '/pica-player.js', '/pica-runtime.js', '/synchronized-player.js', '/style.css', '/vendor/three/build/three.module.js', '/vendor/three/build/three.core.js',
      '/fan.js', '/fan-state.js', '/fan.css',
      '/vendor/three/examples/jsm/loaders/GLTFLoader.js',
      '/vendor/three/examples/jsm/controls/OrbitControls.js',
      '/vendor/three/examples/jsm/utils/BufferGeometryUtils.js',
      '/vendor/three/examples/jsm/utils/SkeletonUtils.js',
    ];
    for (const route of assets) {
      const result = await request(preview, route);
      assert.equal(result.status, 200, route);
      assert.ok(result.data.length > 0, route);
      assert.equal(result.headers['x-content-type-options'], 'nosniff');
      if (route.endsWith('.js')) {
        // Check the installed version's actual static import closure, not only the entry files.
        for (const match of result.data.toString().matchAll(/^import\s+[^;]*?\s+from\s+['"]([^'"]+)['"]/gm)) {
          const dependency = match[1] === 'three'
            ? '/vendor/three/build/three.module.js'
            : new URL(match[1], `${preview.url.slice(0, -1)}${route}`).pathname;
          assert.equal((await request(preview, dependency)).status, 200, `${route} imports ${dependency}`);
        }
      }
    }
    const fan=await request(preview,'/fan');
    assert.equal(fan.status,200);assert.match(fan.data.toString(),/새잎 여행/);
    assert.match(fan.data.toString(),/팬게임 UI 시제품/);assert.doesNotMatch(fan.data.toString(),/__CSP_NONCE__/);
    const fanNonce=fan.data.toString().match(/nonce="([^"]+)"/)[1];
    assert.ok(fan.headers['content-security-policy'].includes(`'nonce-${fanNonce}'`));
  });

  await t.test('rejects traversal, queries, listings and every unlisted file', async () => {
    for (const route of [
      '/../../package.json', '/%2e%2e/package.json', '/vendor/three/../../package.json',
      '/%2f..%2fpackage.json', '/..\\package.json', '/vendor/three/',
      '/vendor/three/package.json', '/node_modules/three/package.json', '/package.json',
      '/model.glb?file=other.glb', '/api/model?path=secret', '/index.html', '/missing',
    ]) {
      assert.equal((await request(preview, route)).status, 404, route);
    }
  });

  await t.test('rejects cross-origin, foreign-host and mutation requests', async () => {
    assert.equal((await request(preview, '/model.glb', { headers: { Host: 'attacker.example' } })).status, 403);
    assert.equal((await request(preview, '/api/model', { headers: { Origin: 'https://attacker.example' } })).status, 403);
    assert.equal((await request(preview, '/api/model', { headers: { Origin: `http://127.0.0.1:${preview.port}` } })).status, 200);
    assert.equal((await request(preview, '/model.glb', { method: 'POST' })).status, 405);
    const head = await request(preview, '/model.glb', { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(head.data.length, 0);
    assert.equal(Number(head.headers['content-length']), original.length);
  });

  await t.test('serves the stable read-only snapshot even when the caller later changes its input', async () => {
    await writeFile(input, 'caller changed the file after startup');
    assert.deepEqual((await request(preview, '/model.glb')).data, original);
    assert.equal((await readFile(input, 'utf8')), 'caller changed the file after startup');
  });
});

test('preview validates input paths, ports, GLB header/chunks and external dependencies before listening', async t => {
  const directory = await mkdtemp(path.join(tmpdir(), 'rea3d-preview-invalid-'));
  t.after(() => removeOwnTemporary(directory));
  const input = path.join(directory, 'invalid.glb');
  await assert.rejects(startPreview({ input: 'relative.glb' }), /absolute/);
  await assert.rejects(startPreview({ input: path.join(directory, 'asset.gltf') }), /\.glb/);
  await assert.rejects(startPreview({ input, port: -1 }), /port/);
  await assert.rejects(startPreview({ input, port: 1.2 }), /port/);
  await writeFile(input, Buffer.alloc(30));
  await assert.rejects(startPreview({ input }), /magic/);
  let corrupt = makeGlb();
  corrupt.writeUInt32LE(1, 4);
  await writeFile(input, corrupt);
  await assert.rejects(startPreview({ input }), /version/);
  corrupt = makeGlb();
  corrupt.writeUInt32LE(corrupt.length - 4, 8);
  await writeFile(input, corrupt);
  await assert.rejects(startPreview({ input }), /declared length/);
  corrupt = makeGlb();
  corrupt.writeUInt32LE(corrupt.length + 4, 12);
  await writeFile(input, corrupt);
  await assert.rejects(startPreview({ input }), /chunk length/);
  for (const uri of ['https://example.com/image.png', 'file:///private/model.bin', '../texture.png']) {
    await writeFile(input, makeGlb({ asset: { version: '2.0' }, images: [{ uri }] }));
    await assert.rejects(startPreview({ input }), /External resource/);
  }
});
