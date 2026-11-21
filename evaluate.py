"""Offline game-policy checks and paired navigation benchmark. Requires the original kit."""

import argparse
import copy
import gzip
import json
import math
import random
import runpy
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import mechfight
from bot import OvalRunner, angle_diff
from navigation import Arena, Router, direction, distance


class FlatArena:
    def ground_height(self, point):
        return 0

    def visible(self, start, end):
        return True

    def clear(self, start, end):
        return True


def policy_checks(hello, example):
    passed = []
    def case(name, setup, check):
        bot = OvalRunner(hello, FlatArena())
        bot.router.waypoint = lambda pos, goal: goal
        obs = copy.deepcopy(example)
        obs["self"].update(pos=[0,0,0],aim=[0,0])
        obs["zone"].update(center=[0,0],radius=180,next_center=[0,0],next_radius=100)
        setup(obs)
        result = bot.act(obs)
        assert check(bot,result), (name,bot.mode,result)
        passed.append(name)

    def actor(dist=40):
        return {"kind":"mech","id":9,"pos":[dist,0,0],"vel":[0,0,0],"hp":1400,"max_hp":1400}

    def recovery(obs):
        obs["self"]["hp"] = 320
        obs["mechs"] = [actor()]

    def storm(obs):
        recovery(obs)
        obs["self"]["pos"] = [190,0,0]
        obs["self"]["in_zone"] = False
        obs["cubes"] = [{"id":5001,"pos":[195,0,0]}]

    def moving(obs):
        obs["mechs"] = [actor(100)]
        obs["mechs"][0]["vel"][1] = 14

    def pickup(obs):
        obs["pickups"] = [{"kind":"laser","id":8000,"pos":[2,0,0]},{"kind":"lightning","id":8001,"pos":[1,0,9]}]

    def crate(obs):
        obs["crates"] = [{"id":1004,"hp":300,"pos":[30,0,0]}]

    def drop(obs):
        obs["drops"] = [{"id":20000,"holder":None,"lands_in":0,"pos":[2,0,0]}]

    def charged(obs, charge):
        obs["mechs"] = [actor(50)]
        obs["self"]["weapons"][1] = {"kind":"laser","charge":charge,"cooldown":0,"stats":hello["config"]["weapons"]["laser"]}
        obs["self"]["aim"][1] = math.atan2(9*.55-7,50)

    case("storm overrides loot and duel",storm,lambda bot,act:bot.mode=="zone" and act["move"][0]<0)
    case("injured mech disengages and stops firing",recovery,lambda bot,act:bot.mode=="recover" and not any(act["fire"]) and act["move"][0]<0)
    case("opponent motion changes game aim",moving,lambda bot,act:act["aim"][0]>.04)
    case("nearby loot respects vertical reach",pickup,lambda bot,act:act.get("take")==8000)
    case("healthy mech farms an uncontested crate",crate,lambda bot,act:bot.mode=="crates" and "aim" in act)
    case("uncontested supply timer held still",drop,lambda bot,act:bot.mode=="drop" and not any(act["move"]))
    case("upgrades are ranked, not index zero",lambda obs:obs.update(offer={"choices":[{"id":"gyros"},{"id":"plating"},{"id":"sensors"}]}),lambda bot,act:act["pick"]==1)
    case("laser held before charge",lambda obs:charged(obs,.2),lambda bot,act:act["fire"][1])
    case("aligned full laser releases",lambda obs:charged(obs,.75),lambda bot,act:not act["fire"][1])
    def finish(obs):
        recovery(obs)
        obs["mechs"][0]["hp"] = 60
    case("one weak pursuer can be finished",finish,lambda bot,act:bot.mode=="duel")
    def final_circle(obs):
        obs.update(t=392,mechs=[actor(10)])
        obs["zone"].update(radius=7,next_radius=0,shrink_start=380)
    case("hold center at final collapse",final_circle,lambda bot,act:bot.mode=="zone" and not any(act["move"]))
    bot = OvalRunner(hello, FlatArena())
    bot.router.waypoint = lambda pos, goal: pos
    obs = copy.deepcopy(example)
    east,north,height = obs["self"]["pos"]
    obs["cubes"] = [{"id":5001,"pos":[east+15,north,height]}]
    obs["zone"].update(center=[east,north],next_center=[east,north])
    bot.act(obs)
    assert bot.mode == "cubes"
    obs["t"] += 6.2
    bot.act(obs)
    assert bot.mode != "cubes"
    passed.append("unreachable loot expires")
    return passed


