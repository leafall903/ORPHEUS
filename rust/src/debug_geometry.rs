//! A bounded view of the collision already prepared for a match.

use std::sync::Arc;

use serde::Serialize;

use crate::{
    collision_scene::CollisionScene,
    contact_mesh::ContactBounds,
    contact_world::{ContactWorld, StaticGeometry},
    game_content::GameMap,
    static_ray::Vec3,
};

const HALF_EXTENTS: Vec3 = [24.0, 16.0, 24.0];
const COORDINATE_LIMIT: f64 = 65_536.0;
const TRIANGLE_LIMIT: usize = 4096;
const PROXY_LIMIT: usize = 256;

pub(crate) struct GeometryRegion {
    center: Vec3,
    half_extent: f64,
}

impl GeometryRegion {
    pub(crate) fn new(center: Vec3, half_extent: f64) -> Option<Self> {
        (center
            .into_iter()
            .all(|axis| (-COORDINATE_LIMIT..=COORDINATE_LIMIT).contains(&axis))
            && (1.0..=16.0).contains(&half_extent))
        .then_some(Self {
            center,
            half_extent,
        })
    }
}

/// A cheap handle. Sample and encode it outside the match loop.
#[derive(Clone)]
pub(crate) struct DebugGeometry {
    map: u64,
    mode: u64,
    movement_cache: String,
    spawn: Option<Vec3>,
    category_mask: u16,
    ground: Arc<CollisionScene>,
    contacts: Option<Arc<ContactWorld>>,
}

impl DebugGeometry {
    pub(crate) fn new(
        map: &GameMap,
        movement_cache: String,
        category_mask: u16,
        ground: Arc<CollisionScene>,
        contacts: Option<Arc<ContactWorld>>,
    ) -> Self {
        Self {
            map: map.map_guid,
            mode: map.mode_guid,
            movement_cache,
            spawn: map
                .spawn_point(0, 0)
                .map(|(position, _)| position)
                .filter(|position| position.iter().all(|axis| axis.is_finite())),
            category_mask,
            ground,
            contacts,
        }
    }

    pub(crate) fn view(&self) -> DebugGeometryView {
        let (origin, source) = self.spawn.map_or(([0.0; 3], "world-origin"), |spawn| {
            (spawn, "first-player-spawn")
        });
        self.sample(origin, HALF_EXTENTS, source)
    }

    pub(crate) fn region(&self, region: GeometryRegion) -> DebugGeometryView {
        self.sample(region.center, [region.half_extent; 3], "requested-center")
    }

    fn sample(
        &self,
        origin: Vec3,
        half_extents: Vec3,
        origin_source: &'static str,
    ) -> DebugGeometryView {
        let min = std::array::from_fn(|axis| origin[axis] - half_extents[axis]);
        let max = std::array::from_fn(|axis| origin[axis] + half_extents[axis]);
        let ground = self.ground.triangles();
        let triangle_sample =
            ground.nearby_limited(min, max, self.category_mask, TRIANGLE_LIMIT + 1);
        let triangles_truncated = triangle_sample.len() > TRIANGLE_LIMIT;
        let triangles: Vec<_> = triangle_sample
            .into_iter()
            .take(TRIANGLE_LIMIT)
            .map(|triangle| DebugTriangle {
                vertices: triangle.vertices,
                mask: triangle.mask,
                surface: triangle.surface,
            })
            .collect();
        let mut proxies = self.contact_proxies(min, max);
        let proxies_truncated = proxies.len() > PROXY_LIMIT;
        proxies.truncate(PROXY_LIMIT);

        DebugGeometryView {
            map: format!("{:016X}", self.map),
            mode: format!("{:016X}", self.mode),
            origin,
            origin_source,
            category_mask: self.category_mask,
            bounds: DebugBounds { min, max },
            sources: DebugSources {
                movement_cache: self.movement_cache.clone(),
                triangles: "movement-cache-probe-and-hitscan",
                proxies: self
                    .contacts
                    .as_ref()
                    .map(|_| "movement-cache-character-contact-bounds"),
            },
            counts: DebugLayers {
                triangles: triangles.len(),
                proxies: proxies.len(),
            },
            totals: DebugLayers {
                triangles: ground.triangle_count(),
                proxies: self
                    .contacts
                    .as_ref()
                    .map_or(0, |world| world.shapes().len()),
            },
            limits: DebugLayers {
                triangles: TRIANGLE_LIMIT,
                proxies: PROXY_LIMIT,
            },
            truncated: DebugLayers {
                triangles: triangles_truncated,
                proxies: proxies_truncated,
            },
            triangles,
            proxies,
        }
    }

