"""Count cost-clearing outcomes on the already fixed EI baseline."""

from collections import Counter
from datetime import date
from fractions import Fraction


GROUPS = ("all", "A", "B", "A_or_B", "other")
OUTCOMES = ("above", "equal", "below", "no_entry", "no_fill", "unsupported", "unresolved")
COST_BPS = 33
WEEKLY_TARGET = 15


def calculate(rows):
    """Keep all original coordinates, weeks, and unavailable observations."""
    panels = {group: {} for group in GROUPS}
    days, coordinates = set(), set()
    frames, questions = Counter(), Counter()
    for row, roster in rows:
        day, symbol = row["session"], row["symbol"]
        if (day, symbol) in coordinates:
            raise ValueError("Duplicate cost census coordinate")
        coordinates.add((day, symbol))
        days.add(day)
        year, number, _ = date.fromisoformat(day).isocalendar()
        week = f"{year}-W{number:02}"
        for panel in panels.values():
            panel.setdefault(week, dict.fromkeys(OUTCOMES, 0))
        frame_counts = dict(roster["frame_states"])
        if (len(frame_counts) != len(roster["frame_states"])
                or any(type(n) is not int or n <= 0 for n in frame_counts.values())
                or sum(frame_counts.values()) != row["original_frames"]):
            raise ValueError("Invalid cost census frame counts")
        frames.update(frame_counts)
        for question in row["questions"]:
            questions[question["status"]] += 1
            if question["status"] != "opportunity":
                continue
            arm = question["baseline"]
            status = arm["status"]
            if status == "closed":
                gross, basis, cost, net = (Fraction(arm[k]) for k in ("gross", "entry_notional", "cost", "net"))
                if basis <= 0 or cost * 10_000 != basis * COST_BPS or net != gross - cost:
                    raise ValueError("Invalid cost census closed payoff")
                status = "above" if net > 0 else "below" if net < 0 else "equal"
            elif status not in OUTCOMES[3:]:
                raise ValueError("Unexpected cost census entry state")
            context = question["episode_origin_context"]
            if context not in ("A", "B", "other"):
                raise ValueError("Unexpected cost census origin context")
            for group in ("all", context, "A_or_B") if context in ("A", "B") else ("all", context):
                panels[group][week][status] += 1
    groups = {}
    for group, weekly in panels.items():
        groups[group] = {
            "weekly": dict(sorted(weekly.items())),
            "totals": {status: sum(row[status] for row in weekly.values()) for status in OUTCOMES},
            "weeks_at_least_15": sum(row["above"] >= WEEKLY_TARGET for row in weekly.values()),
            "weeks_at_least_15_if_unresolved_clear": sum(
                row["above"] + row["unresolved"] + row["unsupported"] >= WEEKLY_TARGET for row in weekly.values()),
        }
    return {"coordinates": len(coordinates), "sessions": len(days), "weeks": len(panels["all"]),
            "frames": sum(frames.values()), "frame_states": dict(frames), "question_states": dict(questions),
            "groups": groups}
