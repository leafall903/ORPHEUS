//! Authored node-shape predicates and StateScript value conversion helpers.

use serde_json::Value as Json;

use crate::{
    script_expr::{self, Scope, Variable},
    script_graph::{Graph, Node, asset_guid, message_parameter_id},
    script_runtime::{
        BOSS_CAMERA, BOSS_PAUSE, POST_MATCH_PHASE, POST_MATCH_SIGNAL, PoolKind, RESULT_SUMMARY,
    },
    script_wire::Value,
};

use super::{
    MOVEMENT_BOOLEAN_OUTPUTS,
    machine::{RegisteredClass, RegisteredState, VarKey},
    types::Snapshot,
};

pub(super) fn participant_filter(node: &Node) -> Option<&Json> {
    let filter = field(&node.fields, "m_filter");
    (filter.get("$").and_then(Json::as_str) == Some("STUConfigVarFilter")
        && ["m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"]
            .iter()
            .all(|name| field(&node.fields, name).is_null()))
    .then_some(filter)
}

pub(super) fn placement_collection_tag(node: &Node) -> Option<u64> {
    if !field(&node.fields, "m_9C7BD8B8").is_null() {
        return None;
    }
    script_expr::target_tag_config(static_target_operand(node)?).filter(|&tag| tag != 0)
}

pub(super) fn excluded_target_tags(node: &Node) -> Option<(u64, u64)> {
    if !field(&node.fields, "m_filter").is_null() || !field(&node.fields, "m_B486603E").is_null() {
        return None;
    }
    let tag = script_expr::target_tag_config(field(&node.fields, "m_9C7BD8B8"))
        .filter(|&tag| tag != 0)?;
    let excluded = script_expr::target_tag_config(field(&node.fields, "m_8B2A4DF3"))
        .filter(|&tag| tag != 0)?;
    Some((tag, excluded))
}

pub(super) fn static_target_operand(node: &Node) -> Option<&Json> {
    if !field(&node.fields, "m_filter").is_null() || !field(&node.fields, "m_8B2A4DF3").is_null() {
        return None;
    }
    let primary = field(&node.fields, "m_9C7BD8B8");
    let required = field(&node.fields, "m_B486603E");
    match (primary.is_null(), required.is_null()) {
        (false, true) => Some(primary),
        (true, false) => Some(required),
        _ => None,
    }
}

pub(super) fn is_entry(node: &Node) -> bool {
    matches!(node.class.as_str(), "STUStatescriptEntry" | "STU_571CC72E")
}

pub(super) fn registered_state(graph: &Graph, node: &Node) -> Option<RegisteredState> {
    let placement = (graph.index, node.pos);
    let linked_state = node.class == "STU_9028E035";
    if !node.runs_on_server() || (!node.is_networked() && !linked_state) {
        return None;
    }
    let fields = &node.fields;
    let flag = |name, expected| {
        let config = field(fields, name);
        script_expr::native_shape(config, "STUConfigVarBool", Some("m_value"))
            && (field(config, "m_value").as_bool() == Some(expected)
                || field(config, "m_value").as_u64() == Some(u64::from(expected)))
    };
    let has_raw_state_slot = || {
        node.is_state()
            && !node.is_action()
            && node
                .state
                .and_then(|slot| graph.state(usize::from(slot)))
                .is_some_and(|linked| linked.pos == node.pos)
    };
    let has_local_state_slot = || node.remote == -1 && has_raw_state_slot();
    // The static form has only the protected-entity rule. A phase-model rule
    // or any other configured field needs its own verified consumer.
    let suppresses_model = node.class == "STU_33A46033"
        && has_raw_state_slot()
        && usize::try_from(node.remote)
            .ok()
            .and_then(|slot| graph.remote_node(slot))
            .is_some_and(|linked| linked.pos == node.pos)
        && flag("m_7E42F868", true)
        && registered_state_fields(node, "m_7E42F868");
    let class = match node.class.as_str() {
        "STU_9935FBC8" if placement == (0x224e, 34) && captured_aim_lock_shape(node) => {
            RegisteredClass::CinematicAim
        }
        "STU_9935FBC8"
            if matches!(placement, (0x0ce0, 45) | (0x0ca5, 70))
                && cinematic_aim_lock_shape(node) =>
        {
            RegisteredClass::CinematicAim
        }
        // The native begin/end pair maintains one ordered list per class.
        // Both authored type values use the same registration and link codec.
        "STU_9028E035"
            if has_local_state_slot()
                && matches!(field(&node.fields, "m_type").as_u64(), Some(0 | 1)) =>
        {
            RegisteredClass::State9028E035
        }
        "STU_43994F1B" | "STU_93B8758C" if has_local_state_slot() => {
            resource_free_linked_class(node)?
        }
        "STU_33A46033" if suppresses_model => RegisteredClass::EntityControl,
        "STU_33A46033" if placement == (0x0ca5, 61) => {
            if !flag("m_7E42F868", false)
                || !flag("m_D82DB14B", false)
                || [
                    "m_069FD7A5",
                    "m_72FD7742",
                    "m_381879C1",
                    "m_C3E68133",
                    "m_3C443404",
                    "m_DFD8E2EA",
                    "m_6771D268",
                ]
                .iter()
                .any(|name| !field(fields, name).is_null())
            {
                return None;
            }
            RegisteredClass::EntityControl
        }
        _ => return None,
    };
    Some(RegisteredState {
        class,
        server_only: node.server_only,
        suppresses_model,
    })
}

fn resource_free_linked_class(node: &Node) -> Option<RegisteredClass> {
    let (resource, class) = match node.class.as_str() {
        "STU_43994F1B" => ("m_contactSet", RegisteredClass::State43994F1B),
        "STU_93B8758C" => ("m_A5B7A147", RegisteredClass::State93B8758C),
        _ => return None,
    };
    // Native records 1122/1121 share the registered-state lifecycle.
    // A configured contact or material resource requires a separate host effect.
    (is_self_target(node, "m_4A5CA514")
        && field(&node.fields, resource).is_null()
        && registered_state_fields(node, "m_4A5CA514"))
    .then_some(class)
}

fn registered_state_fields(node: &Node, operand: &str) -> bool {
    node.fields.as_object().is_some_and(|fields| {
        fields.iter().all(|(name, value)| {
            value.is_null()
                || name == operand
                || match name.as_str() {
                    "m_beginPlug" | "m_F198FD3A" | "m_onBeginPlug" | "m_onEndPlug"
                    | "m_subgraphPlug" => true,
                    "m_A2287776" | "m_AED90719" | "m_ADEB6E05" => {
                        value.as_bool() == Some(false) || value.as_u64() == Some(0)
                    }
                    "m_2BBEEAB8" => value.as_bool() == Some(true) || value.as_u64() == Some(1),
                    _ => false,
                }
        })
    })
}

pub(super) fn captured_aim_lock_shape(node: &Node) -> bool {
    let fields = &node.fields;
    field(fields, "m_93CDFD2F").as_u64() == Some(1)
        && field(fields, "m_4BE66EFF").is_null()
        && [("m_A53161C8", 501), ("m_AB37A892", 502)]
            .iter()
            .all(|(name, id)| {
                matches!(script_expr::dynamic(field(fields,name)), Ok(Some(variable))
                if variable.scope == Scope::Instance && variable.id == *id)
            })
        && ["m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"]
            .iter()
            .all(|name| {
                let config = field(fields, name);
                script_expr::native_shape(config, "STUConfigVarInt", Some("m_value"))
                    && field(config, "m_value").as_i64() == Some(0)
            })
}

