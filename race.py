"""Paired shrinking-circle and supply races, not a match or leaderboard simulator."""

import argparse
import copy
import gzip
import json
import math
import random
import runpy
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from bot import OvalRunner, angle_diff
from evaluate import FlatArena
from navigation import Arena, distance


def on_ground(arena, point, region):
    cell = arena.snap(point,region)
    if cell is None:
        return None
    east,north = arena.point(cell)
    return east,north,arena.ground_height((east,north))


def cases(arena, config, seed, count):
    rng = random.Random(seed)
    region = Counter(area for area in arena.region if area >= 0).most_common(1)[0][0]
    cells = [index for index,area in enumerate(arena.region) if area==region]
    phases = config["zone"]["phases"]
    result = []
    while len(result) < count:
        stage = rng.randrange(1,len(phases))
        phase = phases[stage]
        outer,inner = phases[stage-1]["radius"],phase["radius"]
        destination = arena.point(rng.choice(cells))
        if len(result)%3==1:
            destination = (destination[0]+rng.uniform(-14,14),destination[1]+rng.uniform(-14,14))
        turn = rng.uniform(0,math.tau)
        axis = math.cos(turn),math.sin(turn)
        offset = (outer-inner)*rng.uniform(.35,.98)
        origin = (destination[0]-offset*axis[0],destination[1]-offset*axis[1])
        offset_angle = turn+math.pi+rng.uniform(-1.3,1.3)
        radius = outer*rng.uniform(.62,.98)
        start = on_ground(arena,(origin[0]+math.cos(offset_angle)*radius,
                                 origin[1]+math.sin(offset_angle)*radius),region)
        if start is None or not .55*outer < distance(start,origin) < outer or distance(start,destination)<inner+4:
            continue
        hold = rng.uniform(0,min(22,phase["hold_s"]))
        piles = []
        for idx in range(9):
            angle = rng.uniform(0,math.tau)
            length = rng.uniform(6,75)
            point = on_ground(arena,(start[0]+length*math.cos(angle),start[1]+length*math.sin(angle)),region)
            if point is not None and distance(point,origin)<outer:
                piles.append({"id":5000+idx,"pos":point})
        result.append((origin,destination,outer,inner,hold,phase,start,piles))
    return result


def update_motion(own, action, arena, elapsed_since_boost):
    pos,stats = own["pos"],own["stats"]
    boost = action["boost"] and own["energy"]>stats["boost_drain"]*.1
    speed = stats["boost_speed"] if boost else stats["walk_speed"]
    elapsed_since_boost = 0 if boost else elapsed_since_boost+.1
    energy_gain = stats["energy_regen"] if elapsed_since_boost >= stats["energy_regen_delay"] else 0
    own["energy"] = max(0,min(own["max_energy"],own["energy"]+.1*(-stats["boost_drain"] if boost else energy_gain)))
    vector = action["move"]
    scale = max(1,math.hypot(*vector))
    velocity = tuple(old+max(-stats["ground_accel"]*.1,min(stats["ground_accel"]*.1,speed*value/scale-old))
                     for old,value in zip(own["vel"],vector))
    delta = (velocity[0]*.1,velocity[1]*.1)
    for east,north in (delta,(delta[0],0),(0,delta[1]),(0,0)):
        point = (pos[0]+east,pos[1]+north)
        if arena.clear(pos,point):
            own["pos"] = (*point,arena.ground_height(point))
            own["vel"] = [10*east,10*north,0]
            break
    return elapsed_since_boost


