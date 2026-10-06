"""Inventory accounting inputs without reading price values or running a trial.

Recent master labels describe cache contamination, never historical eligibility.
This command neither repairs a database nor authorizes a corrected sizing run.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from data.krx_instrument import is_common_stock_issue, is_reit, is_spac
from research.activity_portfolio import readonly


def inventory(scan_path: Path, identity_path: Path, *, include_code_inventory: bool = False) -> dict:
    """Read completion/identity metadata only, after refusing reserved panels."""
    with closing(readonly(scan_path)) as scan, closing(readonly(identity_path)) as master:
        # Pin read transactions so a collector cannot change one snapshot while
        # its maximum date and rows are read. Separate databases stay separate.
        scan.execute("BEGIN")
        master.execute("BEGIN")
        panel = scan.execute("SELECT start,end FROM scan_panel WHERE id=1").fetchone()
        if panel != ("20190102", "20260918"):
            raise ValueError("only the spent 20190102..20260918 panel is allowed")
        codes = [r[0] for r in scan.execute(
            "SELECT code FROM scan_progress WHERE status='done' ORDER BY code")]
        if not codes:
            raise ValueError("no completed scan names")
        snapshots = {}
        identities: dict[str, list[dict]] = defaultdict(list)
        for table, kind in (("krx_universe", "live"), ("krx_delisted", "delisted")):
            first, last, count = master.execute(
                f"SELECT min(snapshot_date),max(snapshot_date),count(distinct snapshot_date) FROM {table}"
            ).fetchone()
            if last is None:
                raise ValueError(f"missing identity snapshot: {table}")
            snapshots[kind] = {"first": first, "last": last, "count": count}
            group = "group_code" if kind == "live" else "NULL"
            for code, name, isin, group_code, market in master.execute(
                f"SELECT code,name,standard_code,{group},market FROM {table} "
                "WHERE snapshot_date=? ORDER BY code", (last,),
            ):
                identities[code].append(dict(kind=kind, name=name, isin=isin,
                                             group_code=group_code, market=market))
        raw = [list(row) for row in master.execute(
            "SELECT symbol,count(*) FROM klines WHERE symbol GLOB 'KRX-RAW:*' "
            "AND interval='1d' AND open_time_ms BETWEEN ? AND ? GROUP BY symbol ORDER BY symbol",
            (int(datetime(2019, 1, 2, tzinfo=timezone.utc).timestamp() * 1000),
             int(datetime(2026, 9, 18, tzinfo=timezone.utc).timestamp() * 1000)),
        )]
        counts = Counter()
        exceptions = []
        code_inventory = []
        for code in codes:
            rows = identities[code]
            if len(rows) != 1:
                category = "missing_or_multiple_identity"
            else:
                row = rows[0]
                if (is_common_stock_issue(row["name"], row["isin"]) and
                        (row["kind"] != "live" or row["group_code"] == "ST")):
                    category = "passes_current_filter"
                elif is_spac(row["name"]):
                    category = "spac"
                elif is_reit(row["name"]):
                    category = "reit"
                else:
                    category = "other_non_candidate"
            counts[category] += 1
            if include_code_inventory:
                code_inventory.append(dict(code=code, category=category, identities=rows))
            if category != "passes_current_filter":
                exceptions.append(dict(code=code, category=category, identities=rows))
        inputs = {"panel": panel, "done_codes": codes, "snapshots": snapshots,
                  "identities": {c: identities[c] for c in codes}, "raw_daily_rows": raw}
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode()).hexdigest()
        result = {
            "metadata_sha256": digest, "completed_codes": len(codes),
            "snapshots": snapshots, "current_label_counts": dict(sorted(counts.items())),
            "exceptions": exceptions, "raw_daily_rows_in_spent_window": raw,
            "interpretation": "metadata inventory only; current labels are not historical membership",
        }
        if include_code_inventory:
            result["code_inventory"] = code_inventory
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-db", type=Path, required=True)
    parser.add_argument("--identity-db", type=Path, required=True)
    parser.add_argument("--include-code-inventory", action="store_true",
                        help="retain every completed code, including unknown/conflicting current identities")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    sources = ["python/research/activity_readiness.py", "python/research/activity_portfolio.py",
               "python/research/__init__.py", "python/research/experiment_log.py",
               "python/research/krx_tax_schedule.py", "python/data/krx_instrument.py",
               "python/data/__init__.py", "python/data/_paths.py"]
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", *sources])
    if dirty.strip():
        raise ValueError("metadata audit sources must be committed")
    version = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    result = inventory(args.scan_db, args.identity_db,
                       include_code_inventory=args.include_code_inventory)
    print(json.dumps({"code_version": version, **result},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