pub(super) fn cinematic_aim_lock_shape(node: &Node) -> bool {
    let fields = &node.fields;
    let self_angle = |name, class| {
        let config = field(fields, name);
        script_expr::native_shape(config, class, Some("m_entity"))
            && script_expr::native_shape(field(config, "m_entity"), "STU_B5A0CAF0", None)
    };
    field(fields, "m_93CDFD2F").as_u64() == Some(1)
        && field(fields, "m_4BE66EFF").is_null()
        && self_angle("m_A53161C8", "STU_CC53F52B")
        && self_angle("m_AB37A892", "STU_7BE6D76D")
        && ["m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"]
            .iter()
            .all(|name| {
                let config = field(fields, name);
                script_expr::native_shape(config, "STUConfigVarInt", Some("m_value"))
                    && field(config, "m_value").as_i64() == Some(0)
            })
}

pub(super) fn is_self_target(node: &Node, name: &str) -> bool {
    script_expr::native_shape(field(&node.fields, name), "STU_B5A0CAF0", None)
}

pub(super) struct ImmediateSelfDamage {
    pub(super) amount: f64,
    pub(super) skip_dead_owner: bool,
}

pub(super) fn immediate_self_damage(node: &Node, state: u16) -> Option<ImmediateSelfDamage> {
    let fields = &node.fields;
    let effect = field(fields, "m_modifyHealth");
    let amount = field(effect, "m_amount");
    let source = field(effect, "m_724CBA57");
    if !node.is_networked()
        || node.state != Some(state)
        || node.remote != -1
        || !node.is_state()
        || node.is_action()
        || field(fields, "m_DD792FF0").as_u64() != Some(3)
        || field(fields, "m_7B2B3F5C").as_u64() != Some(0)
        || field(fields, "m_8B707686").as_u64() != Some(1)
        || !is_self_target(node, "m_FA3ECC43")
        || field(effect, "$").as_str() != Some("STUStatescriptModifyHealth")
        || field(effect, "m_93CDFD2F").as_u64() != Some(1)
        || !script_expr::native_shape(source, "STU_B5A0CAF0", None)
        || !script_expr::native_shape(amount, "STUConfigVarInt", Some("m_value"))
    {
        return None;
    }

    let amount = i32::try_from(field(amount, "m_value").as_i64()?).ok()?;
    if amount <= 0 {
        return None;
    }
    let instigator = field(effect, "m_instigator");
    let skip_dead_owner = instigator.is_null();
    // The reference only handles the no-instigator cleanup at this amount.
    if (skip_dead_owner && amount != 10_000)
        || (!skip_dead_owner && !script_expr::native_shape(instigator, "STU_B5A0CAF0", None))
        || !effect.as_object()?.iter().all(|(name, value)| {
            value.is_null()
                || match name.as_str() {
                    "$" | "m_93CDFD2F" | "m_amount" | "m_724CBA57" | "m_instigator" => true,
                    "m_EE729DCB" | "m_430E83BE" => value.as_u64() == Some(0),
                    _ => false,
                }
        })
        || !fields.as_object()?.iter().all(|(name, value)| {
            value.is_null()
                || match name.as_str() {
                    "m_DD792FF0" | "m_7B2B3F5C" | "m_8B707686" | "m_modifyHealth"
                    | "m_FA3ECC43" => true,
                    "m_2BBEEAB8" => value.as_bool() == Some(true) || value.as_u64() == Some(1),
                    "m_A2287776" | "m_AED90719" | "m_ADEB6E05" => {
                        value.as_bool() == Some(false) || value.as_u64() == Some(0)
                    }
                    "m_79758CE9" | "m_FF9E82FA" | "m_48BED081" => {
                        script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
                            && (field(value, "m_value").as_bool() == Some(false)
                                || field(value, "m_value").as_u64() == Some(0))
                    }
                    "m_beginPlug" => is_unlinked_plug(fields, name, "STUStatescriptInputPlug"),
                    "m_F198FD3A" => is_unlinked_plug(fields, name, "STU_1CCEA5F7"),
                    "m_onBeginPlug" => is_unlinked_plug(fields, name, "STU_DA98CA6D"),
                    "m_onAbortPlug" | "m_onFinishedPlug" | "m_onEndPlug" => {
                        is_unlinked_plug(fields, name, "STUStatescriptOutputPlug")
                    }
                    "m_subgraphPlug" => {
                        field(value, "$").as_str() == Some("STU_904BFCEC")
                            && field(value, "links").as_array().is_some_and(Vec::is_empty)
                            && field(value, "m_FF3DAF1E").is_null()
                            && value.as_object().is_some_and(|plug| {
                                plug.keys()
                                    .all(|key| matches!(key.as_str(), "$" | "links" | "m_FF3DAF1E"))
                            })
                    }
                    _ => false,
                }
        })
    {
        return None;
    }

    Some(ImmediateSelfDamage {
        amount: f64::from(amount as f32),
        skip_dead_owner,
    })
}

pub(super) fn is_named_shield_decay(node: &Node, state: u16) -> bool {
    let fields = &node.fields;
    let effect = field(fields, "m_modifyHealth");
    let interval = field(fields, "m_95F1FB0B");
    let tags = field(effect, "m_148F3152");
    let flag = |value: &Json, expected| {
        script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
            && (field(value, "m_value").as_bool() == Some(expected)
                || field(value, "m_value").as_u64() == Some(u64::from(expected)))
    };
    node.is_networked()
        && node.state == Some(state)
        && node.remote == -1
        && node.is_state()
        && !node.is_action()
        && field(fields, "m_DD792FF0").as_u64() == Some(2)
        && field(fields, "m_7B2B3F5C").as_u64() == Some(1)
        && field(fields, "m_8B707686").as_u64() == Some(1)
        && script_expr::native_shape(interval, "STUConfigVarInt", Some("m_value"))
        && field(interval, "m_value").as_u64() == Some(1)
        && script_expr::named_shield_identifier(field(fields, "m_7BE02F89")).is_some()
        && is_self_target(node, "m_FA3ECC43")
        && field(effect, "$").as_str() == Some("STUStatescriptModifyHealth")
        && field(effect, "m_93CDFD2F").as_u64() == Some(1)
        && script_expr::native_shape(field(effect, "m_724CBA57"), "STU_B5A0CAF0", None)
        && !field(effect, "m_amount").is_null()
        && (tags.is_null()
            || tags.as_array().is_some_and(|tags| {
                tags.len() <= super::world::MAX_HIT_TAGS
                    && tags.iter().all(|tag| {
                        script_expr::native_shape(
                            tag,
                            "STUStatescriptModifyHealthTag",
                            Some("m_EE3055BD"),
                        ) && script_expr::named_shield_identifier(field(tag, "m_EE3055BD"))
                            .is_some()
                    })
            }))
        && effect.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| {
                value.is_null()
                    || match name.as_str() {
                        "$" | "m_724CBA57" | "m_93CDFD2F" | "m_amount" | "m_148F3152" => true,
                        "m_430E83BE" => value.as_u64() == Some(0),
                        "m_62E3835A" => flag(value, false),
                        "m_F8EA2036" => flag(value, true),
                        _ => false,
                    }
            })
        })
        && fields.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| {
                value.is_null()
                    || match name.as_str() {
                        "m_DD792FF0" | "m_7B2B3F5C" | "m_8B707686" | "m_95F1FB0B"
                        | "m_7BE02F89" | "m_FA3ECC43" | "m_modifyHealth" | "m_2BBEEAB8"
                        | "m_beginPlug" | "m_F198FD3A" | "m_onBeginPlug" | "m_onEndPlug"
                        | "m_subgraphPlug" | "m_onFinishedPlug" | "m_onAbortPlug" => true,
                        "m_48BED081" | "m_79758CE9" | "m_FF9E82FA" => flag(value, false),
                        "m_A2287776" | "m_AED90719" | "m_ADEB6E05" => {
                            value.as_u64() == Some(0) || value.as_bool() == Some(false)
                        }
                        _ => false,
                    }
            })
        })
}

