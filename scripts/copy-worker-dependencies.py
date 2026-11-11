"""Reproduce the application's tested dependencies without copying its environment."""

import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

from packaging.requirements import Requirement

destination = Path(sys.argv[1]).resolve()
requirements = Path(__file__).with_name("desktop-requirements.txt").read_text().splitlines()
pending = [Requirement(line) for line in requirements if line and not line.startswith("#")]
visited = set()
inventory = {}
while pending:
    requirement = pending.pop()
    key = (requirement.name.lower(), tuple(sorted(requirement.extras)))
    if key in visited:
        continue
    visited.add(key)
    distribution = importlib.metadata.distribution(requirement.name)
    assert distribution.version in requirement.specifier
    source = Path(distribution.locate_file("")).resolve()
    inventory[distribution.metadata["Name"]] = distribution.version
    for item in distribution.files or []:
        origin = Path(distribution.locate_file(item)).resolve()
        if not origin.is_relative_to(source) or not origin.is_file():
            continue
        relative = origin.relative_to(source)
        if "__pycache__" in relative.parts or origin.suffix == ".pyc":
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    for dependency in distribution.requires or []:
        candidate = Requirement(dependency)
        if candidate.marker is None or any(
            candidate.marker.evaluate({"extra": extra}) for extra in ("", *requirement.extras)
        ):
            pending.append(candidate)
destination.parent.joinpath("DEPENDENCIES.json").write_text(json.dumps(inventory, indent=2))
print("Bundled tested dependency distributions:", len(inventory))
