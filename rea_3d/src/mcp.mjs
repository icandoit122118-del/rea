#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema, McpError, ErrorCode } from '@modelcontextprotocol/sdk/types.js';
import { execute } from './operations.mjs';

const absoluteFile = { type: 'string', description: 'Absolute local path to a caller-selected regular asset file.' };
const newDirectory = { type: 'string', description: 'Absolute path to a new output directory. Existing directories are refused.' };
const schema = (properties = {}, required = []) => ({ type: 'object', properties, required, additionalProperties: false });
export const TOOLS = [
  { name: 'rea3d_capabilities', method: 'capabilities', description: 'Report actual 3DS parser/export capabilities, runtime availability, and unverified boundaries.', inputSchema: schema(), readOnly: true },
  { name: 'rea3d_inspect', method: 'inspect', description: 'Inspect a selected raw 3DS asset/PC/PS container. Reports mesh, skeleton, texture, and skeletal animation inventory; does not launch a game.', inputSchema: schema({ path: absoluteFile }, ['path']), readOnly: true },
  { name: 'rea3d_export', method: 'export', description: 'Decode a selected 3DS model and explicitly selected companion files; write GLB/PNG and provenance into a NEW directory. Original-game rendering parity is not claimed.', inputSchema: schema({ path: absoluteFile, output_dir: newDirectory, motion_paths: { type: 'array', items: absoluteFile, maxItems: 128 }, texture_paths: { type: 'array', items: absoluteFile, maxItems: 128 }, fps: { type: 'number', minimum: 1, maximum: 240, description: 'Source frame-rate interpretation, default 30. Converts source frame numbers to seconds; changing this changes clip duration. This is not a resampling rate.' }, model_index: { type: 'integer', minimum: 0 } }, ['path', 'output_dir']), readOnly: false },
  { name: 'rea3d_archive_inspect', method: 'archive_inspect', description: 'Read a GARC archive table with strict bounds. Archive inventory only; no model restoration claim.', inputSchema: schema({ path: absoluteFile }, ['path']), readOnly: true },
  { name: 'rea3d_archive_extract', method: 'archive_extract', description: 'Extract a GARC archive to a NEW directory with per-file SHA256 hashes. No overwrite, ROM decryption, or game execution.', inputSchema: schema({ path: absoluteFile, output_dir: newDirectory }, ['path', 'output_dir']), readOnly: false },
  { name: 'rea3d_analyze_code', method: 'rea_analyze_code', description: 'Use the installed official REA CLI to statically analyze a selected JavaScript directory and retain its complete Evidence JSON. Separate from 3D conversion.', inputSchema: schema({ path: { type: 'string', description: 'Absolute path to a caller-selected JavaScript application directory.' } }, ['path']), readOnly: true },
];

const server = new Server({ name: 'rea-3d-lab', version: '0.1.0' }, { capabilities: { tools: {} }, instructions: 'Local 3DS asset inspection and conversion companion. Read capabilities first. Require explicit input paths and new output directories. Results distinguish archive extraction, model conversion, original-game evidence, and live-game execution. Never infer game-runtime success from these tools.' });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS.map(({ method, readOnly, ...tool }) => ({ ...tool, annotations: { readOnlyHint: readOnly, destructiveHint: false, idempotentHint: readOnly, openWorldHint: false } })) }));
server.setRequestHandler(CallToolRequestSchema, async request => {
  const tool = TOOLS.find(tool => tool.name === request.params.name);
  if (!tool) throw new McpError(ErrorCode.MethodNotFound, 'Unknown rea3d tool');
  const result = await execute(tool.method, request.params.arguments ?? {});
  const encoded = JSON.stringify(result);
  const delivery = Buffer.byteLength(encoded) > 512 * 1024
    ? { ok: result.ok, method: result.method, error: result.error, receipt: result.receipt, result_saved_in_receipt: true }
    : result;
  return { content: [{ type: 'text', text: JSON.stringify(delivery, null, 2) }], structuredContent: delivery, isError: !result.ok };
});
await server.connect(new StdioServerTransport());