pub(super) fn is_self_shield_recharge(node: &Node, state: u16) -> bool {
    let fields = &node.fields;
    let effect = field(fields, "m_modifyHealth");
    let interval = field(fields, "m_95F1FB0B");
    let amount = field(effect, "m_amount");
    let off =
        |value: &Json| matches!(value, Json::Null | Json::Bool(false)) || value.as_u64() == Some(0);
    let supported_rate = match field(amount, "$").as_str() {
        Some(class @ ("STUConfigVarInt" | "STUConfigVarFloat")) => {
            script_expr::native_shape(amount, class, Some("m_value"))
                && field(amount, "m_value").is_number()
        }
        Some("STUConfigVarDynamic") => {
            script_expr::native_fields(amount, &["m_identifier", "m_60DB8F99"])
                && script_expr::dynamic(amount).is_ok_and(|operand| operand.is_some())
        }
        Some("STU_919FD47C" | "STUConfigVarExpression") => {
            script_expr::native_fields(amount, &["m_expression", "m_configVars"])
                && field(amount, "m_expression").is_object()
        }
        _ => false,
    };

    node.server_only
        && !node.client_only
        && node.state == Some(state)
        && node.remote == -1
        && node.is_state()
        && !node.is_action()
        && field(fields, "m_DD792FF0").as_u64() == Some(3)
        && field(fields, "m_7B2B3F5C").as_u64() == Some(1)
        && off(field(fields, "m_8B707686"))
        && script_expr::native_shape(interval, "STUConfigVarInt", Some("m_value"))
        && field(interval, "m_value").as_u64() == Some(1)
        && is_self_target(node, "m_FA3ECC43")
        && field(effect, "$").as_str() == Some("STUStatescriptModifyHealth")
        && field(effect, "m_93CDFD2F").as_u64() == Some(0)
        && script_expr::native_shape(field(effect, "m_724CBA57"), "STU_B5A0CAF0", None)
        && supported_rate
        && effect.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| match name.as_str() {
                "$" | "m_93CDFD2F" | "m_amount" | "m_724CBA57" => true,
                "m_EE729DCB" | "m_430E83BE" => value.as_u64() == Some(0),
                _ => value.is_null(),
            })
        })
        && fields.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| match name.as_str() {
                "m_DD792FF0" | "m_7B2B3F5C" | "m_8B707686" | "m_95F1FB0B" | "m_modifyHealth"
                | "m_FA3ECC43" | "m_2BBEEAB8" | "m_beginPlug" | "m_F198FD3A" | "m_onBeginPlug"
                | "m_onEndPlug" | "m_subgraphPlug" | "m_onAbortPlug" | "m_onFinishedPlug" => true,
                "m_A2287776" | "m_AED90719" | "m_ADEB6E05" => off(value),
                "m_48BED081" => {
                    value.is_null()
                        || (script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
                            && !field(value, "m_value").is_null()
                            && off(field(value, "m_value")))
                }
                // Container bookkeeping has no health effect.
                "m_0B1AA8CA" => {
                    value.is_null()
                        || value.as_object().is_some_and(|item| {
                            item.len() == 1 && field(value, "item").as_u64().is_some()
                        })
                }
                _ => value.is_null(),
            })
        })
}

pub(super) fn is_self_stat_timer(node: &Node) -> bool {
    if node.remote != -1
        || !field(&node.fields, "m_stats").is_null()
        || !is_self_target(node, "m_targets")
    {
        return false;
    }
    let stat = field(&node.fields, "m_stat");
    let stat_list = field(&node.fields, "m_D32CB484");
    let is_stat = |value| asset_guid(value).is_some_and(|id| id >> 48 == 0x0860);
    if node.server_only {
        is_stat(stat) && stat_list.is_null()
    } else {
        node.is_networked()
            && stat.is_null()
            && matches!(stat_list.as_array().map(Vec::as_slice), Some([entry]) if is_stat(entry))
    }
}

pub(super) fn is_stat_action(node: &Node) -> bool {
    let fields = &node.fields;
    let target = field(fields, "m_targets");
    let has_variable_target = native_dynamic_operand(target)
        && field(target, "m_identifier")
            .as_u64()
            .is_some_and(|id| id != 0);
    if !node.runs_on_server()
        || node.remote != -1
        || node.state.is_some()
        || node.is_state()
        || !node.is_action()
        || !(is_self_target(node, "m_targets") || has_variable_target)
    {
        return false;
    }
    let stat = field(fields, "m_stat");
    let stats = field(fields, "m_stats");
    let is_stat = |value| asset_guid(value).is_some_and(|id| id >> 48 == 0x0860);
    let stat_selection = if stat.is_null() {
        stats
            .as_array()
            .is_some_and(|items| !items.is_empty() && items.iter().all(is_stat))
    } else {
        stats.is_null() && is_stat(stat)
    };
    let amount = field(fields, "m_amount");
    let output = field(fields, "m_outPlug");
    let output_states = field(output, "m_FF3DAF1E");
    let neutral_flag =
        |value: &Json| matches!(value, Json::Null | Json::Bool(false)) || value.as_u64() == Some(0);
    stat_selection
        && (native_integer_operand(amount)
            || native_dynamic_operand(amount)
            || native_expression_operand(amount))
        && (field(fields, "m_inPlug").is_null()
            || is_unlinked_plug(fields, "m_inPlug", "STUStatescriptInputPlug"))
        && matches!(
            field(output, "$").as_str(),
            Some("STUStatescriptOutputPlug" | "Plug")
        )
        && script_expr::native_fields(output, &["links", "m_FF3DAF1E"])
        && (output_states.is_null() || output_states.as_array().is_some_and(Vec::is_empty))
        && field(output, "links").as_array().is_some_and(|links| {
            links.iter().all(|link| {
                matches!(
                    link.as_array().map(Vec::as_slice),
                    Some([position, input])
                        if (position.is_null()
                            || position.as_u64().is_some_and(|pos| u16::try_from(pos).is_ok()))
                            && input.as_str().is_some_and(|name| !name.is_empty())
                )
            })
        })
        && fields.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| match name.as_str() {
                "m_2BBEEAB8" | "m_A2287776" | "m_AED90719" => neutral_flag(value),
                "m_ADEB6E05" => value.as_bool() == Some(true) || value.as_u64() == Some(1),
                "m_amount" | "m_inPlug" | "m_outPlug" | "m_stat" | "m_stats" | "m_targets" => true,
                _ => value.is_null(),
            })
        })
}

