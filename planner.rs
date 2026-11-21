use std::cmp::Ordering;
use std::slice;

const MAX_SITES: usize = 14;
const WIDTH: usize = 32;
const DEPTH: usize = 5;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct Site {
    pub kind: i32,
    pub cubes: i32,
    pub open: f64,
    pub expires: f64,
    pub service: f64,
    pub return_time: f64,
    pub confidence: f64,
    pub continuity: f64,
}

#[repr(C)]
pub struct Config {
    pub walk: f64,
    pub boost: f64,
    pub energy: f64,
    pub capacity: f64,
    pub drain: f64,
    pub regen: f64,
    pub regen_delay: f64,
    pub health: f64,
    pub time_left: f64,
    pub horizon: f64,
    pub cubes: i32,
    pub cap: i32,
    pub secondary: i32,
    pub threshold_count: i32,
    pub thresholds: [i32; 16],
}

#[derive(Clone, Copy)]
struct State {
    last: usize,
    mask: u32,
    first: i32,
    sprint: bool,
    time: f64,
    energy: f64,
    cubes: i32,
    secondary: bool,
    health: f64,
    value: f64,
    rank: f64,
}

fn utility(before: i32, after: i32, cfg: &Config) -> f64 {
    let stats = after.min(cfg.cap) - before.min(cfg.cap);
    let offers = cfg.thresholds[..cfg.threshold_count.clamp(0,16) as usize].iter()
        .filter(|&&threshold| before < threshold && after >= threshold).count();
    9.0 * f64::from(stats) + 11.0 * offers as f64 - 0.8 * f64::from(after-before-stats)
}

fn extend(cfg: &Config, state: State, dest: usize, site: Site, leg: f64, sprint: bool) -> Option<State> {
    if state.mask & (1 << dest) != 0 || (site.kind == 2 && state.secondary) || leg < 0.0
        || (site.kind==4 && state.first<0) {
        return None;
    }
    let fast = if sprint {
        let seconds = ((state.energy - 0.34*cfg.capacity)/cfg.drain.max(0.1)).max(0.0);
        (leg/cfg.boost).min(seconds)
    } else { 0.0 };
    if sprint && fast < 0.35 { return None; }
    let slow = (leg-fast*cfg.boost).max(0.0)/cfg.walk;
    let arrival = state.time+fast+slow;
    let time = arrival.max(site.open)+site.service;
    if time > cfg.horizon || time > site.expires || time+site.return_time > cfg.time_left {
        return None;
    }
    let idle = time-state.time-fast;
    let energy = (state.energy-fast*cfg.drain+(idle-cfg.regen_delay).max(0.0)*cfg.regen).clamp(0.0,cfg.capacity);
    let cubes = state.cubes+site.cubes;
    let mut reward = utility(state.cubes,cubes,cfg);
    if site.kind == 2 { reward += 26.0; }
    if site.kind == 1 && !state.secondary { reward += 3.0; }
    if site.kind >= 3 { reward += (1.0-state.health)*68.0+(1.0-energy/cfg.capacity)*7.0; }
    let value = state.value + site.confidence * reward / (1.0+time*0.095)
        + if state.first < 0 { site.continuity } else { 0.0 };
    let final_energy = if site.kind>=3 { cfg.capacity } else { energy };
    let rank = value-0.64*time-3.2*(1.0-final_energy/cfg.capacity);
    Some(State {
        last:dest+1, mask:state.mask | (1<<dest),
        first:if state.first<0 { dest as i32 } else { state.first },
        sprint:if state.first<0 { sprint } else { state.sprint },
        time, energy:final_energy, cubes, secondary:state.secondary || site.kind==2,
        health:if site.kind>=3 { 1.0 } else { state.health }, value, rank
    })
}

fn order(left: &State, right: &State) -> Ordering {
    right.rank.total_cmp(&left.rank).then_with(|| left.time.total_cmp(&right.time))
        .then_with(|| left.first.cmp(&right.first)).then_with(|| left.mask.cmp(&right.mask))
}

