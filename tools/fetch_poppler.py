"""
tools/fetch_poppler.py
One-time setup: downloads a portable Poppler build for Windows (provides
pdftoppm.exe) into tools/poppler/bin. Not vendored in git (91MB) - run this
once per machine before using dd1391_parser.py's image fallback.
"""

import io
import json
import os
import shutil
import urllib.request
import zipfile

RELEASES_API = "https://api.github.com/repos/oschwartz10612/poppler-windows/releases/latest"
DEST = os.path.join(os.path.dirname(__file__), "poppler")


def main():
    if os.path.exists(os.path.join(DEST, "bin", "pdftoppm.exe")):
        print(f"Already installed: {DEST}\\bin\\pdftoppm.exe")
        return

    print("Looking up latest poppler-windows release...")
    with urllib.request.urlopen(RELEASES_API) as resp:
        release = json.load(resp)

    zip_asset = next(a for a in release["assets"] if a["name"].endswith(".zip"))
    print(f"Downloading {zip_asset['name']} ({release['tag_name']})...")
    with urllib.request.urlopen(zip_asset["browser_download_url"]) as resp:
        data = resp.read()

    print("Extracting...")
    extract_dir = os.path.join(os.path.dirname(__file__), "_poppler_extract_tmp")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(extract_dir)

    # Zip contains a single top-level "poppler-<version>/Library/bin" dir.
    version_dir = next(
        os.path.join(extract_dir, d) for d in os.listdir(extract_dir)
        if os.path.isdir(os.path.join(extract_dir, d))
    )
    src_bin = os.path.join(version_dir, "Library", "bin")

    os.makedirs(DEST, exist_ok=True)
    shutil.copytree(src_bin, os.path.join(DEST, "bin"), dirs_exist_ok=True)
    shutil.rmtree(extract_dir)

    print(f"Installed: {DEST}\\bin\\pdftoppm.exe")


if __name__ == "__main__":
    main()
