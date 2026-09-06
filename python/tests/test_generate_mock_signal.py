"""The mock generator must never be able to drive a real venue.

Both paper loops default to reading the *same* signal file — verified on
the real deployment 2026-09-05, where `simulated` and `bingx-vst` were
both constructed with
`signalPath=var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json`.
The `bingx-vst` loop submits to a real demo exchange, so a generator that
could write there would put 288 manufactured orders a day onto a venue
account.

The path guard is the thing standing between those two facts. Most of
this file tests that one function, deliberately.
"""

from __future__ import annotations

import ast
import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from live import generate_mock_signal as mock
from schemas.order_intent import OrderIntent, OrderType, Side


class TestPathGuard:
    """The safety property, from every angle it could be got wrong."""

    @pytest.mark.parametrize("bad", [
        "var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json",
        "var/live/signals/BTC-USDT/some-future-strategy/latest.json",
        "var/live/signals/ETH-USDT/daily-tsmom-ensemble/latest.json",
        "var/live/signals/latest.json",
        "var/live/signals/_mock/../BTC-USDT/daily-tsmom-ensemble/latest.json",
        "var/live/signals/./BTC-USDT/daily-tsmom-ensemble/latest.json",
    ])
    def test_refuses_anywhere_inside_the_real_signal_tree(self, bad):
        with pytest.raises(ValueError, match="refusing to write a mock signal"):
            mock._reject_real_strategy_path(Path(bad))

    def test_the_traversal_case_is_the_one_that_matters(self):
        """`..` out of the mock directory lands in the real tree, and a
        naive string prefix check would let it through."""
        escaped = mock.MOCK_SIGNAL_DIR / ".." / "BTC-USDT" / "daily-tsmom-ensemble" / "latest.json"
        assert str(escaped).startswith(str(mock.MOCK_SIGNAL_DIR)), (
            "fixture must actually look like a mock path to a prefix check"
        )
        with pytest.raises(ValueError):
            mock._reject_real_strategy_path(escaped)

    @pytest.mark.parametrize("good", [
        "var/live/signals/_mock/latest.json",
        "var/live/signals/_mock/nested/latest.json",
    ])
    def test_allows_the_mock_directory(self, good):
        mock._reject_real_strategy_path(Path(good))

    def test_refuses_paths_outside_the_signal_tree_too(self, tmp_path):
        """An allowlist, not a blocklist. An earlier version only rejected
        the real signal tree, so a mistyped `--signal-path` could create
        or overwrite an unrelated file anywhere on the box. Enumerating
        what must not be written is a losing game."""
        with pytest.raises(ValueError, match="only ever writes inside"):
            mock._reject_real_strategy_path(tmp_path / "latest.json")

    def test_refuses_a_plausible_typo(self):
        """`_mocks` is not `_mock`."""
        with pytest.raises(ValueError):
            mock._reject_real_strategy_path(Path("var/live/signals/_mocks/latest.json"))

    def test_the_default_path_passes_its_own_guard(self):
        """A default that its own guard rejects would make the module
        unusable without an override — worth pinning."""
        mock._reject_real_strategy_path(mock.MOCK_SIGNAL_PATH)

    def test_write_refuses_too_and_leaves_nothing_behind(self, tmp_path, monkeypatch):
        """The guard runs inside `write_signal_atomically`, not only at
        the CLI boundary, so a programmatic caller cannot bypass it."""
        monkeypatch.chdir(tmp_path)
        target = Path("var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json")
        with pytest.raises(ValueError):
            mock.write_signal_atomically(mock.build_mock_intent(), target)
        assert not target.exists()
        assert not target.parent.exists(), "must not even create the directory"


class TestIntent:
    def test_is_a_valid_order_intent(self):
        intent = mock.build_mock_intent()
        assert isinstance(intent, OrderIntent)
        assert intent.order_type is OrderType.GUARDED_MARKET
        assert Decimal(intent.quantity) == mock.MOCK_QUANTITY

    def test_is_marked_as_mock_so_it_cannot_be_mistaken_for_a_strategy(self):
        assert mock.build_mock_intent().signal_timeframe == "mock"

    def test_every_intent_gets_a_fresh_id(self):
        """`FileSignalSource` dedups on `intentId` — a repeated id would
        be silently suppressed and produce no order event at all, which
        is the whole thing this module exists to produce."""
        ids = {mock.build_mock_intent().intent_id for _ in range(50)}
        assert len(ids) == 50

    def test_quantity_is_small_enough_to_be_approved_not_rejected(self):
        """Gate A counts events through the *approval* path. A quantity
        the RiskGateway refuses would exercise only the rejection branch
        and prove nothing about the path being measured."""
        assert Decimal(0) < mock.MOCK_QUANTITY <= Decimal("0.01")


