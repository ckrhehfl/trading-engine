"""Logged fixed LG/LX raw episode acquisition, with retained cache provenance.

A committed specification must register the price-free plan hashes, counts and
new request matrix before invocation. Saved receipts precede cached body reads;
durable trial start precedes every financial input and environment credential.
No KIS scan, base quote restore, book, return or unit certificate is produced.
Missing market fields do not certify market identity; endpoint and registered
KOSPI source provenance remain separate from this target-row sanity check.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from data import krx_openapi_probe as probe
from research import activity_exit_preflight as logged
from research import activity_preflight as saved
from research import activity_price_parity as parity
from research import activity_raw_anchor_preflight as anchors
from research import activity_raw_episode_plan as planning
from research import activity_raw_quote_view as view
from research import experiment_log
from research.activity_raw_episode import extract_raw_episode_targets, validate_raw_observation

SCHEMA = "activity-raw-episode-preflight-v1"
INPUTS = {role: deepcopy(parity.INPUTS[role]) for role in ("aq", "ar_original", "ar_recovery")}
INPUTS.update(scope_review={
    "path": "/home/minjun4897/research-evidence/large-liquid-bq-raw-episode-scope-review-20261010-v1/content.json",
    "sha256": "13252867fc7ca0b3c615e16723cddb2eb5b46eb3372e8f744f5a09a00f9d95ce"},
    bp_raw={"root": "/home/minjun4897/research-evidence/large-liquid-bp-raw-anchor-20261010-v1/run",
        "result_sha256": "0db5101d5ca4392d090e3d2685b78b52a223ea6ed681d71de92107267da5ecf0"})
HTTP_CONTRACT = dict(host=probe.HOST, service="stk_bydd_trd", timeout_seconds=20,
    max_response_bytes=8 * 1024**2, interval_seconds=1.1, automatic_retries=0,
    authentication_header="AUTH_KEY", redirects_allowed=False, proxies_allowed=False)
COUNT_FIELDS = {"calendar_sessions", "potential_origins", "raw_unique_days", "raw_code_dates", "cached_responses",
    "new_requests", "cached_code_dates", "new_code_dates", "cache_geometry_unique_days",
    "aq_responses", "ar_original_responses", "ar_recovery_responses", "bp_raw_responses"}
PLAN_PINS = dict(acquisition_plan_sha256="cfe9c20a31539947eb8a396932f3c7ffb6d44ad848c7fbc60245da918e0792f0",
    unit_plan_sha256="4acf0dbb395bcda7163effa9fc6ffacc0fe8c94223e3c1d3767a4f67a72669e8")
PLAN_COUNTS = dict(calendar_sessions=1898, potential_origins=2, raw_unique_days=128, raw_code_dates=229,
    cached_responses=63, new_requests=65, cached_code_dates=125, new_code_dates=104, cache_geometry_unique_days=854,
    aq_responses=1, ar_original_responses=0, ar_recovery_responses=60, bp_raw_responses=2)
FALSE_FLAGS = (*anchors.FALSE_FLAGS, "unit_bridge_certified", "isin_join_certified",
    "atomic_source_vintage_certified", "actions_applied", "actual_study_completed",
    "episode_end_is_actual_exit", "original_end_obligation_discharged")


def validate_spec(spec: dict, reference: dict) -> None:
    """Actual counts are caller-registered metadata, never inferred from prices."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs", "request_contract", "registration"}
        and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == "discovery_raw_episode_acquisition"
        and spec["promotion_allowed"] is False and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW)
        and spec["reference_specification"] == saved.REFERENCE and type(spec["parameters"]) is dict
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
        and saved.encoded(spec["inputs"]) == saved.encoded(INPUTS)
        and saved.encoded(spec["request_contract"]) == saved.encoded(HTTP_CONTRACT), "fixed raw episode specification mismatch")
    registration = spec["registration"]
    saved.require(type(registration) is dict and set(registration) == {
        "acquisition_plan_sha256", "unit_plan_sha256", "counts", "new_request_matrix"}, "exact frozen plan registration required")
    for key in ("acquisition_plan_sha256", "unit_plan_sha256"):
        saved._pin(registration[key])
        saved.require(registration[key] == PLAN_PINS[key], "plan pin differs from registered price-free evaluation")
    counts, matrix = registration["counts"], registration["new_request_matrix"]
    saved.require(type(counts) is dict and set(counts) == COUNT_FIELDS
        and all(type(value) is int and value >= 0 for value in counts.values())
        and counts == PLAN_COUNTS
        and type(matrix) is list and len(matrix) == counts["new_requests"] <= 190,
        "bounded exact request counts required")
    days = []
    for item in matrix:
        saved.require(type(item) is dict and set(item) == {"service", "bas_dd", "targets"}
            and item["service"] == planning.SERVICE and type(item["bas_dd"]) is str
            and date.fromisoformat(item["bas_dd"]).strftime("%Y%m%d") == item["bas_dd"]
            and "20210416" <= item["bas_dd"] <= "20211022"
            and type(item["targets"]) is list and bool(item["targets"])
            and item["targets"] == sorted(set(item["targets"]))
            and all(code in {"003550", "383800"} for code in item["targets"]), "fixed KOSPI request coordinate required")
        days.append(item["bas_dd"])
    saved.require(days == sorted(set(days)), "new requests must be unique ordered days")