pub(super) fn is_elapsed_wait(node: &Node) -> bool {
    let dynamic = field(&node.fields, "m_BF1A93B0");
    if matches!(dynamic, Json::Null | Json::Bool(false)) || dynamic.as_u64() == Some(0) {
        return true;
    }
    let rearm = field(&node.fields, "m_0DBAFD7F");
    let timeout = field(&node.fields, "m_timeout");
    let scope = field(timeout, "m_60DB8F99");
    let is_flag = |value: &Json| {
        matches!(value, Json::Null | Json::Bool(_)) || matches!(value.as_u64(), Some(0 | 1))
    };
    node.remote == -1
        && (dynamic.as_bool() == Some(true) || dynamic.as_u64() == Some(1))
        && is_flag(rearm)
        && is_flag(scope)
        && script_expr::native_fields(timeout, &["m_identifier", "m_60DB8F99"])
        && script_expr::dynamic(timeout).is_ok_and(|value| value.is_some())
}

pub(super) fn is_literal_frame_wait(node: &Node) -> bool {
    let duration = field(&node.fields, "m_3016B9A1");
    let rearm = field(&node.fields, "m_0DBAFD7F");
    node.remote == -1
        && (matches!(rearm, Json::Null | Json::Bool(false)) || rearm.as_u64() == Some(0))
        && script_expr::native_shape(duration, "STUConfigVarInt", Some("m_value"))
        && field(duration, "m_value")
            .as_u64()
            .is_some_and(|count| i32::try_from(count).is_ok())
}

pub(super) fn is_cinematic_time_scale_envelope(graph: &Graph, node: &Node, state: u16) -> bool {
    let scale = field(&node.fields, "m_E75DF5AC");
    let scale_matches = match field(scale, "$").as_str() {
        Some("STUConfigVarDynamic") => {
            script_expr::native_fields(scale, &["m_identifier", "m_60DB8F99"])
                && script_expr::dynamic(scale).is_ok_and(|value| value.is_some())
        }
        Some("STU_919FD47C") => {
            script_expr::native_fields(scale, &["m_expression", "m_configVars"])
                && field(scale, "m_expression").is_object()
                && field(scale, "m_configVars").is_array()
        }
        _ => false,
    };
    node.is_networked()
        && node.is_state()
        && !node.is_action()
        && node.state == Some(state)
        && graph
            .state(usize::from(state))
            .is_some_and(|owner| owner.pos == node.pos)
        && usize::try_from(node.remote)
            .ok()
            .and_then(|slot| graph.remote_node(slot))
            .is_some_and(|remote| remote.pos == node.pos)
        && ["m_17D12F6B", "m_D8989B37", "m_89C63AB3"]
            .into_iter()
            .all(|name| {
                let operand = field(&node.fields, name);
                script_expr::native_shape(operand, "STUConfigVarInt", Some("m_value"))
                    && field(operand, "m_value")
                        .as_i64()
                        .is_some_and(|value| i32::try_from(value).is_ok())
            })
        && scale_matches
}

pub(super) fn is_static_map_explosion(graph: &Graph, node: &Node, state: u16) -> bool {
    if !node.runs_on_server()
        || !node.is_state()
        || node.is_action()
        || node.state != Some(state)
        || graph
            .state(usize::from(state))
            .is_none_or(|owner| owner.pos != node.pos)
    {
        return false;
    }
    let visibility_matches = if node.server_only {
        node.remote == -1
    } else {
        usize::try_from(node.remote)
            .ok()
            .and_then(|slot| graph.remote_node(slot))
            .is_some_and(|remote| remote.pos == node.pos)
    };

    let fields = &node.fields;
    let position = field(fields, "m_position");
    let direction = field(fields, "m_direction");
    let scalar = field(fields, "m_D80AEDB1");
    let extra_scalar = field(fields, "m_3E3DFE0F");
    let optional_float = field(fields, "m_015FF666");
    let owner = field(fields, "m_0B1AA8CA");
    let literal_number = |config| native_integer_operand(config) || explosion_float(config);
    let number = |config| literal_number(config) || native_expression_operand(config);
    visibility_matches
        && field(fields, "m_86CE02DE").as_u64() == Some(1)
        && field(fields, "m_A2287776").as_u64() == Some(0)
        && field(fields, "m_AED90719").as_u64() == Some(0)
        && ["m_17E9238F", "m_4A518273", "m_falloff"]
            .into_iter()
            .all(|name| field(fields, name).is_null())
        && (owner.is_null()
            || owner
                .as_object()
                .is_some_and(|item| item.len() == 1 && field(owner, "item").as_u64().is_some()))
        && empty_explosion_plug(field(fields, "m_F198FD3A"), "STU_1CCEA5F7")
        && empty_explosion_plug(field(fields, "m_subgraphPlug"), "STU_904BFCEC")
        && explosion_position(position)
        && (direction.is_null() || explosion_direction(direction))
        && number(field(fields, "m_radius"))
        && explosion_float(field(fields, "m_65438EEB"))
        && native_integer_operand(field(fields, "m_66D29548"))
        && {
            let strength = field(fields, "m_6EFD110C");
            native_integer_operand(strength) || native_expression_operand(strength)
        }
        && number(scalar)
        && (extra_scalar.is_null() || literal_number(extra_scalar))
        && (optional_float.is_null() || explosion_float(optional_float))
        && explosion_sync_matches(graph, fields)
}

fn empty_explosion_plug(config: &Json, class: &str) -> bool {
    let data = field(config, "m_FF3DAF1E");
    config.is_null()
        || (field(config, "$").as_str() == Some(class)
            && field(config, "links").as_array().is_some_and(Vec::is_empty)
            && (data.is_null() || data.as_array().is_some_and(Vec::is_empty)))
}

fn explosion_sync_matches(graph: &Graph, fields: &Json) -> bool {
    let sync = field(fields, "m_8BF03679");
    sync == field(fields, "m_BF5B22B7")
        && (sync.is_null()
            || sync.as_array().is_some_and(|rows| {
                rows.iter().all(|row| {
                    script_expr::native_fields(row, &["m_0D09D2D9", "m_56341592"])
                        && field(row, "$").as_str() == Some("STUStatescriptRemoteSyncVar")
                        && field(row, "m_56341592").as_u64() == Some(0)
                        && asset_guid(field(row, "m_0D09D2D9")).is_some_and(|resource| {
                            let variable = resource as u32;
                            resource >> 52 == 0x0d8
                                && variable != 0
                                && graph
                                    .sync_vars
                                    .iter()
                                    .any(|entry| entry.0 == Some(variable) && entry.1 == 0)
                        })
                })
            }))
}

fn explosion_position(config: &Json) -> bool {
    match field(config, "$").as_str() {
        Some("STU_71A87E92") => {
            script_expr::native_shape(config, "STU_71A87E92", Some("m_entity"))
                && native_dynamic_operand(field(config, "m_entity"))
        }
        Some("STU_B5A0CAF0") => script_expr::native_shape(config, "STU_B5A0CAF0", None),
        Some("STUConfigVarDynamic") => native_dynamic_operand(config),
        _ => native_expression_operand(config),
    }
}