def run_route(arena, start, goal, planned):
    router, pos = Router(arena), start
    idle = 0
    for tick in range(1200):
        if distance(pos,goal) < 3:
            return True,tick
        waypoint = router.waypoint(pos,goal) if planned else goal
        vector = direction(pos,waypoint)
        length = min(1.17,distance(pos,waypoint))
        proposal = (pos[0]+vector[0]*length,pos[1]+vector[1]*length)
        if distance(pos,proposal) > .01 and arena.clear(pos,proposal):
            pos, idle = proposal,0
        else:
            idle += 1
            if idle > 60:
                break
    return False,tick


def route_checks(arena, count, seed):
    randomizer = random.Random(seed)
    region = Counter(key for key in arena.region if key >= 0).most_common(1)[0][0]
    cells = [index for index,key in enumerate(arena.region) if key==region]
    results = Counter()
    failing = []
    for _ in range(count):
        start,goal = (arena.point(cell) for cell in randomizer.sample(cells,2))
        for name, planned in (("straight",False),("planner",True)):
            reached,_ = run_route(arena,start,goal,planned)
            results[name] += reached
            if planned and not reached:
                failing.append((start,goal))
    print(f"Navigation (seed {seed}): straight-line {results['straight']}/{count}, planner {results['planner']}/{count}")
    if failing:
        print("First incomplete routes:",failing[:3])
    return results


def pressure_checks(hello, example, policy=OvalRunner):
    def fixture(hp, enemies, supply=None):
        obs = copy.deepcopy(example)
        obs["self"].update(pos=[0,0,0],hp=hp,aim=[0,0])
        obs["zone"].update(center=[0,0],radius=180,next_center=[0,0],next_radius=100)
        obs["mechs"] = [{"kind":"mech","id":10+index,"pos":[dist,0,0],"vel":[0,0,0],"hp":1400,
                         "max_hp":1400} for index,dist in enumerate(enemies)]
        if supply is not None:
            obs["drops"] = [{"id":20000,"holder":obs["self"]["id"],"lands_in":0,"progress":supply,
                             "radius":8,"capture_s":4,"pos":[0,0,0]}]
        bot = policy(hello,FlatArena())
        bot.router.waypoint = lambda pos,goal:goal
        return bot,obs

    results = {}
    bot,obs = fixture(1400,[55,80])
    action = bot.act(obs)
    results["healthy outnumbered mech moves away without switching its gun off"] = action["move"][0]<0 and action["fire"][0]
    bot,obs = fixture(350,[125])
    action = bot.act(obs)
    results["injured mech does not pursue and reset its healing timer"] = bot.mode!="duel" and not any(action["fire"])
    bot,obs = fixture(420,[45],3.2)
    action = bot.act(obs)
    results["keeps an uncontested capture with 0.8 seconds remaining"] = bot.mode=="drop" and not any(action["move"])
    bot,obs = fixture(420,[11],2.0)
    bot.act(obs)
    results["does not commit to a ring an opponent can contest first"] = bot.mode!="drop"
    bot,obs = fixture(320,[45])
    for moment in (1,1.4):
        obs.update(t=moment,tick=round(moment*20),events=[{"type":"damaged","by":10,"weapon":"gun","amount":45}])
        action = bot.act(obs)
    results["can return fire during repeated incoming damage"] = action["fire"][0] and bot.mode=="recover"
    obs.update(t=2.2,tick=44,events=[])
    action = bot.act(obs)
    results["gun goes quiet again when pressure stops"] = not any(action["fire"])
    bot,obs = fixture(320,[45])
    for moment in (1,1.4):
        obs.update(t=moment,events=[{"type":"hit","target":10,"amount":45}])
        action = bot.act(obs)
    results["own hits cannot self-renew the return-fire window"] = not any(action["fire"])
    return results