def prepare_inputs(spec: dict, inputs: saved.PinnedInputs) -> tuple[dict, dict, list[dict]]:
    """Bind all metadata/receipt pins and cache ordinals before any raw read."""
    declared = spec["inputs"]
    review = declared["scope_review"]
    metadata = inputs.json(Path(review["path"]), review["sha256"], "scope_review")
    plans = planning.build_raw_episode_plan(metadata, scope_review_sha256=review["sha256"])
    plan, unit = plans["acquisition_plan"], plans["unit_plan"]
    actual = dict(acquisition_plan_sha256=saved.digest(saved.encoded(plan)), unit_plan_sha256=saved.digest(saved.encoded(unit)),
        counts=plan["counts"], new_request_matrix=plan["new_request_matrix"])
    saved.require(saved.encoded(actual) == saved.encoded(spec["registration"]), "price-free plan differs from preregistration")
    geometry = plan["cache_geometry"]
    receipts = parity.cache_receipts(inputs, declared, geometry)
    saved.require(all(receipts[key] == geometry[key] for key in ("aq_matrix", "ar_matrix")), "cache receipt geometry mismatch")
    bp = inputs.json(Path(declared["bp_raw"]["root"]) / "result.json", declared["bp_raw"]["result_sha256"], "bp_raw:result")
    saved.require(bp.get("schema") == anchors.SCHEMA and bp.get("status") == "completed"
        and type(bp.get("requests_attempted")) is int and bp["requests_attempted"] == 3
        and type(bp.get("automatic_retries")) is int and bp["automatic_retries"] == 0
        and all(bp.get(key) is False for key in anchors.FALSE_FLAGS)
        and bp.get("database_access") is False and bp.get("api_access") is True
        and all(type(bp.get(key)) is int and bp[key] == 0 for key in ("D1_completed_studies", "D2_completed_studies")),
        "original raw-anchor result mismatch")
    expected = {("20210428", "003550"), ("20210527", "003550"), ("20210527", "383800")}
    targets = bp.get("targets")
    saved.require(type(targets) is list and len(targets) == 4, "four prior raw anchor targets required")
    raw = {(row.get("requested_bas_dd"), row.get("code")): row for row in targets if row.get("kind") == "raw_close"}
    saved.require(set(raw) == expected and sum(row.get("kind") == "raw_close" for row in targets) == 3,
        "exact prior raw trade targets required")
    for (day, _), row in raw.items():
        saved.require(row.get("status") == "observed" and row.get("reason") is None
            and row.get("response_sha256") == planning.BP_RESPONSES[day][1]
            and row.get("source") == "KRX OpenAPI " + planning.SERVICE
            and row.get("returned_BAS_DD") == day and type(row.get("row_position")) is int
            and row["row_position"] >= 0 and row.get("observation_date") == date.fromisoformat(day).isoformat()
            and datetime.fromisoformat(row["retrieved_at"]).utcoffset() is not None, "prior raw target provenance mismatch")
    bound = []
    for wanted in plan["cached_requests"]:
        item = {key: wanted[key] for key in ("service", "bas_dd", "targets")}
        day = item["bas_dd"]
        saved.require(item["service"] == planning.SERVICE, "only planned KOSPI cache bodies allowed")
        if wanted["role"] == "bp_raw":
            saved.require(wanted == {**planning.cached_request(day, geometry), "targets": item["targets"]}, "BP cache coordinate mismatch")
            prior = [raw[day, code] for code in item["targets"]]
            saved.require(len({row["retrieved_at"] for row in prior}) == 1, "BP response retrieval receipt mismatch")
            bound.append({**wanted, "retrieved_at": prior[0]["retrieved_at"], "prior_targets": prior})
        else:
            role, logical, coordinate, observation, aq = parity.raw_coordinate(day, item["service"], receipts)
            saved.require(wanted == {**planning.cached_request(day, geometry), "targets": item["targets"]}
                and role == wanted["role"] and logical == wanted["logical_number"]
                and coordinate == {key: item[key] for key in ("service", "bas_dd")}
                and observation.get("service") == item["service"] and observation.get("requested_bas_dd") == day
                and observation.get("schema_valid") is True, "planned cache receipt coordinate mismatch")
            saved._pin(observation["response_sha256"])
            saved.require(datetime.fromisoformat(observation["at"]).utcoffset() is not None, "recorded cache retrieval timestamp required")
            bound.append({**wanted, "response_sha256": observation["response_sha256"], "retrieved_at": observation["at"],
                "observation": observation, "aq": aq})
    return plans, receipts, bound


