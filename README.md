# Oval Runner

A standard-library Python entry for the Builders Mech Royale track. The Dockerfile belongs at the repository root. The server supplies the arena at runtime; no map, credentials, training set, or network access is bundled or needed.

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
| Official recorded checker, Linux/amd64 Docker with match limits | 300/300 in time, no stale ticks or invalid lines. Ready in 554 ms; p99 1.2 ms; max 3.3 ms on the development machine. |
| Policy scenarios | 12 passed; includes recovery, storm priority, final-circle holding, laser release, item reach and abandoning a stuck goal. |
| Navigation, seed 19087 | 256/256 reachable pairs on the map-derived grid, versus 39/256 for straight-line movement. |
| Zone re-entry, seed 19087 | The full policy entered 64/64 stationary test circles. Uses simplified movement, without enemies, health loss or a moving zone. |
| Flat gun-only duels, seed 19087 | 48 wins in 48 runs for Oval Runner; 0 wins for the kit starter against the same scripted opponent and starting conditions. |

Neither the checker nor these scenarios measure tournament placement. The navigation harness uses the same approximate grid as the planner. The duel harness includes approximate movement, game aim, spread, ammo and regeneration; it omits cover, real house-bot logic, special weapons, upgrades, and other players. Its role is to detect control mistakes, not estimate a real win rate.

## First official practice run

Push this folder's **contents** to a public GitHub repository and submit its URL on the Mech Royale track page. Keep the scored commit available. Start by recording its mean placement points as the baseline. For the next version, change one thing based on the most frequent observable failure: routing for missed circles, recovery thresholds for prolonged losing duels, or item valuation for weak late-game starts. The kit's local replay cannot supply this feedback.

## Credits

`mechfight.py` is the unmodified Python protocol wrapper supplied by the Builders Mech Royale starter kit. The optional evaluator loads the kit's example bot for one comparator. All other bot and evaluation code was developed for this entry. There are no third-party Python packages.
