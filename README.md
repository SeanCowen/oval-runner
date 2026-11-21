# Oval Runner

A Rust strategic planner with a standard-library Python game controller for the Builders Mech Royale track. The Dockerfile belongs at the repository root. The server supplies the arena at runtime; no map, credentials, training set or runtime network access is required.

## V5 repair decisions

A crate hit restarts the same repair timer as incoming damage. While wounded and inside the zone, V5 estimates the HP it would give up by opening a visible crate. It uses the current repair delay and rate, the remaining crate HP, the magazine and the reload time. It compares that cost with the cube's immediate heal, permanent stats and proximity to the next upgrade offer. A nearly open crate can still be worth finishing right after a hit. Loose cubes and supplies remain available during repair. When wounded, the bot also gives the nanites offer higher priority.

| Scripted six-wave test | V4 survived | V5 survived | V4 mean cubes | V5 mean cubes |
| --- | ---: | ---: | ---: | ---: |
| Light damage, long gaps | 192/192 | 192/192 | 9.43 | 8.28 |
| Mixed gaps | 73/192 | 97/192 | 2.96 | 2.83 |
| Short gaps | 31/192 | 47/192 | 2.68 | 2.32 |

Each pair receives the same incoming damage and the same time-limited crates. The test holds position fixed, immediately credits an opened crate's cube, and omits opponents' decisions and secondary equipment. It uses two seeds and two assumptions about plating. The official game awards points for placement among 16 mechs.

Build both the scored commit `b845d48` and this version as `oval-runner:v4` and `oval-runner:v5`, respectively. Run the following from the V5 checkout with `KIT` pointing to the original kit.

```sh
for tag in v4 v5; do
  for seed in 33851 80669; do
    fill=""
    if [ "$seed" = 80669 ]; then fill="--plating-fills"; fi
    docker run --platform linux/amd64 --rm --network none --read-only --cpus 1 --memory 512m \
      -v "$KIT:/kit:ro" -v "$PWD/repair_eval.py:/bot/repair_eval.py:ro" \
      --entrypoint python "oval-runner:$tag" -Bu repair_eval.py --kit /kit --count 96 --seed "$seed" $fill
  done
done
```

## V4: five decisions ahead

Instead of rating only the next resource, a bounded Rust beam search ranks sequences of up to five stops among fourteen eligible objectives. Each simulated step tracks elapsed time, energy, first-time weapon acquisition, cube thresholds and supply rewards. It compares walking to spending boost, excludes invalid repeats and missed deadlines, and prices the time needed to get back to the safe ring. It executes only the first choice and reconsiders it after about half a match-second or sooner if the objective disappears. V3 keeps control of close encounters, retreat and emergency zone movement. This is a resource planner, not a full match simulator or a complete rewrite in Rust.

The compiler and Python images are pinned. Three Rust unit tests run during the build, and the runtime contains only the compiled library, Python and the controller. Running Python outside the image without the library uses V3's heuristic choice instead; use the image for V4 benchmarks.

| Held-out resource benchmark, 96 paired windows (seed 40321) | V3 | One-step Rust | Five-step Rust |
| --- | ---: | ---: | ---: |
| Mean cubes | 7.62 | 5.86 | 8.51 |
| Acquired a second weapon | 60 | 88 | 87 |
| Captured a supply | 46 | 66 | 51 |
| Reached six cubes | 81 | 53 | 89 |

These short, combat-free scenarios alternate scattered and clustered loot. They do not establish tournament placement. On 64 separate storm scenarios (seed 12109), mean storm damage was unchanged at 58.1 HP and both policies captured 62/64 uncontested supplies in the separate supply test. The goal is to enter later encounters better equipped; the official run tests whether that pays off.

To repeat the depth comparison, build this image as `oval-runner:v4`, set `KIT` to the kit and `BASE` to a checkout of commit `34bbb13`, then run from this folder:

```sh
docker build --platform linux/amd64 --network none -t oval-runner:v4 .
docker run --platform linux/amd64 --rm --network none --cpus 1 --memory 512m --read-only \
  -v "$KIT:/kit:ro" -v "$BASE:/baseline:ro" -v "$PWD/evaluate.py:/bot/evaluate.py:ro" \
  -v "$PWD/race.py:/bot/race.py:ro" -v "$PWD/economy.py:/bot/economy.py:ro" \
  --entrypoint python oval-runner:v4 -Bu economy.py --kit /kit --baseline /baseline --count 96 --seed 40321
```

## How it plays

The bot chooses between reaching the next circle, collecting a cube or second weapon, farming a crate or drone, holding a supply drop, dueling, and getting out of a losing engagement. It ranks only what the game reports and uses the same policy in practice and finals. A small preference for the existing goal prevents it from changing direction every observation.

