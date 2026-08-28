"""Tests for `live.generate_operational_mock_signal`.

The contamination guards get the most attention. A mock generator that
could be aimed at a real strategy's signal path, or whose output could be
mistaken for strategy evidence, is one misconfiguration away from
destroying the record it was meant to protect.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from live import generate_operational_mock_signal as mock
from schemas.order_intent import OrderIntent, OrderType, Side


class TestContaminationGuards:
    def test_the_signal_path_is_under_an_operational_mock_directory(self):
        assert "operational-mock" in str(mock.SIGNAL_PATH)

    def test_the_signal_path_is_not_a_strategy_path(self):
        """`daily-tsmom-ensemble`'s own path must be unreachable from here."""
        assert "daily-tsmom" not in str(mock.SIGNAL_PATH)

    def test_no_argument_or_environment_can_redirect_the_output(self):
        """The path is a module constant, not a parameter -- `main` takes
        only `--dry-run`."""
        parser_args = mock.main.__doc__ or ""
        del parser_args
        with pytest.raises(SystemExit):
            mock.main(["--signal-path", "/tmp/anywhere"])

    def test_every_intent_is_self_identifying_as_a_mock(self):
        for i in range(4):
            assert mock.build_intent(i).signal_timeframe == "MOCK-NOT-A-STRATEGY"

    def test_the_marker_is_not_a_plausible_timeframe(self):
        """A reader skimming a record must not mistake it for '1d'."""
        assert mock.MOCK_MARKER not in ("1m", "5m", "15m", "1h", "1d")


class TestIntent:
    def test_alternates_side_so_positions_open_and_close(self):
        sides = [mock.build_intent(i).side for i in range(6)]
        assert sides == [Side.LONG, Side.SHORT] * 3

    def test_is_a_guarded_market_order_with_no_limit(self):
        intent = mock.build_intent(0)
        assert intent.order_type is OrderType.GUARDED_MARKET
        assert intent.limit_price is None

    def test_size_stays_far_inside_the_canary_notional_cap(self):
        """0.001 BTC is ~0.1% of a 100k paper account against a 2% cap."""
        assert mock.QUANTITY == Decimal("0.001")

    def test_each_intent_gets_a_fresh_id(self):
        assert mock.build_intent(0).intent_id != mock.build_intent(0).intent_id

    def test_created_at_is_timezone_aware(self):
        assert mock.build_intent(0).created_at.tzinfo is not None

    def test_an_explicit_now_is_honoured(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert mock.build_intent(0, now=now).created_at == now


class TestCounter:
    def test_missing_counter_starts_at_zero(self, tmp_path: Path):
        assert mock._read_counter(tmp_path / "absent.json") == 0

    @pytest.mark.parametrize("body", ["", "not json", "{}", '{"emitted": "x"}', "[]"])
    def test_a_corrupt_counter_falls_back_to_zero_rather_than_crashing(
        self, tmp_path: Path, body: str
    ):
        path = tmp_path / "counter.json"
        path.write_text(body)
        assert mock._read_counter(path) == 0

    def test_round_trips(self, tmp_path: Path):
        path = tmp_path / "counter.json"
        mock._write_counter(path, 7)
        assert mock._read_counter(path) == 7

    def test_a_restart_continues_alternating_rather_than_repeating(self, tmp_path: Path):
        """A loop stuck on one side would accumulate a position instead of
        opening and closing, exercising neither the flattening path nor
        reconciliation."""
        path = tmp_path / "counter.json"
        mock._write_counter(path, 3)
        assert mock.build_intent(mock._read_counter(path)).side is Side.SHORT


class TestAtomicWrite:
    def test_writes_a_readable_intent(self, tmp_path: Path):
        target = tmp_path / "latest.json"
        intent = mock.build_intent(0)
        mock.write_atomically(intent, target)
        assert OrderIntent.model_validate_json(target.read_text()) == intent

    def test_creates_the_parent_directory(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "latest.json"
        mock.write_atomically(mock.build_intent(0), target)
        assert target.exists()

    def test_leaves_no_temp_file_behind(self, tmp_path: Path):
        target = tmp_path / "latest.json"
        mock.write_atomically(mock.build_intent(0), target)
        assert [p.name for p in tmp_path.iterdir()] == ["latest.json"]

    def test_overwrites_cleanly(self, tmp_path: Path):
        target = tmp_path / "latest.json"
        mock.write_atomically(mock.build_intent(0), target)
        mock.write_atomically(mock.build_intent(1), target)
        assert json.loads(target.read_text())["side"] == "SHORT"


class TestCli:
    def test_dry_run_writes_nothing(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setattr(mock, "SIGNAL_PATH", tmp_path / "latest.json")
        monkeypatch.setattr(mock, "COUNTER_PATH", tmp_path / "counter.json")
        assert mock.main(["--dry-run"]) == 0
        assert not (tmp_path / "latest.json").exists()
        assert "MOCK-NOT-A-STRATEGY" in capsys.readouterr().out

    def test_a_real_run_writes_the_signal_and_advances_the_counter(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(mock, "SIGNAL_PATH", tmp_path / "latest.json")
        monkeypatch.setattr(mock, "COUNTER_PATH", tmp_path / "counter.json")
        assert mock.main([]) == 0
        assert json.loads((tmp_path / "latest.json").read_text())["side"] == "LONG"
        assert mock._read_counter(tmp_path / "counter.json") == 1
        assert mock.main([]) == 0
        assert json.loads((tmp_path / "latest.json").read_text())["side"] == "SHORT"