def run_case(arena, hello, example, policy, case):
    origin,destination,outer,inner,hold,phase,start,piles = case
    obs = copy.deepcopy(example)
    own = obs["self"]
    own.update(pos=start,vel=[0,0,0],hp=own["max_hp"],energy=own["max_energy"])
    obs.update(mechs=[],pickups=[],drops=[],events=[],crates=[],sounds=[])
    obs["zone"].update(next_center=destination,next_radius=inner,shrink_start=hold,
                       shrink_end=hold+phase["shrink_s"],dps=phase["dps"])
    bot = policy(hello,arena)
    remaining = list(piles)
    damage = 0
    elapsed = 10
    for tick in range(math.ceil((hold+phase["shrink_s"]+2)*10)):
        now = tick*.1
        fraction = min(1,max(0,(now-hold)/phase["shrink_s"]))
        center = tuple(first+(second-first)*fraction for first,second in zip(origin,destination))
        radius = outer+(inner-outer)*fraction
        obs["zone"].update(center=center,radius=radius)
        own["in_zone"] = distance(own["pos"],center)<=radius
        if not own["in_zone"]:
            damage += phase["dps"]*.1
        own["hp"] = max(1,own["max_hp"]-damage)
        remaining = [item for item in remaining if distance(own["pos"],item["pos"])>hello["config"]["cubes"]["pickup_radius"]
                     or abs(own["pos"][2]-item["pos"][2])>=4]
        own["cubes"] = len(piles)-len(remaining)
        obs.update(t=now,tick=tick*2,cubes=[item for item in remaining if distance(own["pos"],item["pos"])<50])
        elapsed = update_motion(own,bot.act(obs),arena,elapsed)
    return damage,own["cubes"]


def compare(arena, hello, example, baseline, seed, count):
    scenarios = cases(arena,hello["config"],seed,count)
    records = []
    for label,policy in (("3.88 baseline",baseline),("candidate",OvalRunner)):
        outcomes = [run_case(arena,hello,example,policy,case) for case in scenarios]
        costs = sorted(damage for damage,_ in outcomes)
        print(f"{seed} {label}: mean zone damage {sum(costs)/count:.1f}, p90 {costs[int(.9*count)]:.1f}, "
              f"zero damage {sum(value<.01 for value in costs)}/{count}, cubes collected {sum(cubes for _,cubes in outcomes)}")
        records.append(outcomes)
    pairs = list(zip(*records))
    print("Paired zone damage, better/same/worse:",
          sum(new[0]+.01<old[0] for old,new in pairs),
          sum(abs(new[0]-old[0])<.01 for old,new in pairs),
          sum(old[0]+.01<new[0] for old,new in pairs))
    if sum(cost for cost,_ in records[1]) >= sum(cost for cost,_ in records[0]):
        print("No reduction in total zone damage on this seed.")


def supply_cases(arena, seed, count):
    rng = random.Random(seed+91)
    region = Counter(area for area in arena.region if area>=0).most_common(1)[0][0]
    cells = [index for index,area in enumerate(arena.region) if area==region]
    results = []
    while len(results)<count:
        first,second = (arena.point(cell) for cell in rng.sample(cells,2))
        if 85<distance(first,second)<285:
            results.append((first,second,rng.uniform(.35,.98),rng.uniform(0,math.tau)))
    return results


def capture(arena,hello,example,policy,case):
    start,goal,health,angle = case
    obs = copy.deepcopy(example)
    own = obs["self"]
    own.update(pos=(*start,arena.ground_height(start)),vel=[0,0,0],hp=health*own["max_hp"],in_zone=True)
    center = (goal[0]+90*math.cos(angle),goal[1]+90*math.sin(angle))
    obs["zone"].update(center=center,radius=560,next_center=center,next_radius=380,shrink_start=60,shrink_end=110)
    obs.update(cubes=[],crates=[],mechs=[],events=[],pickups=[],sounds=[])
    rules = hello["config"]["drops"]
    drop = dict(id=20000,pos=(*goal,arena.ground_height(goal)),holder=None,progress=0,
                radius=rules["radius"],capture_s=rules["capture_s"])
    obs["drops"] = [drop]
    bot = policy(hello,arena)
    elapsed = 10
    for tick in range(math.ceil((rules["fall_s"]+rules["capture_s"]+8)*10)):
        now = tick*.1
        obs.update(t=now,tick=tick*2)
        drop["lands_in"] = max(0,rules["fall_s"]-now)
        if now>=rules["fall_s"] and distance(own["pos"],goal)<rules["radius"]:
            drop.update(holder=own["id"],progress=drop["progress"]+.1)
            if drop["progress"]>=rules["capture_s"]:
                return True
        else:
            drop.update(holder=None,progress=0)
        elapsed = update_motion(own,bot.act(obs),arena,elapsed)
    return False