    fn contact_proxies(&self, min: Vec3, max: Vec3) -> Vec<DebugProxy> {
        let Some(world) = &self.contacts else {
            return Vec::new();
        };
        let bounds = ContactBounds {
            low: min.map(|axis| axis as f32),
            high: max.map(|axis| axis as f32),
        };
        world
            .query(bounds)
            .into_iter()
            .filter_map(|id| {
                let shape = &world.shapes()[id as usize];
                let geometry = shape.geometry();
                let mask = geometry.mask();
                if mask & self.category_mask == 0 {
                    return None;
                }
                let bounds = shape.fat_bounds()?;
                let kind = match geometry {
                    StaticGeometry::Mesh(_) => "mesh",
                    StaticGeometry::Hull(_) => "hull",
                    StaticGeometry::Round { .. } => "round",
                };
                Some(DebugProxy {
                    id,
                    min: bounds.low,
                    max: bounds.high,
                    kind,
                    mask,
                })
            })
            .take(PROXY_LIMIT + 1)
            .collect()
    }
}

#[derive(Serialize)]
pub(crate) struct DebugGeometryView {
    map: String,
    mode: String,
    origin: Vec3,
    origin_source: &'static str,
    category_mask: u16,
    bounds: DebugBounds,
    sources: DebugSources,
    counts: DebugLayers<usize>,
    totals: DebugLayers<usize>,
    limits: DebugLayers<usize>,
    truncated: DebugLayers<bool>,
    triangles: Vec<DebugTriangle>,
    proxies: Vec<DebugProxy>,
}

#[derive(Serialize)]
struct DebugBounds {
    min: Vec3,
    max: Vec3,
}

#[derive(Serialize)]
struct DebugSources {
    movement_cache: String,
    triangles: &'static str,
    proxies: Option<&'static str>,
}

#[derive(Serialize)]
struct DebugLayers<T> {
    triangles: T,
    proxies: T,
}

#[derive(Serialize)]
struct DebugTriangle {
    vertices: [Vec3; 3],
    mask: u16,
    surface: u16,
}

#[derive(Serialize)]
struct DebugProxy {
    id: u32,
    min: [f32; 3],
    max: [f32; 3],
    kind: &'static str,
    mask: u16,
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use serde_json::json;

    use super::*;
    use crate::collision_cache::{CollisionCache, Mesh, Placement, Shape, Triangle};

    const TEAM0: u16 = 0x08;
    const TEAM1: u16 = 0x10;

    fn wall(mask: u16, copies: usize) -> Mesh {
        Mesh {
            vertices: vec![[0.0; 3], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            triangles: vec![
                Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask,
                    surface: 7,
                };
                copies
            ],
        }
    }

    fn place(group: u16, x: f32) -> Placement {
        Placement {
            model: 0,
            group,
            translation: [x, 1.0, 42.0],
            scale: [2.0, 1.0, 1.0],
            rotation: [0.0, 1.0, 0.0, 0.0],
        }
    }

    #[test]
    fn view_uses_selected_placed_triangles_and_solver_proxy_bounds() {
        let map = GameMap::practice_range();
        let cache = CollisionCache {
            header: serde_json::from_value(json!({
                "meshes": [[3, 1]], "hulls": 0, "models": 1, "placements": 3,
                "identifiers": ["AB", "BC"],
            }))
            .unwrap(),
            meshes: vec![wall(TEAM0, 1)],
            hulls: vec![],
            models: vec![vec![Shape::Mesh {
                mask: TEAM1,
                mesh: wall(TEAM1, 1),
            }]],
            placements: vec![
                place(1, 55.0),
                place(2, 58.0),
                Placement {
                    translation: [200.0, 18.0, 42.0],
                    ..place(1, 200.0)
                },
            ],
        };
        let identifiers = BTreeSet::from([0xab]);
        let ground = Arc::new(CollisionScene::movement_probe(&cache, &identifiers).unwrap());
        let contacts = Arc::new(ContactWorld::from_movement_cache(cache, &identifiers).unwrap());
        let expected = contacts.shapes()[1].fat_bounds().unwrap();

        let geometry = DebugGeometry::new(
            &map,
            "0688.col".into(),
            TEAM0 | TEAM1,
            ground,
            Some(contacts),
        );
        let view = geometry.view();
        assert_eq!(view.map, "0800000000000688");
        assert_eq!(view.origin, map.spawn_point(0, 0).unwrap().0);
        assert_eq!(view.origin_source, "first-player-spawn");
        assert_eq!(view.sources.movement_cache, "0688.col");
        assert_eq!(view.sources.triangles, "movement-cache-probe-and-hitscan");
        assert_eq!(
            view.sources.proxies,
            Some("movement-cache-character-contact-bounds")
        );
        assert_eq!(view.totals.triangles, 3);
        assert_eq!(view.totals.proxies, 3);
        assert_eq!(view.counts.triangles, 1);
        assert_eq!(view.counts.proxies, 1);
        assert_eq!(
            view.triangles[0].vertices,
            [[55.0, 1.0, 42.0], [53.0, 1.0, 42.0], [55.0, 2.0, 42.0]]
        );
        assert_eq!(view.triangles[0].mask, TEAM1);
        assert_eq!(view.triangles[0].surface, 0);
        assert_eq!(view.proxies[0].id, 1);
        assert_eq!(view.proxies[0].kind, "mesh");
        assert_eq!(view.proxies[0].mask, TEAM1);
        assert_eq!(view.proxies[0].min, expected.low);
        assert_eq!(view.proxies[0].max, expected.high);

        let center = [200.0, 18.0, 42.0];
        let view = geometry.region(GeometryRegion::new(center, 2.0).unwrap());
        assert_eq!(view.origin, center);
        assert_eq!(view.origin_source, "requested-center");
        assert_eq!(view.bounds.min, [198.0, 16.0, 40.0]);
        assert_eq!(view.bounds.max, [202.0, 20.0, 44.0]);
        assert_eq!(view.counts.triangles, 1);
        assert_eq!(view.triangles[0].vertices[0], center);
        assert_eq!(view.counts.proxies, 1);
        assert_eq!(view.proxies[0].id, 2);
    }

