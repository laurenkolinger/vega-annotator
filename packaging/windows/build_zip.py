#!/usr/bin/env python3
"""Build dist/VEGAAnnotator-<version>-Windows.zip, the Windows installer.

The zip holds the app and the install scripts. It needs nothing compiled, so a Mac can
build it. The person installing it unzips it and double-clicks Install VEGA Annotator.cmd.

Run: python3 packaging/windows/build_zip.py
"""

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from vega_annotator import VERSION  # noqa: E402

TOP_FOLDER = "VEGA Annotator Installer"
INCLUDED_FOLDERS = ("vega_annotator", "vendor", "requirements")
SCRIPTS = ("Install VEGA Annotator.cmd", "setup.ps1", "VEGA Annotator.vbs")
SKIPPED_NAMES = {"__pycache__", ".DS_Store"}


def add_tree(archive, folder):
    """Add one folder of the app to the zip.

    Parameters:
        archive: the open ZipFile.
        folder: the folder name under the repository root.
    Returns:
        int: how many files were added.
    """
    count = 0
    for path in sorted((ROOT / folder).rglob("*")):
        if path.is_file() and not SKIPPED_NAMES & set(path.parts) and path.suffix != ".pyc":
            archive.write(path, f"{TOP_FOLDER}/{path.relative_to(ROOT).as_posix()}")
            count += 1
    return count


def main():
    """Write the zip and print its path.

    Returns:
        int: 0 on success.
    """
    out = ROOT / "dist" / f"VEGAAnnotator-{VERSION}-Windows.zip"
    out.parent.mkdir(exist_ok=True)
    icon = ROOT / "dist" / "icon.ico"
    subprocess.run([sys.executable, str(ROOT / "packaging" / "make_icon.py"), str(icon)], check=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        total = sum(add_tree(archive, folder) for folder in INCLUDED_FOLDERS)
        for name in SCRIPTS:
            archive.write(ROOT / "packaging" / "windows" / name, f"{TOP_FOLDER}/{name}")
        archive.write(icon, f"{TOP_FOLDER}/icon.ico")
    icon.unlink()
    print(f"{out} ({total + len(SCRIPTS) + 1} files, {out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
