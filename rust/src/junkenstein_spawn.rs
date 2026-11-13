//! Bounded collision sampling for the authored encounter spawn searches.

use std::collections::{BTreeSet, VecDeque};

use crate::{
    collision_scene::CollisionScene,
    mover_ground::NOT_GROUND,
    mover_settings::MoverSettings,
    script_init::{Component, InitFault, SpawnSearch, SpawnSearchKey},
};

const CATEGORY: u16 = 0x18;
const STEP_UP: f64 = 0.6;
const STEP: f64 = 0.25;
const STEP_BUDGET: usize = 320;

#[derive(Default)]
pub(crate) struct SpawnSearches {
    pending: VecDeque<(SpawnSearchKey, Job)>,
    finished: BTreeSet<SpawnSearchKey>,
}

struct Job {
    request: SpawnSearch,
    start: Option<[f64; 3]>,
    next: usize,
    positions: Vec<[f64; 3]>,
}

impl SpawnSearches {
    pub(crate) fn step(
        &mut self,
        frame: u32,
        script: &mut Component,
        collision: &CollisionScene,
        mover: &MoverSettings,
    ) -> Result<(), InitFault> {
        let requests: Vec<_> = script.spawn_searches().collect();
        let active: BTreeSet<_> = requests.iter().map(|&(key, _)| key).collect();
        self.pending.retain(|(key, _)| active.contains(key));
        self.finished.retain(|key| active.contains(key));
        let mut tracked: BTreeSet<_> = self.pending.iter().map(|(key, _)| *key).collect();
        tracked.extend(&self.finished);
        for (key, request) in requests {
            if tracked.insert(key) {
                self.pending.push_back((
                    key,
                    Job {
                        request,
                        start: None,
                        next: 0,
                        positions: Vec::new(),
                    },
                ));
            }
        }

        let mut budget = STEP_BUDGET;
        while let Some((key, mut job)) = self.pending.pop_front() {
            let cost = (job.request.radius / STEP).ceil() as usize;
            if cost > budget {
                self.pending.push_front((key, job));
                break;
            }
            budget -= cost;
            if job.next == 0 {
                let [x, y, z] = job.request.position;
                job.start = walkable(collision, mover, x, z, y + STEP_UP, y - mover.step_down)
                    .map(|floor| [x, floor, z]);
            }
            if job.next == job.request.maximum || job.start.is_none() {
                self.finished.insert(key);
                if job.positions.len() >= job.request.minimum {
                    script.queue_spawn_result(frame, key, job.positions)?;
                }
                continue;
            }
            if let Some(position) = job.probe(collision, mover) {
                job.positions.push(position);
            }
            job.next += 1;
            self.pending.push_back((key, job));
        }
        Ok(())
    }
}

impl Job {
    fn probe(&self, collision: &CollisionScene, mover: &MoverSettings) -> Option<[f64; 3]> {
        let start = self.start?;
        let [dx, _, dz] = self.request.direction;
        let golden_turn = (5.0_f64.sqrt() - 1.0) / 2.0;
        let radial =
            self.request.radius * ((self.next as f64 + 0.5) / self.request.maximum as f64).sqrt();
        let angle = dz.atan2(dx)
            + self.request.half_angle * (2.0 * (self.next as f64 * golden_turn).fract() - 1.0);
        let target_x = start[0] + radial * angle.cos();
        let target_z = start[2] + radial * angle.sin();
        walk_edge(collision, mover, start, target_x, target_z)
    }
}

pub(crate) fn walk_edge(
    collision: &CollisionScene,
    mover: &MoverSettings,
    start: [f64; 3],
    target_x: f64,
    target_z: f64,
) -> Option<[f64; 3]> {
    let distance = (target_x - start[0]).hypot(target_z - start[2]);
    let count = (distance / STEP).ceil().max(1.0) as usize;
    let mut previous = start;
    let radius = mover.stand_radius;
    let top = mover.stand_height - radius;
    for step in 1..=count {
        let fraction = step as f64 / count as f64;
        let x = start[0] + (target_x - start[0]) * fraction;
        let z = start[2] + (target_z - start[2]) * fraction;
        let y = walkable(
            collision,
            mover,
            x,
            z,
            previous[1] + STEP_UP + 0.05,
            previous[1] - mover.step_down - 0.05,
        )?;
        if top > radius && blocked(collision, [x, y + radius, z], [0.0, 1.0, 0.0], top - radius) {
            return None;
        }
        let dx = x - previous[0];
        let dz = z - previous[2];
        let length = dx.hypot(dz);
        if length > 0.0 {
            let direction = [dx / length, 0.0, dz / length];
            let side = [-direction[2] * radius, direction[0] * radius];
            for offset in [-1.0, 0.0, 1.0] {
                for height in [radius + 0.01, top] {
                    let origin = [
                        previous[0] + side[0] * offset,
                        previous[1].max(y) + height,
                        previous[2] + side[1] * offset,
                    ];
                    if blocked(collision, origin, direction, length) {
                        return None;
                    }
                }
            }
        }
        previous = [x, y, z];
    }
    Some(previous)
}

fn walkable(
    collision: &CollisionScene,
    mover: &MoverSettings,
    x: f64,
    z: f64,
    top: f64,
    bottom: f64,
) -> Option<f64> {
    collision
        .triangles()
        .ground(x, z, top, bottom, CATEGORY)
        .filter(|hit| {
            hit.surface & NOT_GROUND == 0 && hit.normal[1] >= mover.max_slope.to_radians().cos()
        })
        .map(|hit| hit.y)
}

fn blocked(collision: &CollisionScene, origin: [f64; 3], direction: [f64; 3], length: f64) -> bool {
    !matches!(
        collision.cast_ray(origin, direction, length, CATEGORY),
        Ok(None)
    )
}
