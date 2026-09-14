from __future__ import annotations

import json
import os
import sys


def main() -> None:
    if os.environ.get("PAR_TEST_AMBIENT_SECRET"):
        raise RuntimeError("ambient credential leaked into supervisor provider")
    proposal = json.load(sys.stdin)
    json.dump(proposal, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
