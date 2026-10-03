import os
import json
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    input_data = 'protocol=https\nhost=github.com\n\n'
    res = subprocess.run(['git', 'credential', 'fill'], input=input_data, capture_output=True, text=True, check=True)
    token = None
    for line in res.stdout.splitlines():
        if line.startswith('password='):
            token = line.split('=', 1)[1]

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'LAAS-Publisher/1.0'
    }

    url = 'https://api.github.com/repos/Wave-is/laas/releases/tags/v0.2.0-beta.24'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        rel = json.loads(resp.read().decode('utf-8'))

    upload_base = rel['upload_url'].split('{')[0]

    for a in rel.get('assets', []):
        name = a['name']
        asset_id = a['id']
        print(f"Deleting asset: {name} ({asset_id})")
        del_req = urllib.request.Request(f"https://api.github.com/repos/Wave-is/laas/releases/assets/{asset_id}", headers=headers, method='DELETE')
        try:
            with urllib.request.urlopen(del_req):
                print(f"Deleted {name}")
        except Exception as e:
            print(f"Delete failed for {name}: {e}")

    release_dir = os.path.join(ROOT, 'dist', 'release')
    target_files = [f for f in os.listdir(release_dir) if '0.2.0-beta.24' in f or f in ('BUILD.json', 'SHA256SUMS.txt')]

    for fname in target_files:
        fpath = os.path.join(release_dir, fname)
        if not os.path.isfile(fpath):
            continue
        print(f"Uploading {fname} ({os.path.getsize(fpath)} bytes)...")
        up_headers = dict(headers)
        up_headers['Content-Type'] = 'application/octet-stream'
        with open(fpath, 'rb') as stream:
            data = stream.read()
        up_req = urllib.request.Request(f"{upload_base}?name={fname}", data=data, headers=up_headers, method='POST')
        with urllib.request.urlopen(up_req) as up_resp:
            asset_info = json.loads(up_resp.read().decode('utf-8'))
            print(f"Successfully uploaded {fname}! ID: {asset_info['id']}")

    print("DONE SYNCING RELEASE ASSETS FOR BETA 24!")

if __name__ == '__main__':
    main()