fn solve(cfg: &Config, sites: &[Site], legs: &[f64], depth: usize) -> i32 {
    let root = State { last:0,mask:0,first:-1,sprint:false,time:0.0,energy:cfg.energy,
        cubes:cfg.cubes,secondary:cfg.secondary!=0,health:cfg.health,value:0.0,rank:0.0 };
    let stride = sites.len()+1;
    let mut best = root;
    let mut beam = Vec::with_capacity(WIDTH);
    beam.push(root);
    let mut next = Vec::with_capacity(WIDTH*sites.len()*2);
    for _ in 0..depth {
        next.clear();
        for &state in &beam {
            for (index, &site) in sites.iter().enumerate() {
                let leg = legs[state.last*stride+index+1];
                for sprint in [false,true] {
                    if let Some(child) = extend(cfg,state,index,site,leg,sprint) {
                        if order(&child,&best)==Ordering::Less { best=child; }
                        next.push(child);
                    }
                }
            }
        }
        if next.is_empty() { break; }
        next.sort_unstable_by(order);
        beam.clear();
        for &child in &next {
            if beam.iter().any(|prior| prior.last==child.last && prior.mask==child.mask
                && prior.time<=child.time+0.12 && prior.energy>=child.energy-2.0 && prior.value>=child.value-0.1) {
                continue;
            }
            beam.push(child);
            if beam.len()==WIDTH { break; }
        }
    }
    if best.first < 0 { -1 } else { 2*best.first + i32::from(best.sprint) }
}

#[no_mangle]
pub unsafe extern "C" fn oval_plan(cfg: *const Config, sites: *const Site, legs: *const f64, count: usize, depth: usize) -> i32 {
    if cfg.is_null() || sites.is_null() || legs.is_null() || count==0 || count>MAX_SITES { return -1; }
    let config = &*cfg;
    if !(config.walk>0.0 && config.boost>=config.walk && config.capacity>0.0) { return -1; }
    solve(config,slice::from_raw_parts(sites,count),slice::from_raw_parts(legs,(count+1)*(count+1)),depth.clamp(1,DEPTH))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> Config {
        Config { walk:10.0,boost:15.0,energy:0.0,capacity:100.0,drain:15.0,regen:0.0,regen_delay:0.6,
            health:0.5,time_left:40.0,horizon:12.0,cubes:0,cap:10,secondary:0,threshold_count:3,
            thresholds:[1,3,6,0,0,0,0,0,0,0,0,0,0,0,0,0] }
    }
    fn cube() -> Site { Site {kind:0,cubes:1,expires:20.0,confidence:1.0,..Site::default()} }
    #[test]
    fn deeper_route_changes_first_pick() {
        let sites = [cube(),cube(),cube(),cube()];
        let legs = [0.,12.,25.,27.,28.,12.,0.,37.,39.,40.,25.,37.,0.,2.,3.,
                    27.,39.,2.,0.,1.,28.,40.,3.,1.,0.];
        let mut cfg=config(); cfg.horizon=4.0;
        assert_eq!(solve(&cfg,&sites,&legs,1)/2,0);
        assert_ne!(solve(&cfg,&sites,&legs,5)/2,0);
    }
    #[test]
    fn rejects_unclaimable_or_unsafe_stops() {
        let mut site=cube(); site.open=4.; site.service=4.; site.expires=6.;
        assert_eq!(solve(&config(),&[site],&[0.,10.,10.,0.],5),-1);
        site.expires=12.; site.return_time=35.;
        assert_eq!(solve(&config(),&[site],&[0.,10.,10.,0.],5),-1);
    }
    #[test]
    fn accounts_for_stat_cap_and_unique_gear() {
        let mut cfg=config(); cfg.cubes=10; cfg.secondary=1;
        let gear=Site {kind:2,cubes:0,..cube()};
        assert_eq!(solve(&cfg,&[gear,cube()],&[0.;9],5),-1);
    }
}