    #[test]
    fn view_marks_truncated_triangles_and_proxies() {
        let mut map = GameMap::practice_range();
        map.spawns.clear();
        let copies = TRIANGLE_LIMIT + 1;
        let placements = PROXY_LIMIT + 1;
        let cache = CollisionCache {
            header: serde_json::from_value(json!({
                "meshes": [[3, copies]], "hulls": 0, "models": 1,
                "placements": placements, "identifiers": [],
            }))
            .unwrap(),
            meshes: vec![wall(TEAM0, copies)],
            hulls: vec![],
            models: vec![vec![Shape::Mesh {
                mask: TEAM1,
                mesh: wall(TEAM1, 1),
            }]],
            placements: vec![
                Placement {
                    translation: [0.0; 3],
                    ..place(0, 0.0)
                };
                placements
            ],
        };
        let groups = BTreeSet::new();
        let ground = Arc::new(CollisionScene::movement_probe(&cache, &groups).unwrap());
        let contacts = Arc::new(ContactWorld::from_movement_cache(cache, &groups).unwrap());
        let geometry = DebugGeometry::new(
            &map,
            "0688.col".into(),
            TEAM0 | TEAM1,
            ground,
            Some(contacts),
        );
        let view = geometry.view();

        assert_eq!(view.origin, [0.0; 3]);
        assert_eq!(view.origin_source, "world-origin");
        assert_eq!(view.counts.triangles, TRIANGLE_LIMIT);
        assert_eq!(view.counts.proxies, PROXY_LIMIT);
        assert!(view.truncated.triangles);
        assert!(view.truncated.proxies);
        assert_eq!(view.proxies[0].id, 0);
        assert_eq!(view.proxies[PROXY_LIMIT - 1].id, PROXY_LIMIT as u32 - 1);

        let view = geometry.region(GeometryRegion::new([0.0; 3], 2.0).unwrap());
        assert_eq!(view.counts.triangles, TRIANGLE_LIMIT);
        assert_eq!(view.counts.proxies, PROXY_LIMIT);
        assert!(view.truncated.triangles && view.truncated.proxies);
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range pack"]
    fn prepared_practice_geometry_samples_the_highest_patrol_waypoint() {
        let assets = crate::scene_assets::SceneAssets::load(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap(),
        )
        .unwrap();
        let scene = assets.prepare(GameMap::practice_range()).unwrap();
        let center = scene
            .bots()
            .unwrap()
            .flat_map(|bot| bot.spawn.route.iter().copied())
            .max_by(|a, b| a[1].total_cmp(&b[1]))
            .expect("the prepared scene has no authored patrol route");

        let geometry = scene.debug_geometry();
        let view = geometry.region(GeometryRegion::new(center, 4.0).unwrap());
        assert_eq!(view.origin, center);
        assert_eq!(view.sources.movement_cache, "0688.col");
        assert!(view.counts.triangles > 0 && !view.truncated.triangles);
        assert!(view.counts.proxies > 0 && !view.truncated.proxies);
    }
}
