//! Bounded collision sampling for the authored encounter spawn searches.

use std::collections::{BTreeSet, VecDeque};

use crate::{
    collision_scene::CollisionScene,
    mover_settings::MoverSettings,
    navigation::{self, DEFAULT_NAV_CATEGORY},
    script_init::{Component, InitFault, SpawnSearch, SpawnSearchKey},
};

const CATEGORY: u16 = DEFAULT_NAV_CATEGORY;
const STEP_UP: f64 = 0.6;
const STEP: f64 = 0.25;
const STEP_BUDGET: usize = 320;

#[derive(Default)]
pub(crate) struct SpawnSearches {
    pending: VecDeque<(SpawnSearchKey, Job)>,
    finished: BTreeSet<SpawnSearchKey>,
    #[cfg(any(test, feature = "inspector"))]
    recent: VecDeque<crate::game_debug::DebugSpawnCandidate>,
    #[cfg(any(test, feature = "inspector"))]
    truncated: bool,
}

struct Job {
    request: SpawnSearch,
    start: Option<[f64; 3]>,
    next: usize,
    positions: Vec<[f64; 3]>,
}

impl SpawnSearches {
    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_candidates(
        &self,
    ) -> (&VecDeque<crate::game_debug::DebugSpawnCandidate>, bool) {
        (&self.recent, self.truncated)
    }

    #[cfg(any(test, feature = "inspector"))]
    fn record_candidate(&mut self, entry: crate::game_debug::DebugSpawnCandidate) {
        if self.recent.len() == crate::game_debug::MAX_DEBUG_SPAWN_CANDIDATES {
            self.recent.pop_front();
            self.truncated = true;
        }
        self.recent.push_back(entry);
    }

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
                #[cfg(any(test, feature = "inspector"))]
                if job.start.is_none() {
                    self.record_candidate(crate::game_debug::DebugSpawnCandidate {
                        tick: frame,
                        instance: key.instance(),
                        state: key.raw(),
                        origin: job.request.position,
                        candidate: job.request.position,
                        accepted: false,
                        landed: None,
                    });
                }
            }
            if job.next == job.request.maximum || job.start.is_none() {
                self.finished.insert(key);
                if job.positions.len() >= job.request.minimum {
                    script.queue_spawn_result(frame, key, job.positions)?;
                }
                continue;
            }
            if let Some(probe) = job.probe_candidate(collision, mover) {
                if let Some(position) = probe.landed {
                    job.positions.push(position);
                }
                #[cfg(any(test, feature = "inspector"))]
                self.record_candidate(crate::game_debug::DebugSpawnCandidate {
                    tick: frame,
                    instance: key.instance(),
                    state: key.raw(),
                    origin: probe.origin,
                    candidate: probe.candidate,
                    accepted: probe.landed.is_some(),
                    landed: probe.landed,
                });
            }
            job.next += 1;
            self.pending.push_back((key, job));
        }
        Ok(())
    }
}

struct SpawnProbe {
    #[cfg(any(test, feature = "inspector"))]
    origin: [f64; 3],
    #[cfg(any(test, feature = "inspector"))]
    candidate: [f64; 3],
    landed: Option<[f64; 3]>,
}

impl Job {
    fn probe_candidate(
        &self,
        collision: &CollisionScene,
        mover: &MoverSettings,
    ) -> Option<SpawnProbe> {
        let start = self.start?;
        let [dx, _, dz] = self.request.direction;
        let golden_turn = (5.0_f64.sqrt() - 1.0) / 2.0;
        let radial =
            self.request.radius * ((self.next as f64 + 0.5) / self.request.maximum as f64).sqrt();
        let angle = dz.atan2(dx)
            + self.request.half_angle * (2.0 * (self.next as f64 * golden_turn).fract() - 1.0);
        let target_x = start[0] + radial * angle.cos();
        let target_z = start[2] + radial * angle.sin();
        let landed = walk_edge(collision, mover, start, target_x, target_z);
        Some(SpawnProbe {
            #[cfg(any(test, feature = "inspector"))]
            origin: start,
            #[cfg(any(test, feature = "inspector"))]
            candidate: [target_x, start[1], target_z],
            landed,
        })
    }
}

pub(crate) fn walk_edge(
    collision: &CollisionScene,
    mover: &MoverSettings,
    start: [f64; 3],
    target_x: f64,
    target_z: f64,
) -> Option<[f64; 3]> {
    let agent = mover.nav_agent(CATEGORY);
    navigation::walk_edge(collision, None, &agent, start, target_x, target_z).landed()
}

