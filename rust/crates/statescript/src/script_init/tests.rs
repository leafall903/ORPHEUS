use super::*;

mod abilities_and_weapons;
mod actions_contract;
mod actor_relative;
mod actor_scope_identity;
mod actor_voice;
mod ai_and_spawning;
mod arrays_and_boss;
mod authored_classes;
mod contact_combat;
mod destination_and_victory;
mod directed_charge;
mod elapsed_wait;
mod facing_claim;
mod frame_wait;
mod hook_chain;
mod horizontal_velocity_clamp;
mod linked_state;
mod local_combat_accumulator;
mod local_command;
mod local_message;
mod logical_button_lifecycle;
mod move_to;
mod movement_edges;
mod named_shield;
mod observation_sample;
mod observer_eye_position;
mod parent_owned_state;
mod physics_explosion;
mod projected_messages;
mod relocation;
mod retail_semantics;
mod room_cue;
mod runtime_state_metadata;
mod self_damage;
mod shield_recharge;
mod stack_controls;
mod stat_state;
mod states_and_switches;
mod static_model_protection;
mod subgraph_boxes;
mod swept_displacement;
mod vertical_velocity_impulse;
mod view_targets;

pub(super) use std::path::PathBuf;

pub(super) use serde_json::json;

pub(super) use super::machine::{LinkValue, StateKey, VarKey, VariableLink};

pub(super) fn node(position: u16, class: &str, state: Option<u16>, mut fields: Json) -> Json {
    fields["m_2BBEEAB8"] = json!(state.is_some());
    fields["m_ADEB6E05"] = json!(
        class.contains("Action")
            || matches!(
                class,
                "STU_12F5D52D" | "STU_22926252" | "STU_DA54F8F7" | "STU_4C3509FB" | "STU_CBE8BDAA"
            )
    );
    json!({
        "pos":position, "class":class, "uid":position, "rid":position, "remote":-1,
        "state":state, "client_only":false, "server_only":false, "fields":fields,
    })
}

pub(super) fn graph(index: u32, nodes: Vec<Json>, states: Json, entries: Json) -> Json {
    json!({
        "index":index, "items":nodes.len(), "nodes":nodes, "states":states,
        "entries":entries, "remote":[], "sync_vars":[], "containers":{},
        "unknown_nodes":[], "fields":{},
        "header":{
            "m_nodesBitCount":4, "m_statesBitCount":4, "m_remoteSyncNodesBitCount":4,
            "m_syncVarsBitCount":4
        }
    })
}

pub(super) fn store(graphs: Vec<Json>) -> GraphStore {
    GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":graphs, "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap()
}

pub(super) fn plug(links: &[(u16, &str)], states: &[u16]) -> Json {
    json!({"$":"Plug","links":links,"m_FF3DAF1E":states})
}

pub(super) fn var(id: u32, entity: bool) -> Json {
    json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":entity})
}

pub(super) fn parent_var(id: u32) -> Json {
    json!({"$":"STU_919FD47C","m_expression":{
        "m_opcodes":[10,1,0,0], "m_dynamicVars":[var(id, false)]
    }})
}

pub(super) struct ReferenceWorld;

impl World for ReferenceWorld {
    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        // Values observed in the Python fixtures, for this test only.
        match (site.graph, config.get("$")?.as_str()?) {
            (0x0c18, "STU_AC829876") => Some(Value::Bool(true)),
            (0x0c18, "STU_4A101787" | "STU_A77AC2AC") => Some(Value::Null),
            (0x288a, "STU_37F20D24") => Some(Value::Null),
            (0x0b92, "STU_71A87E92" | "STU_2200A66D") if matches!(site.node, Some(7 | 11)) => {
                Some(Value::Null)
            }
            _ => None,
        }
    }

    fn selects_ai_behavior(&mut self, site: Site) -> Option<bool> {
        (site.graph == 0x0b92 && site.node == Some(1)).then_some(false)
    }
    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
    fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
        None
    }
}

pub(super) fn active(snapshot: &Snapshot, id: u16) -> Vec<u16> {
    snapshot.instances[&id]
        .states
        .iter()
        .filter_map(|(&raw, state)| state.active.then_some(raw))
        .collect()
}

#[test]
fn shared_voice_lifecycle_contract() {
    destination_and_victory::shared_voice_lifecycle_contract();
}

#[test]
fn shared_nested_destination_contract() {
    destination_and_victory::shared_nested_destination_contract();
}

#[test]
fn nested_destination_writes_notify_existing_readers() {
    destination_and_victory::nested_destination_writes_notify_existing_readers();
}

#[test]
fn nested_destination_keeps_unavailable_storage_explicit() {
    destination_and_victory::nested_destination_keeps_unavailable_storage_explicit();
}

#[test]
fn shared_foreign_notification_contract() {
    destination_and_victory::shared_foreign_notification_contract();
}

#[test]
fn shared_spatial_owner_absence_contract() {
    destination_and_victory::shared_spatial_owner_absence_contract();
}

#[test]
fn shared_contains_condition_contract() {
    arrays_and_boss::shared_contains_condition_contract();
}

#[test]
fn shared_corpse_retirement_contract() {
    arrays_and_boss::shared_corpse_retirement_contract();
}

#[test]
fn shared_unkeyed_array_contract() {
    arrays_and_boss::shared_unkeyed_array_contract();
}

#[test]
fn shared_unbound_loadout_action_contract() {
    states_and_switches::shared_unbound_loadout_action_contract();
}

#[test]
fn shared_stack_admission_contract() {
    abilities_and_weapons::shared_stack_admission_contract();
}

#[test]
fn shared_ability_press_window_contract() {
    abilities_and_weapons::shared_ability_press_window_contract();
}

#[test]
#[ignore = "requires a local 1.74 graph pack"]
fn authored_sprint_stack_gates_track_input_and_reveal_the_base_on_release() {
    abilities_and_weapons::authored_sprint_stack_gates_track_input_and_reveal_the_base_on_release();
}

#[test]
#[ignore = "requires a local 1.74 graph pack"]
fn isolated_soldier_sprint_ability_matches_the_authored_toggle_and_modifier_states() {
    abilities_and_weapons::isolated_soldier_sprint_ability_matches_the_authored_toggle_and_modifier_states();
}

#[test]
#[ignore = "requires a local 1.74 graph pack"]
fn soldier_sprint_graph_reads_primary_crouch_and_melee_edges_as_in_python() {
    abilities_and_weapons::soldier_sprint_graph_reads_primary_crouch_and_melee_edges_as_in_python();
}

#[test]
#[ignore = "requires a local 1.74 Practice Range NPC graph pack with graph 0B92"]
fn firing_placement_reset_and_timer_boundaries_match_python() {
    abilities_and_weapons::firing_placement_reset_and_timer_boundaries_match_python();
}

#[test]
#[ignore = "requires an authorized 104319 graph pack in ORPHEUS_RUNTIME_DIR"]
fn controller_and_training_bot_supported_startup_edges_match_python() {
    abilities_and_weapons::controller_and_training_bot_supported_startup_edges_match_python();
}
