//! Room layers built from the same placed models the objective uses.

use std::{collections::BTreeMap, io, sync::Arc};

use orpheus_collision::{
    collision_cache::{HalfEdge, Hull, Mesh, Triangle},
    contact_gjk::sweep::MeshProbe,
    contact_math::ContactFrame,
    contact_mesh::ContactMesh,
    contact_round::{Capsule, RoundShape, Sphere},
    contact_world::{ContactWorld, StaticGeometry, StaticShape},
};
use serde::Deserialize;

use super::{
    AssaultRound, ControlRound, EscortRound, MatchPhase, spawn_door_open, stage_room_visual,
};
use crate::{
    game_content::{ObjectiveModel, PayloadRoute},
    room_primitives::{BarrierKind, SplinePose},
};

type ModelTriangle = (i32, i32, i32, i32, i32, i32, u16, u16);

#[derive(Deserialize)]
#[serde(untagged)]
enum ModelShape {
    Hull(
        String,
        u16,
        [f32; 3],
        Vec<[f32; 3]>,
        Vec<[f32; 4]>,
        Vec<u8>,
        Vec<(i8, u8, u8, u8)>,
    ),
    Mesh(String, u16, Vec<[f32; 3]>, Vec<ModelTriangle>),
    Capsule(String, u16, [f32; 3], [f32; 3], f32),
    Sphere(String, u16, [f32; 3], f32),
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

struct PlacedModel {
    shapes: Vec<StaticGeometry>,
    frame: ContactFrame,
}

impl PlacedModel {
    fn load(model: &ObjectiveModel) -> io::Result<Self> {
        let scale = model.scale;
        if scale
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
        {
            return Err(invalid("invalid objective model scale"));
        }
        let scaled = |point: [f32; 3]| std::array::from_fn(|axis| point[axis] * scale[axis]);
        let mut shapes = Vec::with_capacity(model.shapes.len());
        for raw in &model.shapes {
            let shape: ModelShape = serde_json::from_value(raw.clone()).map_err(invalid)?;
            let geometry = match shape {
                ModelShape::Hull(kind, mask, centroid, vertices, planes, faces, edges)
                    if kind == "hull" =>
                {
                    let half_edges = edges
                        .into_iter()
                        .map(|(twin, origin, face, next)| HalfEdge {
                            twin,
                            origin,
                            face,
                            next,
                        })
                        .collect();
                    // With axis scaling the inverse transpose maps plane normals.
                    let planes = planes
                        .into_iter()
                        .map(|[x, y, z, w]| {
                            let [x, y, z] = [x / scale[0], y / scale[1], z / scale[2]];
                            let length = x.hypot(y).hypot(z);
                            [x / length, y / length, z / length, w / length]
                        })
                        .collect();
                    StaticGeometry::Hull(Arc::new(Hull {
                        mask,
                        centroid: scaled(centroid),
                        vertices: vertices.into_iter().map(scaled).collect(),
                        planes,
                        faces,
                        half_edges,
                    }))
                }
                ModelShape::Mesh(kind, mask, vertices, records) if kind == "mesh" => {
                    let triangles = records
                        .into_iter()
                        .map(|(a, b, c, d, e, f, mask, surface)| Triangle {
                            corners: [a, b, c],
                            wings: [d, e, f],
                            mask,
                            surface,
                        })
                        .collect();
                    StaticGeometry::Mesh(Arc::new(
                        ContactMesh::new(
                            Mesh {
                                vertices,
                                triangles,
                            },
                            scale,
                            mask,
                        )
                        .map_err(invalid)?,
                    ))
                }
                ModelShape::Capsule(kind, mask, a, b, radius) if kind == "capsule" => {
                    StaticGeometry::Round {
                        shape: RoundShape::Capsule(Capsule {
                            a: scaled(a),
                            b: scaled(b),
                            radius: radius * scale[0],
                        }),
                        mask,
                    }
                }
                ModelShape::Sphere(kind, mask, center, radius) if kind == "sphere" => {
                    StaticGeometry::Round {
                        shape: RoundShape::Sphere(Sphere {
                            center: scaled(center),
                            radius: radius * scale[0],
                        }),
                        mask,
                    }
                }
                _ => return Err(invalid("unknown objective collision shape")),
            };
            shapes.push(geometry);
        }
        Ok(Self {
            shapes,
            frame: ContactFrame {
                translation: model.position,
                rotation: model.rotation,
            },
        })
    }

    fn append(&self, out: &mut Vec<StaticShape>, frame: ContactFrame) -> io::Result<()> {
        for geometry in &self.shapes {
            out.push(StaticShape::new(geometry.clone(), Some(frame)).map_err(invalid)?);
        }
        Ok(())
    }
}

pub(crate) struct PayloadContacts {
    base: Arc<ContactWorld>,
    cart: Option<PlacedModel>,
    rooms: BTreeMap<u32, PlacedModel>,
    layer: Arc<ContactWorld>,
    cart_support: ContactWorld,
    pose: Option<SplinePose>,
    closed: Vec<u32>,
}

impl PayloadContacts {
    pub(crate) fn new(base: Arc<ContactWorld>, route: &PayloadRoute) -> io::Result<Self> {
        Self::for_rooms(
            base,
            &route.room_models,
            Some(PlacedModel::load(&route.cart_model)?),
        )
    }

