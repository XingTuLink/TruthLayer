"""Score an end-to-end HCR run from human labels.

Reads the run artifacts produced by scripts/e2e_eval.py and a hand-written
labels JSON that adjudicates EVERY reported drift against the source documents:

    {
      "0": {"verdict": "confirmed", "actionable": true, "gt": "G4",
            "note": "直营/经销数据迁移报价确实不一致"},
      "1": {"verdict": "false_alarm", "actionable": false,
            "fp_category": "extraction_error", "gt": null,
            "note": "..."}
    }

verdict is "confirmed" (a real, human-validated problem) or "false_alarm".
gt optionally ties the finding to one of the planted scenarios in
examples/eval_corpus/GROUND_TRUTH.md (G1..G8) for end-to-end recall.

Metrics:
* HCR (Human Confirmation Rate) = confirmed / reported  (≈ end-to-end precision)
* Actionable rate = actionable / reported
* per-drift-type confirmation
* false-alarm category tally
* planted-scenario recall (how many of G1..G8 surfaced at all), which captures
  extraction losses the zero-LLM golden gate cannot see.

Usage::

    ./.venv/Scripts/python scripts/e2e_score.py
        --run build/e2e_eval/<timestamp> --labels path/to/labels.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

# Scenarios planted in the corpus (see GROUND_TRUTH.md).
GT_SCENARIOS: dict[str, str] = {
    "G1": "手册 2023 被 2025 显式取代（superseded/stale）",
    "G2": "价格表 2024 被 2025 取代",
    "G3": "价格表 2025 被 2026 取代",
    "G4": "数据迁移服务直营 800/经销 950 跨源冲突",
    "G5": "餐补标准 2025-06-30 到期（confirmed_stale）",
    "G6": "高温津贴复核 >365 天（possibly_stale）",
    "G7": "延保促销价格 >90 天（pricing possibly_stale）",
    "G8": "星云客服中心同名/同热线重复建档（duplicate）",
}

CONFIRMED = "confirmed"
FALSE_ALARM = "false_alarm"


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Run artifact dir")
    parser.add_argument("--labels", type=Path, required=True, help="Labels JSON")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write score.json (default: <run>/score.json)",
    )
    args = parser.parse_args()

    drifts = _load_jsonl(args.run / "drifts.jsonl")
    labels = json.loads(args.labels.read_text(encoding="utf-8"))

    if not drifts:
        raise SystemExit("no drifts in this run; nothing to score")

    confirmed = 0
    actionable = 0
    by_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"reported": 0, "confirmed": 0}
    )
    fp_categories: dict[str, int] = defaultdict(int)
    scenario_types: dict[str, set[str]] = defaultdict(set)
    adjudicated: list[dict] = []

    for drift in drifts:
        key = str(drift["idx"])
        if key not in labels:
            raise SystemExit(f"missing label for drift idx={key} ({drift['type']})")
        label = labels[key]
        verdict = label["verdict"]
        if verdict not in {CONFIRMED, FALSE_ALARM}:
            raise SystemExit(f"idx={key}: verdict must be confirmed/false_alarm")

        by_type[drift["type"]]["reported"] += 1
        is_confirmed = verdict == CONFIRMED
        if is_confirmed:
            confirmed += 1
            by_type[drift["type"]]["confirmed"] += 1
            if label.get("actionable"):
                actionable += 1
        else:
            cat = label.get("fp_category") or "uncategorized"
            fp_categories[cat] += 1

        gt = label.get("gt")
        if gt:
            if gt not in GT_SCENARIOS:
                raise SystemExit(f"idx={key}: unknown gt scenario {gt}")
            scenario_types[gt].add(drift["type"])

        adjudicated.append(
            {
                "idx": drift["idx"],
                "short_id": drift["short_id"],
                "type": drift["type"],
                "severity": drift["severity"],
                "subject": drift.get("subject"),
                "title": drift["title"],
                "verdict": verdict,
                "actionable": bool(label.get("actionable")),
                "gt": gt,
                "fp_category": label.get("fp_category") if not is_confirmed else None,
                "note": label.get("note"),
            }
        )

    total = len(drifts)
    hcr = confirmed / total
    actionable_rate = actionable / total
    per_type = {
        t: {
            "reported": v["reported"],
            "confirmed": v["confirmed"],
            "hcr": round(v["confirmed"] / v["reported"], 4) if v["reported"] else None,
        }
        for t, v in sorted(by_type.items())
    }
    scenarios = {
        gt: {
            "description": desc,
            "surfaced": gt in scenario_types,
            "types_surfaced": sorted(scenario_types.get(gt, set())),
        }
        for gt, desc in sorted(GT_SCENARIOS.items())
    }
    surfaced = sum(1 for g in scenarios.values() if g["surfaced"])

    score = {
        "reported": total,
        "confirmed": confirmed,
        "false_alarms": total - confirmed,
        "hcr": round(hcr, 4),
        "actionable": actionable,
        "actionable_rate": round(actionable_rate, 4),
        "gate_hcr_threshold": 0.60,
        "gate_hcr_passed": hcr > 0.60,
        "per_type": per_type,
        "false_alarm_categories": dict(sorted(fp_categories.items())),
        "planted_scenarios": len(GT_SCENARIOS),
        "planted_scenarios_surfaced": surfaced,
        "planted_scenario_recall": round(surfaced / len(GT_SCENARIOS), 4),
        "scenarios": scenarios,
        "adjudicated": adjudicated,
    }

    out_path = args.out or (args.run / "score.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(score, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )

    print(f"reported={total} confirmed={confirmed} false_alarms={total - confirmed}")
    print(
        f"HCR={hcr:.1%}  actionable_rate={actionable_rate:.1%}  "
        f"gate(>60%)={'PASS' if score['gate_hcr_passed'] else 'FAIL'}"
    )
    print("per type:")
    for t, v in per_type.items():
        print(f"  {t:<18} reported={v['reported']:<3} confirmed={v['confirmed']:<3} hcr={v['hcr']}")
    if fp_categories:
        print("false-alarm categories:")
        for cat, n in sorted(fp_categories.items()):
            print(f"  {cat}: {n}")
    print(
        f"planted scenario recall: {surfaced}/{len(GT_SCENARIOS)} "
        f"({score['planted_scenario_recall']:.0%})"
    )
    missing = [gt for gt, s in scenarios.items() if not s["surfaced"]]
    if missing:
        print("not surfaced: " + ", ".join(f"{g}（{scenarios[g]['description']}）" for g in missing))
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
