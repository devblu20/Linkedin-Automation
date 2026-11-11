"""Distribute only public source, verified runtime and dependency wheels."""

import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root, destination = map(Path, sys.argv[1:])
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if (
            path.name.startswith(".env")
            or ".local." in path.name
            or path.suffix in {".sqlite3", ".db"}
            or path.name == "device.json"
        ):
            raise ValueError("Private runtime file cannot enter the helper download")
        archive.write(path, relative.as_posix())
print("Portable helper archive created without credentials, cookies or local runtime data.")