At startup it rasterizes the arena terrain and solid prisms, pads obstacles, labels connected walkable regions and builds a ground movement graph. Weighted A* searches run in bounded chunks across decisions. A grid traversal checks shortcuts before the bot takes them. It reconsiders loot that remains out of reach. Movement and aim are independent; when recovering it can face an opponent without firing and interrupting regeneration.

The standard gun is always available, and the bot supports lightning and the charge/release behavior of the laser. This version intentionally passes up mortars. It does not plan roof-to-roof travel. The grid is an approximation for a ground-sized mech, not the server collision engine; tall cube stacks, awkward spawns and circles centered inside buildings are important live test cases.

## Reproduce the checks

From this repository root, set `KIT` to the extracted original starter kit:

```sh
KIT=/absolute/path/to/mechfight-kit
python3 -B evaluate.py --kit "$KIT" --seed 19087 --routes 256 --zones 64 --duels 48
docker build --platform linux/amd64 --network none -t oval-runner:local .
python3 "$KIT/check/check.py" --map-dir /arena -- docker run --platform linux/amd64 --rm -i \
  --network none --cpus 1 --memory 512m --memory-swap 512m --pids-limit 128 --cap-drop ALL \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$KIT/arena:/arena:ro" oval-runner:local
```

| Check | Observed result |
| --- | --- |
| Official recorded checker, Linux/amd64 Docker with match limits | 300/300 in time, no stale ticks or invalid lines. Ready in 551 ms; p99 1.7 ms; max 4.3 ms on the development machine. |
| Recovery and capture scenarios | 7/7 versus 3/7 for the scored baseline on the same scripted observations. These check decisions, not placement. |
| Policy scenarios | 12 passed; includes recovery, storm priority, final-circle holding, laser release, item reach and abandoning a stuck goal. |
| Navigation, seed 19087 | 256/256 reachable pairs on the map-derived grid, versus 39/256 for straight-line movement. |
| Zone re-entry, seed 19087 | The full policy entered 64/64 stationary test circles. Uses simplified movement, without enemies, health loss or a moving zone. |
| Flat gun-only duels, seed 19087 | 48 wins in 48 runs for Oval Runner; 0 wins for the kit starter against the same scripted opponent and starting conditions. |

Neither the checker nor these scenarios measure tournament placement. The navigation harness uses the same approximate grid as the planner. The duel harness includes approximate movement, game aim, spread, ammo and regeneration; it omits cover, real house-bot logic, special weapons, upgrades, and other players. Its role is to detect control mistakes, not estimate a real win rate.

## Practice comparison

Practice scores were 3.77 for `1d9e5cb`, 3.88 for `7f4965d`, 3.98 for `34bbb13` and 4.06 for V4 (`b845d48`).

## V3: routing to a region

The published next circle is a set of valid destinations. A reverse multi-source Dijkstra search starts from walkable cells along a buffered inner boundary in the mech's connected component. As the mech moves, it can reuse the cost-to-safety and downhill directions rather than repeatedly selecting an arbitrary geometric point. Each decision performs at most 550 queue pops. Until the field reaches the mech it falls back to the existing point-to-point planner. The policy forecasts the boundary at the estimated arrival time, allows short cube detours when time permits, and slows down near the final center rather than repeatedly overshooting it.

The same version looks farther ahead for announced supply drops when they are reachable before a rival. During the opening it favors clustered cubes and nearby crates when underpowered, healthy, and not under immediate pressure. No new weapons or map-specific locations are hard-coded.

### Offline comparison against 3.88

Check out `7f4965d` in another folder. Run `python3 -B race.py --kit "$KIT" --baseline /path/to/baseline --cases 96 --seed 61783`. Both policies receive the same starts, phase parameters, loot and terrain. This is a stress test for movement and resource decisions, not the house-bot simulator.

| Seed 61783, 96 paired scenarios per test | 3.88 policy | V3 |
| --- | ---: | ---: |
| Mean storm damage during one moving-circle phase | 335.6 | 149.7 |
| No storm damage | 73 | 87 |
| Cubes collected in the storm test | 484 | 409 |
| Uncontested supply captured within 8 s of earliest possible capture | 88 | 94 |
| First cube collected within 12 s with a distant, non-attacking observer and a nearby crate | 0 | 96 |
| Chooses a duel instead when the observer is replaced by a nearby opponent | 96 | 96 |

The moving-circle model includes acceleration, boost energy, collision with the navigation grid, and bait cubes placed around the start. It counts uncapped zone damage and omits combat, jets, roofs and regeneration. V3 trades some of those starting cubes for earlier safety. Supply races are uncontested; opening tests isolate one decision and do not model damage from the observer. Only an official run can establish a placement gain.

## Credits

`mechfight.py` is the unmodified Python protocol wrapper supplied by the Builders Mech Royale starter kit. The optional evaluator loads the kit's example bot for one comparator. All other bot and evaluation code was developed for this entry. There are no third-party Python packages.
