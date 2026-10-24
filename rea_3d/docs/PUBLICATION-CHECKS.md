# Source publication checks — 2026-10-24

These are the initial publication checks before the Iris blend correction. Current fix and follow-up regression results, including the separately retried optional REA integration test, are in [IRIS-BLENDING-FIX.ko.md](IRIS-BLENDING-FIX.ko.md).

These checks ran on the separate source-publication snapshot, after replacing personal paths and making private original-asset tests opt-in. The active MCP project was not changed.

| Check | Current result |
| --- | --- |
| Node test runner | 85 passed, 0 failed, 0 skipped |
| Python unittest discovery | 94 total, 89 passed, 5 skipped, 0 failures/errors |
| Official MCP SDK synthetic demo | Real GARC extraction, inspection and GLB export; 2 bones, 1 animation |
| Khronos validator on new synthetic GLB | 0 errors, 0 warnings |
| Fresh project-local Python installation | Official 3.12.10 x64 ZIP downloaded, pinned SHA256 matched, no existing runtime copied |
| CLI using fresh runtime | capabilities succeeded, Python backend ready |

Skipped Python coverage remains explicitly skipped; it is not original-asset verification. The optional original members directory must be explicitly selected with REA3D_ORAS_MEMBERS_DIR. The official REA CLI integration test ran on this host because that separate CLI is installed; it skips on hosts without it.

The actual 8-species browser, shader-state and 13-condition performance results predate publication packaging and are summarized separately in VALIDATION-20261024.ko.md. Packaging checks did not repeat or resolve original-game rendering parity, the known Iris blend omission, or blocked multi-species source extraction.

Generated fixtures, test transcripts, downloaded runtimes, dependencies, original game files and local evidence are excluded from Git. Vendored reader files retain their original bytes and license.