fn walkable(
    collision: &CollisionScene,
    mover: &MoverSettings,
    x: f64,
    z: f64,
    top: f64,
    bottom: f64,
) -> Option<f64> {
    let agent = mover.nav_agent(CATEGORY);
    navigation::walkable_ground(collision, &agent, x, z, top, bottom)
        .walkable_hit()
        .map(|hit| hit.y)
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use serde_json::json;

    use super::*;
    use crate::{
        collision_cache::{CollisionCache, Mesh, Triangle},
        script_graph::GraphStore,
        script_init::{EmptyWorld, Root},
        script_wire::Variables,
    };

    fn floor_scene() -> CollisionScene {
        // Walkable floor on +Z (x in [-4, 4], z in [-1, 8]), missing elsewhere so
        // candidates accept in +Z and reject outside or across the gap.
        let floor = Mesh {
            vertices: vec![
                [-2.0, 0.0, -1.0],
                [-2.0, 0.0, 8.0],
                [2.0, 0.0, 8.0],
                [2.0, 0.0, -1.0],
            ],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: CATEGORY,
                    surface: 0,
                },
                Triangle {
                    corners: [2, 3, 0],
                    wings: [-1; 3],
                    mask: CATEGORY,
                    surface: 0,
                },
            ],
        };
        let cache = CollisionCache {
            header: serde_json::from_value(json!({
                "meshes": [[4, 2]], "hulls": 0, "models": 0, "placements": 0, "identifiers": [],
            }))
            .unwrap(),
            meshes: vec![floor],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        CollisionScene::movement_probe(&cache, &BTreeSet::new()).unwrap()
    }

    fn spawn_search_graph(origin: [f64; 3], max_samples: i64) -> Arc<GraphStore> {
        let payload = serde_json::to_vec(&json!({
            "version": 1,
            "graphs": [{
                "index": 0x0cba,
                "items": 2,
                "nodes": [
                    {
                        "pos": 0,
                        "class": "STUStatescriptEntry",
                        "uid": 0,
                        "rid": 0,
                        "remote": -1,
                        "state": null,
                        "client_only": false,
                        "server_only": false,
                        "fields": {
                            "m_2BBEEAB8": false,
                            "m_ADEB6E05": false,
                            "m_outPlug": {
                                "$": "Plug",
                                "links": [[1, "m_beginPlug"]],
                                "m_FF3DAF1E": [0]
                            }
                        }
                    },
                    {
                        "pos": 1,
                        "class": "STU_BA2FCDEB",
                        "uid": 1,
                        "rid": 1,
                        "remote": -1,
                        "state": 0,
                        "client_only": false,
                        "server_only": false,
                        "fields": {
                            "m_2BBEEAB8": true,
                            "m_ADEB6E05": false,
                            "m_8FEA3023": {"$": "STUConfigVarDynamic", "m_identifier": 9000, "m_60DB8F99": false},
                            "m_position": {"$": "STUConfigVarVec3", "m_value": {"X": origin[0], "Y": origin[1], "Z": origin[2]}},
                            "m_ADC02DA1": {"$": "STUConfigVarVec3", "m_value": {"X": 0.0, "Y": 0.0, "Z": 1.0}},
                            "m_radius": {"$": "STUConfigVarFloat", "m_value": 5.0},
                            "m_A30B383E": {"$": "STUConfigVarFloat", "m_value": 360.0},
                            "m_EC1B83C7": {"$": "STUConfigVarInt", "m_value": 1},
                            "m_7084DA43": {"$": "STUConfigVarInt", "m_value": max_samples},
                            "m_F649CC6C": {"$": "STUConfigVarFloat", "m_value": 0.5}
                        }
                    }
                ],
                "states": [1],
                "entries": [0],
                "remote": [],
                "sync_vars": [],
                "containers": {},
                "unknown_nodes": [],
                "fields": {},
                "header": {
                    "m_nodesBitCount": 4,
                    "m_statesBitCount": 4,
                    "m_remoteSyncNodesBitCount": 4,
                    "m_syncVarsBitCount": 4
                }
            }],
            "bodies": {},
            "missing": []
        }))
        .unwrap();
        Arc::new(GraphStore::from_json(&payload).unwrap())
    }

    #[test]
    fn records_accepted_rejected_and_capped_spawn_candidates() {
        let collision = floor_scene();
        let mover = MoverSettings::default();
        let mut world = EmptyWorld;
        let graphs = spawn_search_graph([0.0, 0.0, 0.0], 70);
        let mut component = Component::start(
            graphs,
            1,
            Variables::new(),
            vec![Root::new(1, 0x0cba)],
            0,
            &mut world,
            10_000,
        )
        .unwrap();
        let mut searches = SpawnSearches::default();
        for frame in 1..=6 {
            searches
                .step(frame, &mut component, &collision, &mover)
                .unwrap();
        }
        let (candidates, truncated) = searches.debug_candidates();
        assert!(truncated);
        assert_eq!(
            candidates.len(),
            crate::game_debug::MAX_DEBUG_SPAWN_CANDIDATES
        );
        assert!(
            candidates
                .iter()
                .any(|entry| entry.accepted && entry.landed.is_some())
        );
        assert!(
            candidates
                .iter()
                .any(|entry| !entry.accepted && entry.landed.is_none())
        );

        // Missing walkable start origin records an immediate rejected entry.
        let bad_graphs = spawn_search_graph([50.0, 0.0, 50.0], 4);
        let mut bad_component = Component::start(
            bad_graphs,
            2,
            Variables::new(),
            vec![Root::new(1, 0x0cba)],
            0,
            &mut world,
            10_000,
        )
        .unwrap();
        let mut bad_searches = SpawnSearches::default();
        bad_searches
            .step(1, &mut bad_component, &collision, &mover)
            .unwrap();
        let (bad_candidates, bad_truncated) = bad_searches.debug_candidates();
        assert!(!bad_truncated);
        assert_eq!(bad_candidates.len(), 1);
        assert!(!bad_candidates[0].accepted);
        assert_eq!(bad_candidates[0].origin, [50.0, 0.0, 50.0]);
    }
}