def zone_checks(arena, hello, example, count, seed):
    rng = random.Random(seed)
    largest = Counter(key for key in arena.region if key >= 0).most_common(1)[0][0]
    cells = [index for index,key in enumerate(arena.region) if key == largest]
    reached = 0
    for _ in range(count):
        start,goal = (arena.point(cell) for cell in rng.sample(cells,2))
        obs = copy.deepcopy(example)
        obs["zone"].update(center=goal,next_center=goal,radius=28,next_radius=22,shrink_start=0,shrink_end=10)
        pos = (*start,arena.ground_height(start))
        bot = OvalRunner(hello,arena)
        for tick in range(1500):
            if distance(pos,goal) <= 28:
                reached += 1
                break
            obs.update(t=tick/10,tick=tick*2)
            obs["self"].update(pos=pos,in_zone=False)
            move = bot.act(obs)["move"]
            proposal = (pos[0]+move[0]*1.17,pos[1]+move[1]*1.17)
            if arena.clear(pos,proposal):
                pos = (*proposal,arena.ground_height(proposal))
    print(f"Zone re-entry, full policy + simplified ground movement: {reached}/{count}")
    return reached


def duel(hello, example, policy, seed):
    rng = random.Random(seed)
    obs = copy.deepcopy(example)
    own = obs["self"]
    cfg = hello["config"]
    start = rng.uniform(45,90)
    own.update(pos=[0,0,0],vel=[0,0,0],aim=[rng.uniform(-1,1),0])
    obs["zone"].update(center=[0,0],next_center=[0,0],radius=400,next_radius=300)
    foe = copy.deepcopy(own)
    foe.update(pos=[start,0,0],aim=[math.pi,0],id=12,kind="mech")
    actors = [own,foe]
    next_shot = [0,0]
    reload_at = [None,None]
    arrivals = []
    last_damage = [0,0]
    phase = rng.random()*math.tau
    for step in range(650):
        now = step/10
        obs.update(tick=step*2,t=now,mechs=[foe])
        for index,actor in enumerate(actors):
            gun = actor["weapons"][0]
            if reload_at[index] is not None and now >= reload_at[index]:
                gun["ammo"], reload_at[index] = gun["stats"]["mag"],None
            gun["reload"] = max(0,(reload_at[index] or 0)-now)
            gun["cooldown"] = max(0,next_shot[index]-now)
        mechfight.draw.clear()
        action = policy.act(obs)
        towards = direction(foe["pos"],own["pos"])
        gap = distance(foe["pos"],own["pos"])
        radial = max(-1,min(1,(gap-43)/16))
        lateral = .78 if math.sin(phase+now*.62)>0 else -.78
        controls = [action,{"move":[towards[0]*radial-towards[1]*lateral,towards[1]*radial+towards[0]*lateral],
                             "fire":[True,False],"aim":[math.atan2(towards[1],towards[0]),math.atan2(-2.5,gap)]}]
        for index,(actor,commands) in enumerate(zip(actors,controls)):
            gun = actor["weapons"][0]
            stats = gun["stats"]
            desired = commands["aim"]
            for axis,rate in enumerate(("turn_rate","pitch_rate")):
                delta = angle_diff(desired[axis],actor["aim"][axis])
                limit = actor["stats"][rate]*.1
                actor["aim"][axis] += max(-limit,min(limit,delta))
            if (commands.get("reload") or gun["ammo"]==0) and reload_at[index] is None and gun["ammo"]<stats["mag"]:
                reload_at[index] = now+stats["reload"]
            if commands["fire"][0] and now >= next_shot[index] and reload_at[index] is None:
                gun["ammo"] -= 1
                next_shot[index] = now+stats["cooldown"]
                flight = gap/stats["speed"]
                yaw = actor["aim"][0]+rng.gauss(0,math.radians(stats["spread_deg"]))
                landing = (actor["pos"][0]+gap*math.cos(yaw),actor["pos"][1]+gap*math.sin(yaw))
                height = cfg["mech"]["muzzle_height"]+math.tan(actor["aim"][1])*gap
                if 0 <= height <= cfg["mech"]["height"] and gap <= stats["range"]:
                    arrivals.append((now+flight,1-index,landing,stats["damage"]))
        for actor,commands in zip(actors,controls):
            move = commands["move"]
            norm = max(1,math.hypot(*move))
            for axis in (0,1):
                speed = actor["stats"]["walk_speed"]*move[axis]/norm
                delta = speed-actor["vel"][axis]
                actor["vel"][axis] += max(-5.85,min(5.85,delta))
                actor["pos"][axis] += actor["vel"][axis]*.1
        pending = []
        for due,victim,point,damage in arrivals:
            if now+.1 < due:
                pending.append((due,victim,point,damage))
            elif distance(actors[victim]["pos"],point) < cfg["mech"]["radius"]:
                actors[victim]["hp"] -= damage
                last_damage[:] = [now,now]
        arrivals = pending
        for index,actor in enumerate(actors):
            if now-last_damage[index] >= cfg["mech"]["regen_delay"]:
                actor["hp"] = min(actor["max_hp"],actor["hp"]+cfg["mech"]["regen_per_s"]*.1)
        if min(own["hp"],foe["hp"]) <= 0:
            break
    return "win" if foe["hp"]<=0<own["hp"] else ("loss" if own["hp"]<=0<foe["hp"] else "draw")


