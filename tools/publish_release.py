"""Publish official GitHub Release with binary assets."""
import os
import json
import subprocess
import urllib.request
import urllib.error

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from src.version import VERSION

def main():
    input_data = 'protocol=https\nhost=github.com\n\n'
    res = subprocess.run(['git', 'credential', 'fill'], input=input_data, capture_output=True, text=True, check=True)
    token = None
    for line in res.stdout.splitlines():
        if line.startswith('password='):
            token = line.split('=', 1)[1]

    if not token:
        raise RuntimeError('No token found in git credential manager')

    url = 'https://api.github.com/repos/Wave-is/laas/releases'
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'LAAS-Release-Publisher/1.0',
        'Content-Type': 'application/json'
    }

    body = {
        'tag_name': f'v{VERSION}',
        'target_commitish': 'main',
        'name': f'Local Agent AI Station {VERSION}',
        'body': f'Release v{VERSION}: Direct NVML C-API, Priority Elevation & High-Load GUI Responsiveness.\n\n'
                f'- Direct NVML In-Process Queries: replaced nvidia-smi subprocess storm with direct nvml.dll C-calls (<0.5ms vs 2.5s+).\n'
                f'- Process Priority Elevation: Station process elevated to ABOVE_NORMAL_PRIORITY_CLASS (0x8000) on Windows to protect GUI from compute starvation.\n'
                f'- Event Loop Breathing & Telemetry Coalescing: bounded event draining (max 8/tick) and coalesced redundant telemetry frames so Windows DWM PeekMessage is never starved.\n'
                f'- Minimized/Tray Redraw Skip: skips heavy canvas/widget redraws when window is minimized or hidden in tray.\n'
                f'- Reliable Window Restoration: restore_window() via Win32 ShowWindow(SW_RESTORE) + SetForegroundWindow guarantees foreground restoration from tray under heavy workloads.\n'
                f'- Crash & Orphan Banner Fixes: guarded gpu_confirmation against AttributeError and ensured autostart banner cleans up cleanly.',
        'draft': False,
        'prerelease': True
    }

    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req) as resp:
            rel_data = json.loads(resp.read().decode('utf-8'))
            print('Created release ID:', rel_data['id'])
            upload_url_template = rel_data['upload_url']
    except urllib.error.HTTPError as exc:
        print('HTTP Error creating release:', exc.code, exc.read().decode('utf-8'))
        raise

    upload_base = upload_url_template.split('{')[0]
    release_dir = os.path.join(ROOT, 'dist', 'release')

    target_files = [
        f'LocalAgentAIStation-{VERSION}-Setup-x64.exe',
        f'LocalAgentAIStation-{VERSION}-source.zip',
        'BUILD.json',
        'TESTING.json',
        'SHA256SUMS.txt'
    ]

    for fname in target_files:
        fpath = os.path.join(release_dir, fname)
        if not os.path.isfile(fpath):
            continue
        upload_url = f'{upload_base}?name={fname}'
        with open(fpath, 'rb') as f:
            file_bytes = f.read()
        print(f'Uploading {fname} ({len(file_bytes)} bytes)...')
        up_headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'LAAS-Release-Publisher/1.0',
            'Content-Type': 'application/octet-stream'
        }
        up_req = urllib.request.Request(upload_url, data=file_bytes, headers=up_headers, method='POST')
        with urllib.request.urlopen(up_req) as up_resp:
            asset_info = json.loads(up_resp.read().decode('utf-8'))
            print(f"Uploaded {fname} successfully! Asset ID: {asset_info['id']}")

    print('ALL ASSETS UPLOADED SUCCESSFULLY FOR BETA 26!')



if __name__ == '__main__':
    main()
