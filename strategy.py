import ctypes
import math
import os
from pathlib import Path

from navigation import distance


class Site(ctypes.Structure):
    _fields_ = [(name,ctypes.c_int32) for name in ("kind","cubes")] + [
        (name,ctypes.c_double) for name in ("open","expires","service","return_time","confidence","continuity")]


class Config(ctypes.Structure):
    _fields_ = [(name,ctypes.c_double) for name in
                ("walk","boost","energy","capacity","drain","regen","regen_delay","health","time_left","horizon")] + [
        (name,ctypes.c_int32) for name in ("cubes","cap","secondary","threshold_count")] + [
        ("thresholds",ctypes.c_int32*16)]


class Strategy:
    def __init__(self, rules, arena):
        self.rules,self.arena = rules,arena
        self.cached = None
        self.expires = -1
        self.legs = {}
        self.sprint = False
        self.depth = 5
        try:
            self.library = ctypes.CDLL(str(Path(__file__).resolve().with_name("liboval.so")))
        except OSError:
            if os.environ.get("OVAL_REQUIRE_RUST")=="1":
                raise
            self.library = None
        if self.library:
            solve = self.library.oval_plan
            solve.argtypes = [ctypes.POINTER(Config), ctypes.POINTER(Site), ctypes.POINTER(ctypes.c_double),
                              ctypes.c_size_t,ctypes.c_size_t]
            solve.restype = ctypes.c_int32

    def leg(self, first, second, fixed):
        key = tuple(sorted((tuple(first[:2]),tuple(second[:2])))) if fixed else None
        if fixed and key in self.legs:
            return self.legs[key]
        length = distance(first,second)
        if length>5 and not self.arena.clear(first,second):
            length = 1.55*length+9
        if fixed:
            if len(self.legs)>4096:
                self.legs.clear()
            self.legs[key] = length
        return length

    def pick(self, bot, obs, options, baseline, enemies):
        self.sprint = False
        own,zone,now = obs["self"],obs["zone"],obs["t"]
        pos = own["pos"]
        mode = baseline[0][0]
        nearest = min((distance(pos,actor["pos"]) for actor in enemies),default=math.inf)
        if (not self.library or mode in ("zone","recover","evade","farm") or bot.retreating and mode!="drop"
                or nearest<95 or now-bot.damage_frames[1]<2 or
                mode=="duel" and (own["cubes"]>=3 or nearest<130)):
            self.cached = None
            return baseline
        records = {key:(score,goal) for score,key,goal in options if key[0] in ("crates","cubes","pickups","drop")}
        future_drops = set()
        for drop in obs["drops"]:
            key = "drop",drop["id"]
            if (key not in records and drop["holder"] is None and drop["lands_in"]>0
                    and distance(pos,drop["pos"])<=own["stats"]["boost_speed"]*22):
                records[key] = (115,drop["pos"])
                future_drops.add(key)
        if not records:
            self.cached = None
            return baseline
        if self.cached and self.cached[0] in records and self.cached[0] not in future_drops and now<self.expires:
            key,self.sprint = self.cached
            return key,records[key][1]
        stats = own["stats"]
        threshold_values = self.rules["cubes"]["upgrade_thresholds"][:16]
        thresholds = (ctypes.c_int32*16)(*threshold_values)
        remaining = max(0,zone["shrink_end"]-now)
        cfg = Config(stats["walk_speed"],stats["boost_speed"],own["energy"],own["max_energy"],
                     stats["boost_drain"],stats["energy_regen"],stats["energy_regen_delay"],
                     own["hp"]/own["max_hp"],remaining,
                     min(28,max(9,(nearest-35)/max(1,stats["walk_speed"])+8)),own["cubes"],
                     self.rules["cubes"]["stat_cap"],int(bool(own["weapons"][1])),len(threshold_values),thresholds)
        rows = []
        for key,(score,goal) in sorted(records.items(),key=lambda entry:-entry[1][0]):
            group,ident = key
            kind = ("cubes","crates","pickups","drop").index(group)
            opening,deadline,service = 0,math.inf,0
            award = 0 if kind==2 else 1
            conf = 1.0
            if kind==3:
                drop = next(item for item in obs["drops"] if item["id"]==ident)
                if drop["holder"]==own["id"] and drop["progress"]>0:
                    self.cached = None
                    return baseline
                rules = self.rules["drops"]
                opening = drop["lands_in"]
                service = drop.get("capture_s",rules["capture_s"])
                award = rules["cubes"]
                if key in future_drops:
                    kind = 4
                deadline = min(((distance(actor["pos"],goal)-drop.get("radius",rules["radius"]))/cfg.boost
                                for actor in enemies),default=math.inf)-.3
            else:
                _,item,seen = bot.loot_memory[ident]
                conf = max(.5,1-.11*(now-seen))
                if kind==1:
                    gun = own["weapons"][0]
                    service = 1+item.get("hp",300)*gun["stats"]["cooldown"]/max(1,gun["stats"]["damage"])
                    conf *= .82
            home = max(0,distance(goal,zone["next_center"])-max(0,zone["next_radius"]-5))*1.4
            if bot.ring and bot.ring_route and bot.ring.settled[self.arena.cell(goal)]:
                home = bot.ring.cost[self.arena.cell(goal)]*self.arena.step/10
            rows.append((key,goal,Site(kind,award,opening,deadline,service,home/cfg.walk,conf,
                                      1.4 if key==bot.intent else 0)))
            if len(rows)>=14:
                break
        points = [pos]+[row[1] for row in rows]
        stride = len(points)
        legs = (ctypes.c_double*(stride*stride))()
        for first,point in enumerate(points):
            for second in range(first+1,stride):
                length = self.leg(point,points[second],first!=0)
                legs[first*stride+second] = legs[second*stride+first] = length
        sites = (Site*len(rows))(*(row[2] for row in rows))
        answer = self.library.oval_plan(ctypes.byref(cfg),sites,legs,len(rows),self.depth)
        self.expires = now+.55
        if answer<0 or (mode=="duel" and distance(pos,rows[answer//2][1])>42):
            self.cached = None
            return baseline
        key,goal,_ = rows[answer//2]
        self.sprint = bool(answer%2)
        self.cached = (key,self.sprint)
        return key,goal
