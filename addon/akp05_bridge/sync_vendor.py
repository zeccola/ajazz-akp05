"""
Docker's build context for a Supervisor add-on is this folder only -- it
can't COPY files from the repo root. akp05_device.py stays the single
source of truth there (per its own docstring); this script just copies
the current version of it (and akp05_icons.py) in here before building.

Run this any time either file changes at the repo root, before building
the add-on image:
    python addon/akp05_bridge/sync_vendor.py
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent

FILES = ["akp05_device.py", "akp05_icons.py"]


def main():
    for name in FILES:
        src = ROOT / name
        dst = HERE / name
        shutil.copy2(src, dst)
        print(f"synced {name}")


if __name__ == "__main__":
    main()
