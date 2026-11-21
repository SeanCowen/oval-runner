"""Offline paired placement proxy. Writes JSONL to the supplied file, never stdout."""

import argparse
import copy
import gzip
import json
import math
import random
import time
from collections import deque
from pathlib import Path

from bot import OvalRunner
from evaluate import FlatArena
from navigation import distance


def disk(rng, radius):
    angle = rng.random() * math.tau
    length = math.sqrt(rng.random()) * radius
    return (math.cos(angle) * length, math.sin(angle) * length, 0)


def step_toward(start, goal, speed):
    scale = min(1, speed / max(.001, distance(start, goal)))
    return (start[0] + (goal[0] - start[0]) * scale, start[1] + (goal[1] - start[1]) * scale, 0)


def points_for(hp, deaths):
    if deaths[0] is not None:
        return sum(when is not None and when < deaths[0] for when in deaths[1:])
    better = sum(health > hp[0] and when is None for health, when in zip(hp[1:], deaths[1:]))
    return 15 - better


def ring_at(phases, tick):
    for origin, destination, outer, inner, start, end in phases:
        if tick <= end:
            break
    fraction = min(1, max(0, (tick - start) / (end - start)))
    center = tuple(first + fraction * (second - first) for first, second in zip(origin, destination))
    return center, outer + fraction * (inner - outer), destination, inner, start, end


