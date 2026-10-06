"""Acquire public exchange notices for Task AL's fixed metadata candidates.

No credentials, price database, quotation endpoint, eligibility or return trial.
Original responses and document versions survive interpretation failures.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from research.kind_notices import (
    KindParseError, parse_body_url, parse_notice, parse_search, parse_versions,
)


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / ".planning/rd-al-merger-notice-candidates.json"
SEARCH = "https://kind.krx.co.kr/disclosure/details.do"
VIEWER = "https://kind.krx.co.kr/common/disclsviewer.do"
MAX_BYTES = 2_000_000
MAX_PAGES = 5
MAX_VERSIONS = 10


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_new(path: Path, payload: bytes) -> None:
    # Publish only fully written files. A crash may leave .pending, never a
    # truncated result.json that looks like a completed acquisition.
    pending = path.with_name(path.name + ".pending")
    with pending.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(pending, path)  # Atomic and refuses an existing destination.
    pending.unlink()


def allowed_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "kind.krx.co.kr" or parsed.fragment:
        raise ValueError("unexpected public document host")
    if parsed.path not in {"/disclosure/details.do", "/common/disclsviewer.do"} and not re.fullmatch(
        r"/external/\d{4}/\d{2}/\d{2}/\d{6}/\d{14}/\d+\.htm", parsed.path,
    ):
        raise ValueError("unexpected public document path")


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Evidence:
    """Persist response bytes before parsing; replay never falls back to network."""

    def __init__(self, output: Path, source: Path | None = None, *, fetch_missing: bool = False) -> None:
        self.output = output
        self.source = source
        self.fetch_missing = fetch_missing
        self.cache: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.network_requests = 0
        self.opener = build_opener(_PublicRedirect())
        if source is not None:
            for line in (source / "sources.jsonl").read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                key = sha(encoded(entry["request"]))
                if key in self.sources:
                    raise ValueError("duplicate saved request")
                self.sources[key] = entry

    def get(self, url: str, form: dict | None = None) -> tuple[bytes, dict]:
        allowed_url(url)
        request = {"url": url, "form": form, "method": "POST" if form is not None else "GET"}
        key = sha(encoded(request))
        if key in self.cache:
            entry = self.cache[key]
            return (self.output / entry["file"]).read_bytes(), entry
        original = self.sources.get(key)
        if self.source is not None and original is None and not self.fetch_missing:
            raise ValueError("response absent from offline evidence")
        if original is not None:
            filename = original["file"]
            if Path(filename).name != filename or not re.fullmatch(r"[0-9a-f]{64}\.html", filename):
                raise ValueError("invalid saved response path")
            payload = (self.source / filename).read_bytes()
            if sha(payload) != original["sha256"]:
                raise ValueError("saved response hash mismatch")
            retrieved_at = original["retrieved_at"]
            if datetime.fromisoformat(retrieved_at).utcoffset() is None:
                raise ValueError("saved retrieval time has no timezone")
        else:
            time.sleep(0.25)
            req = Request(url, data=urlencode(form).encode() if form is not None else None,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; trading-engine-metadata/0.1)"})
            # HTTP/transport failures terminate the acquisition; no retries into a ban.
            self.network_requests += 1
            with self.opener.open(req, timeout=30) as response:
                allowed_url(response.geturl())
                payload = response.read(MAX_BYTES + 1)
            retrieved_at = datetime.now(timezone.utc).isoformat()
        if len(payload) > MAX_BYTES:
            raise ValueError("oversized public response")
        entry = {"request": request, "retrieved_at": retrieved_at,
                 "file": key + ".html", "sha256": sha(payload),
                 "transport": "saved_response" if original is not None else "https"}
        write_new(self.output / entry["file"], payload)
        with (self.output / "sources.jsonl").open("ab") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.cache[key] = entry
        return payload, entry


def search_form(code: str, listing: str, page: int) -> dict[str, str]:
    day = date.fromisoformat(listing)
    return dict(method="searchDetailsSub", forward="details_sub", currentPageSize="100",
                pageIndex=str(page), orderMode="0", orderStat="D", searchCodeType="number",
                searchCorpName=code, repIsuSrtCd="A" + code,
                fromDate=max(date(2019, 1, 2), day - timedelta(days=45)).isoformat(),
                toDate=min(date(2026, 9, 18), day + timedelta(days=7)).isoformat())


def notice_kind(title: str, merger_type: str) -> str | None:
    title = "".join(title.split()).removeprefix("[정정]")
    if merger_type == "spac_survives":
        if "추가상장" in title and "타법인흡수합병" in title:
            return "additional"
        if "변경상장" in title and "상호변경" in title:
            return "rename"
    elif re.fullmatch(r"SPAC소멸합병상장(?:\([0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}\))?", title):
        return "spac_listing"
    return None


def company_key(value: str) -> str:
    value = "".join(unicodedata.normalize("NFKC", value).split())
    return re.sub(r"^(?:주식회사|\(주\))|(?:주식회사|\(주\))$", "", value)


def facts_key(notice: dict) -> bytes:
    fields = {key: company_key(value) if key.endswith("name") else value
              for key, value in notice["fields"].items()}
    return encoded(fields)


def verify_candidate(candidate: dict, notices: list[dict]) -> dict:
    """Confirm notice facts only. Never turn these into historical eligibility."""
    kind = "additional" if candidate["merger_type"] == "spac_survives" else "spac_listing"
    matches = [n for n in notices if n["fields"]["kind"] == kind
               and n["fields"]["code"] == candidate["candidate_code"]
               and n["fields"]["listing_on"] == candidate["listed_on"]]
    result = {**candidate, "status": "unresolved", "reason": "no_matching_listing_notice",
              "evidence": [], "historical_intervals_ready": False, "known_on": None}
    if not matches:
        return result
    # A later conflicting version of an otherwise matching receipt cannot be ignored.
    matching_receipts = {n["receipt_no"] for n in matches}
    versions = [n for n in notices if n["receipt_no"] in matching_receipts]
    if len({facts_key(n) for n in versions}) != 1:
        result.update(reason="conflicting_listing_versions", evidence=versions)
        return result
    matches.sort(key=lambda n: (n["publication_on"], n["doc_no"]))
    listing = matches[0]
    evidence = [listing]
    if kind == "additional":
        renames = [n for n in notices if n["fields"]["kind"] == "rename"
                   and n["fields"]["listing_on"] == candidate["listed_on"]
                   and company_key(n["fields"]["before_company_name"])
                   == company_key(listing["fields"]["company_name"])]
        if not renames:
            result.update(reason="missing_linked_rename", evidence=evidence)
            return result
        rename_receipts = {n["receipt_no"] for n in renames}
        rename_versions = [n for n in notices if n["receipt_no"] in rename_receipts]
        if len({facts_key(n) for n in rename_versions}) != 1:
            result.update(reason="conflicting_rename_versions", evidence=evidence + rename_versions)
            return result
        renames.sort(key=lambda n: (n["publication_on"], n["doc_no"]))
        evidence.append(renames[0])
        operating_name = renames[0]["fields"]["after_company_name"]
    else:
        operating_name = listing["fields"]["company_name"]
        if listing["fields"]["isin"] != candidate["candidate_isin"]:
            result.update(reason="current_isin_conflict", evidence=evidence)
            return result
    result.update(status="listing_identity_verified", reason=None, evidence=evidence,
                  operating_name_at_listing=operating_name,
                  notice_evidence_available_on=max(n["publication_on"] for n in evidence))
    return result


def acquire_candidate(candidate: dict, evidence: Evidence) -> dict:
    rows = []
    first = None
    for page in range(1, MAX_PAGES + 1):
        form = search_form(candidate["candidate_code"], candidate["listed_on"], page)
        payload, _ = evidence.get(SEARCH, form)
        parsed = parse_search(payload, form["fromDate"], form["toDate"], page)
        if first is None:
            first = parsed
        elif parsed["total"] != first["total"] or parsed["pages"] != first["pages"]:
            raise KindParseError("search pagination changed during acquisition")
        if parsed["pages"] > MAX_PAGES:
            raise KindParseError("search exceeds bounded page count")
        rows.extend(parsed["rows"])
        if page == parsed["pages"]:
            break
    if len(rows) != first["total"] or len({r["receipt_no"] for r in rows}) != len(rows):
        raise KindParseError("incomplete or duplicate search records")
    notices, failures = [], []
    for row in rows:
        kind = notice_kind(row["title"], candidate["merger_type"])
        if kind is None:
            continue
        if row["issuer_id"] != candidate["issuer_id"] or row["submitter"] != "코스닥시장본부":
            failures.append({"receipt_no": row["receipt_no"], "reason": "unexpected_notice_issuer"})
            continue
        viewer, _ = evidence.get(VIEWER + "?" + urlencode({"method": "search", "acptno": row["receipt_no"]}))
        versions = parse_versions(viewer, row["receipt_no"])
        if not versions or len(versions) > MAX_VERSIONS:
            raise KindParseError("unexpected document version count")
        if row.get("later_correction_reported") and len(versions) < 2:
            raise KindParseError("reported later correction absent from viewer")
        for version in versions:
            contents, _ = evidence.get(VIEWER + "?" + urlencode({"method": "searchContents", "docNo": version["doc_no"]}))
            url = parse_body_url(contents, version["doc_no"])
            if "/".join(version["publication_on"].split("-")) not in url:
                raise KindParseError("document publication date disagrees with its body path")
            body, source = evidence.get(url)
            try:
                fields = parse_notice(body, kind)
            except KindParseError as exc:
                failures.append({"receipt_no": row["receipt_no"], "doc_no": version["doc_no"],
                                 "body_url": url, "reason": str(exc)})
                continue
            notices.append({**version, "receipt_no": row["receipt_no"],
                            "search_published_at": row["published_at"], "search_title": row["title"],
                            "later_correction_reported": row.get("later_correction_reported", False),
                            "body_url": url, "body_sha256": source["sha256"], "fields": fields})
    result = verify_candidate(candidate, notices)
    result.update(search_rows=len(rows), notices=notices, parse_failures=failures)
    # An unread correction may contradict a parsed original; keep the candidate open.
    if failures:
        result.update(status="unresolved", reason="notice_parse_or_identity_failure")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="fresh public-evidence directory")
    saved = parser.add_mutually_exclusive_group()
    saved.add_argument("--source-dir", type=Path, help="offline replay of saved responses; never uses network")
    saved.add_argument("--resume-dir", type=Path, help="reuse verified saved responses, fetch missing public requests")
    args = parser.parse_args(argv)
    source = args.source_dir or args.resume_dir
    sources = ["python/research/kind_notices.py", "python/research/activity_merger_notices.py",
               str(INPUT.relative_to(ROOT)), ".planning/rd-al-merger-notices.md"]
    if subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--", *sources]).strip():
        raise ValueError("public acquisition sources must be committed")
    version = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    input_bytes = INPUT.read_bytes()
    candidates = json.loads(input_bytes)["records"]
    if (len(candidates) != 104 or Counter(c["merger_type"] for c in candidates)
            != {"spac_survives": 51, "spac_disappears": 53}
            or len({(c["issuer_id"], c["process_id"]) for c in candidates}) != 104
            or any(not re.fullmatch(r"[0-9A-Z]{6}", c["candidate_code"]) for c in candidates)):
        raise ValueError("unexpected fixed candidate manifest")
    args.output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_new(args.output_dir / "input.json", input_bytes)
    write_new(args.output_dir / "started.json", encoded({"code_version": version,
              "started_at": datetime.now(timezone.utc).isoformat(), "input_sha256": sha(input_bytes),
              "source_sha256": {name: sha((ROOT / name).read_bytes()) for name in sources},
              "candidate_count": 104, "purpose": "public listing metadata only; no eligibility or returns",
              "source_dir": str(source) if source else None,
              "fetch_missing": args.resume_dir is not None}))
    records = []
    evidence = None
    try:
        evidence = Evidence(args.output_dir, source, fetch_missing=args.resume_dir is not None)
        for i, candidate in enumerate(candidates, 1):
            try:
                result = acquire_candidate(candidate, evidence)
            except KindParseError as exc:
                result = {**candidate, "status": "unresolved", "reason": str(exc),
                          "historical_intervals_ready": False, "known_on": None}
            write_new(args.output_dir / f"candidate-{i:03d}.json", encoded(result))
            records.append(result)
            print(json.dumps({"completed": i, "code": candidate["candidate_code"],
                              "status": result["status"], "reason": result["reason"]}), flush=True)
        summary = {"status": "completed_metadata_acquisition", "code_version": version,
                   "input_sha256": sha(input_bytes), "completed_at": datetime.now(timezone.utc).isoformat(),
                   "candidate_count": len(records), "counts": dict(Counter(r["status"] for r in records)),
                   "network_requests": evidence.network_requests, "source_count": len(evidence.cache),
                   "historical_intervals_ready": False, "records": records}
        write_new(args.output_dir / "result.json", encoded(summary))
        return 0
    except BaseException:
        write_new(args.output_dir / "failure.json", encoded({"status": "incomplete",
                  "completed_candidates": len(records),
                  "network_requests": evidence.network_requests if evidence else 0}))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