fn explosion_direction(config: &Json) -> bool {
    let Some(class) = field(config, "$").as_str() else {
        return false;
    };
    match class {
        "STU_2200A66D" | "STU_309B0570" => {
            script_expr::native_fields(config, &["m_entity", "m_5B94EDB8"])
                && field(config, "m_5B94EDB8").is_null()
                && script_expr::native_shape(field(config, "m_entity"), "STU_B5A0CAF0", None)
        }
        "STUConfigVarDynamic" => native_dynamic_operand(config),
        _ => native_expression_operand(config),
    }
}

fn native_integer_operand(config: &Json) -> bool {
    script_expr::native_shape(config, "STUConfigVarInt", Some("m_value"))
        && field(config, "m_value")
            .as_i64()
            .is_some_and(|value| i32::try_from(value).is_ok())
}

fn explosion_float(config: &Json) -> bool {
    script_expr::native_shape(config, "STUConfigVarFloat", Some("m_value"))
        && field(config, "m_value")
            .as_f64()
            .is_some_and(|value| (value as f32).is_finite())
}

fn native_expression_operand(config: &Json) -> bool {
    let vars = field(config, "m_configVars");
    field(config, "$").as_str() == Some("STU_919FD47C")
        && script_expr::native_fields(config, &["m_expression", "m_configVars"])
        && field(config, "m_expression").is_object()
        && (vars.is_null() || vars.is_array())
}

fn native_dynamic_operand(config: &Json) -> bool {
    field(config, "$").as_str() == Some("STUConfigVarDynamic")
        && script_expr::native_fields(config, &["m_identifier", "m_60DB8F99"])
        && script_expr::dynamic(config).is_ok_and(|value| value.is_some())
}

pub(super) fn is_message_recipient_set(target: &Json) -> bool {
    field(target, "$").as_str() == Some("STU_9B213168")
}

pub(super) fn is_projected_self_message(graph: &Graph, node: &Node, state: u16) -> bool {
    let target = field(&node.fields, "m_target");
    let notification = field(&node.fields, "m_67FD6A60");
    node.is_networked()
        && node.is_state()
        && !node.is_action()
        && node.state == Some(state)
        && usize::try_from(node.remote)
            .ok()
            .and_then(|slot| graph.remote_node(slot))
            .is_some_and(|remote| remote.pos == node.pos)
        && node.literal_game_message_id().is_some()
        && script_expr::native_shape(target, "STU_9B213168", Some("m_targets"))
        && script_expr::native_shape(field(target, "m_targets"), "STU_B5A0CAF0", None)
        && (notification.as_u64() == Some(1) || notification.as_bool() == Some(true))
        && field(&node.fields, "m_targets").is_null()
        && field(&node.fields, "m_priority").is_null()
        && !configured(node, "m_AED90719")
        && node.valid_message_parameters(usize::MAX)
}

pub(super) fn persistent_message(graph: u32, node: &Node) -> Option<u64> {
    let message = node
        .field("m_gameMessage.m_gameMessage")
        .and_then(asset_guid)?;
    let fields = &node.fields;
    let target = field(fields, "m_target");
    if (graph, node.pos, message) == (0x0cc7, 39, RESULT_SUMMARY)
        || (graph, node.pos, message) == (0x0cc7, 82, RESULT_SUMMARY)
    {
        return (is_message_recipient_set(target)
            && script_expr::native_shape(field(target, "m_targets"), "STU_A3DEF368", None)
            && field(fields, "m_67FD6A60").as_u64() == Some(1)
            && field(fields, "m_priority").is_null())
        .then_some(message);
    }
    if (graph, node.pos, message) == (0x0cbc, 122, 0x0240_0000_0000_0744) {
        // The self-targeted receiver at 0D24:2 is client-only.
        return (is_message_recipient_set(target)
            && script_expr::native_shape(field(target, "m_targets"), "STU_B5A0CAF0", None)
            && field(fields, "m_67FD6A60").as_u64() == Some(1)
            && field(fields, "m_params").is_null()
            && field(fields, "m_priority").is_null())
        .then_some(message);
    }
    let notify = match (graph, node.pos, message) {
        (0x0c8c, 17 | 126, 0x0240_0000_0000_0694) | (0x0c8c, 83, 0x0240_0000_0000_06a8)
            if field(fields, "m_params").is_null() =>
        {
            1
        }
        (0x0c8c, 18, POST_MATCH_PHASE) => 1,
        (0x180a, 7, POST_MATCH_SIGNAL) if field(fields, "m_params").is_null() => 1,
        (0x0ce0, 15, BOSS_PAUSE) | (0x0c8c, 76 | 92, BOSS_PAUSE)
            if field(fields, "m_params").is_null() =>
        {
            1
        }
        (0x0ced, 9, 0x0240_0000_0000_0721) if field(fields, "m_params").is_null() => 1,
        (0x0ce0, 21, BOSS_CAMERA) => {
            let [focus, bone] = array_field(fields, "m_params") else {
                return None;
            };
            if message_parameter_id(focus) != 8
                || !script_expr::native_shape(field(focus, "m_value"), "STU_B5A0CAF0", None)
                || message_parameter_id(bone) != 0x3b6
                || !matches!(script_expr::dynamic(field(bone, "m_value")), Ok(Some(variable)) if variable.id == 8695)
            {
                return None;
            }
            0
        }
        _ => return None,
    };
    (field(target, "$").as_str() == Some("STU_1D719351")
        && field(target, "m_111E4A0E").as_u64() == Some(0)
        && field(target, "m_1199BFE0").as_u64() == Some(notify)
        && field(target, "m_6A8DB059").as_u64() == Some(1)
        && field(fields, "m_67FD6A60").as_u64() == Some(notify)
        && field(fields, "m_priority").is_null())
    .then_some(message)
}

pub(super) fn living_body_array_shape(node: &Node) -> bool {
    let filter = field(&node.fields, "m_filter");
    filter.get("$").and_then(Json::as_str) == Some("STUConfigVarFilter")
        && filter.get("m_84065FD1").and_then(Json::as_u64) == Some(0x3fff_ffff)
        && filter.get("m_4EA686DB").and_then(Json::as_u64) == Some(0x10)
        && filter.as_object().is_some_and(|fields| {
            fields.iter().all(|(name, value)| {
                matches!(name.as_str(), "$" | "m_84065FD1" | "m_4EA686DB")
                    || value.is_null()
                    || value.as_u64() == Some(0)
            })
        })
        && !configured(node, "m_17A88AEF")
        && !configured(node, "m_C485751C")
        && is_unlinked_plug(&node.fields, "m_A70DB860", "STUStatescriptOutputPlug")
        && matches!(
            script_expr::dynamic(field(&node.fields, "m_F2805C84")),
            Ok(Some(target)) if target.id != 0
        )
}

/// Whether the field is a plug of the given class with an empty link list.
pub(super) fn is_unlinked_plug(fields: &Json, name: &str, class: &str) -> bool {
    field(fields, name).as_object().is_some_and(|plug| {
        plug.len() == 2
            && plug.get("$").and_then(Json::as_str) == Some(class)
            && plug
                .get("links")
                .and_then(Json::as_array)
                .is_some_and(Vec::is_empty)
    })
}