    pub(crate) fn capture(
        base: Arc<ContactWorld>,
        models: &BTreeMap<u32, ObjectiveModel>,
    ) -> io::Result<Self> {
        Self::for_rooms(base, models, None)
    }

    fn for_rooms(
        base: Arc<ContactWorld>,
        models: &BTreeMap<u32, ObjectiveModel>,
        cart: Option<PlacedModel>,
    ) -> io::Result<Self> {
        let rooms = models
            .iter()
            .map(|(&id, model)| Ok((id, PlacedModel::load(model)?)))
            .collect::<io::Result<_>>()?;
        Ok(Self {
            layer: Arc::clone(&base),
            base,
            cart,
            rooms,
            cart_support: ContactWorld::new(Vec::new()).map_err(invalid)?,
            pose: None,
            closed: Vec::new(),
        })
    }

    pub(crate) fn world(&self) -> &Arc<ContactWorld> {
        &self.layer
    }

    pub(crate) fn supported_by_cart(&self, [x, y, z]: [f64; 3], category: u16) -> bool {
        self.cart_support
            .projectile_sweep(MeshProbe {
                points: &[[0.0; 3]],
                start: [x as f32, (y + 0.18) as f32, z as f32],
                end: [x as f32, (y - 0.23) as f32, z as f32],
                radius: 0.0,
                category,
            })
            .is_ok_and(|hit| hit.is_some_and(|hit| hit.hit.normal[1] > 0.55))
    }

    pub(crate) fn update(&mut self, round: &EscortRound, now: f64) -> io::Result<bool> {
        let pose = round.pose();
        let active = matches!(round.phase, MatchPhase::Active | MatchPhase::Complete);
        let closed: Vec<_> = round
            .barriers
            .entries
            .iter()
            .filter(|entry| {
                self.rooms.contains_key(&entry.placeable)
                    && match entry.kind {
                        BarrierKind::Door
                        | BarrierKind::CheckpointGate
                        | BarrierKind::GateDebris => !round.door_passable(entry, now, active),
                        _ => [0, 1]
                            .into_iter()
                            .any(|team| round.room_visual_visible(entry, team)),
                    }
            })
            .map(|entry| entry.placeable)
            .collect();
        self.update_layer(Some(pose), closed)
    }

    pub(crate) fn update_capture(&mut self, round: &AssaultRound, now: f64) -> io::Result<bool> {
        self.update_rooms(
            &round.barriers,
            round.phase,
            round.stage,
            now,
            spawn_door_open,
        )
    }

    pub(crate) fn update_control(&mut self, round: &ControlRound, now: f64) -> io::Result<bool> {
        self.update_rooms(
            &round.barriers,
            round.phase,
            round.stage(),
            now,
            ControlRound::door_open,
        )
    }

    fn update_rooms(
        &mut self,
        barriers: &crate::room_primitives::DynamicBarrierRegistry,
        phase: MatchPhase,
        stage: usize,
        now: f64,
        open: impl Fn(&crate::room_primitives::BarrierEntry, MatchPhase, usize) -> bool,
    ) -> io::Result<bool> {
        let closed =
            barriers
                .entries
                .iter()
                .filter(|entry| {
                    self.rooms.contains_key(&entry.placeable)
                        && match entry.kind {
                            BarrierKind::Door | BarrierKind::CheckpointGate => !barriers
                                .door_passable(entry.entity, now, stage, open(entry, phase, stage)),
                            _ => [0, 1]
                                .into_iter()
                                .any(|team| stage_room_visual(phase, stage, entry, team)),
                        }
                })
                .map(|entry| entry.placeable)
                .collect();
        self.update_layer(None, closed)
    }

    fn update_layer(&mut self, pose: Option<SplinePose>, closed: Vec<u32>) -> io::Result<bool> {
        if self.pose == pose && self.closed == closed {
            return Ok(false);
        }
        let mut shapes = Vec::new();
        if let (Some(cart), Some(pose)) = (&self.cart, pose) {
            let frame = ContactFrame {
                translation: pose.position.map(|value| value as f32),
                rotation: pose.rotation.map(|value| value as f32),
            };
            cart.append(&mut shapes, frame)?;
            let mut support = Vec::new();
            cart.append(&mut support, frame)?;
            self.cart_support = ContactWorld::new(support).map_err(invalid)?;
        }
        for id in &closed {
            let model = &self.rooms[id];
            model.append(&mut shapes, model.frame)?;
        }
        self.layer =
            Arc::new(ContactWorld::with_layer(Arc::clone(&self.base), shapes).map_err(invalid)?);
        self.pose = pose;
        self.closed = closed;
        Ok(true)
    }
}
