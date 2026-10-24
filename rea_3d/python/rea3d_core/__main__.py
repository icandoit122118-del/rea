"""JSON-file command protocol. SPDX-License-Identifier: GPL-3.0-or-later."""
import argparse
import json
from pathlib import Path
import sys

from .bridge import AssetError, dispatch


def main():
    parser = argparse.ArgumentParser(description="Headless 3DS inspection and GLB export")
    parser.add_argument("--request", required=True, help="Absolute path to a JSON request")
    ns = parser.parse_args()
    method = None
    try:
        path = Path(ns.request)
        if not path.is_absolute() or path.stat().st_size > 1024 * 1024:
            raise AssetError("INVALID_REQUEST", "request must be an absolute JSON path under 1 MiB")
        request = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict) or not isinstance(request.get("args", {}), dict):
            raise AssetError("INVALID_REQUEST", "expected an object with method and object args")
        method = request.get("method")
        result = dispatch(method, request.get("args", {}))
        response = {"ok": True, "method": method, "result": result}
        code = 0
    except Exception as exc:
        response = {"ok": False, "method": method,
                    "error": {"code": getattr(exc, "code", "CONVERSION_FAILED"), "message": str(exc)}}
        code = 1
    # ensure_ascii keeps the protocol safe under Windows console encoding defaults.
    print(json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
