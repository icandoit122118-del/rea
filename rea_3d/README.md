# rea_3d

Local Nintendo 3DS asset inspection, GLB export and a Three.js material/animation player. This standalone GPL-3.0-or-later companion is separate from the parent REA runtime: installing it does not add a provider to REA or load an MCP server into an existing client.

**[한국어 설치·사용 안내](README.ko.md)** · **[Verification and limitations](docs/VALIDATION-20261024.ko.md)** · **[Provenance](PROVENANCE.md)**

Initial source publication: branch `codex/rea-3d-initial-20261024`, folder `rea_3d` in `icandoit122118-del/rea`. Current packaging checks are recorded in [PUBLICATION-CHECKS.md](docs/PUBLICATION-CHECKS.md).

## Windows quick start

Requires Node.js >=22.19.0 and npm. From this `rea_3d` folder:

```powershell
npm ci --ignore-scripts
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup-runtime.ps1
node src/cli.mjs capabilities
npm run verify
node scripts/make-demo.mjs
```

The runtime setup downloads the pinned Python 3.12.10 embeddable archive from python.org, verifies its SHA256, and creates only this project's `runtime/python` directory. It does not install a global interpreter. The synthetic demo generates its own model, texture and motion; no game files are distributed here.

## MCP and CLI

Run `node src/mcp.mjs` as an MCP stdio server. See `mcp-config.json` for a path template. For ordinary shell use:

```powershell
node src/cli.mjs inspect --input C:/assets/model.pc
node src/cli.mjs export --input C:/assets/model.pc --motion C:/assets/motion.pc --out C:/assets/new-export
node src/preview-server.mjs --input C:/assets/new-export/model.glb --port 0
```

Use caller-selected absolute input paths and a new output directory. The player serves local Three.js modules on loopback; it does not use an external CDN or automatically open a browser. The optional `rea3d_analyze_code` operation requires a separately installed official REA CLI. Asset inspection/export does not require that operation.

## Current verification boundary

- Existing converted GLBs: 8 species passed actual browser compatibility checks; 47 clips were inventoried, not exhaustively played.
- Playback performance: 13 conditions / 39 runs, including 6 and 12 copies of one model, on Ryzen 5 5600 / RX 9060 XT / isolated Chromium D3D11. These measurements describe the pre-blend-fix build. Approximately 60Hz observed scheduling is not a maximum-FPS or phone benchmark.
- Iris blend correction: source RGB and alpha factors/equations are now applied separately with CustomBlending. Actual draw-state verification confirms the required LIris/RIris alpha blending and unchanged depth-write behavior. See the [fix and playback verification](docs/IRIS-BLENDING-FIX.ko.md). Original external shaders, lighting, outlines and pixel parity remain incomplete.
- Multi-species ORAS source extraction was blocked by local source-storage read failures. Converted-GLB compatibility is not evidence of parser generality.
- No ROMs, extracted game models, textures, gameplay recordings, credentials or browser profiles are included. Private raw validation evidence is retained locally; this repository includes a summarized report and measurements.

Some historical QA scripts contain example workstation paths or require separately installed Playwright, Chromium, Blender or native WGL packages. They are optional developer harnesses; configure their explicit input/output arguments before using them. The core MCP/CLI uses the project-local Python runtime.

GPL-3.0-or-later; vendored reader sources and their original license are retained under `vendor/n3ds_importer`.
