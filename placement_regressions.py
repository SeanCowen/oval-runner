"""Write existing harness outcomes for a single built bot for a case-level comparison."""

import argparse
import gzip
import json
from pathlib import Path

import economy
import race
import repair_eval
from bot import OvalRunner
from navigation import Arena


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with gzip.open(args.kit / "check/stream.jsonl.gz", "rt") as stream:
        hello, example = json.loads(next(stream)), json.loads(next(stream))
    arena = Arena(args.kit / "arena", hello["config"]["mech"])
    results = {}
    results["zone"] = [race.run_case(arena, hello, example, OvalRunner, case)
                       for case in race.cases(arena, hello["config"], 12109, 64)]
    results["supply"] = [race.capture(arena, hello, example, OvalRunner, case)
                         for case in race.supply_cases(arena, 12109, 64)]
    for close in (False, True):
        results["close" if close else "opening"] = [
            race.opening(hello, example, OvalRunner, 12109 + index, close) for index in range(64)]
    results["economy"] = [economy.simulate(arena, hello, example, OvalRunner, case, 5)[:3]
                          for case in economy.scenarios(arena, 40321, 96)]
    results["repair"] = [repair_eval.simulate(hello, example, seed + index, profile, fill)
                         for seed, fill in ((33851, False), (80669, True))
                         for profile in ("light", "mixed", "short") for index in range(96)]
    args.output.write_text(json.dumps(results))


if __name__ == "__main__":
    main()