def duel_checks(hello, example, folder, count, seed):
    with patch.object(mechfight,"run"):
        starter = runpy.run_path(str(folder/"python/example_bot.py"))["Bot"]
    for name,policy_type in (("kit starter",starter),("Oval Runner",OvalRunner)):
        tally = Counter()
        for match in range(count):
            if policy_type == OvalRunner:
                policy = OvalRunner(hello,FlatArena())
                policy.router.waypoint = lambda pos,goal:goal
            else:
                policy = policy_type(hello)
            tally[duel(hello,example,policy,seed+match)] += 1
        print(f"Flat gun-only toy duels, {name}: {tally['win']} wins / {tally['loss']} losses / {tally['draw']} draws")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit",required=True,type=Path)
    parser.add_argument("--routes",type=int,default=64)
    parser.add_argument("--seed",type=int,default=1457)
    parser.add_argument("--duels",type=int,default=24)
    parser.add_argument("--zones",type=int,default=24)
    args = parser.parse_args()
    with gzip.open(args.kit/"check/stream.jsonl.gz","rt") as stream:
        hello,example = json.loads(next(stream)),json.loads(next(stream))
    passed = policy_checks(hello,example)
    print(f"Policy scenarios: {len(passed)} passed")
    pressure = pressure_checks(hello,example)
    assert all(pressure.values()), [case for case,passed in pressure.items() if not passed]
    print(f"Pressure and capture regressions: {len(pressure)} passed")
    arena = Arena(args.kit/"arena",hello["config"]["mech"])
    results = route_checks(arena,args.routes,args.seed)
    reentered = zone_checks(arena,hello,example,args.zones,args.seed)
    duel_checks(hello,example,args.kit,args.duels,args.seed)
    if results["planner"] < args.routes*.95:
        raise SystemExit("Routing regression: below 95% of reachable pairs")
    if reentered < args.zones*.95:
        raise SystemExit("Zone regression: below 95% of reachable circles")


if __name__ == "__main__":
    main()