def play(hello, example, seed, field, ring_style, decisions):
    rng = random.Random(seed)
    own_obs = copy.deepcopy(example)
    own = own_obs["self"]
    own.update(id=0, aim=[0, 0], in_zone=True, cubes=0, energy=100)
    own_obs.update(mechs=[], pickups=[], cubes=[], drops=[], crates=[], events=[], sounds=[])
    arena = FlatArena()
    bot = OvalRunner(dict(hello, seed=seed), arena)
    bot.router.waypoint = lambda start, goal: goal
    stats = own["stats"]
    positions = [disk(rng, 245) for _ in range(16)]
    homes = positions[:]
    roles = ["chaser"] * 4 + ["camper"] * 4 + ["zone-follower"] * 4 + ["looter"] * 3
    rng.shuffle(roles)
    roles.insert(0, "entrant")
    base = hello["config"]["mech"]["hp"]
    hp = [float(base)] * 16
    maximum = hp[:]
    cubes = [0] * 16
    deaths = [None] * 16
    last_change = [-100] * 16
    loot = {5000 + index: disk(rng, 260) for index in range(56)}
    center = disk(rng, 70)
    origin = (0, 0, 0)
    phases = [(origin, center, 275, 10, 22, 108)]
    if ring_style == "staged":
        middle = disk(random.Random(seed + 122800), 45)
        phases = [(origin, middle, 275, 155, 15, 48), (middle, center, 155, 78, 58, 85),
                  (center, center, 78, 10, 92, 113)]
    history = deque(maxlen=11)
    timings = []
    events = []
    speed = [stats["walk_speed"]] + [rng.uniform(7, 12) for _ in roles[1:]]
    # Draw once per slot and time, even if that slot dies or chooses another action.
    rolls = [[rng.uniform(40, 145) for _ in roles] for _ in range(121)]
    seen_until = {}
    for tick in range(121):
        ring, radius, destination, inner, start, end = ring_at(phases, tick)
        alive = [slot for slot, when in enumerate(deaths) if when is None]
        pos = positions[0]
        own.update(pos=pos, hp=hp[0], max_hp=maximum[0], cubes=cubes[0], in_zone=distance(pos, ring) <= radius)
        visible = []
        for slot in alive[1:]:
            if distance(pos, positions[slot]) <= 115:
                seen_until[slot] = tick + 5
            if seen_until.get(slot, -1) >= tick:
                visible.append(dict(kind="mech", id=slot, pos=positions[slot], hp=hp[slot],
                                    max_hp=maximum[slot], cubes=cubes[slot], weapons=["gun"], vel=[0, 0, 0]))
        own_obs.update(t=tick, tick=tick * 20, alive=len(alive), mechs=visible, events=events,
                       cubes=[{"id": ident, "pos": point} for ident, point in loot.items() if distance(pos, point) < 85])
        own_obs["zone"].update(center=ring, radius=radius, next_center=destination, next_radius=inner,
                               shrink_start=start, shrink_end=end, dps=60)
        started = time.perf_counter_ns()
        action = bot.act(own_obs)
        timings.append((time.perf_counter_ns() - started) / 1e6)
        own["aim"] = action.get("aim", own["aim"])
        history.append([tick, round(hp[0]), bot.mode])
        if len(alive) == 1 or tick == 120:
            cause = "last alive" if len(alive) == 1 else "clock"
            break
        next_positions = positions[:]
        for substep in range(decisions):
            if substep:
                moment = tick + substep / decisions
                pos = next_positions[0]
                subring, subradius, target, target_radius, start, end = ring_at(phases, moment)
                own.update(pos=pos, in_zone=distance(pos, subring) <= subradius)
                own_obs.update(t=moment, tick=round(moment * 20), events=[])
                own_obs["zone"].update(center=subring, radius=subradius, next_center=target,
                                       next_radius=target_radius, shrink_start=start, shrink_end=end)
                started = time.perf_counter_ns()
                action = bot.act(own_obs)
                timings.append((time.perf_counter_ns() - started) / 1e6)
                own["aim"] = action.get("aim", own["aim"])
            boost = action.get("boost") and own["energy"] >= stats["boost_drain"] / decisions
            pace = stats["boost_speed"] if boost else speed[0]
            delta_energy = -stats["boost_drain"] if boost else stats["energy_regen"]
            own["energy"] = min(100, own["energy"] + delta_energy / decisions)
            move = action["move"]
            next_positions[0] = (pos[0] + pace * move[0] / decisions, pos[1] + pace * move[1] / decisions, 0)
        pos = positions[0]
        for slot in alive[1:]:
            here = positions[slot]
            if distance(here, ring) > radius - 8:
                goal = ring
            elif roles[slot] == "chaser":
                nearest = min((other for other in alive if other != slot), key=lambda other: distance(here, positions[other]))
                goal = pos if field == "focused" and distance(here, pos) < 130 else positions[nearest]
            elif roles[slot] == "looter" and loot:
                goal = min(loot.values(), key=lambda point: distance(here, point))
            elif roles[slot] == "zone-follower":
                goal = destination
            else:
                goal = homes[slot] if distance(homes[slot], ring) <= radius else here
            next_positions[slot] = step_toward(here, goal, speed[slot])
        positions = next_positions
        for ident, point in list(loot.items()):
            owner = min(alive, key=lambda slot: distance(positions[slot], point))
            if distance(positions[owner], point) <= hello["config"]["cubes"]["pickup_radius"]:
                del loot[ident]
                cubes[owner] += 1
                if cubes[owner] <= hello["config"]["cubes"]["stat_cap"]:
                    gain = base * hello["config"]["cubes"]["hp_per_cube"]
                    maximum[owner] += gain
                    hp[owner] += gain
        costs = [0.] * 16
        events = []
        for slot in alive:
            other = min((other for other in alive if other != slot),
                        key=lambda other: distance(positions[slot], positions[other]))
            if field == "focused" and slot and distance(positions[slot], positions[0]) <= 50:
                other = 0
            if distance(positions[slot], positions[other]) <= 50:
                amount = rolls[tick][slot] * (1 + min(10, cubes[slot]) * .04)
                costs[other] += amount
                last_change[slot] = tick
                if other == 0:
                    events.append(dict(type="damaged", by=slot, amount=amount, weapon="gun"))
                elif slot == 0:
                    events.append(dict(type="hit", target=other, amount=amount, weapon="gun"))
        for slot in alive:
            zone = 60 if distance(positions[slot], ring) > radius else 0
            loss = costs[slot] + zone
            if loss:
                last_change[slot] = tick
                hp[slot] -= loss
                if slot == 0 and zone:
                    events.append(dict(type="damaged", by=None, amount=zone, weapon="zone"))
            elif tick - last_change[slot] >= stats["regen_delay"]:
                hp[slot] = min(maximum[slot], hp[slot] + stats["regen_per_s"])
            if hp[slot] <= 0:
                deaths[slot] = tick + 1
                if slot == 0:
                    cause = "mixed" if costs[slot] and zone else ("zone" if zone else "encounter")
        if deaths[0] is not None:
            history.append([tick + 1, 0, bot.mode])
            break
    return dict(seed=seed, field=field, ring=ring_style, decisions=decisions, points=points_for(hp, deaths), cause=cause,
                end=history[-1][0], history=list(history), ms=timings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ring", choices=("single","staged"), default="single")
    parser.add_argument("--decisions", type=int, choices=(1, 10), default=1)
    args = parser.parse_args()
    with gzip.open(args.kit / "check/stream.jsonl.gz", "rt") as stream:
        hello, example = json.loads(next(stream)), json.loads(next(stream))
    with args.output.open("w") as output:
        for seed in range(args.start, args.start + args.count):
            for field in ("independent", "focused"):
                row = play(hello, example, seed, field, args.ring, args.decisions)
                row["ms"] = [round(value, 4) for value in row["ms"]]
                output.write(json.dumps(row, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