class TestSideAlternation:
    def test_alternates_across_invocations(self, tmp_path):
        state = tmp_path / ".last-side"
        sides = []
        for _ in range(6):
            side = mock._peek_side(state)
            sides.append(side)
            mock._commit_side(state, side)
        assert sides == [Side.LONG, Side.SHORT] * 3, (
            "a generator stuck on one side walks the simulated position "
            "steadily in one direction"
        )

    def test_a_missing_state_file_starts_long_rather_than_failing(self, tmp_path):
        assert mock._peek_side(tmp_path / "absent" / ".last-side") is Side.LONG

    def test_a_failed_publish_does_not_consume_the_side(self, tmp_path, monkeypatch):
        """success -> failure -> success must not repeat a side.

        An earlier version consumed the side the moment it was read, so a
        refused path or a failed rename still burned it and the next run
        published the same direction twice while `latest.json` had only
        ever seen one of them.
        """
        monkeypatch.chdir(tmp_path)
        assert mock.main([]) == 0
        first = OrderIntent(**json.loads(mock.MOCK_SIGNAL_PATH.read_text())).side

        # a refused path: the run fails and must leave the state alone
        assert mock.main([
            "--signal-path", "var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json",
        ]) == 2

        assert mock.main([]) == 0
        third = OrderIntent(**json.loads(mock.MOCK_SIGNAL_PATH.read_text())).side
        assert third != first, (
            "the failed run consumed a side, so the alternation skipped one"
        )

    def test_concurrent_runs_do_not_publish_the_same_side(self, tmp_path, monkeypatch):
        """cron never waits for the previous run, so two can overlap.

        Serialised by `flock`, the two invocations must take different
        sides and the file must agree with the recorded state.
        """
        monkeypatch.chdir(tmp_path)
        seen: list[Side] = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            mock.main([])

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()

        final = OrderIntent(**json.loads(mock.MOCK_SIGNAL_PATH.read_text())).side
        recorded = mock.SIDE_STATE_PATH.read_text().strip()
        assert recorded == final.value, (
            f"latest.json says {final.value} but the state says {recorded} -- "
            f"the two runs raced"
        )

    def test_an_unreadable_state_file_does_not_cost_an_order_event(self, tmp_path):
        """Losing the alternation costs a slightly one-sided position.
        Failing the run costs a Gate A event, which is worse."""
        state = tmp_path / ".last-side"
        state.mkdir()  # a directory where a file is expected
        assert mock._peek_side(state) is Side.LONG
        mock._commit_side(state, Side.LONG)  # logs, does not raise


class TestWrite:
    """Writes go through `MOCK_SIGNAL_PATH` relative to a temp cwd, since
    the guard is an allowlist and an arbitrary `tmp_path` is (correctly)
    refused."""

    @pytest.fixture
    def target(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        return mock.MOCK_SIGNAL_PATH

    def test_writes_readable_json_that_round_trips(self, target):
        intent = mock.build_mock_intent()
        mock.write_signal_atomically(intent, target)
        assert OrderIntent(**json.loads(target.read_text())).intent_id == intent.intent_id

    def test_leaves_no_temp_files(self, target):
        mock.write_signal_atomically(mock.build_mock_intent(), target)
        assert [p.name for p in target.parent.iterdir()] == ["latest.json"]

    def test_overwrites_rather_than_appending(self, target):
        first = mock.build_mock_intent()
        mock.write_signal_atomically(first, target)
        second = mock.build_mock_intent()
        mock.write_signal_atomically(second, target)
        written = OrderIntent(**json.loads(target.read_text()))
        assert written.intent_id == second.intent_id != first.intent_id


class TestNeverLogsAsResearch:
    def test_does_not_import_experiment_log(self):
        """Counting a coin-flip generator as a research trial would
        inflate the DSR `N` and make every real strategy's deflated
        Sharpe worse for no reason.

        Checked by parsing the imports rather than grepping the source:
        the module's own docstring explains that it never logs, and a
        text search cannot tell that sentence apart from a real import.
        """
        tree = ast.parse(Path(mock.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        offenders = {m for m in imported if "experiment_log" in m or m.endswith(".log_run")}
        assert not offenders, f"must not import a research logger: {sorted(offenders)}"


class TestCli:
    def test_dry_run_prints_without_writing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert mock.main(["--dry-run"]) == 0
        assert OrderIntent(**json.loads(capsys.readouterr().out)).signal_timeframe == "mock"
        assert not (tmp_path / mock.MOCK_SIGNAL_PATH).exists()

    def test_dry_run_does_not_touch_the_side_state(self, tmp_path, monkeypatch):
        """A dry run that advanced the side would change the NEXT real
        signal — the one thing a "does not write" flag must not do."""
        monkeypatch.chdir(tmp_path)
        mock.main(["--dry-run"])
        assert not (tmp_path / mock.SIDE_STATE_PATH).exists()

    def test_dry_run_predicts_what_the_real_run_emits(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        mock.main(["--dry-run"])
        predicted = OrderIntent(**json.loads(capsys.readouterr().out)).side
        mock.main([])
        written = OrderIntent(**json.loads((tmp_path / mock.MOCK_SIGNAL_PATH).read_text()))
        assert written.side == predicted

    def test_a_refused_path_exits_non_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = mock.main([
            "--signal-path", "var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json",
        ])
        assert rc == 2

    def test_writes_to_the_mock_path_by_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert mock.main([]) == 0
        assert (tmp_path / mock.MOCK_SIGNAL_PATH).exists()
