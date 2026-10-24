/** Dedicated isolated Chromium QA. Requires the task-specific human exception.
 * Never connects to TUS, CDP endpoints, existing browser sessions or user profiles.
 * Browser launch is opt-in; --preflight performs Node/filesystem checks only.
 */
import path from 'node:path';
import { readFile, writeFile, mkdir, stat } from 'node:fs/promises';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { startPreview } from '../src/preview-server.mjs';
import { Quaternion } from 'three';

export const AUX_ROOT = 'D:/REA3D-ORAS-AUX-20261023-03';
const DEFAULT_PLAYWRIGHT = 'C:/browser-qa/node_modules/playwright-core/index.mjs';
const DEFAULT_CHROMIUM = 'C:/Users/USER/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe';
const sha = data => createHash('sha256').update(data).digest('hex');
const project = fileURLToPath(new URL('../', import.meta.url));

export function parseArgs(args) {
  const options = { playwright: DEFAULT_PLAYWRIGHT, chromium: DEFAULT_CHROMIUM, authorized: false, preflight: false, ab: false };
  for (let index = 0; index < args.length; index++) {
    const key = args[index];
    if (key === '--authorized-isolated-browser') options.authorized = true;
    else if (key === '--preflight') options.preflight = true;
    else if (key === '--ab') options.ab = true;
    else if (['--input', '--output-dir', '--playwright', '--chromium'].includes(key)) {
      const value = args[++index];
      if (!value || value.startsWith('--')) throw new Error(`Missing ${key} value.`);
      options[{ '--input': 'input', '--output-dir': 'output', '--playwright': 'playwright', '--chromium': 'chromium' }[key]] = value;
    } else throw new Error(`Unsupported argument: ${key}`);
  }
  return options;
}

export function decodePng(data) {
  if (!data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error('Not PNG.');
  let offset = 8, width, height, channels;
  const chunks = [];
  while (offset + 12 <= data.length) {
    const length = data.readUInt32BE(offset), kind = data.toString('ascii', offset + 4, offset + 8);
    const content = data.subarray(offset + 8, offset + 8 + length);
    if (content.length !== length) throw new Error('Truncated PNG.');
    offset += length + 12;
    if (kind === 'IHDR') {
      width = content.readUInt32BE(0); height = content.readUInt32BE(4);
      if (content[8] !== 8 || ![2, 6].includes(content[9]) || content[12]) throw new Error('Unsupported PNG layout.');
      channels = content[9] === 6 ? 4 : 3;
    } else if (kind === 'IDAT') chunks.push(content);
    else if (kind === 'IEND') break;
  }
  if (!width || !height || width * height > 16_777_216) throw new Error('Unbounded PNG.');
  const stride = width * channels, raw = inflateSync(Buffer.concat(chunks), { maxOutputLength: (stride + 1) * height });
  const pixels = Buffer.alloc(width * height * 4);
  let previous = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const mode = raw[y * (stride + 1)], row = Buffer.from(raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)));
    if (mode > 4) throw new Error('Unsupported PNG filter.');
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? row[x - channels] : 0, b = previous[x], c = x >= channels ? previous[x - channels] : 0;
      let predictor = 0;
      if (mode === 1) predictor = a;
      else if (mode === 2) predictor = b;
      else if (mode === 3) predictor = Math.floor((a + b) / 2);
      else if (mode === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); predictor = pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      row[x] = (row[x] + predictor) & 255;
    }
    for (let x = 0; x < width; x++) { row.copy(pixels, (y * width + x) * 4, x * channels, x * channels + 3); pixels[(y * width + x) * 4 + 3] = channels === 4 ? row[x * channels + 3] : 255; }
    previous = row;
  }
  return { width, height, pixels };
}