/// The authored build 104319 shape of an action without a payload: the node
/// header with no remote sync variables and two unlinked plugs. Every
/// `STU_E9694EFD` node in the target build has exactly this shape.
pub(super) fn is_bare_header_action(node: &Node) -> bool {
    const FIELDS: [&str; 8] = [
        "m_2BBEEAB8",
        "m_8BF03679",
        "m_A2287776",
        "m_ADEB6E05",
        "m_AED90719",
        "m_BF5B22B7",
        "m_inPlug",
        "m_outPlug",
    ];
    let header = |name, value| field(&node.fields, name).as_u64() == Some(value);
    node.state.is_none()
        && node.remote < 0
        && !node.server_only
        && node.fields.as_object().is_some_and(|fields| {
            fields.len() == FIELDS.len() && FIELDS.iter().all(|name| fields.contains_key(*name))
        })
        && header("m_2BBEEAB8", 0)
        && header("m_ADEB6E05", 1)
        && header("m_A2287776", 0)
        && header("m_AED90719", 0)
        && field(&node.fields, "m_8BF03679").is_null()
        && field(&node.fields, "m_BF5B22B7").is_null()
        && is_unlinked_plug(&node.fields, "m_inPlug", "STUStatescriptInputPlug")
        && is_unlinked_plug(&node.fields, "m_outPlug", "STUStatescriptOutputPlug")
}

pub(super) fn is_horizontal_velocity_clamp(node: &Node) -> bool {
    let [row] = array_field(&node.fields, "m_BC5E91CF") else {
        return false;
    };
    is_local_velocity_action(node)
        && field(row, "$").as_str() == Some("STU_20E18047")
        && script_expr::native_fields(
            row,
            &["m_A9561CA0", "m_priority", "m_8AD739CE", "m_8213EB22"],
        )
        && field(row, "m_A9561CA0").as_u64() == Some(1)
        && field(row, "m_priority").is_null()
        && !field(row, "m_8AD739CE").is_null()
        && !field(row, "m_8213EB22").is_null()
}

pub(super) fn is_vertical_velocity_impulse(node: &Node) -> bool {
    let [reset, impulse] = array_field(&node.fields, "m_BC5E91CF") else {
        return false;
    };
    let is_row = |row: &Json, class: &str, selector: &str, index| {
        field(row, "$").as_str() == Some(class)
            && script_expr::native_fields(row, &[selector, "m_priority", "m_9B7A63EA"])
            && field(row, selector).as_u64() == Some(index)
            && field(row, "m_priority").is_null()
            && !field(row, "m_9B7A63EA").is_null()
    };
    is_local_velocity_action(node)
        && node.runs_on_server()
        && is_row(reset, "STU_9AF0FA80", "m_A9561CA0", 2)
        && is_row(impulse, "STU_4635C5E2", "m_CFEC5F76", 1)
}

/// Supported single-row edits of the local body's velocity.
pub(super) fn local_velocity_edit(node: &Node) -> Option<(&Json, bool)> {
    if !is_local_velocity_action(node) || !node.runs_on_server() {
        return None;
    }
    let [row] = array_field(&node.fields, "m_BC5E91CF") else {
        return None;
    };
    let (selector, cap) = match field(row, "$").as_str()? {
        "STU_95A10547" => ("m_CFEC5F76", false),
        "STU_748FC65A" => ("m_A9561CA0", true),
        _ => return None,
    };
    let value = field(row, "m_9B7A63EA");
    (script_expr::native_fields(row, &[selector, "m_priority", "m_9B7A63EA"])
        && field(row, selector).as_u64() == Some(1)
        && field(row, "m_priority").is_null()
        && !value.is_null())
    .then_some((value, cap))
}

fn is_local_velocity_action(node: &Node) -> bool {
    let fields = &node.fields;
    node.state.is_none()
        && node.remote == -1
        && node.is_action()
        && !node.is_state()
        && script_expr::native_fields(
            fields,
            &[
                "m_BF5B22B7",
                "m_8BF03679",
                "m_A2287776",
                "m_AED90719",
                "m_2BBEEAB8",
                "m_ADEB6E05",
                "m_BC5E91CF",
                "m_inPlug",
                "m_outPlug",
            ],
        )
        && field(fields, "m_BF5B22B7").is_null()
        && field(fields, "m_8BF03679").is_null()
        && ["m_A2287776", "m_AED90719"].iter().all(|name| {
            let value = field(fields, name);
            matches!(value, Json::Null | Json::Bool(false)) || value.as_u64() == Some(0)
        })
}

// These authored placements resolve to the PC base State.
pub(super) fn encounter_base_state_shape(graph: u32, node: &Node) -> Option<bool> {
    let placement = (graph, node.pos);
    let allowed = match node.class.as_str() {
        "STU_87621906" => placement == (0x0033, 30),
        "STU_0DB9E66B" => matches!(
            placement,
            (0x006b, 38) | (0x0079, 42) | (0x01c9, 65) | (0x224e, 25)
        ),
        "STU_200B27C5" => matches!(
            placement,
            (0x0aaa, 2) | (0x0bc6, 9) | (0x0cb5, 2) | (0x0cd2, 24 | 52) | (0x185d, 2 | 41 | 49)
        ),
        "STU_AF1A4047" => matches!(
            placement,
            (0x0aaa, 3) | (0x0bc6, 12) | (0x0cd2, 37) | (0x185d, 46)
        ),
        "STU_55F72184" => matches!(
            placement,
            (0x0aaa, 4) | (0x0bc6, 8) | (0x0cb5, 10) | (0x0cd2, 22 | 31) | (0x185d, 8 | 39)
        ),
        "STU_E834E718" => matches!(
            placement,
            (0x0aaa, 5) | (0x0bc6, 7) | (0x0cb5, 3) | (0x0cd2, 23 | 30) | (0x185d, 3 | 40)
        ),
        "STU_632B74B9" => matches!(
            placement,
            (0x0aaa, 10) | (0x0bc6, 6) | (0x0cb5, 16) | (0x0cd2, 18 | 53) | (0x185d, 23 | 35)
        ),
        "STU_D3CDCF04" => matches!(placement, (0x0aaa, 17 | 24) | (0x0cd2, 44 | 49)),
        "STU_D52B9026" => matches!(
            placement,
            (0x0aaa, 28) | (0x0bc6, 27) | (0x0cb5, 12) | (0x0cd2, 21) | (0x185d, 15 | 38)
        ),
        "STU_724DFBC6" => matches!(
            placement,
            (0x0aaa, 30)
                | (0x0bc6, 28)
                | (0x0cb5, 19 | 31)
                | (0x0cd2, 17 | 27 | 29 | 51)
                | (0x185d, 24 | 34)
        ),
        "STU_93088159" => matches!(
            placement,
            (0x0aaa, 33) | (0x0cba, 28) | (0x185d, 53) | (0x0ce0, 6 | 40) | (0x0d03, 8 | 10)
        ),
        "STU_E0D2F3AA" => matches!(placement, (0x0bc6, 14 | 25) | (0x0cd2, 38) | (0x185d, 31)),
        "STU_B17E88C9" => matches!(
            placement,
            (0x0cb5, 15) | (0x0cd2, 19 | 26) | (0x185d, 22 | 36)
        ),
        "STU_4531992F" => placement == (0x0cb5, 20),
        "STU_F4BBA185" => placement == (0x185d, 47),
        "STU_14611C1B" => matches!(placement, (0x0ce0, 42 | 43) | (0x0d03, 15 | 16)),
        _ => return None,
    };
    Some(!node.server_only && allowed)
}

