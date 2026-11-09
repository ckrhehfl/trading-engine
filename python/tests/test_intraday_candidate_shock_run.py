from contextlib import contextmanager

import pytest

from research import intraday_candidate_shock_run as run


def test_first_events_and_volume_sealed_before_payoff_projection(tmp_path, monkeypatch):
    project = run._project

    def check(reader, symbol, day, offsets, kind, invented, audit):
        if kind == "CC" and offsets != run.PRICE_OFFSETS:
            assert all((tmp_path / f"{scenario}-pre-entry.jsonl").is_file() for scenario in ("S1", "S2"))
        return project(reader, symbol, day, offsets, kind, invented, audit)

    monkeypatch.setattr(run, "_project", check)
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    reports, outputs = run.calculate({}, parents, tmp_path, True, None)
    rebuilt, _ = run.calculate({}, parents, tmp_path, True, None, audit=True, stored=outputs)
    assert rebuilt == reports
    for report in reports.values():
        comparison = report["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        stats = report["execution"]["totals"]["arms"]
        assert stats["baseline"]["closed"] > stats["candidate"]["closed"] > 0
        assert stats["baseline"]["no_fill"] > 0
        assert stats["baseline"]["unresolved_after_entry"] > 0
        assert stats["candidate"]["unresolved_before_entry"] > stats["baseline"]["unresolved_before_entry"]

    outputs["S1"][0]["questions"][0]["candidate"]["net"] = "1"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, audit=True, stored=outputs)


def test_snapshot_pins_bind_both_archives_to_existing_read_only_index(tmp_path, monkeypatch):
    manifest = [{"name": kind, "sha256": kind.lower() * 32, "size": 1} for kind in ("CB", "CC", "CX")]
    manifest[2]["sha256"] = "a" * 64
    archives = {row["name"]: {"kind": row["name"], "compressed_sha256": row["sha256"],
                             "decoded_sha256": "b" * 64, "expected_chunks": 1, "expected_rows": 2,
                             "expected_daily_rows": 0} for row in manifest[1:]}
    proof = {"verified": True, "full_eof_verified": True, "input_manifest": manifest,
             "input_manifest_sha256": run.wire.digest(run.wire.encode(manifest)),
             "raw_byte_audit": {"verified": True, "full_eof_verified": True, "archives": archives,
                                "cache_sha256": "e" * 64, "raw_keys": 2}}
    publication = {"unit_id": "EI-SNAPSHOT-1", "invented_only": False, "status": "completed",
                   "execution_host": "local_wsl_only", "verification_complete": True, "full_bridge": True,
                   "independent_verification": proof}
    audit = {"unit_id": "EI-SNAPSHOT-1", "invented_only": False,
             "schema": "ei-snapshot-supplemental-saved-receipt-audit-v2", "status": "PASS", "publication_sha256": "p"}
    spec = {"snapshot_receipts": {"publication_sha256": "p", "audit_sha256": "q"},
            "price_archive": {"relative_path": "cache/EI-SNAPSHOT-1-actual1/received/CC", "compressed_sha256": "cc" * 32,
                              "decoded_sha256": "b" * 64, "chunks": 1, "rows": 2}}
    receipts = {"p": publication, "q": audit}
    monkeypatch.setattr(run.saved, "_metadata", lambda _path, pin: receipts[pin])
    cache = tmp_path / "cache" / "EI-SNAPSHOT-1-actual1"
    (cache / "received").mkdir(parents=True)
    for kind in archives:
        (cache / "received" / kind).write_bytes(b"x")

    class Reader:
        verified = False

        def assert_unchanged(self):
            self.verified = True

    reader = Reader()

    @contextmanager
    def open_index(path, inputs, _checkpoint, *, expected_cache_sha256):
        assert path == cache / "raw-byte-chunks.sqlite"
        assert set(inputs) == {"CC", "CX"}
        assert all(path == cache / "received" / kind and pin.kind == kind for kind, (path, pin) in inputs.items())
        assert expected_cache_sha256 == "e" * 64
        yield reader

    monkeypatch.setattr(run.sources, "audit_index", open_index)
    with run._sessions(spec, tmp_path, False) as loaded:
        assert loaded is reader
    assert reader.verified

    marker = tmp_path / "output" / "EI-SNAPSHOT-1-actual1" / "unresolved.json"
    marker.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="snapshot predecessor is unresolved"):
        with run._sessions(spec, tmp_path, False):
            marker.touch()
    with pytest.raises(ValueError, match="snapshot predecessor is unresolved"), run._sessions(spec, tmp_path, False):
        pytest.fail("unresolved snapshot reached the read-only index")
    marker.unlink()

    audit["publication_sha256"] = "other"
    with pytest.raises(ValueError, match="publication or audit differs"), run._sessions(spec, tmp_path, False):
        pytest.fail("mismatched receipt reached the read-only index")