export function pixelDifference(first, second) {
  const a = decodePng(first), b = decodePng(second);
  if (a.width !== b.width || a.height !== b.height) throw new Error('PNG sizes differ.');
  let changedPixels = 0, maximum = 0, minimumX = a.width, minimumY = a.height, maximumX = -1, maximumY = -1;
  for (let index = 0; index < a.pixels.length; index += 4) {
    let changed = false;
    for (let channel = 0; channel < 4; channel++) { const difference = Math.abs(a.pixels[index + channel] - b.pixels[index + channel]); maximum = Math.max(maximum, difference); changed ||= difference !== 0; }
    if (changed) { changedPixels++; const pixel = index / 4, x = pixel % a.width, y = Math.floor(pixel / a.width); minimumX = Math.min(minimumX, x); maximumX = Math.max(maximumX, x); minimumY = Math.min(minimumY, y); maximumY = Math.max(maximumY, y); }
  }
  return { changedPixels, maximumChannelDelta: maximum, bboxTopLeft: changedPixels ? [minimumX, minimumY, maximumX, maximumY] : null };
}

export function glbOracle(data) {
  const document = JSON.parse(data.subarray(20, 20 + data.readUInt32LE(12)).toString('utf8'));
  let offset = 12, binary;
  while (offset < data.length) { const length = data.readUInt32LE(offset), kind = data.readUInt32LE(offset + 4); if (kind === 0x004e4942) binary = data.subarray(offset + 8, offset + 8 + length); offset += length + 8; }
  const cache = new Map();
  const accessor = index => {
    if (cache.has(index)) return cache.get(index);
    const item = document.accessors[index], view = document.bufferViews[item.bufferView], count = { SCALAR: 1, VEC3: 3, VEC4: 4 }[item.type];
    if (item.componentType !== 5126 || !count) throw new Error('Expected float animation accessor.');
    const start = (view.byteOffset || 0) + (item.byteOffset || 0), stride = view.byteStride || count * 4;
    const values = Array.from({ length: item.count }, (_, row) => Array.from({ length: count }, (_, column) => binary.readFloatLE(start + row * stride + column * 4)));
    cache.set(index, values); return values;
  };
  const duration = index => Math.max(0, ...document.animations[index].samplers.map(sampler => accessor(sampler.input).at(-1)[0]));
  return { document, duration, rotation(index, boneName, seconds) {
    const clip = document.animations[index], channel = clip.channels.find(channel => channel.target.path === 'rotation' && document.nodes[channel.target.node].name === boneName);
    if (!channel) throw new Error(`No source auxiliary rotation for ${boneName}`);
    const sampler = clip.samplers[channel.sampler], times = accessor(sampler.input).map(value => value[0]), values = accessor(sampler.output);
    let result;
    if (seconds <= times[0]) result = values[0];
    else if (seconds >= times.at(-1)) result = values.at(-1);
    else { const right = times.findIndex(value => value > seconds), left = right - 1, amount = (seconds - times[left]) / (times[right] - times[left]); result = new Quaternion().slerpQuaternions(new Quaternion(...values[left]), new Quaternion(...values[right]), amount).toArray(); }
    return new Quaternion(...result).normalize().toArray();
  } };
}

