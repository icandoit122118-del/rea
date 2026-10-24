import test from 'node:test';
import assert from 'node:assert/strict';
import { deflateSync } from 'node:zlib';
import vm from 'node:vm';
import { parseArgs, run, decodePng, pixelDifference, glbOracle, installGlProbe } from '../scripts/verify-pica-browser.mjs';

// Tiny independent fixture encoder; screenshots are decoded by the harness itself.
function png(rgb) {
  const chunk = (name, data) => {
    const result = Buffer.alloc(data.length + 12);
    result.writeUInt32BE(data.length); result.write(name, 4, 'ascii'); data.copy(result, 8);
    return result;
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(2); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.from([0, ...rgb]))), chunk('IEND', Buffer.alloc(0))]);
}

test('browser launch requires explicit authorization before any filesystem or import action', async () => {
  await assert.rejects(run({ input: '/missing.glb', output: '/missing-output' }), /explicit task-specific/);
  assert.equal(parseArgs(['--preflight']).authorized, false);
  assert.equal(parseArgs(['--authorized-isolated-browser', '--ab']).authorized, true);
  assert.throws(() => parseArgs(['--input']), /Missing/);
  assert.throws(() => parseArgs(['--connect-existing-browser']), /Unsupported/);
});

test('pixel comparison reports the exact changed pixel and preserves equal images', () => {
  const first = png([20, 30, 40, 100, 110, 120]), second = png([20, 30, 40, 100, 111, 120]);
  assert.deepEqual(pixelDifference(first, second), { changedPixels: 1, maximumChannelDelta: 1, bboxTopLeft: [1, 0, 1, 0] });
  assert.equal(pixelDifference(first, first).changedPixels, 0);
  assert.deepEqual([...decodePng(first).pixels], [20, 30, 40, 255, 100, 110, 120, 255]);
  assert.throws(() => decodePng(Buffer.from('invalid')), /Not PNG/);
});

test('source quaternion oracle interpolates GLB binary source values and clamps endpoints', () => {
  const binary = Buffer.alloc(40); [0, 1, 0, 0, 0, 1, 0, 0, 1, 0].forEach((value, index) => binary.writeFloatLE(value, index * 4));
  const document = { nodes: [{ name: 'Jaw' }], bufferViews: [{ byteOffset: 0 }, { byteOffset: 8 }], accessors: [{ bufferView: 0, componentType: 5126, type: 'SCALAR', count: 2 }, { bufferView: 1, componentType: 5126, type: 'VEC4', count: 2 }], animations: [{ channels: [{ sampler: 0, target: { node: 0, path: 'rotation' } }], samplers: [{ input: 0, output: 1, interpolation: 'LINEAR' }] }] };
  let json = Buffer.from(JSON.stringify(document)); json = Buffer.concat([json, Buffer.alloc((4 - json.length % 4) % 4, 32)]);
  const fixture = Buffer.alloc(12 + 8 + json.length + 8 + binary.length); fixture.write('glTF'); fixture.writeUInt32LE(2, 4); fixture.writeUInt32LE(fixture.length, 8);
  fixture.writeUInt32LE(json.length, 12); fixture.writeUInt32LE(0x4e4f534a, 16); json.copy(fixture, 20);
  fixture.writeUInt32LE(binary.length, 20 + json.length); fixture.writeUInt32LE(0x004e4942, 24 + json.length); binary.copy(fixture, 28 + json.length);
  const oracle = glbOracle(fixture); assert.equal(oracle.duration(0), 1);
  assert.deepEqual(oracle.rotation(0, 'Jaw', -1), [0, 0, 0, 1]);
  assert.deepEqual(oracle.rotation(0, 'Jaw', 2), [0, 0, 1, 0]);
  const half = oracle.rotation(0, 'Jaw', .5); assert.ok(Math.abs(half[2] - Math.SQRT1_2) < 1e-12); assert.ok(Math.abs(half[3] - Math.SQRT1_2) < 1e-12);
  assert.throws(() => oracle.rotation(0, 'missing', 0), /No source/);
});

test('draw probe reads the actual driver uniform with the draw object tag, independently of uploaded cache', () => {
  const driverUv = [2, 0, 0, 0, 1, 0, -2, 0, 1], shader = {}, program = {}, location = {};
  class FakeGL {
    constructor() { this.CURRENT_PROGRAM = 1; this.FRAMEBUFFER_BINDING = 2; this.SHADER_TYPE = 3; this.COMPILE_STATUS = 4; this.LINK_STATUS = 5; }
    getUniformLocation() { return location; }
    getUniform() { return driverUv; }
    uniformMatrix3fv() {}
    getParameter(key) { return key === 1 ? program : null; }
    drawElements() {}
    compileShader() {}
    getShaderParameter(_shader, key) { return key === this.COMPILE_STATUS; }
    getShaderInfoLog() { return ''; }
    linkProgram() {}
    getProgramParameter() { return true; }
    getProgramInfoLog() { return ''; }
    getExtension() { return null; }
    getError() { return 0; }
  }
  class Canvas { getContext() { return gl; } }
  const gl = new FakeGL(), sandbox = { HTMLCanvasElement: Canvas, window: { WebGLRenderingContext: FakeGL }, Float32Array, Uint8Array, WeakMap, Set, Array, Object, Math, queueMicrotask };
  vm.runInNewContext(`(${installGlProbe.toString()})()`, sandbox);
  const returned = new Canvas().getContext('webgl2'); assert.equal(returned, gl);
  gl.compileShader(shader); gl.linkProgram(program); gl.getUniformLocation(program, 'uUvMatrix0');
  gl.uniformMatrix3fv(location, false, [99, 0, 0, 0, 99, 0, 0, 0, 1]);
  sandbox.window.__REA_PICA_DRAW_OBSERVER__({ mesh: 'mesh3', material: 'Eye', source_node: 'EyeSkin', expected_uv: [driverUv] });
  gl.drawElements();
  const record = sandbox.window.__REA_GL_PROBE__.snapshot().drawSamples[0];
  assert.equal(record.tag.material, 'Eye'); assert.equal(record.tag.mesh, 'mesh3');
  assert.deepEqual(Array.from(record.actualUv.uUvMatrix0), driverUv);
  assert.equal(record.uniforms.uUvMatrix0.values[0], 99);
});
