"""Acquire KIND merger-listing metadata and reconcile names, never eligibility.

The issuer/process identifiers are opaque KIND identifiers, not stock codes or
publication dates. Exact-name matches remain candidates for source verification.
No price table, credential, collector, or experiment log is used.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research.activity_portfolio import readonly


START, END = "2019-01-02", "2026-09-18"
URL = "https://kind.krx.co.kr/listinvstg/mergeListingCompany.do?" + urlencode({
    "method": "searchMergeListingCompSub", "forward": "mergeListingCompany_sub",
    "fromDate": START, "toDate": END, "listTypeArrStr": "06|07|",
    "secuGrpArrStr": "ST|FS|", "currentPageSize": "3000", "pageIndex": "1",
})
TYPES = {"SPAC 존속합병": "spac_survives", "SPAC 소멸합병": "spac_disappears"}
MAX_BYTES = 2_000_000


class _ListingHTML(HTMLParser):
    """Read the observed seven-column table; refuse drift instead of skipping it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.text: list[str] = []
        self.in_body = False
        self.row: dict | None = None
        self.cell: list[str] | None = None
        self.cells: list[str] = []
        self.titles: list[str] = []
        self.markets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tbody":
            if self.in_body:
                raise ValueError("nested listing body")
            self.in_body = True
        elif tag == "tr" and self.in_body:
            if self.row is not None:
                raise ValueError("unclosed listing row")
            match = re.fullmatch(r"fnDetailView\('([A-Za-z0-9]+)','([0-9]+)'\)",
                                 attributes.get("onclick") or "")
            if not match:
                raise ValueError("missing or unknown KIND detail identifiers")
            self.row = dict(issuer_id=match[1], process_id=match[2])
            self.cells, self.titles, self.markets = [], [], []
        elif tag == "td" and self.row is not None:
            if self.cell is not None:
                raise ValueError("nested listing cell")
            self.cell = []
            self.titles.append(attributes.get("title") or "")
        elif tag == "img" and self.cell is not None and not self.cells:
            alt = attributes.get("alt")
            if alt in {"유가증권", "코스닥", "코넥스"}:
                self.markets.append(alt)

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None or len(self.cells) != 7 or len(self.markets) != 1:
                raise ValueError("unexpected listing columns or market")
            name, listed_on, kind, security, industry, country, sponsor = self.cells
            if not name or name != self.titles[0] or kind not in TYPES or security != "주권":
                raise ValueError("unexpected listing name, type or security")
            if date.fromisoformat(listed_on).isoformat() != listed_on or not START <= listed_on <= END:
                raise ValueError("merger listing outside requested window")
            self.rows.append({**self.row, "name": name, "listed_on": listed_on,
                              "merger_type": TYPES[kind], "market": self.markets[0],
                              "industry": industry, "country": country, "sponsor": sponsor})
            self.row = None
        elif tag == "tbody":
            if self.row is not None:
                raise ValueError("unclosed listing row")
            self.in_body = False