/** Runs before app scripts only in this new test page. No renderer state changes. */
export function installGlProbe() {
  const contexts = [], shaderEvents = [], links = [], uniforms = new WeakMap(), programs = new WeakMap();
  let drawCount = 0, programCounter = 0, armed = false, pending = false, readback = null, drawSamples = [], drawTag = null;
  const id = program => { if (!programs.has(program)) programs.set(program, { id: ++programCounter, uniforms: {}, locations: {} }); return programs.get(program); };
  // A task-only, read-only callback identifies the object about to issue its draw.
  // Uniform values below are independently read from the actual WebGL driver.
  window.__REA_PICA_DRAW_OBSERVER__ = tag => { drawTag = { ...tag, expected_uv: tag.expected_uv.map(values => [...values]) }; };
  const hash = values => { let result = 2166136261; const bytes = new Uint8Array(new Float32Array(values).buffer); for (const value of bytes) result = Math.imul(result ^ value, 16777619) >>> 0; return result.toString(16).padStart(8, '0'); };
  const canvasGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (...args) {
    const context = canvasGetContext.apply(this, args);
    if (context && /^webgl/.test(args[0]) && !contexts.includes(context)) contexts.push(context);
    return context;
  };
  const prototypes = [window.WebGLRenderingContext?.prototype, window.WebGL2RenderingContext?.prototype].filter(Boolean);
  for (const prototype of prototypes) {
    const wrap = (name, after) => { const original = prototype[name]; if (typeof original !== 'function') return; prototype[name] = function (...args) { const result = original.apply(this, args); after.call(this, args, result); return result; }; };
    wrap('compileShader', function ([shader]) { shaderEvents.push({ type: this.getShaderParameter(shader, this.SHADER_TYPE), ok: this.getShaderParameter(shader, this.COMPILE_STATUS), log: this.getShaderInfoLog(shader) }); });
    wrap('linkProgram', function ([program]) { links.push({ program: id(program).id, ok: this.getProgramParameter(program, this.LINK_STATUS), log: this.getProgramInfoLog(program) }); });
    wrap('getUniformLocation', function ([program, name], location) { if (location) { uniforms.set(location, { program, name }); id(program).locations[name] = location; } });
    for (const method of ['uniformMatrix3fv', 'uniformMatrix4fv']) wrap(method, function ([location, transpose, values]) {
      const binding = uniforms.get(location); if (!binding) return;
      if (/uBoneMatrices|uUvMatrix|uBindMatrix/.test(binding.name)) id(binding.program).uniforms[binding.name] = { length: values.length, hash: hash(values), values: values.length <= 16 ? Array.from(values) : Array.from(values).slice(0, 16) };
    });
    const drawn = function () {
      drawCount++;
      const program = this.getParameter(this.CURRENT_PROGRAM);
      if (program) {
        const record = id(program), actualUv = {};
        for (const [name, location] of Object.entries(record.locations)) if (/^uUvMatrix[012]$/.test(name)) actualUv[name] = Array.from(this.getUniform(program, location));
        drawSamples.push({ drawCount, programId: record.id, tag: drawTag, actualUv, uniforms: { ...record.uniforms } });
        if (drawSamples.length > 24) drawSamples.shift();
      }
      if (armed && !pending && this.getParameter(this.FRAMEBUFFER_BINDING) === null) {
        pending = true;
        const gl = this;
        queueMicrotask(() => {
          const width = gl.drawingBufferWidth, height = gl.drawingBufferHeight, pixels = new Uint8Array(width * height * 4);
          gl.finish(); gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
          let nonzero = 0; for (let index = 0; index < pixels.length; index += 4) nonzero += pixels[index] || pixels[index + 1] || pixels[index + 2] ? 1 : 0;
          readback = { width, height, bytes: pixels.length, hash: hash(new Uint8Array(pixels)), nonzeroRgbPixels: nonzero, glError: gl.getError() };
          armed = false; pending = false;
        });
      }
    };
    wrap('drawElements', drawn); wrap('drawArrays', drawn);
  }
  Object.defineProperty(window, '__REA_GL_PROBE__', { value: {
    arm() { armed = true; readback = null; },
    snapshot() {
      const context = contexts[0], extension = context?.getExtension('WEBGL_debug_renderer_info');
      return { drawCount, shaderEvents, links, readback, drawSamples, contexts: contexts.map(gl => ({ version: gl.getParameter(gl.VERSION), shadingLanguage: gl.getParameter(gl.SHADING_LANGUAGE_VERSION), vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER), unmaskedVendor: extension ? gl.getParameter(extension.UNMASKED_VENDOR_WEBGL) : null, unmaskedRenderer: extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : null, error: gl.getError() })), programs: Array.from({ length: programCounter }, (_, index) => index + 1), uniformStates: links.map(link => link.program) };
    },
    getUniformStates() { return Array.from(new Set(links.map(link => link.program))).map(programId => ({ programId, uniforms: programRecords().find(record => record.id === programId)?.uniforms || {} })); },
    resetDrawSamples() { drawSamples = []; },
  } });
  // WeakMap cannot be enumerated; retain only the program records created here.
  const recordList = [];
  const oldSet = programs.set.bind(programs);
  programs.set = (key, value) => { recordList.push(value); return oldSet(key, value); };
  function programRecords() { return recordList; }
}

