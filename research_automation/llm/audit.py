"""Local, ignored audit files. Write responses before parsing or filtering them."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Audit:
    def __init__(self, directory: Path, operation: str, model: str, version: str, text: str):
        self.path = directory / (operation + "-" + uuid4().hex + ".json")
        self.record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "model": model,
            "prompt_version": version,
            "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "status": "started",
            "attempts": [],
        }
        self.save()

    def save(self, **changes: Any) -> None:
        self.record.update(changes)
        write_json(self.path, self.record)
