"""Register only this companion, preserving all prior configuration bytes.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomllib

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path.home()/'.codex/config.toml'
NODE = Path(shutil.which('node') or 'node')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    ns = parser.parse_args()
    original = CONFIG.read_bytes()
    parsed = tomllib.loads(original.decode('utf-8-sig'))
    entry = {'command': NODE.as_posix(), 'args': [(ROOT/'src/mcp.mjs').as_posix()],
             'cwd': ROOT.as_posix(), 'enabled': True,
             'startup_timeout_sec': 30, 'tool_timeout_sec': 200}
    current = parsed.get('mcp_servers', {}).get('rea_3d')
    if current is not None:
        if current != entry:
            raise ValueError('rea_3d already exists with different settings; no changes made')
        print(json.dumps({'registered': True, 'changed': False, 'server': 'rea_3d'}))
        return
    addition = '\n\n# REA 3D Lab - isolated local asset tools\n[mcp_servers.rea_3d]\n'
    addition += '\n'.join(f'{key} = {json.dumps(value)}' for key, value in entry.items()) + '\n'
    updated = original + addition.encode('utf-8')
    after = tomllib.loads(updated.decode('utf-8-sig'))
    restored = json.loads(json.dumps(after))
    del restored['mcp_servers']['rea_3d']
    if restored != parsed:
        raise ValueError('Unrelated parsed settings would change')
    plan = {'server': 'rea_3d', 'config': str(CONFIG), 'entry': entry,
            'previous_server_count': len(parsed.get('mcp_servers', {})),
            'previous_bytes_preserved': updated.startswith(original),
            'all_other_settings_preserved': restored == parsed, 'apply': ns.apply}
    (ROOT/'evidence').mkdir(exist_ok=True)
    if ns.apply:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = CONFIG.with_name(CONFIG.name + '.rea3d.backup-' + timestamp)
        staged = CONFIG.with_name(CONFIG.name + '.rea3d.staged-' + timestamp)
        if CONFIG.read_bytes() != original:
            raise ValueError('Concurrent config change before backup; retry from fresh state')
        shutil.copy2(CONFIG, backup)
        with staged.open('xb') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        if CONFIG.read_bytes() != original:
            staged.unlink()
            raise ValueError('Concurrent config change before replacement; no registration applied')
        os.replace(staged, CONFIG)
        saved = CONFIG.read_bytes()
        if saved != updated or tomllib.loads(saved.decode('utf-8-sig')) != after:
            raise ValueError('Config readback mismatch; original backup retained')
        plan.update({'registered': True, 'backup': str(backup),
                     'before_sha256': hashlib.sha256(original).hexdigest(),
                     'after_sha256': hashlib.sha256(saved).hexdigest(),
                     'current_session_native_tools': 'requires_client_reload_or_reconnect'})
        (ROOT/'evidence/codex-registration.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
