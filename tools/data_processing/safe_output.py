"""Exclusive local output under the project's ignored evidence directory."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = PROJECT_ROOT / "private" / "data_skills"


def write_new_bytes(path, content: bytes) -> Path:
    if not isinstance(content, bytes):
        raise ValueError("Output content must be bytes")
    output = Path(os.path.abspath(path))
    if not output.is_relative_to(OUTPUT_ROOT) or output == OUTPUT_ROOT:
        raise ValueError("Outputs must be new files under private/data_skills")
    for component in [output, *output.parents]:
        if component.is_symlink():
            raise ValueError("Symlink output components are forbidden")
        if component == PROJECT_ROOT:
            break
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
    return output
