"""Paired loot-window benchmark. Does not emulate combat or measure placement."""

import argparse
import copy
import gzip
import json
import math
import random
import runpy
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from bot import OvalRunner
from navigation import Arena, distance
from race import on_ground, update_motion


def scenarios(arena, seed, count):
    rng = random.Random(seed)
    region = Counter(key for key in arena.region if key>=0).most_common(1)[0][0]
    cells = [index for index,key in enumerate(arena.region) if key==region]
    cases = []
    for index in range(count):
        start = arena.point(rng.choice(cells))
        def offset(point, near, far):
            angle,gap = rng.uniform(0,math.tau),rng.uniform(near,far)
            trial = (point[0]+math.cos(angle)*gap,point[1]+math.sin(angle)*gap)
            return on_ground(arena,trial,region) or (*start,arena.ground_height(start))
        clusters = [offset(start,20,85) for _ in range(3)]
        cubes = [offset(start if index%2==0 else rng.choice(clusters),5,82 if index%2==0 else 23)
                 for _ in range(rng.randint(5,13))]
        gear = offset(start,17,80)
        drop = offset(start,45,125)
        cases.append((start,cubes,gear,drop,rng.uniform(8,20),rng.uniform(17,26),rng.uniform(.55,1)))
    return cases


def simulate(arena,hello,example,policy,case,depth):
    start,cubes,gear,drop,land,end,health = case
    obs = copy.deepcopy(example)
    own = obs["self"]
    obs.update(mechs=[],crates=[],events=[],sounds=[])
    own.update(pos=(*start,arena.ground_height(start)),vel=[0,0,0],hp=health*own["max_hp"],in_zone=True)
    obs["zone"].update(center=start,radius=560,next_center=start,next_radius=240,shrink_start=60,shrink_end=110)
    remaining = [{"pos":point,"id":5100+idx} for idx,point in enumerate(cubes)]
    supply = {"pos":drop,"id":20001,"holder":None,"progress":0,"radius":hello["config"]["drops"]["radius"],
              "capture_s":hello["config"]["drops"]["capture_s"]}
    bot = policy(hello,arena)
    if depth:
        assert bot.strategy.library is not None, "Native library required for the comparison"
        bot.strategy.depth = depth
    elapsed,claimed,equipped = 10,False,False
    latency = []
    for step in range(int(end*10)):
        now = step/10
        pos = own["pos"]
        remaining = [item for item in remaining if distance(pos,item["pos"])>hello["config"]["cubes"]["pickup_radius"]
                     or abs(pos[2]-item["pos"][2])>=4]
        own["cubes"] = len(cubes)-len(remaining)+int(claimed)*hello["config"]["drops"]["cubes"]
        supply["lands_in"] = max(0,land-now)
        if not claimed and now>=land and distance(pos,drop)<=supply["radius"]:
            supply.update(holder=own["id"],progress=supply["progress"]+.1)
            if supply["progress"]>=supply["capture_s"]:
                claimed = True
                own.update(hp=own["max_hp"],energy=own["max_energy"])
        else:
            supply.update(holder=None,progress=0)
        pickup = {"pos":gear,"id":8010,"kind":"lightning"}
        obs.update(t=now,tick=step*2,drops=[] if claimed else [supply],
                   cubes=[item for item in remaining if distance(pos,item["pos"])<82],
                   pickups=[pickup] if not equipped and distance(pos,gear)<82 else [])
        begin = time.perf_counter()
        action = bot.act(obs)
        latency.append((time.perf_counter()-begin)*1000)
        if action.get("take")==8010 and distance(pos,gear)<hello["config"]["loot"]["pickup_radius"] and abs(pos[2]-gear[2])<4:
            equipped = True
            own["weapons"][1] = {"kind":"lightning","cooldown":0,"stats":hello["config"]["weapons"]["lightning"]}
        elapsed = update_motion(own,action,arena,elapsed)
    return len(cubes)-len(remaining)+int(claimed)*hello["config"]["drops"]["cubes"],equipped,claimed,latency


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit",required=True,type=Path)
    parser.add_argument("--baseline",required=True,type=Path)
    parser.add_argument("--count",type=int,default=64)
    parser.add_argument("--seed",type=int,default=8431)
    args = parser.parse_args()
    with gzip.open(args.kit/"check/stream.jsonl.gz","rt") as stream:
        hello,example = json.loads(next(stream)),json.loads(next(stream))
    with patch.dict(sys.modules):
        sys.modules.pop("navigation",None)
        with patch.object(sys,"path",[str(args.baseline.resolve())]+sys.path):
            baseline = runpy.run_path(str(args.baseline/"bot.py"))["OvalRunner"]
    arena = Arena(args.kit/"arena",hello["config"]["mech"])
    cases = scenarios(arena,args.seed,args.count)
    for name,policy,depth in (("V3",baseline,0),("one step",OvalRunner,1),("five steps",OvalRunner,5)):
        results = [simulate(arena,hello,example,policy,case,depth) for case in cases]
        totals = [row[0] for row in results]
        times = sorted(value for row in results for value in row[3])
        print(f"{name}: mean cubes {statistics.mean(totals):.2f}, gear {sum(row[1] for row in results)}/{len(cases)}, "
              f"drops {sum(row[2] for row in results)}/{len(cases)}, 6+ cubes {sum(total>=6 for total in totals)}/{len(cases)}, "
              f"policy p99/max ms {times[int(.99*len(times))]:.2f}/{times[-1]:.2f}",flush=True)


if __name__=="__main__":
    main()
