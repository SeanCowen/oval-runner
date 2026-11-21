import math

import mechfight
from navigation import Arena, Router, SafeRing, direction, distance


def angle_diff(first, second):
    return (first-second+math.pi) % math.tau-math.pi


def advance(point, vector, length):
    return point[0]+vector[0]*length, point[1]+vector[1]*length


def future_margin(zone, now, point, delay):
    start = max(now,zone["shrink_start"])
    fraction = min(1,max(0,(now+delay-start)/max(.001,zone["shrink_end"]-start)))
    center = [origin+(dest-origin)*fraction for origin,dest in zip(zone["center"],zone["next_center"])]
    radius = zone["radius"]+(zone["next_radius"]-zone["radius"])*fraction
    return radius-distance(point,center)


class OvalRunner:
    name = "Oval Runner"

    def __init__(self, hello, arena=None):
        self.config = hello["config"]
        self.arena = arena or Arena(hello["map"]["dir"], self.config["mech"])
        self.router = Router(self.arena)
        self.ring = SafeRing(self.arena) if hasattr(self.arena,"links") else None
        self.ring_route = None
        self.fast_rotation = self.sprinting = False
        self.phase = (hello["seed"] % 997) * math.tau / 997
        self.intent = None
        self.mode = "explore"
        self.retreating = False
        self.damage_frames = (-math.inf,-math.inf)
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
        self.opening_cache = None

    def drop_value(self, obs, drop, enemies):
        own = obs["self"]
        dist = distance(own["pos"],drop["pos"])
        rules = self.config["drops"]
        ring = drop.get("radius",rules["radius"])
        duration = drop.get("capture_s",rules["capture_s"])
        if drop["holder"] not in (None,own["id"]):
            return None
        arrival = max(0,dist-ring)/own["stats"]["walk_speed"]
        if dist > 32 and not self.arena.clear(own["pos"],drop["pos"]):
            arrival *= 1.45
        if arrival > self.config["drops"]["fall_s"]+3:
            return None
        if drop["lands_in"] > arrival+4:
            return None
        banked = drop.get("progress",0) if drop["holder"] == own["id"] and dist <= ring else 0
        finish_in = max(arrival,drop["lands_in"])+max(0,duration-banked)
        if future_margin(obs["zone"],obs["t"],drop["pos"],finish_in) < -ring*.3:
            return None
        enemy_gap = min((distance(enemy["pos"],drop["pos"])-ring for enemy in enemies),default=math.inf)
        contest_in = enemy_gap / self.config["mech"]["boost_speed"]
        if contest_in <= finish_in+.3:
            return None
        health_missing = 1-own["hp"]/own["max_hp"]
        return 110+48*health_missing-.5*min(dist,95)-.18*max(0,dist-95)+40*banked/duration

    def route_pickup(self, obs, travel):
        own, zone, now = obs["self"],obs["zone"],obs["t"]
        pos, speed = own["pos"],own["stats"]["walk_speed"]
        if not own["in_zone"] or self.ring_route is None or travel < 10 or not self.ring:
            return None
        choices = []
        for group in ("cubes","pickups"):
            for item in obs[group]:
                if group=="pickups" and (own["weapons"][1] or item["kind"] not in ("laser","lightning")):
                    continue
                point = item["pos"]
                dist = distance(pos,point)
                if dist>32 or abs(point[2]-pos[2])>4 or now < self.skip_until.get(item["id"],0):
                    continue
                node = self.arena.cell(point)
                if not self.arena.clear(pos,point):
                    continue
                remaining = self.ring.cost[node]*self.arena.step/10 if self.ring.settled[node] else travel+dist
                detour = max(0,dist+remaining-travel)
                spare = zone["shrink_end"]-now-travel*1.18/speed-3
                if detour>min(42,spare*speed*.45) or zone["shrink_end"]-now < (dist+remaining)*1.18/speed+3:
                    continue
                if future_margin(zone,now,point,dist/speed+2.5) < 5:
                    continue
                choices.append((detour+.3*dist,(group,item["id"]),point))
        if choices:
            _,key,goal = min(choices,key=lambda choice:choice[0])
            return key,goal
        return None

    def observe_target(self, obs):
        own = obs["self"]
        pos = own["pos"]
        origin = (*pos[:2], pos[2]+own["stats"]["muzzle_height"])
        rated = []
        farming_window = (own["cubes"]<3 and not own["weapons"][1] and own["hp"]>.65*own["max_hp"]
                          and obs["t"]-self.damage_frames[1]>2.5 and own["in_zone"] and
                          not any(actor["kind"]=="mech" and distance(pos,actor["pos"])<112 for actor in obs["mechs"]))
        self.opening_cache = None
        for obj in obs["mechs"] + obs["crates"]:
            kind = obj.get("kind","crate")
            dist = distance(pos,obj["pos"])
            if kind != "mech" and dist > (65 if kind == "npc" else 95):
                continue
            rating = {"mech":155,"npc":85,"crate":54}[kind] - dist*.46
            rating += 20*(1-obj.get("hp",1)/max(1,obj.get("max_hp",obj.get("hp",1))))
            rating += 8*(self.last_target == obj["id"]) + 8*bool(obj.get("charging"))
            if farming_window and kind=="crate" and dist<46 and abs(pos[2]-obj["pos"][2])<4:
                rating += 86
            rated.append((rating, obj, kind))
        for _, obj, kind in sorted(rated,key=lambda item:-item[0])[:6]:
            size = 1 + obj.get("cubes",0)*self.config["cubes"]["size_per_cube"]
            height = {"mech":self.config["mech"]["height"]*size, "npc":self.config["npc"]["height"],
                      "crate":self.config["crates"]["height"]}[kind]
            end = (*obj["pos"][:2], obj["pos"][2] + height*.55)
            if self.arena.visible(origin,end):
                if farming_window and kind=="crate":
                    self.opening_cache = obj["id"]
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

    def cube_values(self, obs):
        cubes = obs["cubes"]
        if len(cubes)<2 or obs["self"]["cubes"]>=self.config["cubes"]["stat_cap"]:
            return {}
        buckets = {}
        for cube in cubes:
            east,north,height = cube["pos"]
            key = math.floor(east/10),math.floor(north/10),math.floor(height/4)
            buckets[key] = buckets.get(key,0)+1
        values = {}
        for cube in cubes:
            east,north,height = cube["pos"]
            col,row,level = math.floor(east/10),math.floor(north/10),math.floor(height/4)
            count = buckets[col,row,level]-1
            adjacent = sum(buckets.get((col+dx,row+dy,level),0) for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)))
            values[cube["id"]] = min(26,8*count+3*adjacent)
        return values

    def choose(self, obs, target):
        own, zone, now = obs["self"], obs["zone"], obs["t"]
        pos, next_center = own["pos"], zone["next_center"]
        hp = own["hp"]/own["max_hp"]
        self.retreating = hp < .4 or (self.retreating and hp < .76)
        radius = zone["next_radius"]
        to_next = distance(pos,next_center)
        excess = max(0,to_next-max(0,radius-8))
        speed = own["stats"]["walk_speed"]
        self.ring_route = self.ring.route(pos,next_center,radius) if self.ring else None
        travel = self.ring_route[0] if self.ring_route is not None else 1.55*excess
        eta = travel/speed
        slack = zone["shrink_end"]-now-eta
        buffer = min(10, max(3, radius*.12))
        danger = travel > 2 and (slack < 4 or future_margin(zone,now,pos,eta*1.25+3) < buffer)
        keep_rotating = (self.mode=="zone" and travel>3 and now>=zone["shrink_start"] and
                         future_margin(zone,now,pos,eta*1.45+6)<buffer+6)
        urgent = not own["in_zone"] or danger or keep_rotating
        self.fast_rotation = urgent and (not own["in_zone"] or slack < 8 or
                             future_margin(zone,now,pos,eta*.8+2) < buffer)
        hold_radius = max(0,min(radius*.62,radius-15))
        zone_goal = advance(next_center,direction(next_center,pos),hold_radius)
        if radius == 0 and zone["radius"] < 14 and now >= zone["shrink_start"]:
            return ("zone",0),next_center
        if urgent:
            pickup = self.route_pickup(obs,travel)
            if pickup is not None:
                return pickup
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
            add(139, "recover" if self.retreating else "evade",0,advance(pos,away,25))
        if target:
            enemy,kind,_ = target
            if kind == "mech" and (not self.retreating or finisher):
                add(140 if finisher else 78, "duel",enemy["id"],enemy["pos"])
            elif kind == "npc" and not self.retreating and own["cubes"] < 10:
                add(64, "farm",enemy["id"],enemy["pos"])
        bonuses = self.cube_values(obs)
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
                value = 108+bonuses.get(item["id"],0) if own["cubes"] < self.config["cubes"]["stat_cap"] else 72
            else:
                if self.retreating or own["cubes"] >= 10:
                    continue
                value = 92 if item["id"]==self.opening_cache else 62
            add(value-dist*.65,group,item["id"],point)
        for drop in obs["drops"]:
            value = self.drop_value(obs,drop,nearby)
            if value is not None:
                add(value,"drop",drop["id"],drop["pos"])
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
        if any(event.get("type") == "damaged" and event.get("by") is not None and
               event.get("weapon") != "zone" for event in obs["events"]) and now > self.damage_frames[1]:
            self.damage_frames = self.damage_frames[1],now
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
        if self.mode=="zone" and self.ring_route is not None and self.ring_route[0]>0:
            waypoint = self.ring_route[1]
            move = direction(pos,waypoint) if distance(pos,waypoint)>.25 else (0,0)
        if self.mode in ("recover","evade"):
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
        if (self.mode=="zone" and obs["zone"]["next_radius"]==0 and
                distance(pos,obs["zone"]["next_center"])<5 and self.arena.clear(pos,obs["zone"]["next_center"])):
            center = obs["zone"]["next_center"]
            gap = distance(pos,center)
            scale = min(1,5*gap/own["stats"]["walk_speed"])
            move = tuple(value*scale for value in direction(pos,center))
            self.fast_rotation = False
        if now-self.anchor_time >= 1.25:
            if self.anchor and distance(self.anchor,pos) < 2.5 and any(self.last_move) and any(move) and distance(pos,goal) > 8:
                self.router.invalidate()
                self.escape_until = now+.65
            self.anchor,self.anchor_time = tuple(pos),now
        escape = now < self.escape_until
        travel_sprint = self.mode=="zone" and self.fast_rotation or self.mode=="drop" and distance(pos,goal)>25
        self.sprinting = travel_sprint and own["energy"] > own["max_energy"]*(.36 if self.sprinting else .72)
        boost = (math.hypot(*move)>.8 and (escape or (own["grounded"] and own["energy"] > 32 and
                 (self.sprinting or self.mode in ("recover","evade") or self.mode == "zone" and not own["in_zone"]))))
        self.last_move = move
        action = {"move":list(move),"boost":bool(boost and any(move)),"jump":escape and own["grounded"],"fire":[False,False]}
        guns = own["weapons"]
        if target:
            enemy,kind,end = target
            self.last_target = enemy["id"]
            dist = distance(pos,end)
            secondary = guns[1]
            laser = secondary and secondary["kind"] == "laser"
            use_beam = laser and secondary["cooldown"] <= .1 and self.mode not in ("zone","recover","evade")
            recent_pressure = now-self.damage_frames[0] <= 1.5 and now-self.damage_frames[1] <= .35
            fire_ok = not self.retreating or kind == "mech" and (self.mode == "duel" or recent_pressure)
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
