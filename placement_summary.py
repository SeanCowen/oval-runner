"""Summarize paired placement JSONL with one independent statistical unit per seed."""

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def load(path):
    rows = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        key = row["seed"], row["field"]
        if key in rows:
            raise ValueError(f"Duplicate match {key}")
        rows[key] = row
    return rows


def summarize(reference, rows):
    if reference.keys() != rows.keys():
        raise ValueError("Paired seed and field identifiers must match")
    seeds = sorted({seed for seed, field in rows})
    fields = ("independent", "focused")
    scores = [statistics.mean(rows[seed, field]["points"] for field in fields) for seed in seeds]
    differences = [score - statistics.mean(reference[seed, field]["points"] for field in fields)
                   for seed, score in zip(seeds, scores)]
    times = sorted(value for row in rows.values() for value in row["ms"])
    dead = [row for row in rows.values() if row["cause"] in ("encounter", "mixed", "zone")]
    return dict(matches=len(rows), mean=statistics.mean(scores), se=statistics.stdev(scores)/math.sqrt(len(seeds)),
                gain=statistics.mean(differences), paired_se=statistics.stdev(differences)/math.sqrt(len(seeds)),
                fields={field: statistics.mean(rows[seed, field]["points"] for seed in seeds) for field in fields},
                p99_ms=times[math.ceil(.99 * len(times)) - 1], causes=dict(Counter(row["cause"] for row in rows.values())),
                median_death=statistics.median(row["end"] for row in dead),
                last_modes=dict(Counter(row["history"][-1][2] for row in dead)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("logs", type=Path, nargs="+")
    args = parser.parse_args()
    baseline = load(args.baseline)
    for log in args.logs:
        print(log.stem, json.dumps(summarize(baseline, load(log))))