pub(super) fn is_client_subgraph_controller(graph: &Graph, node: &Node) -> bool {
    // The controller is replicated, but its specialized branch starts on the client.
    const BRANCH: &str = "m_5B10D01F";
    let plug = field(&node.fields, BRANCH);
    node.is_networked()
        && field(plug, "$").as_str() == Some("STU_904BFCEC")
        && array_field(plug, "links").len() == 1
        && node
            .links(BRANCH)
            .next()
            .and_then(|(position, _)| graph.node(position?))
            .is_some_and(|target| target.client_only && !target.server_only)
}

pub(super) fn is_owner_movement_sampler(node: &Node) -> bool {
    node.is_networked()
        && node.remote == -1
        && [
            "m_entity",
            "m_A10CC74B",
            "m_0B1AA8CA",
            "m_4F8F2F3F",
            "m_8BF03679",
            "m_BF5B22B7",
        ]
        .iter()
        .all(|name| field(&node.fields, name).is_null())
        && MOVEMENT_BOOLEAN_OUTPUTS
            .iter()
            .all(|name| script_expr::lvalue(field(&node.fields, name)).is_ok())
}

pub(super) fn is_networked_for_code_synced(node: &Node) -> bool {
    node.is_networked()
        && node.remote >= 0
        && field(&node.fields, "m_DC897C87").as_u64() == Some(1)
        && field(&node.fields, "m_598E32D5").as_u64() == Some(0)
        && field(&node.fields, "m_0582B0E4").as_u64() == Some(0)
        && field(&node.fields, "m_0B1AA8CA").is_null()
        && field(&node.fields, "m_values")
            .as_array()
            .is_some_and(|values| {
                !values.is_empty()
                    && values.iter().all(|value| {
                        field(value, "$").as_str() == Some("STUStatescriptForCodeValue")
                            && field(field(value, "m_value"), "$").as_str().is_some()
                    })
            })
}

pub(super) fn is_base_health_pool(node: &Node) -> bool {
    let config = |name| field(&node.fields, name);
    let is_owner = |value| script_expr::native_shape(value, "STU_B5A0CAF0", None);
    let flag = |name, expected| {
        let value = config(name);
        script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
            && match field(value, "m_value") {
                Json::Bool(flag) => *flag == expected,
                Json::Number(flag) => flag.as_u64() == Some(u64::from(expected)),
                _ => false,
            }
    };
    let source = config("m_69A20070");
    node.is_networked()
        && [
            "m_7BE02F89",
            "m_C99FAFDD",
            "m_FF79E783",
            "m_AF1104D0",
            "m_BFA825EA",
        ]
        .into_iter()
        .all(|name| config(name).is_null())
        && pool_kind(config("m_7E8B666C")).is_some()
        && is_owner(config("m_FA3ECC43"))
        && script_expr::native_shape(source, "STU_FEDD683C", Some("m_entity"))
        && is_owner(field(source, "m_entity"))
        && flag("m_4895B8B6", true)
        && flag("m_64839023", false)
        && flag("m_0E8A5C8E", false)
        && (config("m_CEAB19E8").is_null()
            || matches!(script_expr::lvalue(config("m_CEAB19E8")), Ok(Some(_))))
}

pub(super) fn named_temporary_shield(node: &Node, raw: u16) -> Option<u64> {
    let config = |name| field(&node.fields, name);
    let flag = |name, enabled| {
        let value = config(name);
        script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
            && (field(value, "m_value").as_bool() == Some(enabled)
                || field(value, "m_value").as_u64() == Some(u64::from(enabled)))
    };
    let source = config("m_69A20070");
    if !node.is_networked()
        || node.state != Some(raw)
        || !node.is_state()
        || node.is_action()
        || pool_kind(config("m_7E8B666C")) != Some(PoolKind::Shields)
        || !flag("m_0E8A5C8E", true)
        || !flag("m_64839023", false)
        || !is_self_target(node, "m_FA3ECC43")
        || !script_expr::native_shape(source, "STU_FEDD683C", Some("m_entity"))
        || !script_expr::native_shape(field(source, "m_entity"), "STU_B5A0CAF0", None)
        || ![
            "m_BCFDD2E9",
            "m_4895B8B6",
            "m_FF79E783",
            "m_AF1104D0",
            "m_BFA825EA",
        ]
        .into_iter()
        .all(|name| config(name).is_null())
        || !["m_CEAB19E8", "m_C99FAFDD"].into_iter().all(|name| {
            config(name).is_null()
                || matches!(
                    script_expr::lvalue(config(name)),
                    Ok(Some(Variable {
                        scope: Scope::Instance,
                        ..
                    }))
                )
        })
        || !node.fields.as_object()?.iter().all(|(name, value)| {
            value.is_null()
                || matches!(
                    name.as_str(),
                    "m_FA3ECC43"
                        | "m_69A20070"
                        | "m_7E8B666C"
                        | "m_7BE02F89"
                        | "m_0E8A5C8E"
                        | "m_64839023"
                        | "m_amount"
                        | "m_CEAB19E8"
                        | "m_C99FAFDD"
                        | "m_A2287776"
                        | "m_AED90719"
                        | "m_2BBEEAB8"
                        | "m_ADEB6E05"
                        | "m_8BF03679"
                        | "m_BF5B22B7"
                        | "m_F198FD3A"
                        | "m_beginPlug"
                        | "m_onBeginPlug"
                        | "m_onEndPlug"
                        | "m_onAbortPlug"
                        | "m_onFinishedPlug"
                        | "m_subgraphPlug"
                )
        })
    {
        return None;
    }
    script_expr::named_shield_identifier(config("m_7BE02F89"))
}

pub(super) fn is_wave_health_pool(node: &Node) -> bool {
    let config = |name| field(&node.fields, name);
    let amount = config("m_amount");
    let source = field(amount, "m_814F8EC3");
    let pool_source = config("m_69A20070");
    matches!(node.pos, 1 | 4 | 5 | 26 | 43 | 113)
        && node.is_networked()
        && pool_kind(config("m_7E8B666C")) == Some(PoolKind::Health)
        && script_expr::native_shape(pool_source, "STU_FEDD683C", Some("m_entity"))
        && script_expr::native_shape(field(pool_source, "m_entity"), "STU_B5A0CAF0", None)
        && ["m_64839023", "m_0E8A5C8E"].into_iter().all(|name| {
            let value = config(name);
            script_expr::native_shape(value, "STUConfigVarBool", Some("m_value"))
                && (matches!(field(value, "m_value"), Json::Bool(false))
                    || field(value, "m_value").as_u64() == Some(0))
        })
        && [
            "m_7BE02F89",
            "m_BCFDD2E9",
            "m_4895B8B6",
            "m_CEAB19E8",
            "m_C99FAFDD",
            "m_FF79E783",
            "m_AF1104D0",
            "m_BFA825EA",
        ]
        .into_iter()
        .all(|name| config(name).is_null())
        && field(amount, "$").as_str() == Some("STU_4BD39CFD")
        && script_expr::native_fields(amount, &["m_814F8EC3", "m_index"])
        && script_expr::dynamic(field(amount, "m_index"))
            == Ok(Some(Variable {
                scope: Scope::Instance,
                id: 9948,
            }))
        && script_expr::native_shape(source, "STU_06610EBC", Some("m_configVars"))
        && array_field(source, "m_configVars").len() == 4
        && array_field(source, "m_configVars").iter().all(|value| {
            script_expr::native_shape(value, "STUConfigVarInt", Some("m_value"))
                && field(value, "m_value").as_u64().is_some()
        })
}

