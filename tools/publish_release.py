"""Publish official GitHub Release with binary assets."""
import os
import json
import subprocess
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
        'tag_name': 'v0.2.0-beta.24',
        'target_commitish': 'main',
        'name': 'Local Agent AI Station 0.2.0-beta.24',
        'body': 'Release v0.2.0-beta.24: Non-Blocking GPU Load Telemetry & Emergency Stop Controls Unblocked.\n\n- Stop buttons (Model, Server, Agent) remain active under heavy GPU compute.\n- Emergency stop worker bypasses busy lock.\n- smi and HTTP unload timeouts optimized for fast fallback.',
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

    for fname in os.listdir(release_dir):
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

    print('ALL ASSETS UPLOADED SUCCESSFULLY!')

if __name__ == '__main__':
    main()
