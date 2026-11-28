"""``python -m claims_onboarding.synthetic generate | check | seed``."""

from __future__ import annotations

import argparse
import sys

from ai_platform_common.logging import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m claims_onboarding.synthetic")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate", help="rewrite data/synthetic/ (deterministic)")
    sub.add_parser("check", help="verify every file against its answer keys")
    sub.add_parser("seed", help="load the dictionary, clients and week-1 files into the demo "
                   "tenant (DATABASE_URL)")  # fmt: skip
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    if args.command == "generate":
        from claims_onboarding.synthetic.generate import generate_all

        manifest = generate_all()
        sys.stdout.write(f"wrote {len(manifest['files'])} files and their keys\n")
        return 0
    if args.command == "check":
        from claims_onboarding.synthetic.check import check_all

        problems = check_all()
        for problem in problems:
            sys.stderr.write(problem + "\n")
        sys.stdout.write("all files match their answer keys\n" if not problems else "")
        return 1 if problems else 0
    from claims_onboarding.synthetic.seed import main as seed_main

    return seed_main()


if __name__ == "__main__":
    sys.exit(main())
