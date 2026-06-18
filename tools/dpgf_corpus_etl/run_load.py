"""Load CLI — separate module so `extract` never imports Streamlit/DB.

    python -m dpgf_corpus_etl.run_load --review dpgf_corpus_review.xlsx --dry-run
    python -m dpgf_corpus_etl.run_load --review dpgf_corpus_review.xlsx
"""

from __future__ import annotations

import argparse
import sys

from .loader import load_approved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dpgf_corpus_etl.run_load")
    ap.add_argument("--review", required=True, help="path to the APPROVED review xlsx")
    ap.add_argument("--dry-run", action="store_true", help="count would-be writes, open no write txn")
    args = ap.parse_args(argv)
    report = load_approved(args.review, dry_run=args.dry_run)
    print("LOAD REPORT:")
    for k, v in report.items():
        print(f"  {k:18} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
