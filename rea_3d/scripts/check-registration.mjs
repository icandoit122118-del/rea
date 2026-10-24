import { promises as fs } from 'node:fs';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ROOT, PYTHON, runProcess } from '../src/bridge.mjs';

// Read and start exactly the new registration, never the personal server catalog.
const read = await runProcess(PYTHON, ['-I', '-c', "import json,tomllib; from pathlib import Path; d=tomllib.loads(Path('C:/Users/USER/.codex/config.toml').read_text(encoding='utf-8-sig')); e=d['mcp_servers']['rea_3d']; print(json.dumps({k:e[k] for k in ('command','args','cwd')}))"]);
if (read.code !== 0) throw new Error('New registration could not be read');
const entry = JSON.parse(read.stdout);
const client = new Client({ name: 'rea3d-registration-readback-verifier', version: '1.0.0' }, { capabilities: {} });
const transport = new StdioClientTransport({ ...entry, stderr: 'pipe' });
try {
  await client.connect(transport);
  const catalog = await client.listTools();
  const answer = await client.callTool({ name: 'rea3d_capabilities', arguments: {} });
  const data = answer.structuredContent ?? JSON.parse(answer.content[0].text);
  if (!data.ok || catalog.tools.length !== 6) throw new Error('Registered server verification failed');
  const result = { verified_at: new Date().toISOString(), registration: 'rea_3d', execution: 'official_sdk_from_registered_command', server: client.getServerVersion(), tool_names: catalog.tools.map(t => t.name), capability_call_ok: data.ok, current_conversation_native_tool_exposure: 'not_verified', other_mcp_servers_started: false, receipt: data.receipt };
  await fs.writeFile(path.join(ROOT, 'evidence/registered-mcp-verification.json'), JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
} finally { await client.close(); }
