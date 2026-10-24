#!/usr/bin/env node
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { execute } from './operations.mjs';

const help = `REA 3D Lab - isolated companion, no production modifications
Usage:
  node src/cli.mjs --file ABSOLUTE_REQUEST.json
  node src/cli.mjs capabilities
  node src/cli.mjs inspect --input ABSOLUTE_ASSET
  node src/cli.mjs export --input ABSOLUTE_MODEL --out NEW_ABSOLUTE_DIR [--motion ABS_FILE] [--texture ABS_FILE]
  node src/cli.mjs archive-inspect --input ABSOLUTE_GARC
  node src/cli.mjs archive-extract --input ABSOLUTE_GARC --out NEW_ABSOLUTE_DIR
  node src/cli.mjs rea-analyze-code --input ABSOLUTE_CODE_DIR
JSON: {"version":1,"method":"inspect","args":{"path":"C:/assets/model.bin"}}
MCP: node src/mcp.mjs
`;
async function main(argv) {
  if (!argv.length || argv.includes('--help')) { process.stdout.write(help); return; }
  let request;
  if (argv[0] === '--file') {
    if (argv.length !== 2 || !path.isAbsolute(argv[1])) throw new Error('--file requires one absolute JSON file path');
    if ((await fs.stat(argv[1])).size > 1024 * 1024) throw new Error('Request exceeds 1 MiB');
    request = JSON.parse(await fs.readFile(argv[1], 'utf8'));
    if (request.version !== 1) throw new Error('Request version must be 1');
  } else {
    request = { version: 1, method: argv[0].replaceAll('-', '_'), args: {} };
    const mapping = { '--input': 'path', '--out': 'output_dir', '--motion': 'motion_paths', '--texture': 'texture_paths', '--fps': 'fps', '--model-index': 'model_index' };
    for (let i = 1; i < argv.length; i += 2) {
      const name = mapping[argv[i]], value = argv[i + 1];
      if (!name || value === undefined) throw new Error(`Invalid argument: ${argv[i]}`);
      if (name.endsWith('_paths')) (request.args[name] ??= []).push(value);
      else if (name === 'fps' || name === 'model_index') request.args[name] = Number(value);
      else request.args[name] = value;
    }
  }
  const response = await execute(request.method, request.args ?? {});
  process.stdout.write(`${JSON.stringify(response, null, 2)}\n`);
  if (!response.ok) process.exitCode = 1;
}
main(process.argv.slice(2)).catch(e => { process.stdout.write(`${JSON.stringify({ ok: false, error: { code: 'invalid_request', message: e.message } })}\n`); process.exitCode = 1; });
