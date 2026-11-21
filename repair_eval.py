"""Compare repair decisions under identical scripted damage and crate opportunities."""

import argparse
import copy
import gzip
import json
import math
import random
import statistics
from pathlib import Path

from bot import OvalRunner


class Lane:
    def ground_height(self, point):
        return 0

    def visible(self, start, end):
        return True

    def clear(self, start, end):
        return True


def schedule(seed, profile):
    rng = random.Random(seed)
    # Gap in seconds, total incoming HP cost for each wave, mean time between crates.
    settings = {"light": ((12, 18), (140, 400), 9),
                "mixed": ((7, 16), (240, 720), 7),
                "short": ((3, 9), (180, 540), 6)}
    gap, severity, spacing = settings[profile]
    damage, crates = {}, {}
    tick = rng.randint(5, 45)
    for wave in range(6):
        hits = rng.randint(3, 8)
        value = rng.uniform(*severity) / hits
        for offset in range(hits):
            damage[tick] = value
            tick += rng.randint(2, 5)
        if wave < 5:
            tick += rng.randint(gap[0] * 10, gap[1] * 10)
    end = tick + rng.randint(0, 25)
    tick = rng.randint(0, spacing * 10)
    while tick < end:
        crates[tick] = rng.randint(27, 72)
        tick += rng.randint(spacing * 5, spacing * 15)
    return damage, crates, end, rng.uniform(.68, 1)


def simulate(hello, example, seed, profile, plating_fills):
    rules = hello["config"]
    bot = OvalRunner(hello, Lane())
    bot.router.waypoint = lambda pos, goal: goal
    obs = copy.deepcopy(example)
    own = obs["self"]
    own.update(pos=[0, 0, 0], aim=[0, 0], cubes=0, in_zone=True)
    obs.update(cubes=[], drops=[], pickups=[], mechs=[], sounds=[], events=[], crates=[], offer=None)
    obs["zone"].update(center=[0, 0], radius=560, next_center=[0, 0], next_radius=380,
                       shrink_start=180, shrink_end=240)
    damage, crates, end, initial_hp = schedule(seed, profile)
    own["hp"] = initial_hp * own["max_hp"]
    gun = own["weapons"][0]
    base = rules["mech"]["hp"]
    definitions = {item["id"]: item for item in rules["upgrades"]
                   if item.get("requires") in (None, "gun")}
    ready = reload_at = 0
    last_damage = -math.inf
    expires = -1
    for tick in range(end):
        now = tick / 10
        if tick in crates:
            obs["crates"] = [{"id": 1000 + tick, "hp": rules["crates"]["hp"], "pos": [23, 0, 0]}]
            expires = tick + crates[tick]
        elif tick >= expires:
            obs["crates"] = []
        if tick in damage:
            cost = damage[tick] * (1 - own["stats"]["armor"])
            own["hp"] -= cost
            last_damage = now
            obs["events"].append({"type": "damaged", "weapon": "gun", "by": 9, "amount": cost})
        if own["hp"] <= 0:
            return 0, own["cubes"], now
        if now - last_damage >= own["stats"]["regen_delay"]:
            own["hp"] = min(own["max_hp"], own["hp"] + .1 * own["stats"]["regen_per_s"])
        if reload_at and now >= reload_at:
            gun["ammo"] = gun["stats"]["mag"]
            reload_at = 0
        gun.update(reload=max(0, reload_at - now), cooldown=max(0, ready - now))
        obs.update(t=now, tick=tick * 2)
        action = bot.act(obs)
        own["aim"] = action["aim"]
        obs["events"] = []
        if obs["offer"]:
            choice = obs["offer"]["choices"][action["pick"]]["id"]
            own["upgrades"].append(choice)
            for effect in definitions[choice]["effects"]:
                path = effect["path"].split(".")
                stats = own["stats"] if path[0] == "mech" else gun["stats"]
                key = path[-1]
                stats[key] = stats[key] * effect.get("mul", 1) + effect.get("add", 0)
                if path == ["mech", "hp"]:
                    previous = own["max_hp"]
                    own["max_hp"] *= effect["mul"]
                    base *= effect["mul"]
                    if plating_fills:
                        own["hp"] += own["max_hp"] - previous
            obs["offer"] = None
        fire_at = math.ceil((max(now, ready) - 1e-8) * 20) / 20
        if obs["crates"] and action["fire"][0] and not reload_at and fire_at < now + .099 and gun["ammo"]:
            crate = obs["crates"][0]
            amount = gun["stats"]["damage"]
            crate["hp"] -= amount
            gun["ammo"] -= 1
            ready = fire_at + gun["stats"]["cooldown"]
            last_damage = fire_at
            obs["events"].append({"type": "hit", "target": crate["id"], "weapon": "gun", "amount": amount})
            if crate["hp"] <= 0:
                obs["crates"] = []
                own["cubes"] += 1
                if own["cubes"] <= rules["cubes"]["stat_cap"]:
                    gain = base * rules["cubes"]["hp_per_cube"]
                    own["hp"] += gain
                    own["max_hp"] += gain
                    per_cube = rules["cubes"]["damage_per_cube"]
                    gun["stats"]["damage"] *= (1 + per_cube * own["cubes"]) / (1 + per_cube * (own["cubes"] - 1))
                if own["cubes"] in rules["cubes"]["upgrade_thresholds"]:
                    pool = [name for name, item in definitions.items() if own["upgrades"].count(name) < item["max"]]
                    draw = random.Random(seed * 101 + own["cubes"]).sample(pool, 3)
                    obs["offer"] = {"choices": [{"id": name} for name in draw]}
        if not reload_at and (action.get("reload") or gun["ammo"] == 0):
            reload_at = now + gun["stats"]["reload"]
    return own["hp"], own["cubes"], end / 10


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--count", type=int, default=64)
    parser.add_argument("--plating-fills", action="store_true")
    parser.add_argument("--rows", action="store_true")
    args = parser.parse_args()
    with gzip.open(args.kit / "check/stream.jsonl.gz", "rt") as stream:
        hello, example = json.loads(next(stream)), json.loads(next(stream))
    for profile in ("light", "mixed", "short"):
        results = [simulate(hello, example, args.seed + index, profile, args.plating_fills)
                   for index in range(args.count)]
        summary = dict(profile=profile, survivors=sum(row[0] > 0 for row in results),
                       hp=round(statistics.mean(row[0] for row in results), 1),
                       cubes=round(statistics.mean(row[1] for row in results), 2))
        if args.rows:
            summary["rows"] = results
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
