"""Publish release assets to GitHub via REST API."""
import json
import mimetypes
from pathlib import Path
import subprocess
import sys
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.version import VERSION

TAG = f"v{VERSION}"
REPO = "Wave-is/laas"


def get_github_token():
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input=f"url=https://github.com/{REPO}.git\n",
        text=True,
        capture_output=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError("Could not retrieve GitHub token from git credentials")


def main():
    token = get_github_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Extract current version release notes from CHANGELOG.md
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = ""
    lines = changelog.splitlines()
    capturing = False
    for line in lines:
        if line.startswith(f"## {VERSION}"):
            capturing = True
            continue
        elif capturing and line.startswith("## "):
            break
        elif capturing:
            section += line + "\n"

    release_body = f"## What's Changed in {VERSION}\n\n" + section.strip()

    # Check if release already exists
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", headers=headers)
    if r.status_code == 200:
        rel = r.json()
        release_id = rel["id"]
        upload_url_template = rel["upload_url"]
        print(f"Release {TAG} already exists (ID: {release_id})")
    elif r.status_code == 404:
        print(f"Creating release {TAG}...")
        payload = {
            "tag_name": TAG,
            "target_commitish": "main",
            "name": f"Local Agent AI Station {VERSION}",
            "body": release_body,
            "draft": False,
            "prerelease": True,
        }
        r = requests.post(f"https://api.github.com/repos/{REPO}/releases", headers=headers, json=payload)
        r.raise_for_status()
        rel = r.json()
        release_id = rel["id"]
        upload_url_template = rel["upload_url"]
        print(f"Created release {TAG} (ID: {release_id})")
    else:
        r.raise_for_status()

    # Upload URL base
    upload_url_base = upload_url_template.split("{", 1)[0]

    # Existing assets
    r_assets = requests.get(f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets", headers=headers)
    r_assets.raise_for_status()
    existing_assets = {a["name"]: a["id"] for a in r_assets.json()}

    release_dir = ROOT / "dist" / "release"
    asset_files = [
        release_dir / f"LocalAgentAIStation-{VERSION}-Setup-x64.exe",
        release_dir / f"LocalAgentAIStation-{VERSION}-source.zip",
        release_dir / "BUILD.json",
        release_dir / "SHA256SUMS.txt",
    ]

    uploaded = []
    for asset_path in asset_files:
        if not asset_path.exists():
            print(f"Warning: {asset_path} not found, skipping.")
            continue

        asset_name = asset_path.name
        if asset_name in existing_assets:
            print(f"Deleting existing asset {asset_name} (ID {existing_assets[asset_name]})...")
            del_r = requests.delete(
                f"https://api.github.com/repos/{REPO}/releases/assets/{existing_assets[asset_name]}",
                headers=headers,
            )
            del_r.raise_for_status()

        print(f"Uploading {asset_name} ({asset_path.stat().st_size:,} bytes)...")
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        }
        with open(asset_path, "rb") as f:
            up_r = requests.post(
                f"{upload_url_base}?name={asset_name}",
                headers=upload_headers,
                data=f,
            )
            up_r.raise_for_status()
        uploaded.append(asset_name)
        print(f"Uploaded {asset_name} successfully.")

    # Record snapshot in handoff-local
    record = {
        "url": f"https://github.com/{REPO}/releases/tag/{TAG}",
        "id": release_id,
        "tag": TAG,
        "private": False,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "assets_verified": uploaded,
    }
    hl = ROOT / "handoff-local"
    if hl.exists():
        (hl / "github-release.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print("\nRelease publication complete!")
    print(f"URL: {record['url']}")
    print(f"Assets: {', '.join(uploaded)}")


if __name__ == "__main__":
    main()
