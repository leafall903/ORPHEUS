//! Static geometry caches, scene queries and the shared character contact solver.

#![forbid(unsafe_code)]

pub mod collision_cache;
mod collision_grid;
pub mod collision_index;
pub mod collision_scene;
pub mod contact_body;
pub mod contact_cache;
pub mod contact_gjk;
pub mod contact_hull;
pub mod contact_manifolds;
pub mod contact_math;
pub mod contact_mesh;
pub mod contact_round;
pub mod contact_solver;
pub mod contact_triangle;
pub mod contact_world;
pub mod navigation;
pub mod static_ray;
