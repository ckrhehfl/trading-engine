"""Independent EN auditor witnesses using invented saved-record publications."""

from copy import deepcopy
from fractions import Fraction

import pytest

from research import intraday_event_value_audit as producer
from research import intraday_event_value_reference as reference
from research.intraday_event_value_fixtures import case


def _bundle(index=0, **kwargs):
    original = case(index, **kwargs)
    event, fold = original["event"], original["fold"]
    causal = [producer.causal_query(event, wrapper, original["predictions"].get(wrapper["key"]), fold)
              for wrapper in original["wrappers"]]
    choice = producer.select_event(event, causal)
    measured = [producer.measure_query(row, original["labels"][row["key"]], choice) for row in causal]
    ledgers = {tuple(key.split("|")): row for key, row in original["ledgers"].items()}
    episode = producer.measure_episode(event, choice, {row["key"]: row for row in measured},
                                       ledgers, original["head_statuses"], fold)
    return original, causal, choice, measured, ledgers, episode


def _verify(bundle):
    original, causal, choice, measured, ledgers, episode = bundle
    for wrapper, saved in zip(original["wrappers"], causal, strict=True):
        assert reference.verify_causal(original["event"], wrapper, original["predictions"].get(wrapper["key"]),
                                       original["fold"], saved)
    assert reference.verify_choice(original["event"], causal, choice)
    assert choice["trace"] == original["trace"]
    for row, saved in zip(causal, measured, strict=True):
        assert reference.verify_query(row, original["labels"][row["key"]], choice, saved)
    assert reference.verify_episode(original["event"], choice, {row["key"]: row for row in measured},
                                    ledgers, original["head_statuses"], original["fold"], episode)


@pytest.mark.parametrize("variant", range(18))
def test_independent_original_record_reconstruction(variant):
    _verify(_bundle(variant))


def test_audit_does_not_call_producer_calculations(monkeypatch):
    bundle = _bundle()
    original, _, _, queries, _, episode = bundle
    calendar = [original["event"]["session"], "2024-01-15"]
    summary = producer.summarize(calendar, queries, [episode], {})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Independent audit called a producer calculation")

    for name in ("causal_query", "select_event", "measure_query", "measure_episode", "summarize"):
        monkeypatch.setattr(producer, name, forbidden)
    _verify(bundle)
    assert reference.verify_summary(calendar, queries, [episode], {}, summary)


def test_unknown_context_and_first_positive_trace_cannot_be_rewritten():
    original, causal, choice, *_ = _bundle(4)
    assert choice["trace"] == [] and choice["scope_frames"] == 0
    altered = deepcopy(causal)
    altered[0]["scope_supported"] = True
    with pytest.raises(ValueError, match="choice"):
        reference.verify_choice(original["event"], altered, choice)
    original, causal, choice, *_ = _bundle(15)
    assert choice["action"] == "immediate"  # Positive ties are deterministic.
    altered = deepcopy(choice)
    altered["key"] = causal[-1]["key"]
    with pytest.raises(ValueError, match="choice.key"):
        reference.verify_choice(original["event"], causal, altered)
    with pytest.raises(ValueError, match="duplicate"):
        reference.verify_choice(original["event"], causal + [causal[0]], choice)


def test_original_denominator_and_terminal_zero_semantics_fail_closed():
    original, causal, choice, measured, *_ = _bundle(6)
    last = causal[-1]
    assert last["terminal"] and measured[-1]["heads"]["net_immediate"]["target"] is None
    label = deepcopy(original["labels"][last["key"]])
    label["training_labels"]["net_immediate"] = "0"
    with pytest.raises(ValueError, match="training target semantics"):
        reference.verify_query(last, label, choice, measured[-1])
    original, causal, choice, measured, *_ = _bundle(1)
    label = deepcopy(original["labels"][causal[0]["key"]])
    label["targets"]["wait3"]["denominator"] = "1"
    with pytest.raises(ValueError, match="denominator"):
        reference.verify_query(causal[0], label, choice, measured[0])