def _check_target_market(rows: list[dict], item: dict) -> None:
    """An explicit foreign target market contradicts the fixed KOSPI contract.

    Missing MKT_NM remains decoder-compatible and grants no market certificate.
    This check imposes no positive-price condition on observed zero bars.
    """
    saved.require(all("MKT_NM" not in row or row["MKT_NM"] == "KOSPI"
        for row in rows if row.get("ISU_CD") in item["targets"]), "explicit target market contradicts KOSPI request")


def _cached_rows(inputs, declared, entry):
    role = entry["role"]
    item = {key: entry[key] for key in ("service", "bas_dd", "targets")}
    if role == "bp_raw":
        body = inputs.read(Path(declared[role]["root"]) / entry["filename"], entry["response_sha256"], "bp_raw:raw")
        saved.require(len(body) <= probe.MAX_RESPONSE_BYTES, "cached response exceeds acquisition ceiling")
        rows = saved.parse_envelope(body)
        # Rejoin the original observed close and row position before decoding all six fields.
        rebuilt = anchors.extract_targets(rows, item, entry["retrieved_at"], entry["response_sha256"])
        saved.require(saved.encoded(rebuilt) == saved.encoded(entry["prior_targets"]), "BP body/target lineage mismatch")
        return rows
    return saved._rows(inputs, Path(declared[role]["root"]), entry["logical_number"], item,
        entry["observation"], role, aq=entry["aq"])


