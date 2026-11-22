"""Paired, abstract practice ladder for upgrade selection. No arena or weapon simulation."""

import argparse
import copy
import gzip
import json
import random
from pathlib import Path

from bot import OvalRunner
from evaluate import FlatArena


PROFILES = {
    # Mean quiet window in seconds, HP bill multiplier, benefit of a tempo upgrade.
    "packed": (3.5, .92, .10),
    "intermittent": (7.5, 1.00, .13),
    "tempo-rich": (6.0, 1.06, .27),
}


def schedule(seed):
    rng = random.Random(seed)
    return [(rng.lognormvariate(-.18, .65), rng.uniform(.65, 1.45), rng.random(),
             rng.random(), rng.random(), rng.random()) for _ in range(15)]


def trial(bot, example, seed, profile, pocket=None):
    rules = bot.config
    own = copy.deepcopy(example["self"])
    stats = own["stats"]
    base = rules["mech"]["hp"]
    own.update(hp=base, max_hp=base, cubes=0, upgrades=[])
    gaps, pressure, tempo_value = PROFILES[profile]
    opening = random.Random(seed + 812349)
    fills = opening.random() < .5
    gear = opening.choice((None, "lightning", "laser"))
    eligible = {u["id"]: u for u in rules["upgrades"] if u.get("requires") in (None, "gun", gear)}
    drafts = []
    pocket_cubes = pocket_cost = 0
    tempo = 1.
    obs = {"self": own, "alive": 16, "zone": {"dps": 20}}
    bot.retreating = False

    def collect(count):
        nonlocal tempo, base
        for _ in range(count):
            own["cubes"] += 1
            if own["cubes"] <= rules["cubes"]["stat_cap"]:
                gain = base * rules["cubes"]["hp_per_cube"]
                own["max_hp"] += gain
                own["hp"] += gain
            if own["cubes"] not in rules["cubes"]["upgrade_thresholds"]:
                continue
            deck = sorted(eligible)
            random.Random(seed * 191 + own["cubes"]).shuffle(deck)
            choices = [name for name in deck if own["upgrades"].count(name) < eligible[name]["max"]][:3]
            obs["offer"] = {"choices": [{"id": name} for name in choices]}
            bot.retreating = own["hp"] < .4 * own["max_hp"] or (bot.retreating and own["hp"] < .76 * own["max_hp"])
            card = choices[bot.pick_upgrade(obs)]
            drafts.append(card)
            own["upgrades"].append(card)
            for effect in eligible[card]["effects"]:
                prefix, _, key = effect["path"].partition(".")
                if prefix == "mech":
                    stats[key] = stats[key] * effect.get("mul", 1) + effect.get("add", 0)
                    if key == "hp":
                        previous = own["max_hp"]
                        base *= effect["mul"]
                        own["max_hp"] = previous * effect["mul"]
                        if fills:
                            own["hp"] += own["max_hp"] - previous
            if eligible[card].get("requires"):
                tempo *= 1 + tempo_value
            elif card in ("servos","jets","reactor","capacitor"):
                tempo *= 1 + .4 * tempo_value

    own["in_zone"] = True
    collect(opening.choices((0, 1, 2), (.40,.45,.15))[0])
    for index, (interval, magnitude, weather, reward, supply, strain) in enumerate(schedule(seed)):
        obs["alive"] = 16 - index
        own["in_zone"] = weather > .12
        if own["in_zone"]:
            recovery = max(0., gaps * interval - stats["regen_delay"]) * stats["regen_per_s"]
            own["hp"] = min(own["max_hp"], own["hp"] + recovery)
        if index in (4, 8, 12) and supply < .13:
            collect(rules["drops"]["cubes"])
            own["hp"] = own["max_hp"]
        tier = min(3, index // 4)
        cube_bonus = 1 + min(own["cubes"], rules["cubes"]["stat_cap"]) * rules["cubes"]["damage_per_cube"]
        bill = rules["mech"]["hp"] * (.265 + .068 * tier) * magnitude * pressure
        if index > 7 and strain < .35:
            bill *= 1.5
        own["hp"] -= bill * (1 - stats["armor"]) / (tempo * cube_bonus)
        if not own["in_zone"]:
            own["hp"] -= 70 + 18 * tier
        if own["hp"] <= 0:
            return [index, drafts, pocket_cubes, pocket_cost]
        if pocket is None:
            collect(0 if reward < .24 else (1 if reward < .55 else 2))
        else:
            count, cost, exhausted = pocket(bot, example, seed, index, own, profile)
            own["hp"] -= cost
            pocket_cost += cost
            pocket_cubes += count
            if exhausted:
                return [index + 1, drafts, pocket_cubes, pocket_cost]
            collect(count)
            if own["hp"] <= 0:
                return [index + 1, drafts, pocket_cubes, pocket_cost]
    return [15, drafts, pocket_cubes, pocket_cost]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True, type=Path)
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with gzip.open(args.kit / "check/stream.jsonl.gz", "rt") as stream:
        hello, example = json.loads(next(stream)), json.loads(next(stream))
    bot = OvalRunner(hello, FlatArena())
    with args.output.open("w") as output:
        for seed in range(args.start, args.start + args.count):
            results = {p: trial(bot, example, seed, p) for p in PROFILES}
            output.write(json.dumps([seed, results]) + "\n")


if __name__ == "__main__":
    main()
