"""Create a credential-free Railway upload including the validated helper archive."""

import json
import shutil
from pathlib import Path
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
stage = root / "data/device-deploy-stage"
stage.mkdir(parents=True, exist_ok=True)
for folder in ("src", "migrations"):
    shutil.copytree(
        root / folder,
        stage / folder,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
for name in ("Dockerfile", "pyproject.toml", "README.md", "alembic.ini"):
    shutil.copy2(root / name, stage / name)
(stage / "config").mkdir(exist_ok=True)
for path in (root / "config").glob("*.yaml"):
    if not path.name.endswith(".local.yaml"):
        shutil.copy2(path, stage / "config" / path.name)
(stage / "downloads").mkdir(exist_ok=True)
package = root / "downloads/BluQQ-Worker.zip"
with ZipFile(package) as archive:
    names = archive.namelist()
    assert "Start BluQQ Worker.cmd" in names
    assert "runtime/python.exe" in names
    assert all(
        not Path(name).name.startswith(".env")
        and ".local." not in name
        and not name.endswith(".sqlite3")
        for name in names
    )
shutil.copy2(package, stage / "downloads" / package.name)
assert all(not path.name.startswith(".env") for path in stage.rglob("*"))
print(
    json.dumps(
        {"stage": str(stage), "helper_bytes": package.stat().st_size, "contains_credentials": False}
    )
)
