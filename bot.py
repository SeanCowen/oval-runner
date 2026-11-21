import math

import mechfight
from navigation import Arena, Router, direction, distance


def angle_diff(first, second):
    return (first-second+math.pi) % math.tau-math.pi


def advance(point, vector, length):
    return point[0]+vector[0]*length, point[1]+vector[1]*length


class OvalRunner:
    name = "Oval Runner"

    def __init__(self, hello, arena=None):
        self.config = hello["config"]
        self.arena = arena or Arena(hello["map"]["dir"], self.config["mech"])
        self.router = Router(self.arena)
        self.phase = (hello["seed"] % 997) * math.tau / 997
        self.intent = None
        self.mode = "explore"
        self.retreating = False
        self.anchor = None
        self.anchor_time = self.escape_until = -10
        self.last_target = None
        self.next_scan = 0
        self.roam_index = 0
        self.roam = None
        self.loot_memory = {}
        self.last_move = (0,0)
        self.progress_time = 0
        self.progress_dist = math.inf
        self.skip_until = {}

    def observe_target(self, obs):
        own = obs["self"]
        pos = own["pos"]
        origin = (*pos[:2], pos[2]+own["stats"]["muzzle_height"])
        rated = []
        for obj in obs["mechs"] + obs["crates"]:
            kind = obj.get("kind","crate")
            dist = distance(pos,obj["pos"])
            if kind != "mech" and dist > (65 if kind == "npc" else 95):
                continue
            rating = {"mech":155,"npc":85,"crate":54}[kind] - dist*.46
            rating += 20*(1-obj.get("hp",1)/max(1,obj.get("max_hp",obj.get("hp",1))))
            rating += 8*(self.last_target == obj["id"]) + 8*bool(obj.get("charging"))
            rated.append((rating, obj, kind))
        for _, obj, kind in sorted(rated,key=lambda item:-item[0])[:6]:
            size = 1 + obj.get("cubes",0)*self.config["cubes"]["size_per_cube"]
            height = {"mech":self.config["mech"]["height"]*size, "npc":self.config["npc"]["height"],
                      "crate":self.config["crates"]["height"]}[kind]
            end = (*obj["pos"][:2], obj["pos"][2] + height*.55)
            if self.arena.visible(origin,end):
                return obj,kind,end
        return None

    def remember(self, obs):
        now, pos = obs["t"], obs["self"]["pos"]
        visible = set()
        for group in ("cubes","pickups","crates"):
            for item in obs[group]:
                visible.add(item["id"])
                self.loot_memory[item["id"]] = (group,item,now)
        for ident,(group,item,seen) in tuple(self.loot_memory.items()):
            if ident not in visible and (now-seen > 4.5 or distance(pos,item["pos"]) < 8):
                del self.loot_memory[ident]

    def choose(self, obs, target):
        own, zone, now = obs["self"], obs["zone"], obs["t"]
        pos, next_center = own["pos"], zone["next_center"]
        hp = own["hp"]/own["max_hp"]
        self.retreating = hp < .4 or (self.retreating and hp < .76)
        radius = zone["next_radius"]
        to_next = distance(pos,next_center)
        excess = max(0,to_next-max(0,radius-8))
        speed = own["stats"]["walk_speed"]
        urgent = not own["in_zone"] or (excess > 0 and zone["shrink_end"]-now < 6 + 1.9*excess/speed)
        hold_radius = max(0,min(radius*.62,radius-15))
        zone_goal = advance(next_center,direction(next_center,pos),hold_radius)
        if radius == 0 and zone["radius"] < 14 and now >= zone["shrink_start"]:
            return ("zone",0),next_center
        if urgent:
            return ("zone",0),zone_goal
        enemies = [actor for actor in obs["mechs"] if actor["kind"] == "mech"]
        nearby = sorted(enemies,key=lambda actor:distance(pos,actor["pos"]))
        options = []

        def add(score, mode, ident, goal):
            key = mode,ident
            options.append((score + 8*(key == self.intent),key,goal))

        if excess > 0:
            add(43+min(20,excess/7), "zone",0,zone_goal)
        count = sum(distance(pos,enemy["pos"]) < 85 for enemy in nearby)
        finisher = (target and target[1] == "mech" and count == 1 and distance(pos,target[2]) < 70 and
                    own["weapons"][0] and own["weapons"][0].get("ammo",0) >= 2 and
                    target[0].get("hp",math.inf) <= own["weapons"][0]["stats"]["damage"]*1.8)
        if nearby and not finisher and (self.retreating or count >= 2) and distance(pos,nearby[0]["pos"]) < 110:
            away = direction(nearby[0]["pos"],pos)
            add(139, "recover",0,advance(pos,away,25))
        if target:
            enemy,kind,_ = target
            if kind == "mech" and (distance(pos,enemy["pos"]) < 190 or not self.retreating):
                add(140 if finisher else 78, "duel",enemy["id"],enemy["pos"])
            elif kind == "npc" and not self.retreating and own["cubes"] < 10:
                add(64, "farm",enemy["id"],enemy["pos"])
        for group,item,_ in self.loot_memory.values():
            if now < self.skip_until.get(item["id"],0):
                continue
            point = item["pos"]
            dist = distance(pos,point)
            if abs(point[2]-self.arena.ground_height(point)) > 4 and abs(point[2]-pos[2]) > 4:
                continue
            if distance(point,zone["center"]) > zone["radius"]-min(3,zone["radius"]) or dist > 115:
                continue
            if group == "pickups":
                if own["weapons"][1] or item["kind"] not in ("lightning","laser"):
                    continue
                value = 106 if item["kind"] == "lightning" else 101
            elif group == "cubes":
                value = 108 if own["cubes"] < 10 else 72
            else:
                if self.retreating or own["cubes"] >= 10:
                    continue
                value = 62
            add(value-dist*.65,group,item["id"],point)
        for drop in obs["drops"]:
            dist = distance(pos,drop["pos"])
            if drop["holder"] not in (None,own["id"]) or count or dist > 135:
                continue
            if drop["lands_in"] > dist/speed+4:
                continue
            add(110+48*(1-hp)-.5*dist,"drop",drop["id"],drop["pos"])
        if self.roam is None or distance(pos,self.roam) < 10 or now > self.next_scan:
            self.roam_index += 1
            bearing = self.phase + self.roam_index*2.4
            radius_to_walk = min(95,hold_radius)
            self.roam = advance(next_center,(math.cos(bearing),math.sin(bearing)),radius_to_walk)
            self.next_scan = now+15
        add(13,"explore",0,self.roam)
        _,key,goal = max(options,key=lambda item:item[0])
        return key,goal

    def local_move(self, obs, enemy, recover):
        own, zone = obs["self"],obs["zone"]
        pos = own["pos"]
        towards = direction(pos,enemy["pos"])
        dist = distance(pos,enemy["pos"])
        secondary = own["weapons"][1]
        preferred = min(84,max(55,own["stats"]["walk_speed"]*5.8))
        if secondary and secondary["kind"] == "lightning":
            preferred = secondary["stats"]["range"]*.73
        radial = -1 if recover else max(-1,min(1,(dist-preferred)/22))
        side = 1 if math.sin(self.phase + obs["t"]*.8) >= 0 else -1
        lateral = .55 if recover else .94
        ideal = (towards[0]*radial-towards[1]*lateral*side,towards[1]*radial+towards[0]*lateral*side)
        base = math.atan2(ideal[1],ideal[0])
        choices = []
        for offset in (0,.48,-.48,.96,-.96,1.55,-1.55,2.25,-2.25,math.pi):
            vector = math.cos(base+offset),math.sin(base+offset)
            point = advance(pos,vector,12)
            if not self.arena.clear(pos,point):
                continue
            margin = zone["radius"]-distance(point,zone["center"])
            score = 2.5*math.cos(offset)-max(0,4-margin)*1.2
            if recover:
                eye = (*enemy["pos"][:2],enemy["pos"][2]+self.config["mech"]["eye_height"])
                head = (*point,self.arena.ground_height(point)+own["stats"]["eye_height"])
                score += 4*int(not self.arena.visible(eye,head))
            choices.append((score,vector))
        return max(choices,key=lambda item:item[0])[1] if choices else None

    def act(self, obs):
        self.remember(obs)
        own, pos = obs["self"],obs["self"]["pos"]
        now = obs["t"]
        target = self.observe_target(obs)
        key,goal = self.choose(obs,target)
        goal_dist = distance(pos,goal)
        if key != self.intent or goal_dist < self.progress_dist-2:
            self.progress_time,self.progress_dist = now,goal_dist
        elif key[0] in ("pickups","cubes","crates") and now-self.progress_time > 6:
            self.skip_until[key[1]] = now+15
            key,goal = self.choose(obs,target)
            self.progress_time,self.progress_dist = now,distance(pos,goal)
        if key != self.intent:
            self.router.invalidate()
        self.intent, self.mode = key,key[0]
        move = None
        if self.mode == "recover":
            enemy = min((actor for actor in obs["mechs"] if actor["kind"]=="mech"),key=lambda actor:distance(pos,actor["pos"]))
            move = self.local_move(obs,enemy,True)
        elif self.mode == "duel" and target and distance(pos,target[0]["pos"]) < 115:
            move = self.local_move(obs,target[0],False)
        elif self.mode == "crates" and target and target[0]["id"] == key[1] and distance(pos,goal) < 28:
            move = (0,0) if distance(pos,goal) >= 12 else direction(goal,pos)
        if self.mode == "drop" and distance(pos,goal) < self.config["drops"]["radius"]*.72:
            move = (0,0)
        if move is None:
            waypoint = self.router.waypoint(pos,goal)
            move = direction(pos,waypoint) if distance(pos,waypoint) > .5 else (0,0)
        if now-self.anchor_time >= 1.25:
            if self.anchor and distance(self.anchor,pos) < 2.5 and any(self.last_move) and any(move) and distance(pos,goal) > 8:
                self.router.invalidate()
                self.escape_until = now+.65
            self.anchor,self.anchor_time = tuple(pos),now
        escape = now < self.escape_until
        boost = (escape or (own["grounded"] and own["energy"] > 32 and
                 (self.mode == "recover" or self.mode == "zone" and not own["in_zone"])))
        self.last_move = move
        action = {"move":list(move),"boost":bool(boost and any(move)),"jump":escape and own["grounded"],"fire":[False,False]}
        guns = own["weapons"]
        if target:
            enemy,kind,end = target
            self.last_target = enemy["id"]
            dist = distance(pos,end)
            secondary = guns[1]
            laser = secondary and secondary["kind"] == "laser"
            use_beam = laser and secondary["cooldown"] <= .1 and self.mode not in ("zone","recover")
            fire_ok = self.mode != "recover" and (not self.retreating or kind == "mech")
            if fire_ok and not use_beam and guns[0] and guns[0]["kind"] == "gun":
                frames = min(.75,dist/max(1,guns[0]["stats"]["speed"]))
                vel = enemy.get("vel",(0,0,0))
                end = end[0]+vel[0]*frames,end[1]+vel[1]*frames,end[2]
            yaw = math.atan2(end[1]-pos[1],end[0]-pos[0])
            pitch = math.atan2(end[2]-pos[2]-own["stats"]["muzzle_height"],max(.1,distance(pos,end)))
            pitch = max(own["stats"]["min_pitch"],min(own["stats"]["max_pitch"],pitch))
            action["aim"] = [yaw,pitch]
            yaw_error = abs(angle_diff(own["aim"][0],yaw))
            error = max(yaw_error,abs(own["aim"][1]-pitch))
            aligned = error < .045 + 1.7/max(7,dist)
            if guns[0]:
                action["fire"][0] = bool(fire_ok and aligned and dist <= guns[0]["stats"]["range"])
            if use_beam and fire_ok and dist <= secondary["stats"]["range"]:
                action["fire"][1] = not (secondary.get("charge",0) >= secondary["stats"]["charge"]*.97 and aligned)
            elif secondary and secondary["kind"] == "lightning":
                stats = secondary["stats"]
                action["fire"][1] = bool(fire_ok and dist <= stats["range"] and own["energy"] >= stats["energy"]
                                         and yaw_error <= math.radians(stats["cone_deg"])*.85)
        else:
            self.last_target = None
            clues = [event for event in obs["events"] if event["type"] == "damaged" and event.get("bearing") is not None]
            noises = [sound for sound in obs["sounds"] if sound["kind"] not in ("drop","crate","death")]
            bearing = clues[-1]["bearing"] if clues else (min(noises,key=lambda entry:entry["age"])["bearing"] if noises else None)
            action["aim"] = [bearing if bearing is not None else angle_diff(self.phase+now*1.45,0),0]
        gun = guns[0]
        if gun and not action["fire"][0] and gun.get("ammo",0) <= gun["stats"]["mag"]*.66 and not gun.get("reload"):
            if target is None or self.mode == "recover" or gun["ammo"] == 0:
                action["reload"] = True
        if not guns[1]:
            reachable = [item for item in obs["pickups"] if item["kind"] in ("laser","lightning")
                         and distance(pos,item["pos"]) < self.config["loot"]["pickup_radius"] and abs(pos[2]-item["pos"][2]) < 4]
            if reachable:
                action["take"] = max(reachable,key=lambda item:item["kind"]=="lightning")["id"]
        if obs.get("offer"):
            order = ["plating","gun_mag","armor","gun_damage","nanites","lightning_range","gun_rate","laser_damage",
                     "servos","laser_charge","lightning_efficiency","gun_velocity","reactor","gyros","jets","sensors"]
            if self.retreating:
                order.insert(0,"nanites")
            options = obs["offer"]["choices"]
            action["pick"] = min(range(len(options)),key=lambda idx:order.index(options[idx]["id"]) if options[idx]["id"] in order else 99)
        return action


if __name__ == "__main__":
    mechfight.run(OvalRunner)
