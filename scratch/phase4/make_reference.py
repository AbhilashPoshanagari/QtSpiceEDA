"""Write tests/fixtures/grid/<feeder>/opendss_reference.json: the OpenDSS result of the
original, unmodified .dss files (CLAUDE.md: "the OpenDSS result of the original .dss"
is an allowed reference). Tests read these files; nobody types reference numbers.

Re-run only on purpose (new feeder, new pinned dss-python):
    python scratch/phase4/make_reference.py
"""

from __future__ import annotations

import datetime
import json

import common


def main() -> None:
    info = common.versions()
    for feeder, case in common.CASES.items():
        result = common.load_and_solve(feeder)
        data = {
            "about": {
                "what": "OpenDSS snapshot solution of the original feeder files",
                "made_by": "scratch/phase4/make_reference.py",
                "how": {
                    "compile": case.master.name,
                    "after_compile": list(case.after_compile),
                    "solve": case.solve,
                },
                "date": datetime.date.today().isoformat(),
                **info,
            },
            **common.as_reference(result),
        }
        path = common.reference_path(feeder)
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"{feeder}: {len(result.node_names)} nodes -> {path}")


if __name__ == "__main__":
    main()