pub(super) fn pool_kind(value: &Json) -> Option<PoolKind> {
    match value.as_u64()? {
        0 => Some(PoolKind::Health),
        1 => Some(PoolKind::Armour),
        2 => Some(PoolKind::Shields),
        _ => None,
    }
}

pub(super) fn pool_number(value: Value) -> Option<f64> {
    match value {
        Value::Float(amount) => Some(amount),
        Value::Integer(amount) => Some(amount as f64),
        _ => None,
    }
}

pub(super) fn ability_is_stacked(node: &Node) -> bool {
    !field(&node.fields, "m_out_StackVar").is_null() && !field(&node.fields, "m_5E561E77").is_null()
}

pub(super) fn configured(node: &Node, name: &str) -> bool {
    match field(&node.fields, name) {
        Json::Null | Json::Bool(false) => false,
        Json::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Json::String(value) => !value.is_empty(),
        Json::Array(value) => !value.is_empty(),
        Json::Object(value) => !value.is_empty(),
        Json::Bool(true) => true,
    }
}

pub(super) fn field<'a>(fields: &'a Json, name: &str) -> &'a Json {
    fields.get(name).unwrap_or(&Json::Null)
}

pub(super) fn array_field<'a>(fields: &'a Json, name: &str) -> &'a [Json] {
    field(fields, name).as_array().map_or(&[], Vec::as_slice)
}

pub(super) fn raw_indices(values: &[Json]) -> impl Iterator<Item = u16> + '_ {
    values
        .iter()
        .filter_map(|value| u16::try_from(value.as_u64()?).ok())
}

pub(super) fn case_path(case: Option<usize>) -> String {
    case.map_or_else(
        || "m_A5EF385D".into(),
        |index| format!("m_05706D1C[{index}]"),
    )
}

pub(super) fn branch_path(branch: bool) -> &'static str {
    if branch {
        "m_trueSubgraphPlug"
    } else {
        "m_falseSubgraphPlug"
    }
}

pub(super) fn array_insertion_index(mut value: &Value) -> i32 {
    while let Value::Array(items) = value {
        let Some(first) = items.first() else {
            return -1;
        };
        value = first;
    }
    match value {
        Value::Null => 0,
        Value::Bool(value) => i32::from(*value),
        Value::Integer(value) => *value as i32,
        Value::Float(value) => {
            let value = *value as f32;
            let rounded = (if value < 0.0 {
                value - 0.5
            } else {
                value + 0.5
            })
            .trunc();
            if rounded.is_finite() && (-2147483648.0..2147483648.0).contains(&f64::from(rounded)) {
                rounded as i32
            } else {
                i32::MIN
            }
        }
        _ => -1,
    }
}

pub(super) fn same(a: &Value, b: &Value) -> bool {
    a == b || matches!((a, b), (Value::Float(a), Value::Float(b)) if a.is_nan() && b.is_nan())
}

pub(super) fn source_key(instance: u16, variable: Variable) -> VarKey {
    match variable.scope {
        Scope::Instance => VarKey::Instance(instance, variable.id),
        Scope::Entity => VarKey::Entity(variable.id),
    }
}

pub(super) fn stored(snapshot: &Snapshot, key: VarKey) -> Value {
    match key {
        VarKey::Entity(id) => snapshot.variables.get(&id),
        VarKey::Instance(instance, id) => snapshot
            .instances
            .get(&instance)
            .and_then(|instance| instance.variables.get(&id)),
    }
    .cloned()
    .unwrap_or(Value::Null)
}

pub(super) fn shot_delay(ordinal: u32, rate: f64) -> u64 {
    ((f64::from(ordinal) / rate) as f32 * 1000.0).round() as u64
}

pub(super) fn chase_value(value: &Value) -> Value {
    match value {
        Value::Vector(_) => value.clone(),
        _ => Value::Float(script_expr::to_float(value)),
    }
}

pub(super) fn chase_vector(value: Value) -> [f64; 3] {
    match value {
        Value::Vector(vector) => vector,
        _ => [script_expr::to_float(&value), 0.0, 0.0],
    }
}

pub(super) fn chase_lerp(current: Value, destination: Value, share: f64) -> Value {
    let interpolate = |from: f64, to: f64| f64::from((from + (to - from) * share) as f32);
    if let (Value::Float(current), Value::Float(destination)) = (&current, &destination) {
        return Value::Float(interpolate(*current, *destination));
    }
    let current = chase_vector(current);
    let destination = chase_vector(destination);
    Value::Vector(std::array::from_fn(|axis| {
        interpolate(current[axis], destination[axis])
    }))
}

pub(super) fn chase_step(current: Value, destination: Value, step: f64) -> (Value, bool) {
    if let (Value::Float(current), Value::Float(destination)) = (&current, &destination) {
        let delta = f64::from((*destination - *current) as f32);
        return if delta.abs() <= step {
            (Value::Float(*destination), true)
        } else {
            let next = *current + if delta > 0.0 { step } else { -step };
            (Value::Float(f64::from(next as f32)), false)
        };
    }
    let current = chase_vector(current);
    let destination = chase_vector(destination);
    let delta: [f64; 3] =
        std::array::from_fn(|axis| f64::from((destination[axis] - current[axis]) as f32));
    let length = f64::from((delta.iter().map(|value| value * value).sum::<f64>().sqrt()) as f32);
    if length <= step {
        (Value::Vector(destination), true)
    } else {
        let next = std::array::from_fn(|axis| {
            f64::from((current[axis] + delta[axis] * step / length) as f32)
        });
        (Value::Vector(next), false)
    }
}

pub(super) fn is_parent_owned_timed_graph(node: &Node) -> bool {
    field(&node.fields, "m_ED97A97C").is_null() && is_parent_owned_graph(node)
}

pub(super) fn parent_owned_graph_duration(node: &Node) -> Option<&Json> {
    if node.class != "STU_54FEB21C" {
        return None;
    }
    let duration = field(&node.fields, "m_ED97A97C");
    (duration.get("$").and_then(Json::as_str) == Some("STUConfigVarFloat")
        && duration
            .get("m_value")
            .and_then(Json::as_f64)
            .is_some_and(f64::is_finite)
        && is_parent_owned_graph(node))
    .then_some(duration)
}

fn is_parent_owned_graph(node: &Node) -> bool {
    let has_no_finish_operand = ["m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5"]
        .iter()
        .all(|name| field(&node.fields, name).is_null());
    let alternate = field(&node.fields, "m_E5790891");
    let has_no_alternate = alternate.is_null()
        || (alternate.is_object()
            && matches!(
                alternate.get("$").and_then(Json::as_str),
                Some("Plug" | "STUStatescriptOutputPlug" | "STU_904BFCEC")
            )
            && alternate.as_object().is_some_and(|fields| {
                fields
                    .keys()
                    .all(|name| matches!(name.as_str(), "$" | "links" | "m_FF3DAF1E"))
            })
            && alternate
                .get("links")
                .is_some_and(|value| value.as_array().is_some_and(Vec::is_empty))
            && alternate.get("m_FF3DAF1E").is_none_or(|value| {
                value.is_null() || value.as_array().is_some_and(Vec::is_empty)
            }));
    has_no_finish_operand && has_no_alternate
}