def test_cash_is_repriced_instead_of_trusting_matching_totals():
    original, _, choice, measured, ledgers, episode = _bundle()
    altered = deepcopy(ledgers)
    arm = producer.ARM_KEYS[0]
    report = altered[arm]["execution"]
    report["sales"][0]["price"] = str(Fraction(report["sales"][0]["price"]) + 1)
    with pytest.raises(ValueError, match="sale leg cash"):
        reference.verify_episode(original["event"], choice, {row["key"]: row for row in measured}, altered,
                                 original["head_statuses"], 0, episode)
    altered = deepcopy(episode)
    altered["decomposition"]["entry_effect"] = "1"
    with pytest.raises(ValueError, match="entry_effect"):
        reference.verify_episode(original["event"], choice, {row["key"]: row for row in measured}, ledgers,
                                 original["head_statuses"], 0, altered)


def test_all_calendar_summary_and_exact_cash_tampering():
    bundles = [_bundle(i, day="2024-01-02" if i < 9 else "2024-01-03", symbol=f"INVENTED{i:02}")
               for i in range(18)]
    queries = [row for bundle in bundles for row in bundle[3]]
    episodes = [bundle[-1] for bundle in bundles]
    calendar = ["2024-01-02", "2024-01-03", "2024-01-15"]
    diagnostics = {"0": {"initial_loss": 0.25, "final_loss": 0.125, "status": "invented"}}
    summary = producer.summarize(calendar, queries, episodes, diagnostics)
    assert reference.verify_summary(calendar, queries, episodes, diagnostics, summary)
    arm = "|".join(producer.ARM_KEYS[0])
    assert summary["weekly"]["2024-W03"][arm]["fills"] == 0
    altered = deepcopy(summary)
    altered["weekly"]["2024-W03"][arm]["fills"] = 1
    with pytest.raises(ValueError, match="weekly"):
        reference.verify_summary(calendar, queries, episodes, diagnostics, altered)
    altered = deepcopy(summary)
    altered["arms"][arm]["net_cash"] = str(Fraction(altered["arms"][arm]["net_cash"]) + Fraction(1, 10**20))
    with pytest.raises(ValueError, match="net_cash"):
        reference.verify_summary(calendar, queries, episodes, diagnostics, altered)
    altered = deepcopy(summary)
    altered["fit_diagnostics"]["0"]["final_loss"] += 1e-14
    with pytest.raises(ValueError, match="fit_diagnostics"):
        reference.verify_summary(calendar, queries, episodes, diagnostics, altered)


def test_weights_equal_dates_clusters_original_episodes_and_frames():
    rows = []
    for name, day, symbol, repeats, prediction in (
            (0, "2024-01-02", "ONE", 4, 1), (1, "2024-01-02", "ONE", 1, 3),
            (2, "2024-01-02", "TWO", 1, 10), (3, "2024-01-03", "ONE", 1, 100)):
        bundle = _bundle(name, day=day, symbol=symbol)
        for row in bundle[3][:repeats]:
            row = deepcopy(row)
            row["heads"]["net_immediate"].update(prediction=str(prediction), target="0", scored=True)
            rows.append(row)
    stats = reference._head_summary(rows, "net_immediate")
    assert stats["prediction_mean"] == 53  # ((average(1, 3) + 10) / 2 + 100) / 2
    assert stats["scored"] == {"frames": 7, "episodes": 4, "clusters": 3, "dates": 2}


def test_summary_duplicate_and_missing_selected_keys_are_rejected():
    original, _, _, measured, _, episode = _bundle()
    days = [original["event"]["session"]]
    summary = producer.summarize(days, measured, [episode], {})
    with pytest.raises(ValueError, match="duplicate"):
        reference.verify_summary(days, measured + [measured[0]], [episode], {}, summary)
    with pytest.raises(ValueError, match="selected query missing"):
        reference.verify_summary(days, measured[1:], [episode], {}, summary)