def _fetch(opener, item, secret, output, manifest, number):
    """One fixed-host GET; ledger/fsync precedes transport, no error body retained."""
    record = dict(number=number, service=item["service"], bas_dd=item["bas_dd"])
    entry = dict(**record, read_status="started")
    manifest.append(entry)
    probe.persist(output / "requests.jsonl", dict(**record, event="attempt", at=probe.utc_now()), append=True)
    url = f"{probe.HOST}/svc/apis/sto/{item['service']}?basDd={item['bas_dd']}"
    status = None
    try:
        with opener.open(Request(url, headers={"AUTH_KEY": secret}, method="GET"), timeout=20) as response:
            status = response.status
            if status != 200 or response.geturl() != url:
                raise probe.ProbeRefusal("unexpected_response")
            body = response.read(probe.MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        raise probe.ProbeRefusal("redirect_rejected" if 300 <= status < 400 else
            "access_failure" if status in (401, 403) else "http_failure") from None
    except (URLError, TimeoutError, OSError):
        raise probe.ProbeRefusal("transport_failure") from None
    finally:
        probe.persist(output / "requests.jsonl", dict(**record, event="response", at=probe.utc_now(), http_status=status), append=True)
        entry["http_status"] = status
    if len(body) > probe.MAX_RESPONSE_BYTES:
        raise probe.ProbeRefusal("response_too_large")
    rows = probe.parse_response(body, secret)
    filename = f"response-{number:04d}-{item['service']}-{item['bas_dd']}.json"
    saved.write_exclusive(output / filename, body)
    entry.update(read_status="retained", file=filename, bytes=len(body), rows=len(rows),
        sha256=saved.digest(body), retrieved_at=probe.utc_now())
    _check_target_market(rows, item)
    return extract_raw_episode_targets(rows, item, entry["retrieved_at"], entry["sha256"])


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    producer = Path(__file__).resolve()
    saved.require(any(row["path"] == producer.relative_to(root).as_posix() and row["sha256"] == saved.digest(producer.read_bytes())
        for row in sources["files"]), "producer must be committed frozen source before actual invocation")
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = saved.PinnedInputs(saved.read_private if reader is None else reader)
    requests, observations = [], []

    def evaluate():
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            for name, body in (("specification.json", raw), ("source-manifest.json", saved.encoded(sources))):
                saved.write_exclusive(output / name, body)
            plans, _, bound = prepare_inputs(spec, inputs)
            plan, unit = plans["acquisition_plan"], plans["unit_plan"]
            for name, value in (("acquisition-plan.json", plan), ("unit-plan.json", unit),
                    ("cache-manifest.json", bound), ("request-manifest.json", dict(contract=HTTP_CONTRACT, matrix=plan["new_request_matrix"]))):
                saved.write_exclusive(output / name, saved.encoded(value))
            saved.write_exclusive(output / "requests.jsonl", b"")
            for entry in bound:
                rows = _cached_rows(inputs, spec["inputs"], entry)
                item = {key: entry[key] for key in ("service", "bas_dd", "targets")}
                _check_target_market(rows, item)
                observations.extend(extract_raw_episode_targets(rows, item,
                    entry["retrieved_at"], entry["response_sha256"]))
            if plan["new_request_matrix"]:
                secret = probe.credential()
                opener = probe.make_opener(header_case_check=True)
                for number, item in enumerate(plan["new_request_matrix"], 1):
                    if number > 1:
                        time.sleep(1.1)
                    observations.extend(_fetch(opener, item, secret, output, requests, number))
            observations.sort(key=lambda row: (row.code, row.requested_bas_dd))
            wanted = [(code, day.replace("-", "")) for code, days in sorted(plan["requested_code_dates"].items()) for day in days]
            saved.require([(row.code, row.requested_bas_dd) for row in observations] == wanted, "exact raw coordinate union required")
            for row in observations:
                validate_raw_observation(row)
            artifact = saved.encoded(dict(schema=view.ARTIFACT_SCHEMA, activity_snapshot_sha256=unit["activity_snapshot_sha256"],
                base_quote_snapshot_sha256=unit["base_quote_snapshot_sha256"], calendar_sha256=unit["calendar_sha256"],
                unit_plan_sha256=saved.digest(saved.encoded(unit)), rows=view._value(tuple(observations))))
            saved.write_exclusive(output / "raw-episode-quotes.json", artifact)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources, manifest = saved.encoded(logged._resources(started)), saved.encoded(inputs.manifest)
            for name, body in (("resource-manifest.json", resources), ("input-manifest.json", manifest),
                    ("new-response-manifest.json", saved.encoded(requests))):
                saved.write_exclusive(output / name, body)
            summary = dict(plan["counts"], observed_rows=sum(row.status == "observed" for row in observations),
                unresolved_rows=sum(row.status != "observed" for row in observations),
                no_trade_zero_price_rows=sum(row.state == "observed_no_trade_zero_prices" for row in observations))
            result = dict(schema=SCHEMA, status="completed" if not summary["unresolved_rows"] else "completed_with_unresolved",
                summary=summary, **dict.fromkeys(FALSE_FLAGS, False), database_access=False, api_access=bool(requests),
                automatic_retries=0, new_source_requests=len(requests), D1_completed_studies=0, D2_completed_studies=0,
                requests_attempted=len(requests), requests_unattempted=plan["counts"]["new_requests"] - len(requests),
                raw_response_bodies_read=True,
                scope_review_sha256=spec["inputs"]["scope_review"]["sha256"], **{key: saved.digest(body) for key, body in (
                    ("source_manifest_sha256", saved.encoded(sources)), ("specification_sha256", raw),
                    ("resource_manifest_sha256", resources), ("input_manifest_sha256", manifest),
                    ("raw_episode_quotes_sha256", artifact), ("unit_plan_sha256", saved.encoded(unit)),
                    ("acquisition_plan_sha256", saved.encoded(plan)), ("cache_manifest_sha256", saved.encoded(bound)),
                    ("new_response_manifest_sha256", saved.encoded(requests)),
                    ("request_manifest_sha256", saved.encoded(dict(contract=HTTP_CONTRACT, matrix=plan["new_request_matrix"]))),
                    ("request_ledger_sha256", saved.read_private(output / "requests.jsonl")))},
                independent_output_verification="pending")
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return dict(status=result["status"], output_dir=str(output), summary=summary,
                result_sha256=saved.digest(saved.encoded(result)), raw_episode_quotes_sha256=saved.digest(artifact),
                D1_completed_studies=0, D2_completed_studies=0, promotion_allowed=False)
        except BaseException as exc:
            if created:
                if not (output / "resource-manifest.json").exists():
                    saved.write_exclusive(output / "resource-manifest.json", saved.encoded(logged._resources(started)))
                for name, value in (("input-manifest.json", inputs.manifest), ("new-response-manifest.json", requests)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                reason = str(exc) if isinstance(exc, probe.ProbeRefusal) else "validation_or_local_failure"
                if reason not in anchors.SAFE_FAILURE_REASONS:
                    reason = "validation_or_local_failure"
                saved.write_exclusive(output / "failure.json", saved.encoded(dict(status="failed", error_type=type(exc).__name__, failure=reason,
                    actual_inputs_attempted=inputs.manifest, requests_attempted=len(requests), partial_rows=view._value(observations),
                    requests_unattempted=spec["registration"]["counts"]["new_requests"] - len(requests),
                    partial_package_preserved=True, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0,
                    source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                    **dict.fromkeys(FALSE_FLAGS, False))))
            raise probe.ProbeRefusal("raw_episode_attempt_failed") from None

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "request_contract": spec["request_contract"], "registration": spec["registration"],
                "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = probe.SafeParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"):
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception:
        print(saved.encoded(dict(status="failed", failure="raw_episode_attempt_failed", automatic_retries=0)).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
