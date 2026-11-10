"""Combine audited EK folds without treating overlapping rosters as replications."""

from collections import defaultdict
from fractions import Fraction

from research import intraday_campaign_wsl_transport as wire
from research import intraday_recovery_report as report
from research.intraday_recovery_snapshot import _rows


EVALUATION = "ek-evaluation.jsonl"


def collect(folds):
    """Each input is (fold, output directory, audited comparison report)."""
    records, scores = defaultdict(list), defaultdict(list)
    days, sources = [], []
    wire.require(tuple(fold for fold, _, _ in folds) == (1, 2, 3, 4, 5), "EK requires five audited folds")
    for fold, directory, summary in folds:
        evaluation = summary["evaluation_sessions"]
        wire.require(len(evaluation) == (32 if fold <= 2 else 31) and sorted(set(evaluation)) == evaluation
                     and (not days or days[-1] < evaluation[0]), "EK fold calendar differs")
        days.extend(evaluation)
        pin = summary["files"][EVALUATION]
        path = directory / EVALUATION
        wire.require(wire.file_digest(path, 512 * wire.MIB, lambda _:None) == (pin["sha256"], pin["size"]),
                     "EK evaluation output differs from audited bytes")
        seen = set()
        for row in _rows(path):
            wire.require(row["key"] not in seen and row["arms"] and row["forecast_scores"], "EK evaluation row differs")
            seen.add(row["key"])
            for collection, field in ((records, "arms"), (scores, "forecast_scores")):
                for name, record in row[field].items():
                    wire.require(record["key"] == row["key"] and record["session"] in evaluation and record["fold"] == fold,
                                 "EK score coordinate differs from its original fold")
                    collection[name].append(record)
        wire.require(len(seen) == pin["rows"] == 20 * len(evaluation), "EK fold population differs")
        sources.append(pin)

    def summaries(collection):
        result = {}
        for name, rows in sorted(collection.items()):
            wire.require(len(rows) == 3140 and len({row["key"] for row in rows}) == 3140, "EK arm omitted original opportunities")
            item = report.summarize(rows, days)
            report.verify_summary(rows, days, item)
            result[name] = item
        return result

    contrasts = (("own_unfiltered", "old_rank"), ("own_unfiltered", "old_weak"),
        ("own_common", "own_unfiltered"), ("empirical_chart", "own_unfiltered"),
        ("lightgbm_chart", "own_unfiltered"), ("sequence_chart", "own_unfiltered"),
        ("lightgbm_chart_peer", "lightgbm_chart_common"), ("sequence_chart_peer", "sequence_chart_common"),
        ("sequence_chart", "sequence_order_removed"))
    holding = tuple((name, name.removesuffix("_managed")) for name in records if name.endswith("_managed"))
    pairs = {f"{left}_vs_{right}": report.pair(records[left],records[right]) for left,right in (*contrasts,*holding)}
    return {"calendar":days,"sources":sources,"arms":summaries(records),
            "forecast_scores":{name:item["forecast"] for name,item in summaries(scores).items()},"pairs":pairs}
