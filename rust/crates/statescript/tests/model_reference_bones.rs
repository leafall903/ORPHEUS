use orpheus_statescript::script_graph::{GraphStore, asset_guid};
use serde::Deserialize;
use std::{collections::BTreeMap, fs::File, path::PathBuf};

#[derive(Deserialize)]
struct Rig {
    skin: serde_json::Value,
    bones: Vec<Bone>,
    hardpoint_bindings: BTreeMap<String, Binding>,
}

#[derive(Deserialize)]
struct Bone {
    id: u32,
    model_reference_matrix: [f64; 16],
}

#[derive(Deserialize)]
struct Binding {
    bone_index: Option<usize>,
    local_position: [f64; 3],
}

#[test]
#[ignore = "requires ORPHEUS_RUNTIME_DIR enriched with bones and ORPHEUS_MODEL_SKELETONS pointing to its local source export"]
fn prepared_model_reference_bones_match_modelspace_hardpoint_positions() {
    let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let raw = File::open(std::env::var_os("ORPHEUS_MODEL_SKELETONS").unwrap()).unwrap();
    #[derive(Deserialize)]
    struct Export {
        build: u32,
        bodies: BTreeMap<String, Rig>,
    }
    let export: Export = serde_json::from_reader(raw).unwrap();
    let store = GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap();
    assert_eq!(export.build, 104319);
    assert_eq!(export.bodies.len(), 32);
    let mut bones_checked = 0;
    let mut points_checked = 0;
    for (body, rig) in export.bodies {
        let body_id = u64::from_str_radix(body.strip_prefix("0x").unwrap(), 16).unwrap();
        let skin = asset_guid(&rig.skin).unwrap();
        let geometry = store.bodies()[&body_id].model_geometry(skin).unwrap();
        for bone in &rig.bones {
            let id = 0x0880_0000_0000_0000 | u64::from(bone.id);
            let translation: [f64; 3] = bone.model_reference_matrix[12..15].try_into().unwrap();
            let actual = geometry.bone_reference_point(id, [0.0; 3]).unwrap();
            for (a, b) in actual.into_iter().zip(translation) {
                assert!((a - b).abs() < 1.0e-10, "{body} {id:#x}");
            }
            bones_checked += 1;
        }
        let mut offsets_checked = 0;
        for (point, binding) in rig.hardpoint_bindings {
            let Some(i) = binding.bone_index else {
                continue;
            };
            let id = 0x0880_0000_0000_0000 | u64::from(rig.bones[i].id);
            let point_id = u64::from_str_radix(point.strip_prefix("0x").unwrap(), 16).unwrap();
            let expected = geometry.reference_position(point_id).unwrap();
            let actual = geometry
                .bone_reference_point(id, binding.local_position)
                .unwrap();
            for (a, b) in actual.into_iter().zip(expected) {
                assert!(
                    (a - b).abs() < 1.0e-5,
                    "{body} {point} {actual:?} vs {expected:?}"
                );
            }
            offsets_checked += usize::from(binding.local_position != [0.0; 3]);
            points_checked += 1;
        }
        assert!(
            offsets_checked > 0,
            "{body} did not exercise an authored offset"
        );
    }
    eprintln!("Checked {bones_checked} reference transforms and {points_checked} attached points");
}
