# Standalone companion scope

This folder is an independently launched GPL companion, not an integrated provider or release artifact of the parent REA runtime. Keep inputs caller-selected, outputs new, source assets separate and unsupported content explicit. Distinguish synthetic parser tests, original-asset extraction, actual browser execution, shader-state parity and original-game pixel comparison.

Run checks from this folder. `npm run verify` runs local Node/Python tests; `node scripts/make-demo.mjs` exercises the companion with generated synthetic data. Optional official REA CLI coverage requires a separately installed REA. Do not run browser, game, registration or target-execution scripts without the corresponding task authorization. Never commit runtime binaries, game files, credentials, generated browser profiles or private evidence.
