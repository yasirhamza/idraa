"""Triage library entries by FAIR-CAM function completeness (#437 T4)."""

from __future__ import annotations

import json
from pathlib import Path

from idraa.services.control_library_scoring import classify_entry


def main() -> None:
    data = json.loads(Path("data/seed_control_library_entries.json").read_text())
    rows = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
    for e in rows:
        print(
            f"{classify_entry(e):20s}  "
            f"{e.get('name', '?')[:44]:44s}  "
            f"{[a['sub_function'] for a in e.get('assignments', [])]}"
        )


if __name__ == "__main__":
    main()
