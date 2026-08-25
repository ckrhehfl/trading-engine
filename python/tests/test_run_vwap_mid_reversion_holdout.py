"""Tests for `python/research/run_vwap_mid_reversion_holdout.py` -- the
real execution path that must call `verify_1m_gaps` before
`run_preregistered_holdout`, never the other way around, and must never
call the latter at all when the former raises.

Hermetic: `verify_gaps`/`execute_holdout` are injected fakes, so no test
here touches the real database, the real preregistration file's own
`load_preregistration` call aside (which is itself just a local JSON
read/validate -- no holdout data, no network), or `runs/experiments.jsonl`'s
real holdout-access claim tracking.
"""

from unittest.mock import Mock

import pytest

from research.run_vwap_mid_reversion_holdout import run_vwap_mid_reversion_holdout
from research.verify_1m_gaps import UnexpectedGapError

REAL_PREREGISTRATION_PATH = "../configs/research/preregistrations/vwap-mid-reversion-1m-holdout.json"


class TestGapCheckRunsFirst:
    def test_execute_holdout_is_never_called_when_verify_gaps_raises(self):
        def _raising_verify_gaps(start_ms, end_ms, *, db_path):
            raise UnexpectedGapError("simulated unexpected gap")

        execute_holdout = Mock()

        with pytest.raises(UnexpectedGapError, match="simulated unexpected gap"):
            run_vwap_mid_reversion_holdout(
                preregistration_path=REAL_PREREGISTRATION_PATH,
                verify_gaps=_raising_verify_gaps,
                execute_holdout=execute_holdout,
            )

        # The real, structural property this module exists for: no
        # holdout data was loaded and no single-access claim was written,
        # because execute_holdout (which wraps load_holdout_klines) was
        # never reached at all -- not merely "should not have run".
        execute_holdout.assert_not_called()

    def test_execute_holdout_is_called_with_the_loaded_preregistration_after_a_clean_gap_check(self):
        verify_gaps = Mock(return_value=[])
        execute_holdout = Mock(return_value="sentinel-result")

        result = run_vwap_mid_reversion_holdout(
            preregistration_path=REAL_PREREGISTRATION_PATH,
            db_path="some/db/path.sqlite3",
            verify_gaps=verify_gaps,
            execute_holdout=execute_holdout,
        )

        assert result == "sentinel-result"
        verify_gaps.assert_called_once()
        (start_ms, end_ms), kwargs = verify_gaps.call_args
        assert start_ms == 1732982400000
        assert end_ms == 1787585220000
        assert kwargs["db_path"] == "some/db/path.sqlite3"

        execute_holdout.assert_called_once()
        (prereg_arg,), holdout_kwargs = execute_holdout.call_args
        assert prereg_arg.preregistration_id == "vwap-mid-reversion-1m-holdout"
        assert holdout_kwargs["db_path"] == "some/db/path.sqlite3"
        assert holdout_kwargs["force_reclaim_reason"] is None

    def test_force_reclaim_reason_is_forwarded_to_execute_holdout(self):
        verify_gaps = Mock(return_value=[])
        execute_holdout = Mock(return_value="sentinel-result")

        run_vwap_mid_reversion_holdout(
            preregistration_path=REAL_PREREGISTRATION_PATH,
            force_reclaim_reason="deliberate test re-run",
            verify_gaps=verify_gaps,
            execute_holdout=execute_holdout,
        )

        _, holdout_kwargs = execute_holdout.call_args
        assert holdout_kwargs["force_reclaim_reason"] == "deliberate test re-run"
