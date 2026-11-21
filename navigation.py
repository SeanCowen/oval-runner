import heapq
import json
import math
import sys
from array import array
from collections import deque
from pathlib import Path


def direction(start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length else (0.0, 0.0)


def distance(start, end):
    return math.hypot(end[0] - start[0], end[1] - start[1])


class Arena:
    step = 4

    def __init__(self, directory, mech):
        folder = Path(directory) / "v2"
        world = json.loads((folder / "world.json").read_text())
        info = world["terrain"]
        terrain = array("h", (folder / info["file"]).read_bytes())
        if sys.byteorder != "little":
            terrain.byteswap()
        bounds = world["bounds"]
        self.left = math.ceil(bounds["minX"])
        self.bottom = math.ceil(bounds["minY"])
        self.cols = int((bounds["maxX"] - self.left) // self.step) + 1
        self.rows = int((bounds["maxY"] - self.bottom) // self.step) + 1
        self.fcols = self.cols * 2
        self.frows = self.rows * 2
        total = self.fcols * self.frows
        self.ground = array("f")
        self.roof = array("f", [-1e4]) * total
        self.base = array("f", [1e4]) * total
        for row in range(self.frows):
            gy = int((self.bottom + row * 2 - info["minY"]) / info["step"])
            offset = min(info["ny"] - 1, max(0, gy)) * info["nx"]
            for col in range(self.fcols):
                gx = int((self.left + col * 2 - info["minX"]) / info["step"])
                self.ground.append(terrain[offset + min(info["nx"] - 1, max(0, gx))] * info["scale"])
        for prism in world["prisms"]:
            polygons = [prism["ring"]] + prism["holes"]
            edges = []
            for polygon in polygons:
                vertices = list(zip(polygon[::2], polygon[1::2]))
                for first, second in zip(vertices, vertices[1:] + vertices[:1]):
                    if second[1] < first[1]:
                        first, second = second, first
                    if first[1] != second[1]:
                        edges.append((first[1], second[1], first[0], (second[0]-first[0])/(second[1]-first[1])))
            if not edges:
                continue
            low = max(0, math.ceil((min(edge[0] for edge in edges) - self.bottom) / 2))
            high = min(self.frows, math.ceil((max(edge[1] for edge in edges) - self.bottom) / 2))
            for row in range(low, high):
                north = self.bottom + 2 * row
                spans = sorted(east + (north - begin) * slope for begin, end, east, slope in edges if begin <= north < end)
                for left, right in zip(spans[::2], spans[1::2]):
                    begin = max(0, math.ceil((left-self.left)/2))
                    end = min(self.fcols, math.ceil((right-self.left)/2))
                    for index in range(row*self.fcols+begin, row*self.fcols+end):
                        self.roof[index] = max(self.roof[index], prism["top"])
                        self.base[index] = min(self.base[index], prism["base"])
        solid = bytearray(total)
        slope = math.tan(math.radians(mech["max_slope_deg"])) * 2
        for index in range(self.fcols, total-self.fcols):
            floor = self.ground[index]
            solid[index] = (self.roof[index] > floor + mech["step_up"] and self.base[index] < floor + mech["height"] + 1
                            or max(abs(floor-self.ground[index+1]), abs(floor-self.ground[index+self.fcols])) > slope)
        self.walk = bytearray(self.cols*self.rows)
        pad = math.ceil((mech["radius"] + 1.5) / 2)
        offsets = [dy*self.fcols+dx for dy in range(-pad,pad+1) for dx in range(-pad,pad+1) if dx*dx+dy*dy <= pad*pad]
        for row in range(pad, self.rows-pad):
            for col in range(pad, self.cols-pad):
                center = row*2*self.fcols+col*2
                self.walk[row*self.cols+col] = not any(solid[center+delta] for delta in offsets)
        self.links = [()] * len(self.walk)
        for index, open_cell in enumerate(self.walk):
            if not open_cell:
                continue
            neighbours = [(index+delta, 10) for delta in (-1,1,-self.cols,self.cols) if self.walk[index+delta]]
            for vertical in (-self.cols,self.cols):
                for lateral in (-1,1):
                    if self.walk[index+vertical] and self.walk[index+lateral] and self.walk[index+vertical+lateral]:
                        neighbours.append((index+vertical+lateral,14))
            self.links[index] = tuple(neighbours)
        self.region = [-1] * len(self.walk)
        for index, open_cell in enumerate(self.walk):
            if open_cell and self.region[index] == -1:
                self.region[index] = index
                queue = deque([index])
                while queue:
                    for neighbour, _ in self.links[queue.popleft()]:
                        if self.region[neighbour] == -1:
                            self.region[neighbour] = index
                            queue.append(neighbour)

    def cell(self, point):
        col = min(self.cols-1, max(0, math.floor((point[0]-self.left)/self.step+.5)))
        row = min(self.rows-1, max(0, math.floor((point[1]-self.bottom)/self.step+.5)))
        return row*self.cols+col

    def point(self, cell):
        return self.left + (cell % self.cols)*self.step, self.bottom + (cell // self.cols)*self.step

    def snap(self, point, region=None):
        cell = self.cell(point)
        col, row = cell % self.cols, cell // self.cols
        for radius in range(0, 30):
            best, best_dist = None, math.inf
            for dy in range(-radius, radius+1):
                if not 0 <= row+dy < self.rows:
                    continue
                span = range(-radius, radius+1) if abs(dy) == radius else (-radius, radius)
                for dx in span:
                    if not 0 <= col+dx < self.cols:
                        continue
                    candidate = cell+dy*self.cols+dx
                    if self.walk[candidate] and (region is None or self.region[candidate] == region):
                        dist = distance(point, self.point(candidate))
                        if dist < best_dist:
                            best, best_dist = candidate, dist
            if best is not None:
                return best
        return None

    def ground_height(self, point):
        col = min(self.fcols-1,max(0,round((point[0]-self.left)/2)))
        row = min(self.frows-1,max(0,round((point[1]-self.bottom)/2)))
        return self.ground[row*self.fcols+col]

    def visible(self, start, end):
        steps = max(1, math.ceil(distance(start,end)/2))
        for sample in range(1,steps):
            fraction = sample/steps
            east, north, height = (first + (second-first)*fraction for first, second in zip(start,end))
            col, row = round((east-self.left)/2), round((north-self.bottom)/2)
            if not (0 <= col < self.fcols and 0 <= row < self.frows):
                return False
            index = row*self.fcols+col
            if height < self.ground[index] or self.base[index] < height < self.roof[index]:
                return False
        return True

    def clear(self, start, end):
        node, goal = self.cell(start), self.cell(end)
        if not self.walk[node] or not self.walk[goal]:
            return False
        dx,dy = end[0]-start[0],end[1]-start[1]
        east,north = self.point(node)
        side, up = (1 if dx>0 else -1),(1 if dy>0 else -1)
        cross_x = (east+side*self.step*.5-start[0])/dx if dx else math.inf
        cross_y = (north+up*self.step*.5-start[1])/dy if dy else math.inf
        delta_x = self.step/abs(dx) if dx else math.inf
        delta_y = self.step/abs(dy) if dy else math.inf
        while node != goal:
            same_col = node%self.cols == goal%self.cols
            same_row = node//self.cols == goal//self.cols
            if same_row or (not same_col and cross_x < cross_y-1e-10):
                node += side
                cross_x += delta_x
            elif same_col or cross_y < cross_x-1e-10:
                node += up*self.cols
                cross_y += delta_y
            else:
                if not self.walk[node+side] or not self.walk[node+up*self.cols]:
                    return False
                node += side+up*self.cols
                cross_x += delta_x
                cross_y += delta_y
            if not self.walk[node]:
                return False
        return True


class Router:
    def __init__(self, arena):
        self.arena = arena
        self.goal = self.raw_goal = self.search = None
        self.path = deque()

    def invalidate(self):
        self.raw_goal = self.goal = self.search = None
        self.path.clear()

    def waypoint(self, pos, goal):
        arena = self.arena
        start = arena.snap(pos)
        if start is None:
            return goal
        if self.raw_goal is None or distance(goal,self.raw_goal) > 5 or self.goal is None:
            dest = arena.snap(goal, arena.region[start])
            self.raw_goal = tuple(goal[:2])
            if dest != self.goal:
                self.path.clear()
                self.goal, self.search = dest, None
        if self.goal is None:
            return arena.point(start)
        if not arena.walk[arena.cell(pos)]:
            return arena.point(start)
        if distance(pos,goal) < 32 and arena.clear(pos,goal):
            return goal
        if start == self.goal:
            return arena.point(start)
        if not self.path and self.search is None:
            self.search = ([(0,0,start)], {start: 0}, {}, set())
        if self.search is not None:
            frontier, costs, parents, visited = self.search
            goal_col, goal_row = self.goal % arena.cols, self.goal // arena.cols
            for _ in range(750):
                if not frontier:
                    self.search = None
                    break
                _, neg_cost, node = heapq.heappop(frontier)
                if node in visited or -neg_cost != costs[node]:
                    continue
                visited.add(node)
                if node == self.goal:
                    while node in parents:
                        self.path.appendleft(node)
                        node = parents[node]
                    self.search = None
                    break
                for other, cost in arena.links[node]:
                    total = cost-neg_cost
                    if other in visited or total >= costs.get(other, 1<<30):
                        continue
                    costs[other], parents[other] = total, node
                    dx, dy = abs(other%arena.cols-goal_col),abs(other//arena.cols-goal_row)
                    estimate = 10*max(dx,dy)+4*min(dx,dy)
                    heapq.heappush(frontier,(total+1.35*estimate,-total,other))
        while self.path and distance(pos,arena.point(self.path[0])) < 1.8:
            self.path.popleft()
        if self.path:
            for index in range(min(6,len(self.path)-1),0,-1):
                point = arena.point(self.path[index])
                if distance(pos,point) <= 32 and arena.clear(pos,point):
                    for _ in range(index):
                        self.path.popleft()
                    break
            point = arena.point(self.path[0])
            if not arena.clear(pos,point):
                self.invalidate()
                return arena.point(start)
            return point
        return pos
