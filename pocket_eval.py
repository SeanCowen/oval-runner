"""Paired abstract ladder with cube collection through the production movement policy."""
import argparse
import copy
import gzip
import json
import math
import random
from pathlib import Path

from bot import OvalRunner
from draft_eval import PROFILES, trial
from evaluate import FlatArena
from navigation import distance
from race import update_motion


def pocket(policy, example, seed, stage, current, profile):
    rng = random.Random(seed * 167 + stage)
    own = copy.deepcopy(current)
    obs = dict(example)
    own.update(pos=[0,0,0], vel=[0,0,0], in_zone=True, aim=[0,0])
    size = 1 + own["cubes"] * policy.config["cubes"]["size_per_cube"]
    for key in ("muzzle_height","eye_height"):
        own["stats"][key] = policy.config["mech"][key] * size
    origin = rng.uniform(0, math.tau)
    close_probability = .25 if profile == "scarce" else .55
    gap = rng.uniform(5, 33) if rng.random() < close_probability else rng.uniform(25, 85)
    center = (gap*math.cos(origin), gap*math.sin(origin))
    count = 2 + rng.choice((0,0,1,2,4))
    cubes = [dict(id=5000+stage*16+i, pos=[center[0]+rng.uniform(-4,4),center[1]+rng.uniform(-4,4),0])
             for i in range(count)]
    others = []
    for slot in range(rng.choices((0,1,2),(.24,.51,.25))[0]):
        bearing = rng.uniform(0,math.tau)
        reach = rng.uniform(38,90)
        others.append(dict(id=slot+1, kind="mech", hp=1400, max_hp=1400, cubes=0,
                           pos=[reach*math.cos(bearing),reach*math.sin(bearing),0], vel=[0,0,0]))
    duration = rng.randint(12,32)
    tax = {"packed":65, "intermittent":18, "tempo-rich":90, "scarce":110}[profile]
    revisit = rng.random() < (.30 if others else .65)
    obs.update(self=own, crates=[],pickups=[],sounds=[],events=[],drops=[],offer=None, mechs=others, alive=15-stage)
    obs["zone"] = dict(center=[0,0],radius=210,next_center=[0,0],next_radius=180,
                       shrink_start=500,shrink_end=550,dps=20)
    elapsed, charge = 10., 0.
    policy.router.waypoint = lambda start, goal: goal
    cap = policy.config["cubes"]["stat_cap"]
    gain = policy.config["cubes"]["hp_per_cube"]
    per_cube = current["max_hp"] * gain / (1 + min(cap,current["cubes"])*gain)
    for tick in range(duration):
        now = 8 + stage*22 + tick/10
        before = own["cubes"]
        cubes = [item for item in cubes if distance(own["pos"], item["pos"]) > policy.config["cubes"]["pickup_radius"]]
        own["cubes"] = current["cubes"]+count-len(cubes)
        healed = (min(cap,own["cubes"]) - min(cap,before)) * per_cube
        own["hp"] += healed
        own["max_hp"] += healed
        obs.update(t=now, tick=round(now*20), cubes=cubes)
        action = policy.act(obs)
        own["aim"] = action.get("aim",own["aim"])
        elapsed = update_motion(own,action,policy.arena,elapsed)
        if others and distance(own["pos"],center)<12:
            charge += tax*.1
            own["hp"] -= tax*.1
            if own["hp"] <= 0:
                return count-len(cubes), charge, True
    cubes = [item for item in cubes if distance(own["pos"],item["pos"])>policy.config["cubes"]["pickup_radius"]]
    return (count if revisit else count-len(cubes)), charge, False


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit",required=True,type=Path)
    parser.add_argument("--count",required=True,type=int)
    parser.add_argument("--start",required=True,type=int)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--stress",action="store_true")
    args=parser.parse_args()
    if args.stress:
        PROFILES["scarce"] = (2.5, 1.85, .12)
    with gzip.open(args.kit/"check/stream.jsonl.gz","rt") as stream:
        hello,example=json.loads(next(stream)),json.loads(next(stream))
    with args.output.open("w") as out:
        for seed in range(args.start,args.start+args.count):
            rows={}
            for profile in PROFILES:
                policy=OvalRunner(dict(hello,seed=0),FlatArena())
                rows[profile]=trial(policy,example,seed,profile,pocket)
            out.write(json.dumps([seed,rows])+"\n")
