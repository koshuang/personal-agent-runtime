from __future__ import annotations

import json
import os
import sys


def main() -> None:
    if os.environ.get("PAR_TEST_AMBIENT_SECRET"):
        raise RuntimeError("ambient credential leaked into replacement supervisor provider")
    proposal = json.load(sys.stdin)
    normalized = json.loads(json.dumps(proposal, sort_keys=True))
    json.dump(normalized, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