export async function preflight(options) {
  const results = { execution: 'filesystem_and_node_import_only', browserLaunched: false, paths: {} };
  for (const [name, filename] of Object.entries({ chromium: options.chromium, playwright: options.playwright })) {
    const information = await stat(filename); results.paths[name] = { path: filename, bytes: information.size, exists: information.isFile() };
  }
  const playwright = await import(pathToFileURL(path.resolve(options.playwright)).href);
  results.playwrightImport = Boolean(playwright.chromium?.launchPersistentContext);
  return results;
}

export async function run(options) {
  if (options.preflight) return preflight(options);
  if (!options.authorized) throw new Error('Browser execution requires the explicit task-specific isolated Chromium authorization flag.');
  if (!options.input || !path.isAbsolute(options.input) || !options.output || !path.isAbsolute(options.output)) throw new Error('Absolute --input and --output-dir are required.');
  const output = path.resolve(options.output), auxiliary = path.resolve(AUX_ROOT);
  if (output === auxiliary || !output.startsWith(auxiliary + path.sep)) throw new Error('Output must stay in the task-specific D: AUX root.');
  await mkdir(output, { recursive: false });
  await mkdir(path.join(output, 'tmp'));
  const receipt = { schema: 'rea-three-browser-qa-v1', ok: false, execution: 'actual_isolated_headless_chromium_real_three_app', input: { path: options.input, sha256Before: sha(await readFile(options.input)) }, source: {}, console: [], errors: [], requestsBlocked: [], captures: [], checks: [], exceptions: ['User explicitly allowed isolated Chromium for this verification only.'], browserUserProfile: path.join(output, 'browser-profile'), boundaries: { tusAccess: false, existingChromeProfiles: false, browserToolNativeMcp: false, originalGameVisualParity: false } };
  for (const name of ['preview/app.js', 'preview/pica-player.js', 'preview/pica-runtime.js', 'preview/synchronized-player.js', 'preview/index.html', 'src/preview-server.mjs']) receipt.source[name] = sha(await readFile(path.join(project, name)));
  const oracle = glbOracle(await readFile(options.input));
  let preview, context, page;
  const started = Date.now();
  try {
    const playwright = await import(pathToFileURL(path.resolve(options.playwright)).href);
    preview = await startPreview({ input: options.input, port: 0 });
    receipt.localUrl = preview.url;
    context = await playwright.chromium.launchPersistentContext(receipt.browserUserProfile, {
      executablePath: options.chromium, headless: true, viewport: { width: 1100, height: 850 }, deviceScaleFactor: 1,
      args: ['--use-angle=d3d11', '--enable-webgl', '--ignore-gpu-blocklist'],
      env: { ...process.env, TEMP: path.join(output, 'tmp'), TMP: path.join(output, 'tmp') },
    });
    receipt.browserVersion = context.browser()?.version() || null;
    page = context.pages()[0] || await context.newPage();
    await page.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(preview.url) || url.startsWith('blob:') || url.startsWith('data:')) return route.continue();
      receipt.requestsBlocked.push(url); return route.abort('blockedbyclient');
    });
    page.on('console', message => { if (message.type() === 'error' || message.type() === 'warning') receipt.console.push({ type: message.type(), text: message.text() }); });
    page.on('pageerror', error => receipt.errors.push(error.message));
    await page.addInitScript(installGlProbe);
    await page.goto(preview.url, { waitUntil: 'networkidle', timeout: 30_000 });
    await page.waitForFunction(() => document.getElementById('clip')?.disabled === false || document.getElementById('status')?.classList.contains('error'), { timeout: 30_000 });
    const status = await page.locator('#status').textContent();
    if (status) throw new Error(`Player failed to become ready: ${status}`);
    const inspect = async () => page.evaluate(async () => {
      const app = await import('/app.js');
      return { typed: typeof app.inspectPlayback === 'function' ? await app.inspectPlayback() : null, timeline: document.getElementById('timeline')?.value, timeLabel: document.getElementById('time-value')?.textContent, playback: document.getElementById('playback')?.textContent, selectedClip: document.getElementById('clip')?.value, auxiliaryPresent: Boolean(document.getElementById('auxiliary')), probe: window.__REA_GL_PROBE__.snapshot(), uniformStates: window.__REA_GL_PROBE__.getUniformStates() };
    });
    const capture = async name => {
      await page.evaluate(() => window.__REA_GL_PROBE__.arm());
      await page.waitForFunction(() => Boolean(window.__REA_GL_PROBE__.snapshot().readback), { timeout: 10_000 });
      const file = path.join(output, `${name}.png`), image = await page.locator('#viewport canvas').screenshot({ path: file });
      const record = { name, png: { path: file, bytes: image.length, sha256: sha(image) }, state: await inspect() };
      receipt.captures.push(record); return record;
    };
    const seek = async seconds => {
      await page.locator('#timeline').evaluate((element, value) => { element.value = String(value); element.dispatchEvent(new Event('input', { bubbles: true })); }, seconds);
      await page.waitForTimeout(80);
    };
    receipt.loaded = await inspect();
    await page.locator('#pause').click();
    const pausedA = await inspect(); await page.waitForTimeout(180); const pausedB = await inspect();
    receipt.checks.push({ name: 'pause keeps app action time stable', passed: pausedA.timeline === pausedB.timeline, before: pausedA.timeline, after: pausedB.timeline });
    await page.locator('#clip').selectOption('11');
    await seek(0); const open = await capture('eye-ui-000');
    await seek(10 / 30); const half = await capture('eye-ui-010');
    await seek(15 / 30); const closed = await capture('eye-ui-015');
    receipt.pixelComparisons = [
      { names: [open.name, half.name], ...pixelDifference(await readFile(open.png.path), await readFile(half.png.path)) },
      { names: [open.name, closed.name], ...pixelDifference(await readFile(open.png.path), await readFile(closed.png.path)) },
    ];
    receipt.checks.push({ name: 'seek changes actual canvas pixels', passed: receipt.pixelComparisons.every(pair => pair.changedPixels > 0) });
    await page.locator('#speed').focus(); await page.locator('#speed').press('ArrowRight'); await page.locator('#speed').press('ArrowRight');
    receipt.speedValue = await page.locator('#speed').inputValue();
    await seek(0); await page.locator('#play').click();
    receipt.playingSamples = await page.evaluate(async () => {
      const app = await import('/app.js'), samples = [], start = performance.now();
      await new Promise((resolve, reject) => {
        const tick = () => {
          const state = app.inspectPlayback();
          if (!samples.length || state.render_frame > samples.at(-1).render_frame) samples.push(state);
          if (samples.length >= 4 && new Set(samples.map(sample => sample.playback.time_seconds)).size >= 3) resolve();
          else if (performance.now() - start > 5000) reject(new Error('No distinct real RAF action times within five seconds.'));
          else requestAnimationFrame(tick);
        }; requestAnimationFrame(tick);
      }); return samples;
    });
    receipt.checks.push({ name: 'play advances real RAF action time including loops', passed: receipt.playingSamples.length >= 3 && receipt.playingSamples.every((sample, index, samples) => sample.playback.playing && (!index || sample.render_frame > samples[index - 1].render_frame)) && new Set(receipt.playingSamples.map(sample => sample.playback.time_seconds)).size >= 3, times: receipt.playingSamples.map(sample => sample.playback.time_seconds), renderFrames: receipt.playingSamples.map(sample => sample.render_frame) });
    await page.locator('#pause').click(); await page.locator('#clip').selectOption('5'); await seek(0); const vinesHidden = await capture('vine-ui-000');
    await seek(10 / 30); const vinesShown = await capture('vine-ui-010');
    receipt.visibilitySamples = [vinesHidden.state.typed, vinesShown.state.typed];
    receipt.visibilityPixelDifference = pixelDifference(await readFile(vinesHidden.png.path), await readFile(vinesShown.png.path));
    if (await page.locator('#auxiliary').count()) {
      await page.locator('#clip').selectOption('11'); await seek(.5);
      const baseline = await inspect(); receipt.auxiliary = { baseline, samples: [] };
      const options = await page.locator('#auxiliary option').evaluateAll(elements => elements.map(element => element.value));
      for (const auxiliaryIndex of ['9', '29'].filter(value => options.includes(value))) {
        await page.locator('#auxiliary').selectOption(auxiliaryIndex); await seek(.5);
        const selected = await capture(`auxiliary-${auxiliaryIndex}`);
        const before = new Map((baseline.typed?.bones || []).map(bone => [bone.name, bone]));
        const changes = { translation: [], rotation: [], scale: [] };
        for (const bone of selected.state.typed?.bones || []) {
          const prior = before.get(bone.name); if (!prior) continue;
          for (const key of Object.keys(changes)) if (JSON.stringify(prior[key]) !== JSON.stringify(bone[key])) changes[key].push(bone.name);
        }
        receipt.auxiliary.samples.push({ index: Number(auxiliaryIndex), changes, state: selected.state.typed });
        receipt.checks.push({ name: `auxiliary ${auxiliaryIndex} preserves all source translation and scale`, passed: changes.translation.length === 0 && changes.scale.length === 0 });
        await page.locator('#auxiliary').selectOption(''); await seek(.5);
      }
      receipt.realtimeAuxiliary = [];
      for (const auxiliaryIndex of ['9', '29'].filter(value => options.includes(value))) {
        await page.locator('#clip').selectOption('5'); await page.locator('#auxiliary').selectOption(auxiliaryIndex);
        await page.locator('#speed').evaluate(element => { element.value = '1'; element.dispatchEvent(new Event('input', { bubbles: true })); });
        await seek(0);
        const initial = await inspect();
        await page.locator('#play').click();
        const live = await page.evaluate(async () => {
          const app = await import('/app.js'), canvas = document.querySelector('#viewport canvas');
          const types = ['video/webm;codecs=vp8', 'video/webm;codecs=vp9', 'video/webm'];
          const mimeType = typeof MediaRecorder !== 'undefined' ? types.find(type => MediaRecorder.isTypeSupported(type)) : null;
          let recorder, stream, recordingError = null; const chunks = [];
          if (mimeType && typeof canvas.captureStream === 'function') {
            stream = canvas.captureStream(30); recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 2_000_000 });
            recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
            recorder.onerror = event => { recordingError = event.error?.message || String(event.error || event); };
            await new Promise(resolve => { recorder.onstart = resolve; recorder.start(100); });
            stream.getVideoTracks()[0]?.requestFrame?.();
          }
          const samples = [], captures = [], start = performance.now(); let next = 0, nextVideoFrame = 0;
          await new Promise(resolve => {
            const tick = () => {
              const elapsed = performance.now() - start;
              if (elapsed >= nextVideoFrame) { stream?.getVideoTracks()[0]?.requestFrame?.(); nextVideoFrame += 1000 / 30; }
              if (elapsed >= next) {
                const state = app.inspectPlayback(), visual = state.playback?.visual_state;
                const compact = { ...state, playback: state.playback ? { ...state.playback, visual_state: visual ? { frame: visual.frame, clip_name: visual.clip_name, model_visible: visual.model_visible, visibility: visual.visibility, material_states: Object.fromEntries(Object.entries(visual.material_states || {}).map(([name, material]) => [name, { texture_names: material.texture_names, uv_matrices: material.uv_matrices, colors: material.colors }])) } : null } : null };
                samples.push({ elapsedMilliseconds: elapsed, state: compact, drawSamples: window.__REA_GL_PROBE__.snapshot().drawSamples });
                if ([1, 6, 11, 14].includes(samples.length)) captures.push({ sampleIndex: samples.length - 1, elapsedMilliseconds: elapsed, timeSeconds: state.playback.time_seconds, sourceFrame: state.playback.frame, renderFrame: state.render_frame, dataUrl: canvas.toDataURL('image/png') });
                next += 150;
              }
              if (elapsed >= 2000) resolve(); else requestAnimationFrame(tick);
            }; requestAnimationFrame(tick);
          });
          let recording = null;
          if (recorder) {
            await new Promise(resolve => { recorder.onstop = resolve; recorder.stop(); });
            for (const track of stream.getTracks()) track.stop();
            const bytes = new Uint8Array(await new Blob(chunks, { type: mimeType }).arrayBuffer()); let binary = ''; for (let offset = 0; offset < bytes.length; offset += 16384) binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
            recording = { mimeType, bytes: bytes.length, base64: btoa(binary), requestedFramesPerSecond: 30, error: recordingError };
          }
          return { samples, captures, recording };
        });
        await page.locator('#pause').click();
        const observed = { index: Number(auxiliaryIndex), initial, samples: live.samples, validation: [] };
        for (const sample of live.samples) {
          const state = sample.state, playback = state.playback, bones = new Map(state.bones.map(bone => [bone.name, bone]));
          const mask = playback.auxiliary_mask || [], rotationChecks = mask.map(target => {
            const expected = oracle.rotation(Number(auxiliaryIndex), target.bone, playback.auxiliary_time_seconds), actual = bones.get(target.bone)?.rotation;
            const difference = actual ? Math.min(Math.max(...actual.map((value, index) => Math.abs(value - expected[index]))), Math.max(...actual.map((value, index) => Math.abs(value + expected[index])))) : Infinity;
            return { bone: target.bone, sourcePath: target.path, maximumQuaternionError: difference, passed: target.path === 'rotation' && difference < 1e-5 };
          });
          const uvChecks = sample.drawSamples.filter(draw => draw.tag).map(draw => {
            const units = Object.entries(draw.actualUv).map(([name, actual]) => {
              const expected = draw.tag.expected_uv[Number(name.at(-1))], maximumError = Math.max(...actual.map((value, index) => Math.abs(value - expected[index])));
              return { name, maximumError, passed: maximumError < 2e-6 };
            });
            return { drawCount: draw.drawCount, programId: draw.programId, mesh: draw.tag.mesh, sourceNode: draw.tag.source_node, material: draw.tag.material, units, passed: units.length > 0 && units.every(unit => unit.passed) };
          });
          observed.validation.push({ renderFrame: state.render_frame, timeSeconds: playback.time_seconds, primaryFrame: playback.frame, sourceVisualFrame: playback.visual_state?.frame, auxiliaryTimeSeconds: playback.auxiliary_time_seconds, auxiliaryDuration: oracle.duration(Number(auxiliaryIndex)), frameSynchronized: Math.abs(playback.frame - playback.visual_state.frame) < 1e-8, auxiliaryTimeSynchronized: Math.abs(playback.auxiliary_time_seconds - Math.min(playback.time_seconds, oracle.duration(Number(auxiliaryIndex)))) < 1e-6, rotationChecks, actualGlUvMatchesSource: uvChecks.some(draw => draw.material === 'Eye') && uvChecks.every(draw => draw.passed), matchingDrawCount: uvChecks.filter(draw => draw.passed).length, uvChecks, glError: state.gl_error, visibleSourceNodes: state.actual_materials.meshes.filter(mesh => mesh.visible).map(mesh => mesh.source_node) });
        }
        observed.liveCaptures = [];
        for (const capture of live.captures) {
          const bytes = Buffer.from(capture.dataUrl.split(',')[1], 'base64'), filename = path.join(output, `simultaneous-auxiliary-${auxiliaryIndex}-sample-${String(capture.sampleIndex).padStart(2, '0')}.png`);
          await writeFile(filename, bytes, { flag: 'wx' });
          const { dataUrl, ...metadata } = capture; observed.liveCaptures.push({ ...metadata, path: filename, bytes: bytes.length, sha256: sha(bytes) });
        }
        if (live.recording?.bytes > 0) {
          const bytes = Buffer.from(live.recording.base64, 'base64'), filename = path.join(output, `simultaneous-auxiliary-${auxiliaryIndex}.webm`);
          await writeFile(filename, bytes, { flag: 'wx' }); observed.recording = { path: filename, bytes: bytes.length, sha256: sha(bytes), mimeType: live.recording.mimeType, requestedFramesPerSecond: live.recording.requestedFramesPerSecond, error: live.recording.error };
        }
        else observed.recording = { supported: Boolean(live.recording), bytes: live.recording?.bytes || 0, error: live.recording?.error || 'Recorder produced no video data; real RAF samples and current-canvas PNGs remain available.' };
        observed.finalCapture = await capture(`realtime-auxiliary-${auxiliaryIndex}-end`);
        receipt.realtimeAuxiliary.push(observed);
        receipt.checks.push({ name: `auxiliary ${auxiliaryIndex} runs at least three distinct real RAF times`, passed: new Set(live.samples.map(sample => sample.state.playback.time_seconds)).size >= 3 && live.samples.every(sample => sample.state.playback.playing) });
        receipt.checks.push({ name: `auxiliary ${auxiliaryIndex} synchronizes source visual state, source masked quaternions and actual GL UV uploads`, passed: observed.validation.every(sample => sample.frameSynchronized && sample.auxiliaryTimeSynchronized && sample.rotationChecks.length === 4 && sample.rotationChecks.every(check => check.passed) && sample.actualGlUvMatchesSource && sample.glError === 0) });
        await page.locator('#auxiliary').selectOption('');
      }
    }
    await page.locator('#stop').click(); await page.waitForTimeout(60); const stopped = await inspect();
    receipt.checks.push({ name: 'stop returns to time zero', passed: Number(stopped.timeline) === 0, time: stopped.timeline });
    if (options.ab) {
      const available = await page.locator('#material-mode').count() > 0;
      receipt.ab = { available, captures: [] };
      if (available) {
        await seek(0);
        for (const mode of ['fallback', 'source']) {
          await page.locator('#material-mode').selectOption(mode);
          receipt.ab.captures.push(await capture(`ab-${mode}`));
        }
        receipt.ab.pixelDifference = pixelDifference(await readFile(receipt.ab.captures[0].png.path), await readFile(receipt.ab.captures[1].png.path));
      }
    }
    const final = await inspect(); receipt.final = final;
    receipt.checks.push({ name: 'all real WebGL shaders compiled', passed: final.probe.shaderEvents.length > 0 && final.probe.shaderEvents.every(event => event.ok) });
    receipt.checks.push({ name: 'all real WebGL programs linked', passed: final.probe.links.length > 0 && final.probe.links.every(event => event.ok) });
    receipt.checks.push({ name: 'actual framebuffer readback is nonempty', passed: receipt.captures.every(capture => capture.state.probe.readback?.nonzeroRgbPixels > 0) });
    receipt.checks.push({ name: 'no page exceptions', passed: receipt.errors.length === 0 });
    receipt.input.sha256After = sha(await readFile(options.input)); receipt.input.unchanged = receipt.input.sha256Before === receipt.input.sha256After;
    receipt.ok = receipt.checks.every(check => check.passed) && receipt.input.unchanged;
  } catch (error) {
    receipt.failure = { message: error.message, stack: error.stack };
    if (page && !page.isClosed()) {
      try {
        receipt.failure.probe = await page.evaluate(() => window.__REA_GL_PROBE__?.snapshot() || null);
        const filename = path.join(output, 'failure-page.png'), pixels = await page.screenshot({ path: filename });
        receipt.failure.png = { path: filename, bytes: pixels.length, sha256: sha(pixels) };
      } catch (captureError) { receipt.failure.captureError = captureError.message; }
    }
  }
  finally {
    if (context) await context.close();
    if (preview) { preview.server.closeAllConnections(); await new Promise(resolve => preview.server.close(resolve)); }
    receipt.elapsedSeconds = (Date.now() - started) / 1000;
    const filename = path.join(output, 'browser-qa-receipt.json'); await writeFile(filename, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
    console.log(JSON.stringify({ ok: receipt.ok, receipt: filename, failure: receipt.failure?.message, checks: receipt.checks }));
  }
  return receipt;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  try { const result = await run(parseArgs(process.argv.slice(2))); if (result.ok === false) process.exitCode = 1; else if (result.execution === 'filesystem_and_node_import_only') console.log(JSON.stringify(result)); }
  catch (error) { console.error(JSON.stringify({ ok: false, error: error.message })); process.exitCode = 1; }
}