def supply_compare(arena,hello,example,baseline,seed,count):
    scenarios = supply_cases(arena,seed,count)
    for label,policy in (("3.88 baseline",baseline),("candidate",OvalRunner)):
        tally = sum(capture(arena,hello,example,policy,case) for case in scenarios)
        print(f"{seed} {label}: uncontested supply captures {tally}/{count} by 8 seconds after earliest capture")


def opening(hello,example,policy,seed,close=False):
    rng = random.Random(seed)
    arena = FlatArena()
    bot = policy(hello,arena)
    bot.router.waypoint = lambda start,end:end
    obs = copy.deepcopy(example)
    own = obs["self"]
    own.update(pos=[0,0,0],vel=[0,0,0],aim=[0,0])
    angle = rng.uniform(0,math.tau)
    radius = rng.uniform(20,42)
    box = {"id":1000,"pos":[radius*math.cos(angle),radius*math.sin(angle),0],"hp":hello["config"]["crates"]["hp"]}
    enemy_dist = 48 if close else rng.uniform(150,185)
    enemy = {"kind":"mech","id":10,"hp":1400,"max_hp":1400,"pos":[enemy_dist,0,0],"vel":[0,0,0]}
    obs.update(mechs=[enemy],crates=[box],cubes=[],pickups=[],sounds=[],drops=[],events=[])
    obs["zone"].update(center=[0,0],next_center=[0,0],radius=560,next_radius=380,shrink_start=60,shrink_end=110)
    next_shot,elapsed = 0,10
    for tick in range(120):
        obs.update(t=tick*.1,tick=tick*2)
        action = bot.act(obs)
        for axis,rate in enumerate(("turn_rate","pitch_rate")):
            delta = angle_diff(action["aim"][axis],own["aim"][axis])
            limit = own["stats"][rate]*.1
            own["aim"][axis] += max(-limit,min(limit,delta))
        if close and tick==0:
            return bot.mode=="duel"
        if obs["crates"] and action["fire"][0] and obs["t"]>=next_shot:
            gun = own["weapons"][0]["stats"]
            next_shot = obs["t"]+gun["cooldown"]
            dist = max(1,distance(own["pos"],box["pos"]))
            yaw = math.atan2(box["pos"][1]-own["pos"][1],box["pos"][0]-own["pos"][0])
            pitch = math.atan2(hello["config"]["crates"]["height"]*.5-own["stats"]["muzzle_height"],dist)
            if max(abs(angle_diff(yaw,own["aim"][0])),abs(pitch-own["aim"][1])) < .025+1/dist:
                box["hp"] -= gun["damage"]
                if box["hp"]<=0:
                    obs.update(crates=[],cubes=[{"id":5000,"pos":box["pos"]}])
        elapsed = update_motion(own,action,arena,elapsed)
        if obs["cubes"] and distance(own["pos"],box["pos"])<=hello["config"]["cubes"]["pickup_radius"]:
            return True
    return False


def opening_compare(hello,example,baseline,seed,count):
    for label,policy in (("3.88 baseline",baseline),("candidate",OvalRunner)):
        cubes = sum(opening(hello,example,policy,seed+idx) for idx in range(count))
        checks = sum(opening(hello,example,policy,seed+idx,True) for idx in range(count))
        print(f"{seed} {label}: first cube in distant-opponent opening {cubes}/{count}; "
              f"nearby-opponent control retains duel {checks}/{count}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit",type=Path,required=True)
    parser.add_argument("--baseline",type=Path,required=True)
    parser.add_argument("--cases",type=int,default=48)
    parser.add_argument("--seed",type=int,default=8029)
    args = parser.parse_args()
    with gzip.open(args.kit/"check/stream.jsonl.gz","rt") as stream:
        hello,example = json.loads(next(stream)),json.loads(next(stream))
    with patch.dict(sys.modules):
        sys.modules.pop("navigation",None)
        with patch.object(sys,"path",[str(args.baseline.resolve())]+sys.path):
            baseline = runpy.run_path(str(args.baseline/"bot.py"))["OvalRunner"]
    arena = Arena(args.kit/"arena",hello["config"]["mech"])
    compare(arena,hello,example,baseline,args.seed,args.cases)
    supply_compare(arena,hello,example,baseline,args.seed,args.cases)
    opening_compare(hello,example,baseline,args.seed,args.cases)


if __name__=="__main__":
    main()
