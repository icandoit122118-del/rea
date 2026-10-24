import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ROOT, PYTHON, runProcess } from '../src/bridge.mjs';
import validator from 'gltf-validator';
import { createFixture } from '../scripts/fixture.mjs';

test('official MCP SDK performs real initialize/list/inspect/export and rejects overwrite', async (context) => {
  await fs.mkdir(path.join(ROOT, '.tmp'), { recursive: true });
  const temporary = await fs.mkdtemp(path.join(ROOT, '.tmp/mcp-'));
  const { model, motion, archive } = await createFixture(path.join(temporary, 'source'));
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(ROOT, 'src/mcp.mjs')], cwd: ROOT, stderr: 'pipe' });
  const client = new Client({ name: 'rea3d-official-sdk-verifier', version: '1.0.0' }, { capabilities: {} });
  const calls = [];
  try {
    await client.connect(transport);
    assert.equal(client.getServerVersion().name, 'rea-3d-lab');
    const catalog = await client.listTools();
    assert.equal(catalog.tools.length, 6);
    async function call(name, args) {
      const answer = await client.callTool({ name, arguments: args });
      const data = answer.structuredContent ?? JSON.parse(answer.content[0].text);
      calls.push({ name, isError: answer.isError ?? false, ok: data.ok, receipt: data.receipt });
      return { answer, data };
    }
    const caps = await call('rea3d_capabilities', {});
    assert.equal(caps.data.ok, true);
    assert.equal(caps.data.result.asset_backend.gui_required, false);
    const archiveInfo = await call('rea3d_archive_inspect', { path: archive });
    assert.equal(archiveInfo.data.result.fileCount, 2);
    const extracted = await call('rea3d_archive_extract', { path: archive, output_dir: path.join(temporary, 'members') });
    assert.equal(extracted.data.ok, true);
    assert.equal(extracted.data.result.decodedModels, false);
    const extractedModel = path.join(temporary, 'members/00000_00.bin');
    const extractedMotion = path.join(temporary, 'members/00001_00.bin');
    assert.deepEqual(await fs.readFile(extractedModel), await fs.readFile(model));
    assert.deepEqual(await fs.readFile(extractedMotion), await fs.readFile(motion));
    const inspected = await call('rea3d_inspect', { path: extractedModel });
    assert.equal(inspected.data.result.models[0].bones, 2);
    const exported = await call('rea3d_export', { path: extractedModel, motion_paths: [extractedMotion], output_dir: path.join(temporary, 'export') });
    assert.equal(exported.data.ok, true, JSON.stringify(exported.data));
    assert.equal(exported.data.result.animation_count, 1);
    assert.equal(exported.data.result.bone_count, 2);
    assert.equal(exported.data.result.animation_export_complete, true);
    const glb = await fs.readFile(exported.data.result.glb_path);
    const validation = await validator.validateBytes(new Uint8Array(glb), { uri: 'mcp-synthetic.glb', maxIssues: 100 });
    await fs.writeFile(path.join(temporary, 'gltf-validator.json'), JSON.stringify(validation, null, 2));
    assert.equal(validation.issues.numErrors, 0, JSON.stringify(validation.issues));
    const duplicate = await call('rea3d_export', { path: model, output_dir: path.join(temporary, 'export') });
    assert.equal(duplicate.answer.isError, true);
    assert.equal(duplicate.data.error.code, 'output_exists');
    assert.deepEqual(await fs.readFile(exported.data.result.glb_path), glb);
    const wrong = await call('rea3d_inspect', { path: 'relative.pc' });
    assert.equal(wrong.answer.isError, true);
    assert.equal(wrong.data.ok, false);
    const codeFixture = path.join(temporary, 'code');
    await fs.mkdir(codeFixture);
    await fs.writeFile(path.join(codeFixture, 'package.json'), JSON.stringify({ name: 'rea3d-synthetic-code', type: 'module', main: 'index.js' }));
    await fs.writeFile(path.join(codeFixture, 'index.js'), 'export function add(a,b){ return a+b; }\n');
    await context.test('optional separately installed official REA CLI', {skip:caps.data.result.installed_rea_cli_available ? false : 'Official REA CLI is not installed; asset workflows were tested separately'}, async () => {
      const analyzed = await call('rea3d_analyze_code', { path: codeFixture });
      assert.equal(analyzed.data.ok, true, JSON.stringify(analyzed.data));
      const rea = JSON.parse(await fs.readFile(analyzed.data.result.evidence_path, 'utf8'));
      assert.equal(rea.operation, 'analyze_javascript_application');
      assert.equal(rea.normalized_result.statistics.parse_failures, 0);
    });
    await fs.writeFile(path.join(ROOT, 'evidence/mcp-sdk-verification.json'), JSON.stringify({ measured_at: new Date().toISOString(), transport: 'official_sdk_stdio', server: client.getServerVersion(), pid: transport.pid, tool_names: catalog.tools.map(t => t.name), calls, original_game_assets: false, gltf_validator: validation.issues, glb_path: exported.data.result.glb_path }, null, 2));
  } finally { await client.close(); }
});