def parse_listings(payload: bytes) -> list[dict]:
    parser = _ListingHTML()
    parser.feed(payload.decode("utf-8"))
    parser.close()
    if parser.row is not None or parser.in_body:
        raise ValueError("truncated listing response")
    counts = re.findall(r"전체\s*([\d,]+)\s*건\s*:\s*(\d+)\s*/\s*(\d+)",
                        " ".join(parser.text))
    if len(counts) != 1:
        raise ValueError("missing or ambiguous pagination count")
    total, page, pages = (int(v.replace(",", "")) for v in counts[0])
    if page != 1 or pages != 1 or total != len(parser.rows) or total == 0:
        raise ValueError("incomplete or empty merger listing response")
    keys = [(r["issuer_id"], r["process_id"]) for r in parser.rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate merger listing")
    return sorted(parser.rows, key=lambda r: (r["listed_on"], r["issuer_id"], r["process_id"]))


def read_metadata(scan_path: Path, identity_path: Path) -> dict:
    with closing(readonly(scan_path)) as scan, closing(readonly(identity_path)) as master:
        scan.execute("BEGIN")
        master.execute("BEGIN")
        panel = scan.execute("SELECT start,end FROM scan_panel WHERE id=1").fetchone()
        if panel != (START.replace("-", ""), END.replace("-", "")):
            raise ValueError("only the spent 20190102..20260918 panel is allowed")
        progress = dict(scan.execute("SELECT code,status FROM scan_progress ORDER BY code"))
        if "done" not in progress.values():
            raise ValueError("no completed scan names")
        identities = []
        snapshots = {}
        for table, kind in (("krx_universe", "live"), ("krx_delisted", "delisted")):
            snapshot = master.execute(f"SELECT max(snapshot_date) FROM {table}").fetchone()[0]
            if snapshot is None:
                raise ValueError(f"missing identity snapshot: {table}")
            snapshots[kind] = snapshot
            for code, name, isin in master.execute(
                f"SELECT code,name,standard_code FROM {table} WHERE snapshot_date=? ORDER BY code",
                (snapshot,),
            ):
                identities.append(dict(code=code, name=name, isin=isin, kind=kind))
        return dict(panel=panel, progress=progress, snapshots=snapshots, identities=identities)


def reconcile(listings: list[dict], metadata: dict) -> dict:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for identity in metadata["identities"]:
        by_name[identity["name"]].append({**identity, "scan_status":
                                         metadata["progress"].get(identity["code"], "absent")})
    records = []
    counts: Counter[str] = Counter()
    for listing in listings:
        candidates = by_name[listing["name"]]
        status = "unmatched" if not candidates else "unique_name_candidate" if len(candidates) == 1 else "ambiguous"
        counts[status] += 1
        records.append({**listing, "match_status": status, "candidates": candidates})
    canonical = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "metadata_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "snapshots": metadata["snapshots"], "listing_count": len(listings),
        "type_counts": dict(sorted(Counter(r["merger_type"] for r in listings).items())),
        "match_counts": dict(sorted(counts.items())), "records": records,
        "interpretation": "name candidates only; no historical eligibility or completeness claim",
    }


def fetch_listings() -> bytes:
    request = Request(URL, headers={"User-Agent": "trading-engine-research/0.1"})
    with urlopen(request, timeout=30) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("oversized merger listing response")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-db", type=Path, required=True)
    parser.add_argument("--identity-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory; never overwrite evidence")
    parser.add_argument("--source-html", type=Path, help="saved response to the fixed public query")
    parser.add_argument("--retrieved-at", help="original ISO timestamp with timezone; required for saved HTML")
    args = parser.parse_args(argv)
    if bool(args.source_html) != bool(args.retrieved_at):
        parser.error("--source-html and --retrieved-at must be supplied together")
    try:
        retrieved_at = datetime.fromisoformat(args.retrieved_at) if args.retrieved_at else None
    except ValueError:
        parser.error("--retrieved-at must be an ISO timestamp")
    if retrieved_at is not None and retrieved_at.utcoffset() is None:
        parser.error("--retrieved-at must include a timezone")
    root = Path(__file__).resolve().parents[2]
    sources = ["python/research/activity_mergers.py", "python/research/activity_portfolio.py",
               "python/research/__init__.py", "python/research/experiment_log.py",
               "python/research/krx_tax_schedule.py", "python/data/_paths.py", "python/data/__init__.py"]
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", *sources]).strip():
        raise ValueError("metadata audit sources must be committed")
    version = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    metadata = read_metadata(args.scan_db, args.identity_db)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    if args.source_html:
        with args.source_html.open("rb") as source:
            payload = source.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("oversized merger listing response")
    else:
        payload = fetch_listings()
        retrieved_at = datetime.now(timezone.utc)
    # Retain a rejected response for diagnosis too; result.json exists only on success.
    (args.output_dir / "kind-mergers.html").write_bytes(payload)
    result = {"code_version": version, "retrieved_at": retrieved_at.isoformat(),
              "source_transport": "saved_html" if args.source_html else "https",
              "source_url": URL, "source_sha256": hashlib.sha256(payload).hexdigest(),
              **reconcile(parse_listings(payload), metadata)}
    (args.output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
