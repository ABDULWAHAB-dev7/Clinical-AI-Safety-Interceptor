"""Command line entry point.

    python -m interceptor check examples/unsafe.json   # exit 0 allow, 1 review, 2 block
    python -m interceptor serve [port]
"""

import json
import sys

from .engine import InvalidRequest, intercept, load_reference

EXIT_CODES = {"allow": 0, "review": 1, "block": 2}


def main(argv):
    if len(argv) >= 2 and argv[0] == "check":
        with open(argv[1], encoding="utf-8") as fh:
            try:
                formulary, interactions = load_reference()
                result = intercept(json.load(fh), formulary, interactions)
            except (json.JSONDecodeError, InvalidRequest) as exc:
                print(json.dumps({"error": str(exc)}), file=sys.stderr)
                return 2
        print(json.dumps(result, indent=2))
        return EXIT_CODES[result["decision"]]
    if argv and argv[0] == "serve":
        from .server import serve

        serve(int(argv[1]) if len(argv) > 1 else 8001)
        return 0
    print(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
