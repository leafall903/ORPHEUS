//! State graph metadata for retail 1.74 and its owner/remote wire tables.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{self, BufReader, Read},
    num::NonZeroU32,
    path::Path,
    sync::Arc,
};

use flate2::bufread::MultiGzDecoder;
pub use orpheus_runtime::format::asset_guid;
use orpheus_runtime::format::{
    MAX_GRAPH_BYTES, ModeRulesets, RuleValue, TARGET_TAG_TYPE, parse_guid,
};
use serde::Deserialize;
use serde_json::{Map, Value};

use crate::{
    script_expr::{
        EngineSetting, Scope, StackPriority, Variable, authored_stack_priority,
        authored_stack_variable, client_only_fallback, dynamic, native_entity_operand,
        native_fields, native_shape, native_target_tag_operand,
    },
    script_init::{RoomCue, RoomCueSpeaker},
    script_wire::{Value as ScriptValue, Variables},
};

mod charge_compatibility;
mod view_targets;

use charge_compatibility::validate_charge_compatibility;
pub use charge_compatibility::{ChargeCompatibility, ChargeStateBinding};

const GRAPH_TYPE: u64 = 0x0580;
pub(crate) const MAX_ROOM_MESSAGE_PARAMETERS: usize = 16;

#[derive(Debug, Deserialize)]
pub struct Header {
    #[serde(rename = "m_nodesBitCount")]
    pub nodes_bits: u8,
    #[serde(rename = "m_statesBitCount")]
    pub states_bits: u8,
    #[serde(rename = "m_remoteSyncNodesBitCount")]
    pub remote_bits: u8,
    #[serde(rename = "m_syncVarsBitCount")]
    pub sync_vars_bits: u8,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum StateBehavior {
    Plain,
    Observe,
    Interval,
    ButtonLock,
    HeldInput,
    MovementScale,
    StackClaim,
    FacingClaim,
    TrackEntities,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum ActionBehavior {
    Forward,
    Branch,
    LocalMessage,
    RoomMessage,
    RoomCue,
    HealthChange,
    RadialHealth,
    SweptDisplacement,
    Relocation,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd)]
#[serde(rename_all = "snake_case")]
pub(crate) enum HostEventKind {
    HealthApplied,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) enum EntryTopic {
    Message(u64),
    Host(HostEventKind),
}

#[derive(Clone, Copy, Debug)]
enum DirectedNumericRoute {
    Sender(u32),
    Receiver(u32),
}

#[derive(Debug, Deserialize)]
pub struct Node {
    pub pos: u16,
    pub class: String,
    pub uid: u32,
    pub rid: u16,
    pub remote: i32,
    pub state: Option<u16>,
    state_behavior: Option<StateBehavior>,
    action_behavior: Option<ActionBehavior>,
    #[serde(default)]
    room_message_entry: bool,
    host_event_entry: Option<HostEventKind>,
    #[serde(deserialize_with = "node_flag")]
    pub client_only: bool,
    #[serde(deserialize_with = "node_flag")]
    pub server_only: bool,
    #[serde(skip)]
    runtime_plain_state: bool,
    #[serde(skip)]
    runtime_client_state: bool,
    #[serde(skip)]
    directed_numeric_route: Option<DirectedNumericRoute>,
    pub fields: Value,
}

impl Node {
    /// Whether the node can run on the server.
    pub(crate) fn runs_on_server(&self) -> bool {
        !self.client_only && !self.runtime_client_state
    }

    pub fn is_networked(&self) -> bool {
        !self.client_only && !self.server_only
    }

    pub(crate) fn frame_action_variables(&self) -> Option<Vec<Variable>> {
        let rows = self.fields.get("m_BF5B22B7").unwrap_or(&Value::Null);
        if rows.is_null() {
            return Some(Vec::new());
        }
        rows.as_array()?
            .iter()
            .map(|row| {
                if row["$"] != "STUStatescriptRemoteSyncVar"
                    || !native_fields(row, &["m_0D09D2D9", "m_56341592"])
                {
                    return None;
                }
                let guid = asset_guid(&row["m_0D09D2D9"])?;
                let id = u32::from(guid as u16);
                if guid >> 48 != 0x0d80 || id == 0 {
                    return None;
                }
                let scope = match row["m_56341592"].as_u64()? {
                    0 => Scope::Instance,
                    1 => Scope::Entity,
                    _ => return None,
                };
                Some(Variable { id, scope })
            })
            .collect()
    }

    pub(crate) fn contact_effect_resource(&self) -> Option<u64> {
        let fields = &self.fields;
        let resource = &fields["m_contactSet"];
        if self.class != "STU_23A00A53"
            || !self.is_networked()
            || self.remote < 0
            || !native_shape(&fields["m_63CB0D07"], "STU_B5A0CAF0", None)
            || resource["$"] != "STUConfigVarContactSet"
            || !native_fields(resource, &["m_contactSet", "m_resourceKey"])
            || !resource["m_resourceKey"].is_null()
            || !(fields["m_6A3C1510"].is_null() || fields["m_6A3C1510"].as_u64() == Some(0))
            || !fields["m_ignoreEntities"].is_null()
        {
            return None;
        }
        let target = dynamic(&fields["m_53FCEDBF"]).ok()??;
        if target.scope != Scope::Instance || !self.frame_action_variables()?.contains(&target) {
            return None;
        }
        asset_guid(&resource["m_contactSet"])
            .filter(|&guid| guid >> 48 == 0x0120 && guid & 0x0000_ffff_ffff_ffff != 0)
    }

    pub(crate) fn tracks_ground_contact(&self) -> bool {
        let f = &self.fields;
        let segment = &f["m_F5732403"];
        let filter = &f["m_filter"];
        let target = &f["m_target"];
        let configured_fields = |config: &Value, supported: &[&str]| {
            config.as_object().is_some_and(|fields| {
                fields.iter().all(|(name, value)| {
                    name == "$"
                        || supported.contains(&name.as_str())
                        || value.is_null()
                        || value.as_u64() == Some(0)
                })
            })
        };
        self.class == "STUStatescriptStateTrackRay"
            && segment["$"] == "STU_9E9028E6"
            && native_shape(&f["m_3BC31C57"], "STU_8CBDD81C", Some("m_entity"))
            && native_shape(&f["m_3BC31C57"]["m_entity"], "STU_B5A0CAF0", None)
            && filter["$"] == "STUConfigVarFilter"
            && configured_fields(filter, &["m_C1903771"])
            && asset_guid(&filter["m_C1903771"]["m_C1903771"]) == Some(0x0690_0000_0000_002c)
            && f["m_57DAE1B5"].is_null()
            && f["m_ignoreEntities"].is_null()
            && target["$"] == "STU_404DE6D3"
            && configured_fields(target, &["m_5ED803CF", "m_7BA44A69"])
            && ["m_5ED803CF", "m_7BA44A69"]
                .into_iter()
                .all(|name| crate::script_expr::lvalue(&target[name]).is_ok_and(|v| v.is_some()))
    }

    pub fn is_state(&self) -> bool {
        self.state_behavior.is_some() || self.fields.get("m_2BBEEAB8").is_some_and(truthy)
    }

    pub(crate) fn is_ai_weapon_fire(&self) -> bool {
        self.class == "STU_D7F9E1C3"
            && self.field("m_234135C3").is_none_or(Value::is_null)
            && self.runs_on_server()
    }

    /// Whether authored metadata opts this node into the ordinary state lifecycle.
    pub fn declares_plain_state(&self) -> bool {
        matches!(self.state_behavior, Some(StateBehavior::Plain))
    }

    pub(crate) fn declares_runtime_plain_state(&self) -> bool {
        self.runtime_plain_state
    }

    pub(crate) fn declares_observe_state(&self) -> bool {
        matches!(self.state_behavior, Some(StateBehavior::Observe))
    }

    pub(crate) fn interval_frames(&self) -> Option<NonZeroU32> {
        if self.state_behavior != Some(StateBehavior::Interval) {
            return None;
        }
        let frames = self.field("interval_frames")?.as_u64()?;
        NonZeroU32::new(u32::try_from(frames).ok()?)
    }

    pub(crate) fn button_lock(&self) -> Option<(u8, Option<NonZeroU32>)> {
        if self.state_behavior != Some(StateBehavior::ButtonLock) {
            return None;
        }
        let button = u8::try_from(self.field("button")?.as_u64()?).ok()?;
        if button == 0 {
            return None;
        }
        Some((button, self.optional_duration_frames()?))
    }

    pub(crate) fn movement_scale(&self) -> Option<(f64, Option<NonZeroU32>)> {
        if self.state_behavior != Some(StateBehavior::MovementScale) {
            return None;
        }
        let factor = self.field("factor")?.as_f64()?;
        if !(0.0..=1.0).contains(&factor) {
            return None;
        }
        Some((factor, self.optional_duration_frames()?))
    }

    /// Whether authored metadata declares a self-facing claim.
    pub fn declares_facing_claim(&self) -> bool {
        self.state_behavior == Some(StateBehavior::FacingClaim)
    }

    pub(crate) fn facing_claim_priority(&self) -> Option<StackPriority> {
        if !self.declares_facing_claim() {
            return None;
        }
        authored_stack_priority(self.field("priority")?).ok()
    }

    pub(crate) fn declares_stack_claim(&self) -> bool {
        self.state_behavior == Some(StateBehavior::StackClaim)
    }

    pub(crate) fn stack_claim(&self) -> Option<(Variable, StackPriority, Option<NonZeroU32>)> {
        if !self.declares_stack_claim() {
            return None;
        }
        let variable = authored_stack_variable(self.field("variable")?).ok()?;
        let priority = authored_stack_priority(self.field("priority")?).ok()?;
        Some((variable, priority, self.optional_duration_frames()?))
    }

    pub(crate) fn stack_value(&self) -> Option<&Value> {
        if self.declares_stack_claim() {
            self.field("value")
        } else {
            self.field("m_value")
        }
    }

    fn optional_duration_frames(&self) -> Option<Option<NonZeroU32>> {
        match self.field("duration_frames") {
            Some(value) => NonZeroU32::new(u32::try_from(value.as_u64()?).ok()?).map(Some),
            None => Some(None),
        }
    }

    pub(crate) fn target_child_binding(&self) -> Option<(u32, u32)> {
        if self.state_behavior != Some(StateBehavior::TrackEntities) {
            return None;
        }
        let graph = u32::try_from(self.field("child_graph")?.as_u64()?).ok()?;
        let variable = u32::try_from(self.field("target_variable")?.as_u64()?).ok()?;
        (variable != 0).then_some((graph, variable))
    }

    pub(crate) fn tracks_room_actor_identity(&self) -> bool {
        self.state_behavior == Some(StateBehavior::TrackEntities)
            && self.field("identity").and_then(Value::as_str) == Some("room_actor")
    }

    pub fn is_action(&self) -> bool {
        self.action_behavior.is_some() || self.fields.get("m_ADEB6E05").is_some_and(truthy)
    }

    pub(crate) fn declares_forward_action(&self) -> bool {
        matches!(self.action_behavior, Some(ActionBehavior::Forward))
    }

    pub(crate) fn declares_branch_action(&self) -> bool {
        matches!(self.action_behavior, Some(ActionBehavior::Branch))
    }

    pub(crate) fn declares_room_message_action(&self) -> bool {
        self.action_behavior == Some(ActionBehavior::RoomMessage)
    }

    pub(crate) fn declares_room_cue_action(&self) -> bool {
        self.action_behavior == Some(ActionBehavior::RoomCue)
    }

    pub(crate) fn room_cue(&self) -> Option<RoomCue> {
        if !self.declares_room_cue_action() {
            return None;
        }
        let stimulus = asset_guid(self.field("stimulus")?)?;
        let line = asset_guid(self.field("line")?)?;
        let speaker = match self.field("speaker") {
            None => RoomCueSpeaker::Actor,
            Some(value) => match value.as_str()? {
                "actor" => RoomCueSpeaker::Actor,
                "mode" => RoomCueSpeaker::Mode,
                _ => return None,
            },
        };
        (stimulus != 0 && line != 0).then_some(RoomCue {
            stimulus,
            line,
            speaker,
        })
    }

    pub(crate) fn declares_room_message_entry(&self) -> bool {
        self.room_message_entry
    }

    pub(crate) fn directed_numeric_parameter(&self) -> Option<u32> {
        match self.directed_numeric_route {
            Some(DirectedNumericRoute::Sender(slot)) => Some(slot),
            _ => None,
        }
    }

    pub(crate) fn accepts_directed_numeric_message(&self, parameters: &Variables) -> bool {
        matches!(
            self.directed_numeric_route,
            Some(DirectedNumericRoute::Receiver(slot)) if directed_numeric_payload(slot, parameters)
        )
    }

    pub(crate) fn host_event_kind(&self) -> Option<HostEventKind> {
        self.host_event_entry
    }

    fn valid_host_event_parameters(&self, kind: HostEventKind) -> bool {
        let max_slot = match kind {
            HostEventKind::HealthApplied => 8,
        };
        let Some(parameters) = self.fields.get("m_params").filter(|value| !value.is_null()) else {
            return true;
        };
        let mut bound = BTreeSet::new();
        parameters.as_array().is_some_and(|parameters| {
            parameters.len() <= max_slot as usize
                && parameters.iter().all(|parameter| {
                    let slot = message_parameter_id(parameter);
                    (1..=max_slot).contains(&slot)
                        && bound.insert(slot)
                        && parameter.get("m_out_Var").is_some_and(|output| {
                            crate::script_expr::lvalue(output)
                                .ok()
                                .flatten()
                                .is_some_and(|variable| variable.id != 0)
                        })
                })
        })
    }

    pub(crate) fn declares_health_change_action(&self) -> bool {
        matches!(self.action_behavior, Some(ActionBehavior::HealthChange))
    }

    pub(crate) fn declares_swept_displacement_action(&self) -> bool {
        self.action_behavior == Some(ActionBehavior::SweptDisplacement)
    }

    pub(crate) fn declares_relocation_action(&self) -> bool {
        self.action_behavior == Some(ActionBehavior::Relocation)
    }

    pub(crate) fn declares_radial_health_action(&self) -> bool {
        matches!(self.action_behavior, Some(ActionBehavior::RadialHealth))
    }

    pub(crate) fn game_message_id(&self) -> Option<u64> {
        self.field("m_gameMessage.m_gameMessage")
            .and_then(asset_guid)
    }

    pub(crate) fn literal_game_message_id(&self) -> Option<u64> {
        let message = self.field("m_gameMessage")?;
        if !native_fields(message, &["m_gameMessage", "m_resourceKey"])
            || message
                .get("$")
                .is_some_and(|class| class.as_str() != Some("STUConfigVarGameMessage"))
            || message
                .get("m_resourceKey")
                .is_some_and(|key| !key.is_null())
        {
            return None;
        }
        self.game_message_id().filter(|&id| id != 0)
    }

    pub(crate) fn is_message_entry(&self) -> bool {
        self.class == "STUStatescriptEntryGameMessage"
    }

    fn valid_local_message(&self) -> bool {
        self.game_message_id().is_some_and(|message| message != 0)
            && self.fields.get("m_targets").is_none_or(Value::is_null)
            && self.fields.get("m_target").is_none_or(Value::is_null)
            && self.valid_message_parameters(usize::MAX)
    }

    fn valid_room_message(&self) -> bool {
        self.game_message_id().is_some_and(|message| message != 0)
            && self.fields.get("m_targets").is_none()
            && self
                .fields
                .get("m_target")
                .is_some_and(|target| !target.is_null())
            && self.valid_message_parameters(MAX_ROOM_MESSAGE_PARAMETERS)
    }

    pub(crate) fn valid_message_parameters(&self, max_parameters: usize) -> bool {
        let Some(parameters) = self.fields.get("m_params").filter(|value| !value.is_null()) else {
            return true;
        };
        let mut slots = BTreeSet::new();
        parameters.as_array().is_some_and(|parameters| {
            parameters.len() <= max_parameters
                && parameters.iter().all(|parameter| {
                    let slot = message_parameter_id(parameter);
                    slot != 0 && parameter.get("m_value").is_some() && slots.insert(slot)
                })
        })
    }

    pub fn field(&self, path: &str) -> Option<&Value> {
        field_at(&self.fields, path)
    }

    /// The one-shot hostile overlap with an owner-relative centre and static sight.
    pub(crate) fn contact_volume_bindings(
        &self,
    ) -> Option<(
        crate::script_expr::Variable,
        crate::script_expr::Variable,
        u64,
    )> {
        let fields = &self.fields;
        let filter = &fields["m_filter"];
        let sight = &fields["m_3D667CE3"];
        let cast = &sight["m_C1903771"];
        let profile = cast.get("m_C1903771").and_then(asset_guid)?;
        if self.class != "STUStatescriptStateTrackTargets"
            || fields["m_DD792FF0"].as_u64().unwrap_or(0) != 0
            || fields["m_95FFC3C5"].as_u64() != Some(1)
            || fields["m_radius"].is_null()
            || fields["m_05BE62DD"].is_null()
            || !native_shape(&fields["m_8DCA77B6"], "STU_B5A0CAF0", None)
            || !native_shape(&fields["m_D59188D5"], "STU_21A13145", Some("m_entity"))
            || filter["$"] != "STUConfigVarFilter"
            || filter["m_84065FD1"].as_u64() != Some(64)
            || filter["m_4EA686DB"].as_u64() != Some(20)
            || filter["m_89831602"].as_u64() != Some(1)
            || sight["$"] != "STUConfigVarFilter"
            || cast["$"] != "STU_20DB8AB5"
            || profile >> 48 != 0x0690
            || !filter.as_object()?.iter().all(|(key, value)| {
                matches!(
                    key.as_str(),
                    "$" | "m_84065FD1" | "m_4EA686DB" | "m_89831602"
                ) || value.is_null()
                    || value.as_u64() == Some(0)
            })
        {
            return None;
        }
        let target = crate::script_expr::lvalue(&fields["m_8A2905A0"]).ok()??;
        let paired = crate::script_expr::lvalue(&fields["m_24A4AB8A"]).ok()??;
        (target != paired).then_some((target, paired, profile))
    }

    pub(crate) fn subscript_graph_asset(&self) -> Option<u64> {
        self.field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph")
            .and_then(asset_guid)
    }

    /// The plug's targets in data order, including unresolved targets.
    pub fn links(&self, path: &str) -> impl Iterator<Item = (Option<u16>, &str)> {
        self.field(path)
            .into_iter()
            .flat_map(raw_plug_links)
            .filter_map(decode_link)
    }

    pub fn plugs(&self) -> BTreeMap<String, &Value> {
        let mut found = BTreeMap::new();
        collect_plugs(&self.fields, "", &mut found);
        found
    }
}

/// The native directed subset carries exactly one finite numeric value.
pub(crate) fn directed_numeric_payload(slot: u32, parameters: &Variables) -> bool {
    parameters.len() == 1
        && match parameters.get(&slot) {
            Some(ScriptValue::Integer(_)) => true,
            Some(ScriptValue::Float(value)) => value.is_finite(),
            _ => false,
        }
}

fn directed_variable(value: &Value, scope: Scope) -> bool {
    let scope_field = &value["m_60DB8F99"];
    let valid_scope = match scope {
        Scope::Instance => neutral_flag(scope_field),
        Scope::Entity => scope_field.as_bool() == Some(true) || scope_field.as_u64() == Some(1),
    };
    native_fields(value, &["m_identifier", "m_60DB8F99"])
        && valid_scope
        && dynamic(value).is_ok_and(|var| var.is_some_and(|var| var.scope == scope && var.id != 0))
}

fn neutral_flag(value: &Value) -> bool {
    value.is_null() || value.as_bool() == Some(false) || value.as_u64() == Some(0)
}

fn raw_plug_links(field: &Value) -> impl Iterator<Item = &Value> {
    field
        .get("$")
        .and_then(|_| field.get("links")?.as_array())
        .into_iter()
        .flatten()
}

fn valid_output_plug(plug: &Value) -> bool {
    plug.get("$").and_then(Value::as_str).is_some()
        && plug
            .get("links")
            .and_then(Value::as_array)
            .is_some_and(|links| links.iter().all(|link| decode_link(link).is_some()))
}

pub(crate) fn decode_link(link: &Value) -> Option<(Option<u16>, &str)> {
    let pair = link.as_array()?;
    let position = match pair.first()? {
        Value::Null => None,
        value => Some(u16::try_from(value.as_u64()?).ok()?),
    };
    Some((position, pair.get(1)?.as_str()?))
}

/// A decoded link to a position absent from both the node and container tables.
#[derive(Debug, Eq, PartialEq)]
pub struct DanglingLink {
    pub graph: u32,
    pub source: u16,
    pub plug: String,
    /// Zero-based position in the plug's original link list.
    pub link_index: usize,
    pub target: u16,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub struct SyncVar(pub Option<u32>, pub u8, pub u8);

impl SyncVar {
    pub fn has_presence_bit(self) -> bool {
        self.0.is_some() && self.1 == 0
    }
}

#[derive(Debug, Default)]
struct WireSlots {
    by_list_index: Vec<Option<usize>>,
    positions: Vec<u16>,
}

impl WireSlots {
    fn build(order: &[Option<u16>], graph: &Graph, require_state: bool) -> Self {
        let mut positions = Vec::new();
        let mut first_slot = vec![None; graph.by_pos.len()];
        let by_list_index = order
            .iter()
            .map(|&position| {
                let node = graph.node(position?)?;
                if !node.is_networked() || (require_state && !node.is_state()) {
                    return None;
                }
                let slot = *first_slot[usize::from(node.pos)].get_or_insert(positions.len());
                positions.push(node.pos);
                Some(slot)
            })
            .collect();
        Self {
            by_list_index,
            positions,
        }
    }
}

/// A subgraph box item (STUStatescriptSubGraph / STUGraphContainer)
/// extracted from `m_items`. States point at their box through the
/// `m_0B1AA8CA: {"item": position}` field, so an item reference with no
/// entry here is a broken bundle. The reference extractor records the
/// class tag only; the optional `links` carry the box's begin edges and
/// make a taken edge into the box route through it (a tag-only box keeps
/// the missing-node report, because the launch semantics were dropped at
/// extraction).
#[derive(Debug, Clone)]
pub struct Container {
    pub class: String,
    /// Raw plug links (same encoding as any plug's `links` array), or empty.
    pub links: Vec<Value>,
}

struct ContainerVisitor;

impl<'de> serde::de::Visitor<'de> for ContainerVisitor {
    type Value = Container;
    fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("a container class tag or a container with begin links")
    }
    fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Container, E> {
        Ok(Container {
            class: v.to_owned(),
            links: Vec::new(),
        })
    }
    fn visit_map<M>(self, mut map: M) -> Result<Container, M::Error>
    where
        M: serde::de::MapAccess<'de>,
    {
        let mut class = None;
        let mut links = Vec::new();
        while let Some(key) = map.next_key::<String>()? {
            match key.as_str() {
                "class" => class = Some(map.next_value::<String>()?),
                "links" => links = map.next_value::<Vec<Value>>()?,
                other => return Err(serde::de::Error::unknown_field(other, &["class", "links"])),
            }
        }
        Ok(Container {
            class: class.ok_or_else(|| serde::de::Error::missing_field("class"))?,
            links,
        })
    }
}

impl<'de> Deserialize<'de> for Container {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(ContainerVisitor)
    }
}

#[derive(Debug, Deserialize)]
pub struct Graph {
    pub index: u32,
    pub header: Header,
    pub fields: Value,
    pub items: u16,
    pub containers: BTreeMap<u16, Container>,
    pub nodes: Vec<Node>,
    #[serde(rename = "unknown_nodes")]
    pub unknown_rids: Vec<u16>,
    pub states: Vec<Option<u16>>,
    pub entries: Vec<Option<u16>>,
    #[serde(rename = "remote")]
    pub remote_nodes: Vec<Option<u16>>,
    pub sync_vars: Vec<SyncVar>,
    #[serde(default)]
    owner_only_instance_vars: Vec<u32>,
    #[serde(skip)]
    by_pos: Vec<Option<usize>>,
    #[serde(skip)]
    by_rid: Vec<Option<usize>>,
    #[serde(skip)]
    entry_handlers: BTreeMap<EntryTopic, Vec<usize>>,
    #[serde(skip)]
    authored_logical_button_slots: Vec<u16>,
    #[serde(skip)]
    owner: WireSlots,
    #[serde(skip)]
    remote: WireSlots,
    #[serde(skip)]
    presence: Vec<usize>,
}

impl Graph {
    fn apply_runtime_state_metadata(
        &mut self,
        plain_classes: &BTreeSet<String>,
        mut client_states: BTreeMap<u16, RuntimeClientState>,
    ) -> io::Result<()> {
        if plain_classes.is_empty() && client_states.is_empty() {
            return Ok(());
        }
        for node in &mut self.nodes {
            let Some(slot) = node.state.filter(|&slot| {
                self.states.get(usize::from(slot)).copied().flatten() == Some(node.pos)
            }) else {
                continue;
            };
            let authored = node.class.starts_with("authored.")
                || node.state_behavior.is_some()
                || node.action_behavior.is_some()
                || node.declares_room_message_entry()
                || node.host_event_kind().is_some();
            if let Some(state) = client_states.remove(&slot) {
                if node.rid != state.rid
                    || node.class != state.class
                    || !node.is_state()
                    || node.server_only
                    || authored
                {
                    return Err(invalid(format!(
                        "runtime client state in graph {} at slot {slot} does not match an eligible state",
                        self.index
                    )));
                }
                node.runtime_client_state = true;
            }
            node.runtime_plain_state = !authored
                && node.runs_on_server()
                && node.is_state()
                && plain_classes.contains(&node.class);
        }
        if let Some((slot, _)) = client_states.first_key_value() {
            return Err(invalid(format!(
                "runtime client state in graph {} at slot {slot} has no matching state",
                self.index
            )));
        }
        Ok(())
    }

    fn compile(&mut self) -> io::Result<()> {
        if !self.owner_only_instance_vars.is_empty() {
            let mut owner_variables = BTreeSet::new();
            for &variable in &self.owner_only_instance_vars {
                if variable == 0 || !owner_variables.insert(variable) {
                    return Err(invalid(format!(
                        "invalid owner-only instance variable {variable} in graph {}",
                        self.index
                    )));
                }
            }
            let mut synchronized_owner_variables = BTreeSet::new();
            for entry in &self.sync_vars {
                if let Some(variable) = entry.0.filter(|id| owner_variables.contains(id)) {
                    if entry.1 != 0 || !synchronized_owner_variables.insert(variable) {
                        return Err(invalid(format!(
                            "owner-only instance variable {variable} has an ambiguous synchronization scope in graph {}",
                            self.index
                        )));
                    }
                }
            }
            self.owner_only_instance_vars.sort_unstable();
        }
        self.by_pos = vec![None; usize::from(self.items)];
        if let Some(max) = self.nodes.iter().map(|node| node.rid).max() {
            self.by_rid = vec![None; usize::from(max) + 1];
        }
        let mut authored_state_slots = BTreeMap::new();
        if self.nodes.iter().any(|node| {
            node.state_behavior.is_some()
                || node.action_behavior.is_some()
                || node.declares_room_message_entry()
                || node.host_event_kind().is_some()
        }) {
            for &position in self.states.iter().flatten() {
                *authored_state_slots.entry(position).or_insert(0_usize) += 1;
            }
        }
        let mut held_inputs = Vec::new();
        for (index, node) in self.nodes.iter_mut().enumerate() {
            if let Some(behavior) = node.state_behavior {
                let state = match behavior {
                    StateBehavior::Plain => "plain state",
                    StateBehavior::Observe => "observe state",
                    StateBehavior::Interval => "interval state",
                    StateBehavior::ButtonLock => "button lock state",
                    StateBehavior::HeldInput => "held input state",
                    StateBehavior::MovementScale => "movement scale state",
                    StateBehavior::StackClaim => "stack claim state",
                    StateBehavior::FacingClaim => "facing claim state",
                    StateBehavior::TrackEntities => "track entities state",
                };
                let occupies_state_slot = node
                    .state
                    .and_then(|slot| self.states.get(usize::from(slot)))
                    .is_some_and(|&position| position == Some(node.pos));
                if !node.server_only
                    || !node.runs_on_server()
                    || node.is_action()
                    || !occupies_state_slot
                    || authored_state_slots.get(&node.pos) != Some(&1)
                {
                    return Err(invalid(format!(
                        "{state} in graph {} at node {} must be server-only and occupy its state slot exactly once",
                        self.index, node.pos
                    )));
                }
                let stack_outputs: &[&str] = if behavior == StateBehavior::StackClaim {
                    &["m_topSubgraphPlug", "m_underSubgraphPlug"]
                } else {
                    &[]
                };
                for path in [
                    "m_onBeginPlug",
                    "m_onEndPlug",
                    "m_onAbortPlug",
                    "m_onFinishedPlug",
                ]
                .into_iter()
                .chain(node.declares_observe_state().then_some("on_change"))
                .chain(node.declares_observe_state().then_some("on_sample"))
                .chain(node.declares_observe_state().then_some("on_unavailable"))
                .chain(stack_outputs.iter().copied())
                {
                    if node
                        .field(path)
                        .is_some_and(|plug| !plug.is_null() && !valid_output_plug(plug))
                    {
                        return Err(invalid(format!(
                            "{state} in graph {} at node {} has an invalid {path} output plug",
                            self.index, node.pos
                        )));
                    }
                }
                node.class = match behavior {
                    // Class checks outside the main dispatch also use the ordinary state.
                    StateBehavior::Plain => "STUStatescriptState",
                    StateBehavior::Observe => {
                        let has_value = node.field("value").is_some_and(|value| !value.is_null());
                        let has_output = node
                            .field("output")
                            .is_some_and(|output| matches!(crate::script_expr::lvalue(output), Ok(Some(_))));
                        if !has_value || !has_output {
                            return Err(invalid(format!(
                                "observe state in graph {} at node {} requires a value expression and writable output",
                                self.index, node.pos
                            )));
                        }
                        "StateObserve"
                    }
                    StateBehavior::Interval => {
                        if node.interval_frames().is_none() {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a nonzero u32 interval_frames",
                                self.index, node.pos
                            )));
                        }
                        if !node.field("on_interval").is_some_and(valid_output_plug) {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a valid on_interval output plug",
                                self.index, node.pos
                            )));
                        }
                        "StateInterval"
                    }
                    StateBehavior::ButtonLock => {
                        if node.button_lock().is_none() {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires an integer button in 1..=255 and an optional nonzero u32 duration_frames",
                                self.index, node.pos
                            )));
                        }
                        "StateButtonLock"
                    }
                    StateBehavior::HeldInput => {
                        held_inputs.push(index);
                        "STUStatescriptStateLogicalButton"
                    }
                    StateBehavior::MovementScale => {
                        if node.movement_scale().is_none() {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a finite factor in 0..=1 and an optional nonzero u32 duration_frames",
                                self.index, node.pos
                            )));
                        }
                        "StateMovementScale"
                    }
                    StateBehavior::StackClaim => {
                        if node.stack_claim().is_none() || node.stack_value().is_none() {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a valid variable, priority and value, with an optional nonzero u32 duration_frames",
                                self.index, node.pos
                            )));
                        }
                        "StateStackClaim"
                    }
                    StateBehavior::FacingClaim => {
                        if node.field("direction").is_none_or(Value::is_null)
                            || node.facing_claim_priority().is_none()
                        {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a direction expression and explicit i8 level and boolean above_equals priority",
                                self.index, node.pos
                            )));
                        }
                        "StateFacingClaim"
                    }
                    StateBehavior::TrackEntities => {
                        if node.field("targets").is_none_or(Value::is_null) {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a targets expression",
                                self.index, node.pos
                            )));
                        }
                        if node.target_child_binding().is_none() {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires a u32 child_graph and nonzero u32 target_variable",
                                self.index, node.pos
                            )));
                        }
                        if node.field("identity").is_some() && !node.tracks_room_actor_identity() {
                            return Err(invalid(format!(
                                "{state} in graph {} at node {} requires identity to be room_actor when specified",
                                self.index, node.pos
                            )));
                        }
                        "StateTrackEntities"
                    }
                }
                .into();
            }
            if let Some(behavior) = node.action_behavior {
                let (action, outputs): (&str, &[&str]) = match behavior {
                    ActionBehavior::Forward => ("forward action", &["m_outPlug"]),
                    ActionBehavior::Branch => ("branch action", &["m_truePlug", "m_falsePlug"]),
                    ActionBehavior::LocalMessage => ("local message action", &["m_outPlug"]),
                    ActionBehavior::RoomMessage => ("room message action", &["m_outPlug"]),
                    ActionBehavior::RoomCue => ("room cue action", &["m_outPlug"]),
                    ActionBehavior::HealthChange => ("health change action", &["m_outPlug"]),
                    ActionBehavior::RadialHealth => ("radial health action", &["m_outPlug"]),
                    ActionBehavior::SweptDisplacement => {
                        ("swept displacement action", &["m_outPlug"])
                    }
                    ActionBehavior::Relocation => ("relocation action", &["m_outPlug"]),
                };
                let state_slot = authored_state_slots.contains_key(&node.pos);
                if !node.server_only
                    || !node.runs_on_server()
                    || node.is_state()
                    || node.state.is_some()
                    || state_slot
                {
                    return Err(invalid(format!(
                        "{action} in graph {} at node {} must be server-only with no state slot",
                        self.index, node.pos
                    )));
                }
                for &path in outputs {
                    if !node.field(path).is_some_and(valid_output_plug) {
                        return Err(invalid(format!(
                            "{action} in graph {} at node {} requires a valid {path} output plug",
                            self.index, node.pos
                        )));
                    }
                }
                // The declaration supplies dispatch. An authored name that
                // matches an entry or another special class cannot activate it.
                node.class = match behavior {
                    ActionBehavior::Forward => "ActionForward".into(),
                    ActionBehavior::Branch => {
                        if node.field("m_condition").is_none_or(Value::is_null) {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires m_condition",
                                self.index, node.pos
                            )));
                        }
                        "ActionBranch".into()
                    }
                    ActionBehavior::LocalMessage => {
                        if !node.valid_local_message() {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires a nonzero message, unique parameter slots and no external target",
                                self.index, node.pos
                            )));
                        }
                        "STUStatescriptActionSendGameMessage".into()
                    }
                    ActionBehavior::RoomMessage => {
                        if !node.valid_room_message() {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires a nonzero message, one explicit m_target and at most {MAX_ROOM_MESSAGE_PARAMETERS} unique parameter slots",
                                self.index, node.pos
                            )));
                        }
                        "ActionRoomMessage".into()
                    }
                    ActionBehavior::RoomCue => {
                        if node.field("audience").is_none_or(Value::is_null) {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires an audience expression",
                                self.index, node.pos
                            )));
                        }
                        if node.room_cue().is_none() {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires nonzero stimulus and line asset GUIDs and an optional actor or mode speaker",
                                self.index, node.pos
                            )));
                        }
                        "ActionRoomCue".into()
                    }
                    ActionBehavior::SweptDisplacement | ActionBehavior::Relocation => {
                        let (operand, class) = if behavior == ActionBehavior::Relocation {
                            ("destination", "ActionRelocation")
                        } else {
                            ("delta", "ActionSweptDisplacement")
                        };
                        if node.field(operand).is_none_or(Value::is_null) {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires {operand}",
                                self.index, node.pos
                            )));
                        }
                        class.into()
                    }
                    ActionBehavior::HealthChange => {
                        for operand in ["target", "delta"] {
                            if node.field(operand).is_none_or(Value::is_null) {
                                return Err(invalid(format!(
                                    "{action} in graph {} at node {} requires a {operand} expression",
                                    self.index, node.pos
                                )));
                            }
                        }
                        "ActionHealthChange".into()
                    }
                    ActionBehavior::RadialHealth => {
                        if node
                            .field("area")
                            .and_then(|area| area.get("$"))
                            .and_then(Value::as_str)
                            != Some("EntitiesInRadius")
                            || node.field("delta").is_none_or(Value::is_null)
                            || node.field("edge_scale").is_some_and(Value::is_null)
                        {
                            return Err(invalid(format!(
                                "{action} in graph {} at node {} requires an EntitiesInRadius area and a delta expression, with an optional edge_scale expression",
                                self.index, node.pos
                            )));
                        }
                        "ActionRadialHealth".into()
                    }
                };
            }
            if node.declares_room_message_entry()
                && (!node.server_only
                    || !node.runs_on_server()
                    || !node.is_message_entry()
                    || node.is_action()
                    || node.is_state()
                    || node.state.is_some()
                    || authored_state_slots.contains_key(&node.pos)
                    || node.game_message_id().is_none_or(|message| message == 0)
                    || !node.field("m_outPlug").is_some_and(valid_output_plug))
            {
                return Err(invalid(format!(
                    "room message entry in graph {} at node {} must be a server-only message entry with a nonzero message and a valid m_outPlug",
                    self.index, node.pos
                )));
            }
            if (node.host_event_kind().is_some() || node.class == "EntryHostEvent")
                && (node.class != "EntryHostEvent"
                    || !node.server_only
                    || !node.runs_on_server()
                    || node.is_action()
                    || node.is_state()
                    || node.state.is_some()
                    || authored_state_slots.contains_key(&node.pos)
                    || node.declares_room_message_entry()
                    || node.fields.get("m_gameMessage").is_some()
                    || !node.field("m_outPlug").is_some_and(valid_output_plug)
                    || !node
                        .host_event_kind()
                        .is_some_and(|kind| node.valid_host_event_parameters(kind)))
            {
                return Err(invalid(format!(
                    "host event entry in graph {} at node {} must be a standalone server-only EntryHostEvent with a valid host event kind, m_outPlug and parameter bindings",
                    self.index, node.pos
                )));
            }
            let position = self
                .by_pos
                .get_mut(usize::from(node.pos))
                .ok_or_else(|| invalid("graph node outside the item table"))?;
            if position.replace(index).is_some() {
                return Err(invalid(format!(
                    "graph {} defines node position {} more than once",
                    self.index, node.pos
                )));
            }
            self.by_rid[usize::from(node.rid)] = Some(index);
            let topic = node.host_event_kind().map(EntryTopic::Host).or_else(|| {
                node.game_message_id()
                    .filter(|_| {
                        node.runs_on_server()
                            && (node.is_message_entry()
                                || (node.is_state() && node.state.is_some()))
                    })
                    .map(EntryTopic::Message)
            });
            if let Some(topic) = topic {
                self.entry_handlers.entry(topic).or_default().push(index);
            }
        }
        if !held_inputs.is_empty() {
            held_inputs.sort_unstable_by_key(|&index| self.nodes[index].state);
            let mut registered: BTreeSet<_> = self.logical_button_slots().collect();
            for index in held_inputs {
                let raw = self.lower_held_input(index, &authored_state_slots)?;
                if registered.insert(raw) {
                    self.authored_logical_button_slots.push(raw);
                }
            }
        }
        self.owner = WireSlots::build(&self.states, self, false);
        self.remote = WireSlots::build(&self.remote_nodes, self, true);
        self.presence = self
            .sync_vars
            .iter()
            .enumerate()
            .filter_map(|(index, var)| var.has_presence_bit().then_some(index))
            .collect();
        Ok(())
    }

    fn lower_held_input(
        &mut self,
        index: usize,
        state_occurrences: &BTreeMap<u16, usize>,
    ) -> io::Result<u16> {
        let node = &self.nodes[index];
        let context = format!(
            "held input state in graph {} at node {}",
            self.index, node.pos
        );
        let raw = node
            .state
            .ok_or_else(|| invalid(format!("{context} has no state slot")))?;
        let button = node
            .field("button")
            .and_then(Value::as_u64)
            .and_then(|value| u8::try_from(value).ok())
            .filter(|&button| button != 0)
            .ok_or_else(|| invalid(format!("{context} requires an integer button in 1..=255")))?;
        let allow_dead = match node.field("allow_dead") {
            None => false,
            Some(value) => value
                .as_bool()
                .ok_or_else(|| invalid(format!("{context} requires boolean allow_dead")))?,
        };
        for field in [
            "m_logicalButton",
            "m_80AF45FB",
            "m_1C9EF058",
            "m_onGoingDownPlug",
            "m_onComingUpPlug",
            "m_5AE05408",
            "m_53C4B6A6",
            "m_246438AD",
        ] {
            if node.field(field).is_some() {
                return Err(invalid(format!(
                    "{context} conflicts with generated field {field}"
                )));
            }
        }
        let mut owned = BTreeSet::new();
        let held =
            self.held_input_stage(node, "held_states", true, &mut owned, state_occurrences)?;
        let idle =
            self.held_input_stage(node, "idle_states", false, &mut owned, state_occurrences)?;
        let empty = serde_json::json!({"$":"Plug","links":[]});
        let hook = |name| match node.field(name) {
            Some(plug) if valid_output_plug(plug) => Ok(plug.clone()),
            Some(_) => Err(invalid(format!(
                "{context} has an invalid {name} output plug"
            ))),
            None => Ok(empty.clone()),
        };
        let on_press = hook("on_press")?;
        let on_release = hook("on_release")?;
        let fields = self.nodes[index]
            .fields
            .as_object_mut()
            .ok_or_else(|| invalid(format!("{context} requires object fields")))?;
        fields.insert(
            "m_logicalButton".into(),
            serde_json::json!({
            "$":"STUConfigVarLogicalButton","m_logicalButton":button}),
        );
        fields.insert("m_80AF45FB".into(), held);
        fields.insert("m_1C9EF058".into(), idle);
        fields.insert("m_onGoingDownPlug".into(), on_press);
        fields.insert("m_onComingUpPlug".into(), on_release);
        fields.insert("m_5AE05408".into(), Value::Bool(allow_dead));
        Ok(raw)
    }

    fn held_input_stage(
        &self,
        node: &Node,
        name: &str,
        required: bool,
        owned: &mut BTreeSet<u16>,
        state_occurrences: &BTreeMap<u16, usize>,
    ) -> io::Result<Value> {
        let context = format!(
            "held input state in graph {} at node {}",
            self.index, node.pos
        );
        let slots = match node.field(name) {
            Some(Value::Array(slots)) if !required || !slots.is_empty() => slots.as_slice(),
            None if !required => &[],
            _ => {
                return Err(invalid(format!(
                    "{context} requires {}{name} state slots",
                    if required { "nonempty " } else { "array " }
                )));
            }
        };
        let mut links = Vec::with_capacity(slots.len());
        let mut states = Vec::with_capacity(slots.len());
        for slot in slots {
            let raw = slot
                .as_u64()
                .and_then(|value| u16::try_from(value).ok())
                .ok_or_else(|| invalid(format!("{context} has an invalid {name} state slot")))?;
            let target = self
                .state(usize::from(raw))
                .filter(|target| {
                    target.state == Some(raw)
                        && target.is_state()
                        && !target.is_action()
                        && target.runs_on_server()
                        && state_occurrences.get(&target.pos) == Some(&1)
                })
                .ok_or_else(|| {
                    invalid(format!(
                        "{context} {name} slot {raw} must identify one runnable state exactly once"
                    ))
                })?;
            if Some(raw) == node.state || !owned.insert(raw) {
                return Err(invalid(format!(
                    "{context} {name} slot {raw} is self-owned or already owned by another stage"
                )));
            }
            links.push(serde_json::json!([target.pos, "m_beginPlug"]));
            states.push(raw);
        }
        Ok(serde_json::json!({"$":"Plug","links":links,"m_FF3DAF1E":states}))
    }

    /// Preserve legacy dispatch order and append automatically registered authored inputs.
    pub(crate) fn logical_button_slots(&self) -> impl Iterator<Item = u16> + '_ {
        self.fields
            .get("m_680A2CB2")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|value| u16::try_from(value.as_u64()?).ok())
            .chain(self.authored_logical_button_slots.iter().copied())
    }

    pub(crate) fn entry_handlers(&self, topic: EntryTopic) -> impl Iterator<Item = &Node> {
        self.entry_handlers
            .get(&topic)
            .into_iter()
            .flatten()
            .map(|&index| &self.nodes[index])
    }

    pub fn node(&self, pos: u16) -> Option<&Node> {
        self.nodes.get((*self.by_pos.get(usize::from(pos))?)?)
    }

    pub fn by_rid(&self, rid: u16) -> Option<&Node> {
        self.nodes.get((*self.by_rid.get(usize::from(rid))?)?)
    }

    /// The subgraph box declared at this `m_items` position.
    pub fn container(&self, pos: u16) -> Option<&Container> {
        self.containers.get(&pos)
    }

    pub fn state(&self, index: usize) -> Option<&Node> {
        self.node((*self.states.get(index)?)?)
    }

    /// Whether this graph contains a supported server-side weapon input state.
    pub fn drives_ai_weapon_input(&self) -> bool {
        self.states
            .iter()
            .flatten()
            .filter_map(|&position| self.node(position))
            .any(Node::is_ai_weapon_fire)
    }

    pub fn remote_node(&self, index: usize) -> Option<&Node> {
        self.node((*self.remote_nodes.get(index)?)?)
    }

    pub fn node_ref(&self, value: &Value) -> Option<&Node> {
        self.node(u16::try_from(value.get("node")?.as_u64()?).ok()?)
    }

    pub fn owner_positions(&self) -> &[u16] {
        &self.owner.positions
    }

    /// Authored instance variables excluded from observer projection.
    pub fn owner_only_instance_vars(&self) -> &[u32] {
        &self.owner_only_instance_vars
    }

    /// Variables kept on the server by the authored graph.
    pub fn server_only_variables(&self) -> impl Iterator<Item = u32> + '_ {
        self.fields
            .get("m_9CEC6985")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(asset_guid)
            .map(|guid| u32::from(guid as u16))
    }

    pub fn remote_positions(&self) -> &[u16] {
        &self.remote.positions
    }

    pub fn owner_slot(&self, index: usize) -> Option<usize> {
        *self.owner.by_list_index.get(index)?
    }

    pub fn remote_slot(&self, index: usize) -> Option<usize> {
        *self.remote.by_list_index.get(index)?
    }

    pub fn presence_vars(&self) -> impl Iterator<Item = (usize, SyncVar)> + '_ {
        self.presence
            .iter()
            .map(|&index| (index, self.sync_vars[index]))
    }

    pub fn presence_bit(&self, var: u32) -> Option<usize> {
        self.presence_vars()
            .position(|(_, entry)| entry.0 == Some(var))
    }

    /// Unresolved plug links and broken state-group item references.
    /// Unresolved null links and links to declared containers are valid and
    /// do not appear in this report; a `{"item":N}` reference with no declared
    /// container at that item position is a broken bundle (the reference
    /// extractor emits the form only for entries it recorded as containers).
    fn dangling_links(&self) -> impl Iterator<Item = DanglingLink> + '_ {
        let plug_links = self.nodes.iter().flat_map(move |node| {
            node.plugs().into_iter().flat_map(move |(path, plug)| {
                raw_plug_links(plug)
                    .enumerate()
                    .filter_map(move |(link_index, link)| {
                        let (target, _) = decode_link(link)?;
                        let target = target?;
                        if self.node(target).is_some() || self.containers.contains_key(&target) {
                            return None;
                        }
                        Some(DanglingLink {
                            graph: self.index,
                            source: node.pos,
                            plug: path.clone(),
                            link_index,
                            target,
                        })
                    })
            })
        });
        let item_refs = self.nodes.iter().flat_map(move |node| {
            let mut found = Vec::new();
            collect_item_refs(&node.fields, "", &mut found);
            found.into_iter().filter_map(move |(path, target)| {
                if self.containers.contains_key(&target) {
                    return None;
                }
                Some(DanglingLink {
                    graph: self.index,
                    source: node.pos,
                    plug: path,
                    link_index: 0,
                    target,
                })
            })
        });
        plug_links.chain(item_refs)
    }

    pub fn graph_refs(&self) -> BTreeSet<u64> {
        let mut found = BTreeSet::new();
        collect_graphs(&self.fields, &mut found);
        for node in &self.nodes {
            collect_graphs(&node.fields, &mut found);
        }
        found
    }
}

#[derive(Debug, Deserialize)]
pub struct GraphBinding {
    pub graph: Option<u32>,
    #[serde(rename = "m_1EB5A024")]
    pub sync: Option<Vec<Value>>,
    #[serde(rename = "m_75408640")]
    pub flags: i64,
}

#[derive(Debug, Deserialize)]
pub struct Body {
    pub hero: Option<String>,
    pub name: String,
    pub graphs: Vec<GraphBinding>,
    /// Server roots mounted without consuming the body's projected IDs.
    #[serde(default)]
    pub server_graphs: Vec<GraphBinding>,
    /// Private root that handles the player's authored charge route.
    #[serde(default, deserialize_with = "optional_graph_index")]
    pub authored_charge_graph: Option<u32>,
    /// Public charge receiver for modes that still use synchronous damage credit.
    #[serde(default)]
    pub charge_compatibility: Option<ChargeCompatibility>,
    pub manager: Option<u32>,
    pub weapons: Vec<Option<u32>>,
    #[serde(default)]
    pub ai_weapon_buttons: Map<String, Value>,
    #[serde(default)]
    pub ai_association: Option<u64>,
    #[serde(default)]
    pub ai_weapon_identifiers: Map<String, Value>,
    /// None means the local entity definition was unavailable.
    #[serde(default)]
    pub component92_present: Option<bool>,
    /// An omitted list means the source definition was unavailable.
    #[serde(default, deserialize_with = "body_target_tags")]
    pub target_tags: Option<BTreeSet<u64>>,
    #[serde(default, deserialize_with = "body_model_hardpoints")]
    model_hardpoints: ModelHardpointsBySkin,
}

impl Body {
    /// Complete authored launch-model inventory for the exact skin. Skin zero is stock.
    pub fn model_hardpoints(&self, skin: u64) -> Option<Arc<BTreeSet<u64>>> {
        Some(Arc::clone(&self.model_hardpoints.get(&skin)?.ids))
    }

    pub fn model_geometry(&self, skin: u64) -> Option<Arc<ModelHardpoints>> {
        self.model_hardpoints.get(&skin).cloned()
    }

    pub fn has_model_hardpoints(&self) -> bool {
        !self.model_hardpoints.is_empty()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ModeValue {
    Bool(bool),
    Integer(i64),
    Asset(u64),
}

#[derive(Debug, Deserialize)]
pub struct ModeScripts {
    pub pve: bool,
    pub scripted_loading: bool,
    #[serde(default, deserialize_with = "optional_graph_index")]
    pub graph: Option<u32>,
    #[serde(default, deserialize_with = "optional_graph_index")]
    pub shared_graph: Option<u32>,
    pub teams: BTreeMap<u8, TeamScripts>,
}

#[derive(Debug, Deserialize)]
pub struct TeamScripts {
    #[serde(default, deserialize_with = "optional_graph_index")]
    pub controller: Option<u32>,
    #[serde(default, deserialize_with = "optional_graph_index")]
    pub body: Option<u32>,
}

fn optional_graph_index<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<u32>, D::Error> {
    Option::<Value>::deserialize(deserializer)?
        .map(|value| {
            asset_guid(&value)
                .and_then(graph_asset_index)
                .ok_or_else(|| serde::de::Error::custom("invalid StateScript graph GUID"))
        })
        .transpose()
}

/// Resolve an authored StateScript resource to the graph bundle's index.
pub fn graph_asset_index(guid: u64) -> Option<u32> {
    (guid >> 48 == GRAPH_TYPE)
        .then(|| u32::try_from(guid & 0x0000_ffff_ffff_ffff).ok())
        .flatten()
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EnemyPair {
    pub body: u64,
    pub controller: u64,
}

#[derive(Debug, Deserialize)]
struct RawEnemyPair {
    body: String,
    controller: String,
}

#[derive(Debug, Default, Deserialize)]
struct EnemyCatalog {
    #[serde(default)]
    pairs: BTreeMap<String, RawEnemyPair>,
    #[serde(default)]
    entities: BTreeMap<String, Body>,
}

impl EnemyCatalog {
    fn validate(mut self) -> io::Result<Self> {
        let asset = |text: &str, kind| {
            parse_guid(text).filter(|guid| guid >> 48 == kind && guid & 0x0000_ffff_ffff_ffff != 0)
        };
        for (key, body) in std::mem::take(&mut self.entities) {
            let guid = asset(&key, 0x0400)
                .ok_or_else(|| invalid("invalid Junkenstein entity definition"))?;
            if self
                .entities
                .insert(format!("0x{guid:016X}"), body)
                .is_some()
            {
                return Err(invalid("duplicate Junkenstein entity definition"));
            }
        }
        for (hero, mut pair) in std::mem::take(&mut self.pairs) {
            if asset(&hero, 0x02e0).is_none()
                || asset(&pair.body, 0x0400).is_none()
                || asset(&pair.controller, 0x0400).is_none()
                || parse_guid(&pair.body) == parse_guid(&pair.controller)
            {
                return Err(invalid("invalid Junkenstein body/controller pair"));
            }
            pair.body = format!("0x{:016X}", parse_guid(&pair.body).unwrap());
            pair.controller = format!("0x{:016X}", parse_guid(&pair.controller).unwrap());
            let body = self
                .entities
                .get(&pair.body)
                .ok_or_else(|| invalid("missing Junkenstein enemy body"))?;
            let controller = self
                .entities
                .get(&pair.controller)
                .ok_or_else(|| invalid("missing Junkenstein enemy controller"))?;
            if body.hero.as_deref().and_then(parse_guid) != parse_guid(&hero)
                || body
                    .ai_association
                    .is_some_and(|association| Some(association) != parse_guid(&pair.controller))
                || body.graphs.is_empty()
                || controller.graphs.is_empty()
            {
                return Err(invalid("inconsistent Junkenstein body/controller binding"));
            }
            if self
                .pairs
                .insert(format!("0x{:016X}", parse_guid(&hero).unwrap()), pair)
                .is_some()
            {
                return Err(invalid("duplicate Junkenstein hero pair"));
            }
        }
        Ok(self)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthoredClass {
    class: String,
    behavior: AuthoredClassBehavior,
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum AuthoredClassBehavior {
    PlainState,
    ObserveState,
    IntervalState,
    ButtonLockState,
    HeldInputState,
    MovementScaleState,
    StackClaimState,
    FacingClaimState,
    TrackEntitiesState,
    ForwardAction,
    BranchAction,
    LocalMessageAction,
    RoomMessageAction,
    RoomCueAction,
    HealthChangeAction,
    RadialHealthAction,
    SweptDisplacementAction,
    RelocationAction,
    ClientOnly,
}

impl AuthoredClassBehavior {
    fn apply(self, graph: u32, node: &mut Node) -> io::Result<()> {
        let (client_only, state, action) = match self {
            Self::PlainState => (false, Some(StateBehavior::Plain), None),
            Self::ObserveState => (false, Some(StateBehavior::Observe), None),
            Self::IntervalState => (false, Some(StateBehavior::Interval), None),
            Self::ButtonLockState => (false, Some(StateBehavior::ButtonLock), None),
            Self::HeldInputState => (false, Some(StateBehavior::HeldInput), None),
            Self::MovementScaleState => (false, Some(StateBehavior::MovementScale), None),
            Self::StackClaimState => (false, Some(StateBehavior::StackClaim), None),
            Self::FacingClaimState => (false, Some(StateBehavior::FacingClaim), None),
            Self::TrackEntitiesState => (false, Some(StateBehavior::TrackEntities), None),
            Self::ForwardAction => (false, None, Some(ActionBehavior::Forward)),
            Self::BranchAction => (false, None, Some(ActionBehavior::Branch)),
            Self::LocalMessageAction => (false, None, Some(ActionBehavior::LocalMessage)),
            Self::RoomMessageAction => (false, None, Some(ActionBehavior::RoomMessage)),
            Self::RoomCueAction => (false, None, Some(ActionBehavior::RoomCue)),
            Self::HealthChangeAction => (false, None, Some(ActionBehavior::HealthChange)),
            Self::RadialHealthAction => (false, None, Some(ActionBehavior::RadialHealth)),
            Self::SweptDisplacementAction => (false, None, Some(ActionBehavior::SweptDisplacement)),
            Self::RelocationAction => (false, None, Some(ActionBehavior::Relocation)),
            Self::ClientOnly => (true, None, None),
        };
        if (node.client_only && !client_only)
            || (node.server_only && client_only)
            || (node.state_behavior.is_some() && node.state_behavior != state)
            || (node.action_behavior.is_some() && node.action_behavior != action)
        {
            return Err(invalid(format!(
                "authored class {} conflicts with scope or behavior in graph {graph} at node {}",
                node.class, node.pos
            )));
        }
        node.client_only = client_only;
        node.server_only = !client_only;
        node.state_behavior = state;
        node.action_behavior = action;
        Ok(())
    }
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeStateMetadata {
    #[serde(default)]
    plain_classes: Vec<String>,
    #[serde(default)]
    client_states: Vec<RuntimeClientState>,
    #[serde(default)]
    projected_message_topics: Vec<String>,
    #[serde(default)]
    directed_numeric_messages: Vec<RuntimeDirectedNumericMessage>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeDirectedNumericMessage {
    topic: String,
    parameter: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeClientState {
    graph: u32,
    state: u16,
    rid: u16,
    class: String,
}

fn valid_runtime_class_name(class: &str) -> bool {
    (1..=128).contains(&class.len())
        && class.as_bytes()[0].is_ascii_alphabetic()
        && class
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

fn validate_projected_message_topics(
    declarations: Vec<String>,
    graphs: &BTreeMap<u32, Graph>,
) -> io::Result<BTreeSet<u64>> {
    let mut topics = BTreeSet::new();
    for declaration in declarations {
        let id = asset_guid(&Value::String(declaration))
            .filter(|&id| id != 0)
            .ok_or_else(|| invalid("invalid projected message topic"))?;
        if !topics.insert(id) {
            return Err(invalid(format!(
                "duplicate projected message topic {id:#018X}"
            )));
        }
    }
    if topics.is_empty() {
        return Ok(topics);
    }
    let mut unmatched = topics.clone();
    for graph in graphs.values() {
        for node in &graph.nodes {
            let stateful = node.class == "STUStatescriptStateGameMessageEntry";
            if !stateful && !node.is_message_entry() {
                continue;
            }
            let Some(id) = node.game_message_id().filter(|id| topics.contains(id)) else {
                continue;
            };
            if node.runs_on_server() || node.server_only {
                return Err(invalid(format!(
                    "projected message topic {id:#018X} has a server receiver in graph {} at node {}",
                    graph.index, node.pos
                )));
            }
            let valid_receiver = if stateful {
                node.is_state()
                    && node
                        .state
                        .and_then(|slot| graph.state(usize::from(slot)))
                        .is_some_and(|state| state.pos == node.pos)
            } else {
                node.state.is_none() && !node.is_state()
            };
            if valid_receiver && !node.is_action() && node.literal_game_message_id() == Some(id) {
                unmatched.remove(&id);
            }
        }
    }
    if let Some(id) = unmatched.first() {
        return Err(invalid(format!(
            "projected message topic {id:#018X} has no client receiver"
        )));
    }
    Ok(topics)
}

fn directed_numeric_shape(node: &Node, parameter: u64) -> Option<DirectedNumericRoute> {
    let sender = match node.class.as_str() {
        "STUStatescriptActionSendGameMessage" if node.is_action() => true,
        "STUStatescriptEntryGameMessage" if node.server_only && !node.is_action() => false,
        _ => return None,
    };
    let output = node.field("m_outPlug")?;
    if !node.runs_on_server()
        || node.remote != -1
        || node.state.is_some()
        || node.is_state()
        || node.declares_room_message_entry()
        || !valid_output_plug(output)
        || !node
            .fields
            .as_object()?
            .iter()
            .all(|(key, value)| match key.as_str() {
                "m_gameMessage" | "m_outPlug" | "m_params" => true,
                "m_ADEB6E05" if sender => {
                    value.as_bool() == Some(true) || value.as_u64() == Some(1)
                }
                "m_ADEB6E05" | "m_2BBEEAB8" | "m_A2287776" | "m_AED90719" => neutral_flag(value),
                "m_8BF03679" | "m_BF5B22B7" => value.is_null(),
                "m_targets" | "m_inPlug" if sender => true,
                "m_8B34E9C3" if sender => value.is_null(),
                "m_44D0D060" | "m_B60ABDA4" | "m_FF703F20" | "m_filter" if !sender => {
                    value.is_null()
                }
                "m_5AEA0A51" | "m_FDBDCB70" if !sender => neutral_flag(value),
                _ => false,
            })
    {
        return None;
    }
    let [row] = node.field("m_params")?.as_array()?.as_slice() else {
        return None;
    };
    let (class, value_field) = if sender {
        ("STU_814D595E", "m_value")
    } else {
        ("STU_EF930FD7", "m_out_Var")
    };
    if !native_fields(row, &["m_B5051BCE", value_field])
        || row.get("$").and_then(Value::as_str) != Some(class)
        || row.get("m_B5051BCE").and_then(asset_guid) != Some(parameter)
    {
        return None;
    }
    let value = row.get(value_field)?;
    let slot = u32::from(parameter as u16);
    if !sender {
        return directed_variable(value, Scope::Instance)
            .then_some(DirectedNumericRoute::Receiver(slot));
    }
    let target = node.field("m_targets")?;
    let valid_sender = if node.server_only {
        // These senders terminate after forwarding an instance value to one body.
        raw_plug_links(output).next().is_none()
            && directed_variable(value, Scope::Instance)
            && (directed_variable(target, Scope::Entity)
                || native_entity_operand(target, "STU_0AC15F78")
                    .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None)))
    } else {
        !value.is_null() && directed_variable(target, Scope::Instance)
    };
    valid_sender.then_some(DirectedNumericRoute::Sender(slot))
}

fn compile_directed_numeric_messages(
    declarations: Vec<RuntimeDirectedNumericMessage>,
    graphs: &mut BTreeMap<u32, Graph>,
) -> io::Result<()> {
    let mut topics = BTreeMap::new();
    for declaration in declarations {
        let topic = asset_guid(&Value::String(declaration.topic))
            .filter(|&id| id >> 48 == 0x0240 && id as u16 != 0)
            .ok_or_else(|| invalid("invalid directed numeric message topic"))?;
        let parameter = asset_guid(&Value::String(declaration.parameter))
            .filter(|&id| id >> 16 == 0x0a40_0000_0000 && id as u16 != 0)
            .ok_or_else(|| invalid("invalid directed numeric message parameter"))?;
        if topics.insert(topic, (parameter, false, false)).is_some() {
            return Err(invalid(format!(
                "duplicate directed numeric message topic {topic:#018X}"
            )));
        }
    }
    if topics.is_empty() {
        return Ok(());
    }
    for node in graphs.values_mut().flat_map(|graph| &mut graph.nodes) {
        let Some((parameter, found_sender, found_receiver)) = node
            .literal_game_message_id()
            .and_then(|topic| topics.get_mut(&topic))
        else {
            continue;
        };
        let route = directed_numeric_shape(node, *parameter);
        match route {
            Some(DirectedNumericRoute::Sender(_)) => *found_sender = true,
            Some(DirectedNumericRoute::Receiver(_)) => *found_receiver = true,
            None => (),
        }
        node.directed_numeric_route = route;
    }
    if let Some((topic, _)) = topics
        .iter()
        .find(|(_, (_, sender, receiver))| !sender || !receiver)
    {
        return Err(invalid(format!(
            "directed numeric message topic {topic:#018X} requires a matching native sender and receiver",
        )));
    }
    Ok(())
}

#[derive(Deserialize)]
struct Bundle {
    version: u32,
    #[serde(default)]
    authored_classes: Vec<AuthoredClass>,
    #[serde(default)]
    runtime_state_metadata: RuntimeStateMetadata,
    #[serde(default)]
    asset_categories: Option<BTreeMap<String, Vec<AssetCategoryRow>>>,
    #[serde(default)]
    asset_textures: Option<BTreeMap<String, String>>,
    #[serde(default)]
    view_sight_profiles: BTreeMap<String, crate::script_init::ViewSightProfile>,
    graphs: Vec<Graph>,
    bodies: BTreeMap<String, Body>,
    #[serde(default)]
    entities: BTreeMap<String, Body>,
    #[serde(default)]
    mode_variables: BTreeMap<String, BTreeMap<u32, Value>>,
    #[serde(default)]
    mode_scripts: BTreeMap<String, ModeScripts>,
    #[serde(default)]
    mode_rulesets: ModeRulesets,
    #[serde(default)]
    static_ray_profiles: BTreeMap<String, u16>,
    #[serde(default)]
    junkenstein: EnemyCatalog,
    missing: Vec<u32>,
}

#[derive(Deserialize)]
struct AssetCategoryRow {
    id: String,
    #[serde(rename = "type")]
    kind: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd)]
pub enum RoomSpatialNeeds {
    #[default]
    None,
    Queries,
    EntityFacts,
    EntityTags,
}

fn may_query_foreign_entity(mut entity: &Value) -> bool {
    while entity.get("$").and_then(Value::as_str) == Some("ClientOnly") {
        let Ok(Some(fallback)) = client_only_fallback(entity) else {
            return false;
        };
        entity = fallback;
    }
    entity.get("$").and_then(Value::as_str).is_some() && !native_shape(entity, "STU_B5A0CAF0", None)
}

#[derive(Clone, Copy, Default)]
struct RoomSpatialRequirements {
    needs: RoomSpatialNeeds,
    actor_rays: bool,
}

impl RoomSpatialRequirements {
    fn merge(self, other: Self) -> Self {
        Self {
            needs: self.needs.max(other.needs),
            actor_rays: self.actor_rays || other.actor_rays,
        }
    }
}

fn room_spatial_requirements(value: &Value) -> RoomSpatialRequirements {
    use RoomSpatialNeeds::{EntityFacts, EntityTags, None as NoQueries, Queries};

    if value.get("$").and_then(Value::as_str) == Some("ClientOnly") {
        return client_only_fallback(value)
            .ok()
            .flatten()
            .map_or_else(RoomSpatialRequirements::default, room_spatial_requirements);
    }
    match value {
        Value::Object(fields) => {
            let filter = fields.get("filter").and_then(Value::as_object);
            let tags = filter.is_some_and(|filter| filter.contains_key("tags"));
            let class = fields.get("$").and_then(Value::as_str);
            let needs = match class {
                Some("EntityMatches" | "EntitiesInRadius" | "EntityRayCast") if tags => EntityTags,
                Some("EntityMatches") => EntityFacts,
                Some("EntitiesInRadius" | "EntityRayCast")
                    if filter.is_some_and(|filter| filter.contains_key("alive")) =>
                {
                    EntityFacts
                }
                Some(
                    "EntitiesInRadius" | "SegmentClear" | "RayCast" | "EntityRayCast"
                    | "EntityPosition" | "EntityDirection",
                ) => Queries,
                Some(class @ ("STU_21A13145" | "STU_2200A66D"))
                    if native_entity_operand(value, class)
                        .is_some_and(may_query_foreign_entity) =>
                {
                    Queries
                }
                Some("STU_25ECB55F")
                    if native_target_tag_operand(value)
                        .is_some_and(|(_, entity)| may_query_foreign_entity(entity)) =>
                {
                    EntityTags
                }
                _ => NoQueries,
            };
            fields.values().map(room_spatial_requirements).fold(
                RoomSpatialRequirements {
                    needs,
                    actor_rays: class == Some("EntityRayCast"),
                },
                RoomSpatialRequirements::merge,
            )
        }
        Value::Array(values) => values.iter().map(room_spatial_requirements).fold(
            RoomSpatialRequirements::default(),
            RoomSpatialRequirements::merge,
        ),
        _ => RoomSpatialRequirements::default(),
    }
}

#[derive(Debug)]
pub struct GraphStore {
    graphs: BTreeMap<u32, Graph>,
    asset_categories: Option<BTreeMap<u64, BTreeSet<(u64, u64)>>>,
    asset_textures: Option<BTreeMap<u64, u64>>,
    view_sight_profiles: BTreeMap<u64, crate::script_init::ViewSightProfile>,
    projected_message_topics: BTreeSet<u64>,
    room_spatial_needs: RoomSpatialNeeds,
    uses_spatial_actor_rays: bool,
    uses_host_health_events: bool,
    bodies: BTreeMap<u64, Body>,
    entities: BTreeMap<u64, Body>,
    mode_variables: BTreeMap<u64, BTreeMap<u32, ModeValue>>,
    mode_scripts: BTreeMap<u64, ModeScripts>,
    mode_rulesets: ModeRulesets,
    static_ray_profiles: BTreeMap<u64, u16>,
    enemies: EnemyCatalog,
    missing: Vec<u32>,
}

impl GraphStore {
    pub fn load(path: impl AsRef<Path>) -> io::Result<Self> {
        let input = BufReader::new(File::open(path)?);
        Self::from_gzip(input)
    }

    pub fn from_gzip(input: impl io::BufRead) -> io::Result<Self> {
        let mut data = Vec::new();
        MultiGzDecoder::new(input)
            .take(MAX_GRAPH_BYTES + 1)
            .read_to_end(&mut data)?;
        if data.len() as u64 > MAX_GRAPH_BYTES {
            return Err(invalid("state graph bundle exceeds the size limit"));
        }
        Self::from_json(&data)
    }

    pub fn from_json(data: &[u8]) -> io::Result<Self> {
        let bundle: Bundle = serde_json::from_slice(data).map_err(invalid)?;
        if bundle.version != 1 {
            return Err(invalid("unsupported state graph bundle version"));
        }
        let asset_categories = bundle
            .asset_categories
            .map(|records| {
                keyed_by_guid(records)?
                    .into_iter()
                    .map(|(asset, rows)| {
                        if asset == 0 {
                            return Err(invalid("zero asset in category metadata"));
                        }
                        let categories = rows
                            .into_iter()
                            .map(|row| {
                                let parse = |value: &str| {
                                    parse_guid(value)
                                        .filter(|guid| *guid != 0)
                                        .ok_or_else(|| invalid("invalid asset category identifier"))
                                };
                                Ok((parse(&row.kind)?, parse(&row.id)?))
                            })
                            .collect::<io::Result<BTreeSet<_>>>()?;
                        Ok((asset, categories))
                    })
                    .collect::<io::Result<BTreeMap<_, _>>>()
            })
            .transpose()?;
        let asset_textures = bundle
            .asset_textures
            .map(|rows| {
                keyed_by_guid(rows)?
                    .into_iter()
                    .map(|(action, texture)| {
                        if !is_communication_action(action) {
                            return Err(invalid(
                                "invalid communication action key in texture catalog",
                            ));
                        }
                        let texture = parse_guid(&texture)
                            .filter(|&id| id >> 48 == 0x0c00 && id != 0x0c00_0000_0000_0000)
                            .ok_or_else(|| invalid("invalid texture GUID in texture catalog"))?;
                        Ok((action, texture))
                    })
                    .collect::<io::Result<BTreeMap<_, _>>>()
            })
            .transpose()?;
        let mut authored_classes = BTreeMap::new();
        for AuthoredClass { class, behavior } in bundle.authored_classes {
            let valid_name = class.strip_prefix("authored.").is_some_and(|name| {
                !name.is_empty()
                    && name
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
            });
            if !valid_name {
                return Err(invalid(format!(
                    "class {class:?} must use an authored. name with letters, digits, underscores or hyphens"
                )));
            }
            if authored_classes.insert(class.clone(), behavior).is_some() {
                return Err(invalid(format!("duplicate authored class {class}")));
            }
        }
        let mut plain_classes = BTreeSet::new();
        for class in bundle.runtime_state_metadata.plain_classes {
            if !valid_runtime_class_name(&class) {
                return Err(invalid("invalid runtime plain state class name"));
            }
            if !plain_classes.insert(class) {
                return Err(invalid("duplicate runtime plain state class"));
            }
        }
        // The 104319 factory maps this class to the base state even in
        // version 1 bundles prepared before runtime annotations were available.
        plain_classes.insert("STU_F08E1453".into());
        let mut client_states = BTreeMap::<u32, BTreeMap<u16, RuntimeClientState>>::new();
        for row in bundle.runtime_state_metadata.client_states {
            let RuntimeClientState { graph, state, .. } = row;
            if !valid_runtime_class_name(&row.class) {
                return Err(invalid(format!(
                    "invalid runtime client state class in graph {graph} at slot {state}"
                )));
            }
            if client_states
                .entry(graph)
                .or_default()
                .insert(state, row)
                .is_some()
            {
                return Err(invalid(format!(
                    "duplicate runtime client state in graph {graph} at slot {state}"
                )));
            }
        }
        let mut graphs = BTreeMap::new();
        for mut graph in bundle.graphs {
            if !authored_classes.is_empty() {
                for node in &mut graph.nodes {
                    if let Some(behavior) = authored_classes.get(&node.class) {
                        behavior.apply(graph.index, node)?;
                    }
                }
            }
            graph.apply_runtime_state_metadata(
                &plain_classes,
                client_states.remove(&graph.index).unwrap_or_default(),
            )?;
            graph.compile()?;
            let index = graph.index;
            if graphs.insert(index, graph).is_some() {
                return Err(invalid(format!(
                    "bundle defines graph {index} more than once"
                )));
            }
        }
        if let Some((graph, _)) = client_states.first_key_value() {
            return Err(invalid(format!(
                "runtime client state references unknown graph {graph}"
            )));
        }
        for graph in graphs.values() {
            for node in &graph.nodes {
                if let Some((child, _)) = node
                    .target_child_binding()
                    .filter(|(child, _)| !graphs.contains_key(child))
                {
                    return Err(invalid(format!(
                        "track entities state in graph {} at node {} references missing child_graph {child}",
                        graph.index, node.pos
                    )));
                }
            }
        }
        for (body, definition) in bundle
            .bodies
            .iter()
            .chain(&bundle.entities)
            .chain(&bundle.junkenstein.entities)
        {
            for binding in &definition.server_graphs {
                let graph = binding.graph.ok_or_else(|| {
                    invalid(format!(
                        "body {body} declares a server root without a graph"
                    ))
                })?;
                if !graphs.contains_key(&graph) {
                    return Err(invalid(format!(
                        "body {body} declares missing server graph {graph}"
                    )));
                }
            }
        }
        let health_topic = EntryTopic::Host(HostEventKind::HealthApplied);
        for (body, definition) in bundle.entities.iter().chain(&bundle.junkenstein.entities) {
            if definition.authored_charge_graph.is_some() {
                return Err(invalid(format!(
                    "entity {body} cannot declare a player authored charge graph"
                )));
            }
            if definition.charge_compatibility.is_some() {
                return Err(invalid(format!(
                    "entity {body} cannot declare player charge compatibility"
                )));
            }
        }
        for (body, definition) in &bundle.bodies {
            if let Some(binding) = &definition.charge_compatibility {
                validate_charge_compatibility(body, definition, binding, &graphs)?;
            }
            let Some(graph) = definition.authored_charge_graph else {
                continue;
            };
            if definition.hero.as_deref().and_then(parse_guid).is_none() {
                return Err(invalid(format!(
                    "body {body} requires a hero to declare an authored charge graph"
                )));
            }
            let private_roots = definition
                .server_graphs
                .iter()
                .filter(|root| root.graph == Some(graph))
                .count();
            let public_root = definition
                .graphs
                .iter()
                .any(|root| root.graph == Some(graph))
                || definition.manager == Some(graph)
                || definition.weapons.contains(&Some(graph));
            if private_roots != 1 || public_root {
                return Err(invalid(format!(
                    "body {body} must mount authored charge graph {graph} once as a private server root"
                )));
            }
            if !graphs
                .get(&graph)
                .is_some_and(|graph| graph.entry_handlers.contains_key(&health_topic))
            {
                return Err(invalid(format!(
                    "authored charge graph {graph} for body {body} requires a server health_applied entry"
                )));
            }
        }
        let projected_message_topics = validate_projected_message_topics(
            bundle.runtime_state_metadata.projected_message_topics,
            &graphs,
        )?;
        compile_directed_numeric_messages(
            bundle.runtime_state_metadata.directed_numeric_messages,
            &mut graphs,
        )?;
        let static_ray_profiles = keyed_by_guid(bundle.static_ray_profiles)?;
        if static_ray_profiles
            .iter()
            .any(|(guid, category)| guid >> 48 != 0x0690 || *category == 0)
        {
            return Err(invalid(
                "static ray profile requires a collision profile GUID and a nonzero category",
            ));
        }
        let view_sight_profiles = keyed_by_guid(bundle.view_sight_profiles)?;
        for (&guid, profile) in &view_sight_profiles {
            if guid >> 48 != 0x0690
                || profile.static_mask == 0
                || profile.samples.is_empty()
                || profile.samples.len() > 8
                || profile
                    .samples
                    .iter()
                    .any(|height| !(0.0..=1.0).contains(height))
            {
                return Err(invalid(format!("invalid view sight profile {guid:016X}")));
            }
        }
        let room_spatial_requirements = graphs
            .values()
            .flat_map(|graph| &graph.nodes)
            .filter(|node| node.runs_on_server())
            .map(|node| {
                let mut requirements = room_spatial_requirements(&node.fields);
                if node
                    .contact_volume_bindings()
                    .is_some_and(|(_, _, profile)| static_ray_profiles.contains_key(&profile))
                {
                    requirements.needs = requirements.needs.max(RoomSpatialNeeds::Queries);
                    requirements.actor_rays = true;
                }
                let view_track = node.view_track();
                requirements.actor_rays |= view_track
                    .as_ref()
                    .is_some_and(|track| view_sight_profiles.contains_key(&track.profile));
                if node.class == "STU_4BBC5D09"
                    || node.declares_health_change_action()
                    || node.declares_room_message_action()
                    || node.declares_room_cue_action()
                    || node.directed_numeric_parameter().is_some()
                    || node.tracks_room_actor_identity()
                    || node.contact_effect_resource().is_some()
                    || view_track.is_some()
                    || node.class == "STU_861D9015"
                {
                    requirements.needs = requirements.needs.max(RoomSpatialNeeds::Queries);
                }
                requirements
            })
            .fold(
                RoomSpatialRequirements::default(),
                RoomSpatialRequirements::merge,
            );
        let uses_host_health_events = graphs
            .values()
            .any(|graph| graph.entry_handlers.contains_key(&health_topic));
        Ok(Self {
            graphs,
            asset_categories,
            asset_textures,
            view_sight_profiles,
            projected_message_topics,
            room_spatial_needs: room_spatial_requirements.needs,
            uses_spatial_actor_rays: room_spatial_requirements.actor_rays,
            uses_host_health_events,
            bodies: keyed_by_guid(bundle.bodies)?,
            entities: keyed_by_guid(bundle.entities)?,
            mode_variables: keyed_by_guid(bundle.mode_variables)?
                .into_iter()
                .map(|(guid, values)| {
                    let values = values
                        .into_iter()
                        .map(|(var, value)| Ok((var, mode_value(&value)?)))
                        .collect::<io::Result<_>>()?;
                    Ok((guid, values))
                })
                .collect::<io::Result<_>>()?,
            mode_scripts: keyed_by_guid(bundle.mode_scripts)?,
            mode_rulesets: bundle.mode_rulesets,
            static_ray_profiles,
            enemies: bundle.junkenstein.validate()?,
            missing: bundle.missing,
        })
    }

    /// Authored category pairs are keyed by the complete asset and identifier IDs.
    pub(crate) fn asset_has_category(&self, asset: u64, kind: u64, id: u64) -> Option<bool> {
        self.asset_categories.as_ref().map(|categories| {
            categories
                .get(&asset)
                .is_some_and(|pairs| pairs.contains(&(kind, id)))
        })
    }

    /// A configured lookup returns a typed miss when the action has no texture.
    pub fn asset_texture(&self, action: u64) -> Option<u64> {
        self.asset_textures
            .as_ref()
            .map(|textures| textures.get(&action).copied().unwrap_or(0))
    }

    pub(crate) fn view_sight_profile(
        &self,
        guid: u64,
    ) -> Option<&crate::script_init::ViewSightProfile> {
        self.view_sight_profiles.get(&guid)
    }

    pub fn has_asset_category_data(&self) -> bool {
        self.asset_categories
            .as_ref()
            .is_some_and(|rows| !rows.is_empty())
    }

    pub fn has_asset_category_entry(&self, asset: u64) -> bool {
        self.asset_categories
            .as_ref()
            .is_some_and(|rows| rows.contains_key(&asset))
    }

    /// Whether any server graph uses the room's living body snapshot.
    pub fn uses_room_spatial_frame(&self) -> bool {
        self.room_spatial_needs != RoomSpatialNeeds::None
    }

    pub fn static_ray_category(&self, profile: u64) -> Option<u16> {
        self.static_ray_profiles.get(&profile).copied()
    }

    /// The most detailed room data requested by any server graph.
    pub fn room_spatial_needs(&self) -> RoomSpatialNeeds {
        self.room_spatial_needs
    }

    /// Whether a server operand needs frozen actor collision shapes.
    pub fn uses_spatial_actor_rays(&self) -> bool {
        self.uses_spatial_actor_rays
    }

    /// Whether this bundle explicitly subscribes to a host health event.
    pub fn uses_host_health_events(&self) -> bool {
        self.uses_host_health_events
    }

    pub fn graph(&self, index: u32) -> Option<&Graph> {
        self.graphs.get(&index)
    }

    pub(crate) fn is_projected_message_topic(&self, id: u64) -> bool {
        self.projected_message_topics.contains(&id)
    }

    /// Whether any loaded graph declares a server-executed receiver for `topic`.
    pub fn has_server_message_receiver(&self, topic: u64) -> bool {
        self.graphs.values().any(|graph| {
            graph.nodes.iter().any(|node| {
                node.runs_on_server()
                    && matches!(
                        node.class.as_str(),
                        "STUStatescriptEntryGameMessage" | "STUStatescriptStateGameMessageEntry"
                    )
                    && node.game_message_id() == Some(topic)
            })
        })
    }

    /// Whether the graph exists and every node in it executes only on the client.
    pub fn is_client_only_graph(&self, index: u32) -> bool {
        self.graphs.get(&index).is_some_and(|graph| {
            !graph.nodes.is_empty() && graph.nodes.iter().all(|n| !n.runs_on_server())
        })
    }

    /// Inspect local graph structure on demand. Unresolved null links and links
    /// to declared containers are valid and do not appear in this report;
    /// state-group `{"item":N}` references must name a declared container.
    pub fn dangling_links(&self) -> impl Iterator<Item = DanglingLink> + '_ {
        self.graphs.values().flat_map(Graph::dangling_links)
    }

    pub fn graph_indexes(&self) -> impl Iterator<Item = u32> + '_ {
        self.graphs.keys().copied()
    }

    pub fn bodies(&self) -> &BTreeMap<u64, Body> {
        &self.bodies
    }

    pub fn entity_body(&self, guid: u64) -> Option<&Body> {
        self.entities.get(&guid)
    }

    /// Extract the authored `0x0120...` contact-set index from a body or entity's graph schema entries (`0x0D800000000002A3`).
    pub fn body_contact_set(&self, body_guid: u64) -> Option<u16> {
        let body = self
            .bodies
            .get(&body_guid)
            .or_else(|| self.entities.get(&body_guid))?;
        for binding in body.graphs.iter().chain(body.server_graphs.iter()) {
            let Some(entries) = &binding.sync else {
                continue;
            };
            for entry in entries {
                if entry.get("m_0D09D2D9").and_then(Value::as_str) != Some("0x0D800000000002A3") {
                    continue;
                }
                let Some(val) = entry.get("m_value") else {
                    continue;
                };
                if val.get("$").and_then(Value::as_str) != Some("STUConfigVarContactSet") {
                    continue;
                }
                if let Some(guid) = val
                    .get("m_contactSet")
                    .and_then(Value::as_str)
                    .and_then(parse_guid)
                {
                    let index = (guid & 0xffff) as u16;
                    if index != 0 {
                        return Some(index);
                    }
                }
            }
        }
        None
    }

    pub fn body_contact_sets(&self) -> BTreeMap<u64, u16> {
        self.bodies
            .keys()
            .chain(self.entities.keys())
            .copied()
            .filter_map(|guid| self.body_contact_set(guid).map(|cs| (guid, cs)))
            .collect()
    }

    pub fn enemy_pair(&self, hero: u64) -> Option<EnemyPair> {
        self.enemies
            .pairs
            .get(&format!("0x{hero:016X}"))
            .map(|pair| EnemyPair {
                body: parse_guid(&pair.body).expect("validated enemy body"),
                controller: parse_guid(&pair.controller).expect("validated enemy controller"),
            })
    }

    pub fn enemy_body(&self, definition: u64) -> Option<&Body> {
        self.enemies.entities.get(&format!("0x{definition:016X}"))
    }

    pub fn mode_variables(&self, mode: u64) -> Option<&BTreeMap<u32, ModeValue>> {
        self.mode_variables.get(&mode)
    }

    /// Authored per-body overrides take precedence over mode defaults.
    pub fn mode_ruleset_value(
        &self,
        mode: u64,
        body: u64,
        id: u32,
    ) -> Option<crate::script_wire::Value> {
        self.mode_rulesets.value(mode, body, id).map(rule_value)
    }

    /// Returns `None` when this mode has no authored engine setting table.
    pub fn mode_engine_setting(&self, mode: u64, key: u32) -> Option<EngineSetting> {
        Some(
            self.mode_rulesets
                .engine_settings(mode)?
                .get(&key)
                .copied()
                .map_or(EngineSetting::Missing, |value| {
                    EngineSetting::Present(rule_value(value))
                }),
        )
    }

    pub fn mode_scripts(&self, mode: u64) -> Option<&ModeScripts> {
        self.mode_scripts.get(&mode)
    }

    pub fn missing(&self) -> &[u32] {
        &self.missing
    }
}

fn rule_value(value: RuleValue) -> crate::script_wire::Value {
    use crate::script_wire::Value;
    match value {
        RuleValue::Null => Value::Null,
        RuleValue::Bool(value) => Value::Bool(value),
        RuleValue::Integer(value) => Value::Integer(value),
        RuleValue::Float(value) => Value::Float(value),
        RuleValue::Asset(value) => Value::Asset(value),
    }
}

pub(crate) fn message_parameter_id(config: &Value) -> u32 {
    config
        .get("m_B5051BCE")
        .and_then(asset_guid)
        .map_or(0, |guid| u32::from(guid as u16))
}

pub fn target_tag_guid(value: &Value) -> Option<u64> {
    asset_guid(value).filter(|guid| (guid >> 48) & 0xfff == TARGET_TAG_TYPE)
}

fn body_target_tags<'de, D: serde::Deserializer<'de>>(
    decoder: D,
) -> Result<Option<BTreeSet<u64>>, D::Error> {
    Option::<Vec<Value>>::deserialize(decoder)?
        .map(|tags| {
            tags.iter()
                .map(|tag| {
                    target_tag_guid(tag)
                        .or_else(|| {
                            tag.as_u64()
                                .filter(|guid| (guid >> 48) & 0xfff == TARGET_TAG_TYPE)
                        })
                        .ok_or_else(|| {
                            serde::de::Error::custom("body target tag must be a packed .06C GUID")
                        })
                })
                .collect()
        })
        .transpose()
}

type ModelHardpointsBySkin = BTreeMap<u64, Arc<ModelHardpoints>>;

#[derive(Debug, Default)]
pub struct ModelHardpoints {
    ids: Arc<BTreeSet<u64>>,
    reference_positions: Option<BTreeMap<u64, [f64; 3]>>,
}

impl ModelHardpoints {
    pub fn contains(&self, id: &u64) -> bool {
        self.ids.contains(id)
    }

    /// Model-space position after applying the bone hierarchy in its reference pose.
    pub fn reference_position(&self, id: u64) -> Option<[f64; 3]> {
        self.reference_positions.as_ref()?.get(&id).copied()
    }

    pub fn has_reference_positions(&self) -> bool {
        self.reference_positions.is_some()
    }
}

fn body_model_hardpoints<'de, D: serde::Deserializer<'de>>(
    decoder: D,
) -> Result<ModelHardpointsBySkin, D::Error> {
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ModelEntry {
        skin: Value,
        hardpoints: Vec<Value>,
        #[serde(default)]
        reference_positions: Option<BTreeMap<String, [f64; 3]>>,
    }

    let mut by_skin = BTreeMap::new();
    for entry in Vec::<ModelEntry>::deserialize(decoder)? {
        let skin = asset_guid(&entry.skin)
            .ok_or_else(|| serde::de::Error::custom("model skin must be a canonical GUID"))?;
        let hardpoints = entry
            .hardpoints
            .iter()
            .map(|value| {
                asset_guid(value)
                    .filter(|guid| guid >> 48 == 0x0dc0 && (guid & 0x0000_ffff_ffff_ffff) != 0)
                    .ok_or_else(|| {
                        serde::de::Error::custom("model hardpoint must be a nonzero .0DC GUID")
                    })
            })
            .collect::<Result<BTreeSet<_>, D::Error>>()?;
        let reference_positions = entry
            .reference_positions
            .map(|entries| -> Result<_, D::Error> {
                let mut positions = BTreeMap::new();
                for (key, position) in entries {
                    let id = asset_guid(&Value::String(key))
                        .filter(|id| hardpoints.contains(id))
                        .ok_or_else(|| {
                            serde::de::Error::custom(
                                "reference position names an unknown hardpoint",
                            )
                        })?;
                    if !position.iter().all(|axis| axis.is_finite())
                        || positions.insert(id, position).is_some()
                    {
                        return Err(serde::de::Error::custom(
                            "invalid or repeated hardpoint position",
                        ));
                    }
                }
                Ok(positions)
            })
            .transpose()?;
        let geometry = ModelHardpoints {
            ids: Arc::new(hardpoints),
            reference_positions,
        };
        if by_skin.insert(skin, Arc::new(geometry)).is_some() {
            return Err(serde::de::Error::custom("duplicate model skin"));
        }
    }
    Ok(by_skin)
}

pub fn field_at<'a>(fields: &'a Value, path: &str) -> Option<&'a Value> {
    let mut value = fields;
    for part in path.split('.') {
        let (name, mut indexes) = part.split_at(part.find('[').unwrap_or(part.len()));
        if name.is_empty() || !name.chars().all(|ch| ch == '_' || ch.is_alphanumeric()) {
            return None;
        }
        value = value.get(name)?;
        while !indexes.is_empty() {
            let (index, tail) = indexes.strip_prefix('[')?.split_once(']')?;
            if index.is_empty() || !index.bytes().all(|ch| ch.is_ascii_digit()) {
                return None;
            }
            value = value.get(index.parse::<usize>().ok()?)?;
            indexes = tail;
        }
    }
    Some(value)
}

/// Collects every exact `{"item":N}` object (a state-group box reference) in
/// a node's fields, keyed by its field path.
fn collect_item_refs(value: &Value, path: &str, out: &mut Vec<(String, u16)>) {
    match value {
        Value::Object(fields)
            if fields.len() == 1
                && fields
                    .get("item")
                    .and_then(Value::as_u64)
                    .and_then(|value| u16::try_from(value).ok())
                    .is_some() =>
        {
            out.push((
                path.to_owned(),
                u16::try_from(fields["item"].as_u64().unwrap_or_default()).unwrap_or_default(),
            ));
        }
        Value::Object(fields) => {
            for (key, item) in fields {
                let name = if path.is_empty() {
                    key.clone()
                } else {
                    format!("{path}.{key}")
                };
                collect_item_refs(item, &name, out);
            }
        }
        Value::Array(values) => {
            for (index, item) in values.iter().enumerate() {
                collect_item_refs(item, &format!("{path}[{index}]"), out);
            }
        }
        _ => {}
    }
}

fn collect_plugs<'a>(value: &'a Value, path: &str, out: &mut BTreeMap<String, &'a Value>) {
    match value {
        Value::Object(fields) if fields.contains_key("$") && fields.contains_key("links") => {
            out.insert(path.to_owned(), value);
        }
        Value::Object(fields) => {
            for (key, item) in fields {
                if key != "$" {
                    let name = if path.is_empty() {
                        key.clone()
                    } else {
                        format!("{path}.{key}")
                    };
                    collect_plugs(item, &name, out);
                }
            }
        }
        Value::Array(values) => {
            for (index, item) in values.iter().enumerate() {
                collect_plugs(item, &format!("{path}[{index}]"), out);
            }
        }
        _ => {}
    }
}

fn collect_graphs(value: &Value, out: &mut BTreeSet<u64>) {
    match value {
        Value::Object(fields) => {
            for field in fields.values() {
                collect_graphs(field, out);
            }
        }
        Value::Array(values) => {
            for item in values {
                collect_graphs(item, out);
            }
        }
        _ => {
            if let Some(guid) = asset_guid(value).filter(|guid| guid >> 48 == GRAPH_TYPE) {
                out.insert(guid & 0x0000_ffff_ffff_ffff);
            }
        }
    }
}

pub(crate) fn is_communication_action(guid: u64) -> bool {
    guid & !u64::from(u32::MAX) == 0x0a90_0000_0000_0000 && guid as u32 != 0
}

fn keyed_by_guid<T>(records: BTreeMap<String, T>) -> io::Result<BTreeMap<u64, T>> {
    records
        .into_iter()
        .map(|(key, value)| {
            let hex = key.strip_prefix("0x").unwrap_or(&key);
            let guid = u64::from_str_radix(hex, 16).map_err(invalid)?;
            Ok((guid, value))
        })
        .collect()
}

fn mode_value(value: &Value) -> io::Result<ModeValue> {
    if let Some(value) = value.as_bool() {
        return Ok(ModeValue::Bool(value));
    }
    if let Some(value) = value.as_i64() {
        return Ok(ModeValue::Integer(value));
    }
    asset_guid(value)
        .map(ModeValue::Asset)
        .ok_or_else(|| invalid("unsupported mode variable"))
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn node_flag<'de, D: serde::Deserializer<'de>>(decoder: D) -> Result<bool, D::Error> {
    Value::deserialize(decoder).map(|value| truthy(&value))
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> Value {
        let node = |pos, rid, client_only, fields: Value| {
            json!({
                "pos": pos, "uid": 0, "rid": rid, "remote": 0, "state": null,
                "class": "Node", "client_only": client_only, "server_only": 0, "fields": fields
            })
        };
        let body = json!({
            "hero": null, "name": "Fixture", "manager": null, "weapons": [null, 42],
            "graphs": [{"graph": 42, "m_1EB5A024": null, "m_75408640": 0}]
        });
        json!({
            "version": 1,
            "graphs": [{
                "index": 42,
                "header": {"m_nodesBitCount": 4, "m_statesBitCount": 3,
                    "m_remoteSyncNodesBitCount": 3, "m_syncVarsBitCount": 3},
                "fields": {"ref": "0x058000000000001A", "ignored": "0x058000000000001b"},
                "items": 5, "containers": {"3": "Opaque"}, "unknown_nodes": [8],
                "nodes": [
                    node(0, 3, false, json!({
                        "m_2BBEEAB8": true,
                        "entries": [{"trigger": {"$": "Plug", "links": [[2, "input"], [null, "unknown"]]} }],
                        "related": ["0x058000000000002B", "0x040000000000002A"]
                    })),
                    node(1, 5, true, json!({"m_2BBEEAB8": true})),
                    node(2, 6, false, json!({"m_ADEB6E05": true}))
                ],
                "states": [0, 1, null, 0],
                "entries": [0, 3, 2, null],
                "remote": [2, null, 1, 0],
                "sync_vars": [[null, 0, 0], [12, 1, 0], [3, 0, 1], [42, 0, 0]]
            }],
            "bodies": {"0x0400000000000001": body.clone()},
            "entities": {"0x0400000000000002": body},
            "mode_variables": {"0x0230000000000018": {
                "1": false, "2": 3, "3": "0x0D8000000000195F"
            }},
            "missing": [43]
        })
    }

    #[test]
    fn owner_only_instance_variables_validate_identity_and_synchronization_scope() {
        let fixture: Value = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../fixtures/behavior/script-owner-instance-visibility-v1.json"
        )))
        .unwrap();
        let source = &fixture["input"]["bundle"];
        let load = |bundle: &Value| GraphStore::from_json(&serde_json::to_vec(bundle).unwrap());
        let graphs = load(source).unwrap();
        assert_eq!(
            graphs.graph(121).unwrap().owner_only_instance_vars(),
            [871, 872]
        );
        assert_eq!(graphs.graph(122).unwrap().owner_only_instance_vars(), [871]);
        for invalid_ids in [
            json!(null),
            json!(true),
            json!({}),
            json!(0),
            json!([0]),
            json!([871, 871]),
            json!([-1]),
            json!([true]),
            json!([1.5]),
            json!(["871"]),
            json!([4294967296_u64]),
        ] {
            let mut bundle = source.clone();
            bundle["graphs"][0]["owner_only_instance_vars"] = invalid_ids;
            assert!(load(&bundle).is_err());
        }
        for invalid_sync in [
            json!([[871, 1, 0]]),
            json!([[871, 2, 0]]),
            json!([[871, 0, 0], [871, 0, 1]]),
            json!([[871, 0, 0], [871, 1, 0]]),
        ] {
            let mut bundle = source.clone();
            bundle["graphs"][0]["sync_vars"] = invalid_sync;
            assert!(load(&bundle).is_err());
        }
        let mut legacy = source.clone();
        legacy["graphs"][0]
            .as_object_mut()
            .unwrap()
            .remove("owner_only_instance_vars");
        assert!(
            load(&legacy)
                .unwrap()
                .graph(121)
                .unwrap()
                .owner_only_instance_vars()
                .is_empty()
        );
        let mut extras = source.clone();
        extras["graphs"][0]["owner_only_instance_vars"] = json!([u32::MAX]);
        assert_eq!(
            load(&extras)
                .unwrap()
                .graph(121)
                .unwrap()
                .owner_only_instance_vars(),
            [u32::MAX]
        );
    }

    #[test]
    fn directed_numeric_server_senders_require_a_terminal_single_body_route() {
        const PARAMETER: u64 = 0x0a40_0000_0000_0563;
        let variable =
            |id, scope| json!({"$":"STUConfigVarDynamic", "m_identifier":id, "m_60DB8F99":scope});
        let raw = json!({
            "pos":0, "uid":0, "rid":0, "remote":-1, "state":null,
            "class":"STUStatescriptActionSendGameMessage", "client_only":false, "server_only":true,
            "fields":{
                "m_ADEB6E05":1,
                "m_gameMessage":{"$":"STUConfigVarGameMessage","m_gameMessage":"0x0240000000000564"},
                "m_targets":variable(91, 1),
                "m_params":[{
                    "$":"STU_814D595E", "m_B5051BCE":format!("0x{PARAMETER:016X}"), "m_value":variable(92, 0)
                }],
                "m_outPlug":{"$":"STUStatescriptOutputPlug","links":[]}
            }
        });
        let route = |record: &Value| {
            let node: Node = serde_json::from_value(record.clone()).unwrap();
            directed_numeric_shape(&node, PARAMETER)
        };
        assert!(matches!(
            route(&raw),
            Some(DirectedNumericRoute::Sender(0x563))
        ));

        let mut possession = raw.clone();
        possession["fields"]["m_targets"] = json!({
            "$":"STU_0AC15F78", "m_EE729DCB":0,
            "m_entity":{"$":"STU_B5A0CAF0", "m_EE729DCB":0}
        });
        assert!(matches!(
            route(&possession),
            Some(DirectedNumericRoute::Sender(0x563))
        ));
        possession["fields"]["m_targets"]["m_entity"] = variable(91, 1);
        assert!(route(&possession).is_none());

        for (key, replacement) in [
            ("m_targets", variable(91, 0)),
            ("m_targets", variable(0, 1)),
            (
                "m_outPlug",
                json!({"$":"STUStatescriptOutputPlug","links":[[0,"m_inPlug"]]}),
            ),
            (
                "m_params",
                json!([{"$":"STU_814D595E","m_B5051BCE":format!("0x{PARAMETER:016X}"),"m_value":variable(92, 1)}]),
            ),
        ] {
            let mut invalid = raw.clone();
            invalid["fields"][key] = replacement;
            assert!(route(&invalid).is_none(), "{key} accepted");
        }

        let mut networked = raw;
        networked["server_only"] = json!(false);
        assert!(route(&networked).is_none());
        networked["fields"]["m_targets"] = variable(91, 0);
        assert!(matches!(
            route(&networked),
            Some(DirectedNumericRoute::Sender(0x563))
        ));
    }

    #[test]
    fn runtime_state_metadata_checks_node_identity_and_preserves_wire_scope() {
        let mut data = fixture();
        let graph = &mut data["graphs"][0];
        graph["nodes"][0]["state"] = json!(0);
        graph["nodes"][1]["state"] = json!(1);
        graph["nodes"][1]["client_only"] = json!(false);
        let load = |value: &Value| GraphStore::from_json(&serde_json::to_vec(value).unwrap());
        let baseline = load(&data).unwrap();
        let baseline = baseline.graph(42).unwrap();

        let row = json!({"graph": 42, "state": 1, "rid": 5, "class": "Node"});
        data["runtime_state_metadata"] = json!({
            "plain_classes": ["Node"],
            "client_states": [row.clone()],
        });
        let store = load(&data).unwrap();
        let graph = store.graph(42).unwrap();
        assert!(graph.node(0).unwrap().declares_runtime_plain_state());
        let client = graph.node(1).unwrap();
        assert!(!client.declares_runtime_plain_state() && !client.runs_on_server());
        assert!(client.is_networked() && !client.client_only && !client.server_only);
        assert_eq!(client.class, "Node");
        assert!(!graph.node(2).unwrap().declares_runtime_plain_state());
        assert_eq!(graph.owner_positions(), baseline.owner_positions());
        assert_eq!(graph.remote_positions(), baseline.remote_positions());

        for (field, value) in [
            ("rid", json!(3)),
            ("class", json!("OtherState")),
            ("state", json!(2)),
            ("graph", json!(43)),
            ("unexpected", json!(true)),
        ] {
            let mut invalid = data.clone();
            invalid["runtime_state_metadata"]["client_states"][0][field] = value;
            assert_eq!(
                load(&invalid).unwrap_err().kind(),
                io::ErrorKind::InvalidData
            );
        }
        for (field, value) in [
            ("client_states", json!([row.clone(), row.clone()])),
            ("plain_classes", json!(["Node", "Node"])),
            ("plain_classes", json!(["authored.state"])),
            ("plain_classes", json!(["N".repeat(129)])),
        ] {
            let mut invalid = data.clone();
            invalid["runtime_state_metadata"][field] = value;
            assert_eq!(
                load(&invalid).unwrap_err().kind(),
                io::ErrorKind::InvalidData
            );
        }
        for (field, value) in [
            ("server_only", json!(true)),
            ("state_behavior", json!("plain")),
            ("fields", json!({})),
        ] {
            let mut invalid = data.clone();
            invalid["graphs"][0]["nodes"][1][field] = value;
            assert_eq!(
                load(&invalid).unwrap_err().kind(),
                io::ErrorKind::InvalidData
            );
        }
    }

    #[test]
    fn body_contact_set_extracts_authored_index_or_returns_none() {
        let mut data = fixture();
        data["bodies"]["0x0400000000000001"] = json!({
            "hero": null, "name": "Human", "manager": null, "weapons": [],
            "graphs": [{
                "graph": 42,
                "m_1EB5A024": [{
                    "$": "STUStatescriptSchemaEntry",
                    "m_0D09D2D9": "0x0D800000000002A3",
                    "m_value": {"$": "STUConfigVarContactSet", "m_contactSet": "0x01200000000005EE"}
                }],
                "m_75408640": 0
            }]
        });
        data["bodies"]["0x0400000000000002"] = json!({
            "hero": null, "name": "Omnic", "manager": null, "weapons": [],
            "graphs": [{
                "graph": 42,
                "m_1EB5A024": [{
                    "$": "STUStatescriptSchemaEntry",
                    "m_0D09D2D9": "0x0D800000000002A3",
                    "m_value": {"$": "STUConfigVarContactSet", "m_contactSet": "0x01200000000000D5"}
                }],
                "m_75408640": 0
            }]
        });
        let catalog = GraphStore::from_json(data.to_string().as_bytes()).unwrap();
        assert_eq!(
            catalog.body_contact_set(0x0400_0000_0000_0001),
            Some(0x05ee)
        );
        assert_eq!(
            catalog.body_contact_set(0x0400_0000_0000_0002),
            Some(0x00d5)
        );
        assert_eq!(catalog.body_contact_set(0x0400_0000_0000_0099), None);
    }

    #[test]
    fn body_server_graphs_preserve_public_roots_and_bind_variables_in_order() {
        use crate::{
            script_init::{Root, body_roots, player_roots},
            script_wire::Value as ScriptValue,
        };

        const BODY: &str = "0x0400000000000001";
        let mut data = fixture();
        let body = &mut data["bodies"][BODY];
        body["manager"] = json!(42);
        body["graphs"].as_array_mut().unwrap().extend([
            json!({"graph":null,"m_75408640":0}),
            json!({"graph":42,"m_75408640":0}),
        ]);
        body["server_graphs"] = json!([
            {"graph":42,"m_75408640":0,"m_1EB5A024":[{"m_0D09D2D9":"0x0000000000000017","m_value":11}]},
            {"graph":42,"m_75408640":0,"m_1EB5A024":[{"m_0D09D2D9":"0x0000000000000017","m_value":12}]}
        ]);
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let store = load(&data).unwrap();
        let body = &store.bodies()[&0x0400_0000_0000_0001];
        let ids = |roots: &[Root]| roots.iter().map(|root| root.id).collect::<Vec<_>>();
        let body = body_roots(body).unwrap();
        assert_eq!(ids(&body), [1, 3, 0, 0]);
        let player = player_roots(&store.bodies()[&0x0400_0000_0000_0001], &store).unwrap();
        assert_eq!(ids(&player), [1, 3, 4, 5, 0, 0]);
        assert_eq!(player[3].variables[&28], ScriptValue::Integer(1));
        for roots in [&body, &player] {
            let seeded = roots
                .iter()
                .filter(|root| root.is_server_only())
                .map(|root| &root.variables[&23])
                .collect::<Vec<_>>();
            assert_eq!(
                seeded,
                [&ScriptValue::Integer(11), &ScriptValue::Integer(12)]
            );
        }
        for invalid in [Value::Null, json!(43)] {
            data["bodies"][BODY]["server_graphs"][0]["graph"] = invalid;
            assert_eq!(load(&data).unwrap_err().kind(), io::ErrorKind::InvalidData);
        }
    }

    #[test]
    fn authored_charge_route_requires_a_single_private_player_health_event_root() {
        const BODY: &str = "0x040000000000B013";
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/script-authored-charge-route-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(
            fixture["case_id"],
            "script-authored-charge-route.synthetic.v1"
        );
        let bundle = &fixture["input"]["bundle"];
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let graphs = load(bundle).unwrap();
        assert_eq!(
            graphs.bodies()[&0x0400_0000_0000_b013].authored_charge_graph,
            Some(612)
        );
        assert_eq!(
            graphs.bodies()[&0x0400_0000_0000_b014].authored_charge_graph,
            None
        );

        let root = bundle["bodies"][BODY]["server_graphs"][0].clone();
        for (field, value) in [
            ("authored_charge_graph", json!(612)),
            ("authored_charge_graph", json!("0x0400000000000264")),
            ("authored_charge_graph", json!("0x0580000100000264")),
            ("authored_charge_graph", json!("0x0580000000000265")),
            ("hero", Value::Null),
            ("server_graphs", json!([])),
            ("server_graphs", json!([root.clone(), root.clone()])),
            ("graphs", json!([root])),
            ("manager", json!(612)),
            ("weapons", json!([612])),
        ] {
            let mut data = bundle.clone();
            data["bodies"][BODY][field] = value;
            assert_eq!(
                load(&data).unwrap_err().kind(),
                io::ErrorKind::InvalidData,
                "{field}"
            );
        }

        let mut data = bundle.clone();
        let entry = data["graphs"][0]["nodes"][0].as_object_mut().unwrap();
        entry.remove("host_event_entry");
        entry.insert("class".into(), json!("STUStatescriptEntry"));
        assert!(
            load(&data)
                .unwrap_err()
                .to_string()
                .contains("requires a server health_applied entry")
        );

        let mut data = bundle.clone();
        data["graphs"][0]["nodes"][0]["server_only"] = json!(false);
        assert_eq!(load(&data).unwrap_err().kind(), io::ErrorKind::InvalidData);

        let mut data = bundle.clone();
        data["entities"] = json!({BODY: bundle["bodies"][BODY]});
        assert!(
            load(&data)
                .unwrap_err()
                .to_string()
                .contains("cannot declare a player authored charge graph")
        );
        let mut data = bundle.clone();
        data["junkenstein"] = json!({"entities": {BODY: bundle["bodies"][BODY]}});
        assert!(
            load(&data)
                .unwrap_err()
                .to_string()
                .contains("cannot declare a player authored charge graph")
        );
    }

    #[test]
    fn invalid_or_duplicate_mode_rulesets_fail_loading() {
        let mut data = fixture();
        let mode = "0x00000000000000AA";
        let body = "0x00000000000000BA";
        let valid = json!({"mode": mode, "rules": [{"id": 7, "value": false}]});
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        assert_eq!(load(&data).unwrap().mode_ruleset_value(0xaa, 0xba, 7), None);
        for invalid_table in [
            json!([valid.clone(), valid.clone()]),
            json!([{"mode": "0x0000000000000000", "rules": []}]),
            json!([{"mode": "0x00000000000000aa", "rules": []}]),
            json!([{"mode": mode, "rules": [{"id": 7, "body": null, "value": 12}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "body": "BA", "value": 12}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "body": "0x0000000000000000", "value": 12}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "value": [1]}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "value": "0x0000000000000000"}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "value": "bad"}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "value": u64::MAX}]}]),
            json!([{"mode": mode, "rules": [{"id": 7}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "value": 1, "boddy": body}]}]),
            json!([{"mode": mode, "rules": [{"id": 7, "value": 1}, {"id": 7, "value": 2}]}]),
            json!([{"mode": mode, "rules": [
                {"id": 7, "body": body, "value": 1}, {"id": 7, "body": body, "value": 2}
            ]}]),
        ] {
            data["mode_rulesets"] = invalid_table;
            assert_eq!(
                load(&data).unwrap_err().kind(),
                io::ErrorKind::InvalidData,
                "{}",
                data["mode_rulesets"]
            );
        }
    }

    #[test]
    fn mode_engine_settings_keep_types_and_table_availability() {
        use crate::script_wire::Value as ScriptValue;

        let contract: Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/script-mode-rulesets-v1.json"
        ))
        .unwrap();
        let bundle = serde_json::to_vec(&contract["input"]["bundle"]).unwrap();
        let graphs = GraphStore::from_json(&bundle).unwrap();
        for (key, value) in [
            (101, ScriptValue::Bool(false)),
            (102, ScriptValue::Integer(-9007199254740993)),
            (103, ScriptValue::Float(1.0)),
            (104, ScriptValue::Null),
            (u32::MAX, ScriptValue::Integer(0)),
        ] {
            assert_eq!(
                graphs.mode_engine_setting(0xa1, key),
                Some(EngineSetting::Present(value))
            );
        }
        assert_eq!(
            graphs.mode_engine_setting(0xa1, 105),
            Some(EngineSetting::Missing)
        );
        assert_eq!(graphs.mode_engine_setting(0xa2, 102), None);
        assert_eq!(
            graphs.mode_engine_setting(0xa3, 101),
            Some(EngineSetting::Missing)
        );
        assert_eq!(
            graphs.mode_engine_setting(0xa4, 101),
            Some(EngineSetting::Present(ScriptValue::Bool(true)))
        );
        assert_eq!(graphs.mode_engine_setting(0xa5, 101), None);
        assert_eq!(
            graphs.mode_ruleset_value(0xa1, 0xb2, 101),
            Some(ScriptValue::Bool(true))
        );
        assert_eq!(graphs.mode_ruleset_value(0xa4, 0xb2, 101), None);
    }

    #[test]
    fn invalid_or_duplicate_mode_engine_settings_fail_loading() {
        let mut data = fixture();
        let mode = "0x00000000000000AA";
        for table in [
            json!(null),
            json!({"id": 7, "value": true}),
            json!([{"id": 7, "value": true, "body": "0x00000000000000BA"}]),
            json!([{"id": -1, "value": true}]),
            json!([{"id": u64::from(u32::MAX) + 1, "value": true}]),
            json!([{"id": 7.0, "value": true}]),
            json!([{"id": 7, "value": [true]}]),
            json!([{"id": 7, "value": u64::MAX}]),
            json!([{"id": 7}]),
            json!([{"id": 7, "value": true}, {"id": 7, "value": null}]),
        ] {
            data["mode_rulesets"] = json!([{"mode": mode, "engine_settings": table}]);
            let result = GraphStore::from_json(&serde_json::to_vec(&data).unwrap());
            assert_eq!(
                result.unwrap_err().kind(),
                io::ErrorKind::InvalidData,
                "{}",
                data["mode_rulesets"]
            );
        }
    }

    #[test]
    fn rule_integer_tokens_do_not_round_into_floats() {
        let mut data = fixture();
        data["mode_rulesets"] = json!([
            {"mode": "0x00000000000000AA", "rules": [{"id": 7, "value": "replace-numeric-token"}]}
        ]);
        let data = serde_json::to_string(&data).unwrap();
        for token in ["-9223372036854775809", "18446744073709551616", "1e309"] {
            let invalid = data.replace("\"replace-numeric-token\"", token);
            let result = GraphStore::from_json(invalid.as_bytes());
            assert_eq!(
                result.unwrap_err().kind(),
                io::ErrorKind::InvalidData,
                "{token}"
            );
        }
        let key = "\"replace-numeric-token\"";
        let integer =
            GraphStore::from_json(data.replace(key, "-9223372036854775808").as_bytes()).unwrap();
        let float = GraphStore::from_json(data.replace(key, "7e0").as_bytes()).unwrap();
        assert_eq!(
            integer.mode_ruleset_value(0xaa, 0, 7),
            Some(crate::script_wire::Value::Integer(i64::MIN))
        );
        assert_eq!(
            float.mode_ruleset_value(0xaa, 0, 7),
            Some(crate::script_wire::Value::Float(7.0))
        );
    }

    #[test]
    fn body_target_tags_distinguish_unknown_empty_and_invalid_data() {
        let mut data = fixture();
        let body = "0x0400000000000001";
        let entity = "0x0400000000000002";
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());

        assert!(
            load(&data).unwrap().bodies()[&0x0400_0000_0000_0001]
                .target_tags
                .is_none()
        );
        data["bodies"][body]["target_tags"] = json!([]);
        data["entities"][entity]["target_tags"] = json!([
            "0x0D60000000000159",
            "0x0D60000000000109",
            "0x0D60000000000159"
        ]);
        let parsed = load(&data).unwrap();
        assert_eq!(
            parsed.bodies()[&0x0400_0000_0000_0001].target_tags,
            Some(BTreeSet::new())
        );
        assert_eq!(
            parsed
                .entity_body(0x0400_0000_0000_0002)
                .unwrap()
                .target_tags,
            Some(BTreeSet::from([
                0x0d60_0000_0000_0109,
                0x0d60_0000_0000_0159
            ]))
        );
        data["bodies"][body]["target_tags"] = json!(["0x0400000000000109"]);
        assert_eq!(load(&data).unwrap_err().kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn body_model_hardpoints_keep_exact_skin_membership_and_validate_catalog() {
        const BODY: u64 = 0x0400_0000_0000_0001;
        const SKIN: u64 = 0x0a60_0000_0000_002a;
        const HARDPOINT: u64 = 0x0dc0_0000_0000_0042;
        let mut data = fixture();
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let body = format!("0x{BODY:016X}");
        assert!(
            load(&data).unwrap().bodies()[&BODY]
                .model_hardpoints(0)
                .is_none()
        );

        data["bodies"][&body]["model_hardpoints"] = json!([
            {"skin": "0x0000000000000000", "hardpoints": []},
            {"skin": "0x0A6000000000002A", "hardpoints": ["0x0DC0000000000042"],
             "reference_positions":{"0x0DC0000000000042":[0.4, 0.75, -0.2]}},
        ]);
        let parsed = load(&data).unwrap();
        let definition = &parsed.bodies()[&BODY];
        assert!(definition.model_hardpoints(0).unwrap().is_empty());
        assert_eq!(
            *definition.model_hardpoints(SKIN).unwrap(),
            BTreeSet::from([HARDPOINT])
        );
        assert!(definition.model_hardpoints(SKIN + 1).is_none());
        assert!(
            !definition
                .model_geometry(0)
                .unwrap()
                .has_reference_positions()
        );
        assert_eq!(
            definition
                .model_geometry(SKIN)
                .unwrap()
                .reference_position(HARDPOINT),
            Some([0.4, 0.75, -0.2])
        );

        for invalid in [
            json!([
                {"skin": "0x0000000000000000", "hardpoints": []},
                {"skin": "0x0000000000000000", "hardpoints": ["0x0DC0000000000042"]},
            ]),
            json!([{"skin": "0x0A6000000000002A", "hardpoints": ["0x0400000000000042"]}]),
            json!([{"skin": "0x0A6000000000002A", "hardpoints": ["0x0DC0000000000000"]}]),
            json!([{"skin": "0xINVALID", "hardpoints": []}]),
            json!([{"skin":"0x0000000000000000","hardpoints":[],
                    "reference_positions":{"0x0DC0000000000042":[1,2,3]}}]),
        ] {
            data["bodies"][&body]["model_hardpoints"] = invalid;
            assert_eq!(load(&data).unwrap_err().kind(), io::ErrorKind::InvalidData);
        }
    }

    #[test]
    fn enemy_pairs_keep_encounter_definitions_separate_and_validate_associations() {
        let hero = "0x02E0000000000175";
        let body = "0x040000000000000a";
        let controller = "0x040000000000000b";
        let mut data = fixture();
        let mut actor = data["bodies"]["0x0400000000000001"].clone();
        actor["hero"] = json!(hero);
        actor["ai_association"] = json!(0x0400_0000_0000_000bu64);
        actor["target_tags"] = json!([0x0d60_0000_0000_0109u64, "0x0D60000000000109"]);
        let control = data["entities"]["0x0400000000000002"].clone();
        data["junkenstein"] = json!({
            "pairs":{hero:{"body":body,"controller":controller}},
            "entities":{body:actor,controller:control}
        });
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let parsed = load(&data).unwrap();
        assert_eq!(
            parsed.enemy_pair(0x02e0_0000_0000_0175),
            Some(EnemyPair {
                body: 0x0400_0000_0000_000a,
                controller: 0x0400_0000_0000_000b,
            })
        );
        assert!(parsed.entity_body(0x0400_0000_0000_000a).is_none());
        assert_eq!(
            parsed
                .enemy_body(0x0400_0000_0000_000a)
                .unwrap()
                .target_tags,
            Some(BTreeSet::from([0x0d60_0000_0000_0109]))
        );
        for (field, value) in [
            ("ai_association", json!(0x0400_0000_0000_000cu64)),
            ("hero", json!("0x02E0000000000176")),
            ("target_tags", json!([0x0400_0000_0000_0109u64])),
        ] {
            let mut bad = data.clone();
            bad["junkenstein"]["entities"][body][field] = value;
            assert!(load(&bad).is_err());
        }
        let mut missing = data.clone();
        missing["junkenstein"]["entities"]
            .as_object_mut()
            .unwrap()
            .remove(controller);
        assert!(load(&missing).is_err());
        let mut duplicated = data.clone();
        duplicated["junkenstein"]["entities"]["0x040000000000000A"] =
            duplicated["junkenstein"]["entities"][body].clone();
        assert!(load(&duplicated).is_err());
        // Playable boss definitions use the explicit encounter pair instead of an AI association.
        data["junkenstein"]["entities"][body]["ai_association"] = Value::Null;
        assert!(load(&data).is_ok());
    }

    #[test]
    fn duplicate_graph_identities_are_rejected() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());

        let mut duplicate_position = fixture();
        duplicate_position["graphs"][0]["nodes"][2]["pos"] = json!(0);
        let error = load(&duplicate_position).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("node position 0"));

        let mut duplicate_graph = fixture();
        let duplicate = duplicate_graph["graphs"][0].clone();
        duplicate_graph["graphs"]
            .as_array_mut()
            .unwrap()
            .push(duplicate);
        let error = load(&duplicate_graph).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("graph 42"));
    }

    #[test]
    fn authored_class_catalog_rejects_ambiguous_declarations() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        assert_eq!(
            load(&data).unwrap().graph(42).unwrap().owner_positions(),
            [0, 0]
        );

        for (declarations, message) in [
            (
                json!([{"class":"authored.latch","behavior":"plain_state"},
                       {"class":"authored.latch","behavior":"client_only"}]),
                "duplicate authored class",
            ),
            (
                json!([{"class":"STUStatescriptState","behavior":"client_only"}]),
                "must use an authored.",
            ),
            (
                json!([{"class":"authored.","behavior":"client_only"}]),
                "must use an authored.",
            ),
            (
                json!([{"class":"authored.latch","behavior":"unknown"}]),
                "unknown variant",
            ),
            (
                json!([{"class":"authored.latch","behavior":"plain_state","typo":true}]),
                "unknown field",
            ),
        ] {
            data["authored_classes"] = declarations;
            let error = load(&data).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(error.to_string().contains(message), "{error}");
        }
    }

    #[test]
    fn authored_class_catalog_reuses_node_validation() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["authored_classes"] = json!([
            {"class":"authored.latch","behavior":"plain_state"},
            {"class":"authored.overlay","behavior":"client_only"},
            {"class":"authored.forward","behavior":"forward_action"}
        ]);
        let nodes = &mut data["graphs"][0]["nodes"];
        nodes[0]["class"] = json!("authored.latch");
        nodes[0]["state"] = json!(0);
        nodes[0]["state_behavior"] = json!("plain");
        nodes[0]["server_only"] = json!(true);
        nodes[1]["class"] = json!("authored.overlay");
        nodes[2]["class"] = json!("authored.forward");
        nodes[2]["fields"]["m_outPlug"] = json!({"$":"Plug","links":[]});
        let error = load(&data).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("occupy its state slot exactly once")
        );
        data["graphs"][0]["states"][3] = Value::Null;

        let store = load(&data).unwrap();
        let graph = store.graph(42).unwrap();
        assert!(graph.owner_positions().is_empty());
        assert!(graph.remote_positions().is_empty());
        assert!(graph.node(1).unwrap().client_only);

        for (position, field, value, message) in [
            (0, "client_only", json!(true), "graph 42 at node 0"),
            (0, "action_behavior", json!("forward"), "graph 42 at node 0"),
            (0, "state", json!(1), "occupy its state slot"),
            (1, "server_only", json!(true), "graph 42 at node 1"),
            (1, "state_behavior", json!("plain"), "graph 42 at node 1"),
            (2, "fields", json!({}), "output plug"),
        ] {
            let mut malformed = data.clone();
            malformed["graphs"][0]["nodes"][position][field] = value;
            let error = load(&malformed).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(error.to_string().contains(message), "{error}");
        }

        data["graphs"][0]["states"][2] = json!(2);
        let error = load(&data).unwrap_err();
        assert!(error.to_string().contains("no state slot"), "{error}");
    }

    #[test]
    fn local_message_action_rejects_invalid_routes_and_parameter_slots() {
        let mut data = fixture();
        let node = &mut data["graphs"][0]["nodes"][2];
        node["server_only"] = json!(true);
        node["action_behavior"] = json!("local_message");
        node["fields"] = json!({
            "m_gameMessage": {"m_gameMessage":"0xF100000000000001"},
            "m_params": [{"m_B5051BCE":"0xF200000000000007", "m_value":12}],
            "m_outPlug": {"$":"Plug", "links":[]}
        });
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let valid = load(&data).unwrap();
        assert_eq!(
            valid.graph(42).unwrap().node(2).unwrap().game_message_id(),
            Some(0xf100_0000_0000_0001)
        );
        for (field, invalid_value) in [
            (
                "m_gameMessage",
                json!({"m_gameMessage":"0x0000000000000000"}),
            ),
            ("m_gameMessage", json!({})),
            ("m_target", json!(12)),
            ("m_targets", json!([])),
            ("m_params", json!(false)),
            ("m_params", json!([{"m_B5051BCE":"0xF200000000000007"}])),
            (
                "m_params",
                json!([{"m_B5051BCE":"0xF200000000000000","m_value":12}]),
            ),
            (
                "m_params",
                json!([
                    {"m_B5051BCE":"0xF100000000000007","m_value":12},
                    {"m_B5051BCE":"0xF200000000000007","m_value":13}
                ]),
            ),
        ] {
            let mut malformed = data.clone();
            malformed["graphs"][0]["nodes"][2]["fields"][field] = invalid_value;
            let error = load(&malformed).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(
                error
                    .to_string()
                    .contains("local message action in graph 42 at node 2")
            );
        }
        data["graphs"][0]["nodes"][2]["client_only"] = json!(true);
        assert!(
            load(&data)
                .unwrap_err()
                .to_string()
                .contains("must be server-only")
        );
    }

    #[test]
    fn forward_action_declarations_require_a_server_action_and_valid_output() {
        let mut data = fixture();
        let node = &mut data["graphs"][0]["nodes"][2];
        node["action_behavior"] = json!("forward");
        node["server_only"] = json!(true);
        node["fields"] = json!({"m_outPlug": {"$": "Plug", "links": [[null, "input"]]}});
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let store = load(&data).unwrap();
        let forward = store.graph(42).unwrap().node(2).unwrap();
        assert!(forward.is_action());
        assert!(!forward.is_state());
        assert!(!forward.is_networked());

        for (field, value) in [
            ("server_only", json!(false)),
            ("client_only", json!(true)),
            ("state", json!(0)),
            ("state_behavior", json!("plain")),
            ("action_behavior", json!("unknown")),
            ("fields", json!({"m_outPlug": {"links": []}})),
            (
                "fields",
                json!({"m_outPlug": {"$": "Plug", "links": [[3, false]]}}),
            ),
        ] {
            let mut malformed = data.clone();
            malformed["graphs"][0]["nodes"][2][field] = value;
            assert_eq!(
                load(&malformed).unwrap_err().kind(),
                io::ErrorKind::InvalidData
            );
        }
        data["graphs"][0]["states"][2] = json!(2);
        assert_eq!(load(&data).unwrap_err().kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn room_spatial_service_is_requested_by_server_graph_operands() {
        let mut data = fixture();
        let load =
            |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap()).unwrap();
        assert!(!load(&data).uses_room_spatial_frame());
        assert!(load(&data).room_spatial_needs() < RoomSpatialNeeds::EntityFacts);

        data["graphs"][0]["nodes"][0]["fields"]["nested"] =
            json!([{"value":{"$":"EntitiesInRadius","center":null,"radius":1}}]);
        assert!(load(&data).uses_room_spatial_frame());
        assert!(load(&data).room_spatial_needs() < RoomSpatialNeeds::EntityFacts);

        let value = &mut data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"];
        value["filter"] = json!({"relation":"ally","source":null});
        assert!(load(&data).room_spatial_needs() < RoomSpatialNeeds::EntityFacts);

        for (predicate, needs) in [
            (
                json!({"$":"EntityPosition","entity":null}),
                RoomSpatialNeeds::Queries,
            ),
            (
                json!({"$":"EntityDirection","entity":null}),
                RoomSpatialNeeds::Queries,
            ),
            (
                json!({"$":"SegmentClear","origin":null,"target":null}),
                RoomSpatialNeeds::Queries,
            ),
            (
                json!({"$":"RayCast","origin":null,"direction":null,"max_distance":1}),
                RoomSpatialNeeds::Queries,
            ),
            (
                json!({"$":"EntityRayCast","origin":null,"direction":null,"max_distance":1}),
                RoomSpatialNeeds::Queries,
            ),
            (
                json!({"$":"EntityRayCast","origin":null,"direction":null,"max_distance":1,"filter":{"alive":true}}),
                RoomSpatialNeeds::EntityFacts,
            ),
            (
                json!({"$":"EntityRayCast","origin":null,"direction":null,"max_distance":1,"filter":{"tags":{}}}),
                RoomSpatialNeeds::EntityTags,
            ),
            (
                json!({"$":"EntitiesInRadius","center":null,"radius":1,"filter":{"alive":true}}),
                RoomSpatialNeeds::EntityFacts,
            ),
            (
                json!({"$":"EntitiesInRadius","center":null,"radius":1,"filter":{"tags":{}}}),
                RoomSpatialNeeds::EntityTags,
            ),
            (
                json!({"$":"EntityMatches","entity":null,"filter":{"alive":true}}),
                RoomSpatialNeeds::EntityFacts,
            ),
            (
                json!({"$":"EntityMatches","entity":null,"filter":{"tags":{}}}),
                RoomSpatialNeeds::EntityTags,
            ),
        ] {
            let actor_rays = predicate.get("$").and_then(Value::as_str) == Some("EntityRayCast");
            data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"] = predicate;
            let parsed = load(&data);
            assert!(parsed.uses_room_spatial_frame());
            assert_eq!(parsed.room_spatial_needs(), needs);
            assert_eq!(parsed.uses_spatial_actor_rays(), actor_rays);
        }

        let wrapper = json!({
            "$":"ClientOnly",
            "value":{"$":"EntityMatches","entity":null,"filter":{"tags":{}}}
        });
        data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"] = wrapper;
        assert_eq!(load(&data).room_spatial_needs(), RoomSpatialNeeds::None);
        data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"]["server_fallback"] =
            json!({"$":"SegmentClear","origin":null,"target":null});
        assert_eq!(load(&data).room_spatial_needs(), RoomSpatialNeeds::Queries);

        data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"]["server_fallback"] =
            json!({"$":"RayCast","origin":null,"direction":null,"max_distance":1});
        assert_eq!(load(&data).room_spatial_needs(), RoomSpatialNeeds::Queries);

        data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"] = json!({
            "$":"ClientOnly",
            "value":{"$":"EntityRayCast","origin":null,"direction":null,"max_distance":1}
        });
        assert!(!load(&data).uses_spatial_actor_rays());
        assert_eq!(load(&data).room_spatial_needs(), RoomSpatialNeeds::None);
        data["graphs"][0]["nodes"][0]["fields"]["nested"][0]["value"]["server_fallback"] =
            json!({"$":"EntityRayCast","origin":null,"direction":null,"max_distance":1});
        assert!(load(&data).uses_spatial_actor_rays());
        assert_eq!(load(&data).room_spatial_needs(), RoomSpatialNeeds::Queries);

        data["graphs"][0]["nodes"][0]["client_only"] = json!(true);
        assert!(!load(&data).uses_spatial_actor_rays());
        assert!(!load(&data).uses_room_spatial_frame());
        assert!(load(&data).room_spatial_needs() < RoomSpatialNeeds::EntityFacts);
    }

    #[test]
    fn native_direct_target_tags_request_frozen_facts_for_foreign_operands() {
        let contract: Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/actor-direct-target-tags-v1.json"
        ))
        .unwrap();
        let input = &contract["input"];
        let predicate = |entity: &Value| json!({"$":"STU_25ECB55F", "m_entity":entity, "m_88B0050A":input["tags"]["base"]});
        let owner = &input["owner"];
        let foreign = &input["foreign"];
        let client_only = |fallback: &Value| json!({"$":"ClientOnly", "value":foreign, "server_fallback":fallback});
        let mut invalid_tag = predicate(foreign);
        invalid_tag["m_88B0050A"] = json!("0x0400000000001101");
        let mut invalid_header = predicate(foreign);
        invalid_header["m_EE729DCB"] = json!(1);

        let mut data = fixture();
        for (case, config, client_only_node) in [
            ("owner", predicate(owner), false),
            ("foreign", predicate(foreign), false),
            ("owner_fallback", predicate(&client_only(owner)), false),
            ("foreign_fallback", predicate(&client_only(foreign)), false),
            ("null_selector", predicate(&Value::Null), false),
            ("invalid_tag", invalid_tag, false),
            ("invalid_header", invalid_header, false),
            ("client_only_node", predicate(foreign), true),
        ] {
            let node = &mut data["graphs"][0]["nodes"][0];
            node["fields"]["native_tag"] = config;
            node["client_only"] = json!(client_only_node);
            let store = GraphStore::from_json(&serde_json::to_vec(&data).unwrap()).unwrap();
            let needs = if contract["expected"]["requires_tags"][case]
                .as_bool()
                .unwrap()
            {
                RoomSpatialNeeds::EntityTags
            } else {
                RoomSpatialNeeds::None
            };
            assert_eq!(store.room_spatial_needs(), needs, "{case}");
            assert!(!store.uses_spatial_actor_rays(), "{case}");
        }
    }

    #[test]
    fn native_pose_operands_request_spatial_data_for_possible_foreign_entities() {
        use RoomSpatialNeeds::{None as NoQueries, Queries};

        let contract: Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/script-native-pose-demand-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(
            contract["case_id"],
            "script-native-pose-demand.synthetic.v1"
        );
        assert_eq!(contract["evidence"], "synthetic-rust");
        let mut data = fixture();
        let assert_needs = |data: &Value, case: &str| {
            let expected = match contract["expected"][case].as_str().unwrap() {
                "none" => NoQueries,
                "queries" => Queries,
                other => panic!("unexpected demand in {case}: {other}"),
            };
            let store = GraphStore::from_json(&serde_json::to_vec(data).unwrap()).unwrap();
            assert_eq!(store.room_spatial_needs(), expected, "{case}");
            assert_eq!(
                store.uses_room_spatial_frame(),
                expected != NoQueries,
                "{case}"
            );
        };
        let own = &contract["input"]["owner"];
        let foreign = &contract["input"]["dynamic"];
        let client_only = |fallback: Option<&Value>| {
            let mut operand = json!({"$":"ClientOnly","value":foreign});
            if let Some(fallback) = fallback {
                operand["server_fallback"] = fallback.clone();
            }
            operand
        };

        for class in contract["input"]["pose_classes"].as_array().unwrap() {
            let pose = |entity: &Value| json!({"$":class,"m_entity":entity,"m_EE729DCB":0});
            data["graphs"][0]["nodes"][0]["client_only"] = json!(false);
            for (case, operand) in [
                ("owner", pose(own)),
                ("dynamic", pose(foreign)),
                ("owner_fallback", pose(&client_only(Some(own)))),
                ("dynamic_fallback", pose(&client_only(Some(foreign)))),
                ("no_selector_fallback", pose(&client_only(None))),
                (
                    "invalid_selector_wrapper",
                    pose(&json!({"$":"ClientOnly","server_fallback":foreign})),
                ),
                ("null_selector", pose(&Value::Null)),
                ("untyped_selector", pose(&json!(29))),
                ("missing_selector", json!({"$":class})),
                (
                    "extra_pose_field",
                    json!({"$":class,"m_entity":foreign,"extra":null}),
                ),
                (
                    "invalid_pose_header",
                    json!({"$":class,"m_entity":foreign,"m_EE729DCB":1}),
                ),
                (
                    "no_pose_fallback",
                    json!({"$":"ClientOnly","value":pose(foreign)}),
                ),
                (
                    "pose_fallback",
                    json!({"$":"ClientOnly","value":null,"server_fallback":pose(foreign)}),
                ),
                (
                    "invalid_pose_wrapper",
                    json!({"$":"ClientOnly","server_fallback":pose(foreign)}),
                ),
            ] {
                data["graphs"][0]["nodes"][0]["fields"]["value"] = operand;
                assert_needs(&data, case);
            }

            data["graphs"][0]["nodes"][0]["client_only"] = json!(true);
            data["graphs"][0]["nodes"][0]["fields"]["value"] = pose(foreign);
            assert_needs(&data, "client_only_node");
        }
    }

    #[test]
    fn branch_action_declarations_require_a_condition_and_both_outputs() {
        let mut data = fixture();
        let node = &mut data["graphs"][0]["nodes"][2];
        node["action_behavior"] = json!("branch");
        node["server_only"] = json!(true);
        node["fields"] = json!({
            "m_condition": false,
            "m_truePlug": {"$": "Plug", "links": [[null, "input"]]},
            "m_falsePlug": {"$": "Plug", "links": []},
        });
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let store = load(&data).unwrap();
        let branch = store.graph(42).unwrap().node(2).unwrap();
        assert!(branch.declares_branch_action() && branch.is_action() && !branch.is_networked());
        assert!(!branch.is_state());
        for field in ["m_condition", "m_truePlug", "m_falsePlug"] {
            let mut invalid = data.clone();
            invalid["graphs"][0]["nodes"][2]["fields"][field] = Value::Null;
            let result = load(&invalid).unwrap_err();
            assert_eq!(result.kind(), io::ErrorKind::InvalidData);
            assert!(
                result
                    .to_string()
                    .contains("branch action in graph 42 at node 2")
            );
            assert!(result.to_string().contains(field));
        }
    }

    #[test]
    fn health_change_action_requires_server_scope_and_operands() {
        let mut data = fixture();
        let node = &mut data["graphs"][0]["nodes"][2];
        node["action_behavior"] = json!("health_change");
        node["server_only"] = json!(true);
        node["fields"] = json!({
            "target": {"$": "STUConfigVarDynamic", "m_identifier": 1, "m_60DB8F99": false},
            "delta": -1,
            "m_outPlug": {"$": "Plug", "links": []},
        });
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let store = load(&data).unwrap();
        assert!(store.uses_room_spatial_frame());
        let action = store.graph(42).unwrap().node(2).unwrap();
        assert!(action.declares_health_change_action() && action.is_action());
        assert!(!action.is_networked() && !action.is_state());
        assert_eq!(action.class, "ActionHealthChange");

        for field in ["target", "delta", "m_outPlug"] {
            let mut invalid = data.clone();
            invalid["graphs"][0]["nodes"][2]["fields"][field] = Value::Null;
            let error = load(&invalid).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(
                error
                    .to_string()
                    .contains("health change action in graph 42 at node 2")
            );
            assert!(error.to_string().contains(field));
        }
        data["graphs"][0]["nodes"][2]["server_only"] = json!(false);
        let error = load(&data).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(
            error
                .to_string()
                .contains("must be server-only with no state slot")
        );
    }

    #[test]
    fn radial_health_action_requires_a_server_radius_and_delta() {
        let mut data = fixture();
        let node = &mut data["graphs"][0]["nodes"][2];
        node["action_behavior"] = json!("radial_health");
        node["server_only"] = json!(true);
        node["fields"] = json!({
            "area": {"$":"EntitiesInRadius","center":{"$":"EntityPosition","entity":null},"radius":4},
            "delta": -5,
            "m_outPlug": {"$":"Plug","links":[]},
        });

        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let store = load(&data).unwrap();
        assert_eq!(store.room_spatial_needs(), RoomSpatialNeeds::Queries);
        let action = store.graph(42).unwrap().node(2).unwrap();
        assert!(action.declares_radial_health_action() && action.is_action());
        assert!(!action.is_networked() && !action.is_state());

        for field in ["area", "delta", "edge_scale", "m_outPlug"] {
            let mut invalid = data.clone();
            invalid["graphs"][0]["nodes"][2]["fields"][field] = Value::Null;
            let error = load(&invalid).unwrap_err().to_string();
            assert!(error.contains("radial health action in graph 42 at node 2"));
        }
        data["graphs"][0]["nodes"][2]["server_only"] = json!(false);
        let error = load(&data).unwrap_err().to_string();
        assert!(error.contains("must be server-only with no state slot"));
    }

    #[test]
    fn room_cue_requires_an_authored_server_action_audience_and_assets() {
        let mut data = fixture();
        let node = &mut data["graphs"][0]["nodes"][2];
        node["action_behavior"] = json!("room_cue");
        node["server_only"] = json!(true);
        node["fields"] = json!({
            "audience": [17],
            "stimulus": "0x00000000000000A1",
            "line": "0x00000000000000B2",
            "m_outPlug": {"$": "Plug", "links": []},
        });

        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let store = load(&data).unwrap();
        let action = store.graph(42).unwrap().node(2).unwrap();
        assert_eq!(action.class, "ActionRoomCue");
        assert!(action.is_action() && !action.is_networked() && !action.is_state());
        assert_eq!(
            action.room_cue(),
            Some(RoomCue {
                stimulus: 0xA1,
                line: 0xB2,
                speaker: RoomCueSpeaker::Actor,
            })
        );
        assert_eq!(store.room_spatial_needs(), RoomSpatialNeeds::Queries);

        for (field, value, diagnostic) in [
            ("audience", Value::Null, "audience expression"),
            ("stimulus", json!("0x0000000000000000"), "asset GUIDs"),
            ("stimulus", json!("0x00000000000000a1"), "asset GUIDs"),
            ("line", json!(178), "asset GUIDs"),
            ("line", json!("0xB2"), "asset GUIDs"),
            ("speaker", json!("other"), "actor or mode speaker"),
            ("speaker", Value::Null, "actor or mode speaker"),
            ("m_outPlug", Value::Null, "m_outPlug output plug"),
        ] {
            let mut malformed = data.clone();
            malformed["graphs"][0]["nodes"][2]["fields"][field] = value;
            let error = load(&malformed).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            let error = error.to_string();
            assert!(error.contains("room cue action in graph 42 at node 2"));
            assert!(error.contains(diagnostic), "{error}");
        }

        data["graphs"][0]["nodes"][2]["server_only"] = json!(false);
        let error = load(&data).unwrap_err().to_string();
        assert!(error.contains("must be server-only with no state slot"));

        let node = &mut data["graphs"][0]["nodes"][2];
        node["action_behavior"] = Value::Null;
        node["class"] = json!("ActionRoomCue");
        let store = load(&data).unwrap();
        let action = store.graph(42).unwrap().node(2).unwrap();
        assert!(!action.declares_room_cue_action());
        assert_eq!(action.room_cue(), None);
        assert_eq!(store.room_spatial_needs(), RoomSpatialNeeds::None);

        let node = &mut data["graphs"][0]["nodes"][2];
        node["class"] = json!("authored.cue");
        node["fields"]["audience"] = json!({
            "$": "EntitiesInRadius",
            "center": {"$": "EntityPosition", "entity": null},
            "radius": 10,
            "filter": {"tags": {"all": ["0xF100000000000007"]}},
        });
        node["fields"]["speaker"] = json!("mode");
        data["authored_classes"] =
            json!([{"class": "authored.cue", "behavior": "room_cue_action"}]);
        let store = load(&data).unwrap();
        let action = store.graph(42).unwrap().node(2).unwrap();
        assert_eq!(action.class, "ActionRoomCue");
        assert!(action.server_only && action.declares_room_cue_action());
        assert_eq!(
            action.room_cue(),
            Some(RoomCue {
                stimulus: 0xA1,
                line: 0xB2,
                speaker: RoomCueSpeaker::Mode,
            })
        );
        assert_eq!(store.room_spatial_needs(), RoomSpatialNeeds::EntityTags);
    }

    #[test]
    fn plain_state_declarations_require_a_unique_server_state_slot() {
        let mut data = fixture();
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let legacy = load(&data).unwrap();
        let graph = legacy.graph(42).unwrap();
        assert_eq!(graph.state(0).unwrap().pos, graph.state(3).unwrap().pos);

        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("plain");
        let error = load(&data).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("graph 42 at node 0"));
        assert!(
            error
                .to_string()
                .contains("occupy its state slot exactly once")
        );
        data["graphs"][0]["states"][3] = Value::Null;

        let store = load(&data).unwrap();
        let graph = store.graph(42).unwrap();
        let plain = graph.state(0).unwrap();
        assert!(plain.is_state());
        assert!(!plain.is_networked());
        assert!(graph.owner_positions().is_empty());

        for (field, value) in [
            ("server_only", json!(false)),
            ("client_only", json!(true)),
            ("state", json!(1)),
            ("state", Value::Null),
            ("state_behavior", json!("unrecognized")),
        ] {
            let mut malformed = data.clone();
            malformed["graphs"][0]["nodes"][0][field] = value;
            assert_eq!(
                load(&malformed).unwrap_err().kind(),
                io::ErrorKind::InvalidData
            );
        }
        data["graphs"][0]["nodes"][0]["fields"]["m_ADEB6E05"] = json!(true);
        assert_eq!(load(&data).unwrap_err().kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn plain_state_declarations_validate_optional_lifecycle_plugs() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("plain");

        for path in [
            "m_onBeginPlug",
            "m_onEndPlug",
            "m_onAbortPlug",
            "m_onFinishedPlug",
        ] {
            let mut sample = data.clone();
            sample["graphs"][0]["nodes"][0]["fields"][path] =
                json!({"$": "Plug", "links": [[2, "input"], [null, "unresolved"]]});
            assert!(load(&sample).is_ok());

            for malformed in [
                json!({"$": "Plug"}),
                json!({"$": 1, "links": []}),
                json!({"$": "Plug", "links": [[2, 3]]}),
            ] {
                sample["graphs"][0]["nodes"][0]["fields"][path] = malformed;
                let error = load(&sample).unwrap_err();
                assert_eq!(error.kind(), io::ErrorKind::InvalidData);
                assert!(error.to_string().contains("graph 42 at node 0"));
                assert!(error.to_string().contains(path));

                sample["graphs"][0]["nodes"][0]["state_behavior"] = Value::Null;
                assert!(load(&sample).is_ok());
                sample["graphs"][0]["nodes"][0]["state_behavior"] = json!("plain");
            }
            sample["graphs"][0]["nodes"][0]["fields"][path] = Value::Null;
            assert!(load(&sample).is_ok());
        }
    }

    #[test]
    fn observe_state_declarations_require_a_writable_output_and_valid_change_plug() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("observe");
        node["fields"] = json!({
            "value": false,
            "output": {"$": "STUConfigVarDynamic", "m_identifier": 7, "m_60DB8F99": true},
            "on_change": {"$": "Plug", "links": [[2, "input"]]},
        });

        for (field, invalid, message) in [
            ("value", Value::Null, "value expression"),
            ("output", json!(3), "writable output"),
            (
                "on_change",
                json!({"$": "Plug", "links": [[2, 3]]}),
                "on_change",
            ),
        ] {
            let mut sample = data.clone();
            sample["graphs"][0]["nodes"][0]["fields"][field] = invalid;
            let error = load(&sample).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(error.to_string().contains("graph 42 at node 0"));
            assert!(error.to_string().contains(message), "{error}");
        }

        let graph = load(&data).unwrap();
        let observe = graph.graph(42).unwrap().state(0).unwrap();
        assert!(observe.declares_observe_state() && observe.is_state() && !observe.is_networked());
        assert_eq!(observe.class, "StateObserve");

        data["authored_classes"] = json!([
            {"class": "authored.sample", "behavior": "observe_state"}
        ]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.sample");
        node["state_behavior"] = Value::Null;
        node["server_only"] = json!(false);
        node["fields"]["on_change"] = Value::Null;
        assert!(load(&data).is_ok());

        for plug in [Value::Null, json!({"$":"Plug","links":[]})] {
            data["graphs"][0]["nodes"][0]["fields"]["on_sample"] = plug;
            assert!(load(&data).is_ok());
        }
        data["graphs"][0]["nodes"][0]["fields"]["on_sample"] =
            json!({"$":"Plug","links":[[0,false]]});
        let error = load(&data).unwrap_err();
        assert!(error.to_string().contains("invalid on_sample output plug"));

        data["graphs"][0]["nodes"][0]["fields"]["on_sample"] = Value::Null;
        for plug in [Value::Null, json!({"$":"Plug","links":[]})] {
            data["graphs"][0]["nodes"][0]["fields"]["on_unavailable"] = plug;
            assert!(load(&data).is_ok());
        }
        data["graphs"][0]["nodes"][0]["fields"]["on_unavailable"] =
            json!({"$":"Plug","links":[[0,false]]});
        let error = load(&data).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("invalid on_unavailable output plug")
        );
    }

    #[test]
    fn interval_state_declarations_require_scope_period_and_output() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let rejects = |data: &Value, expected: &str| {
            let error = load(data).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            let message = error.to_string();
            assert!(message.contains("graph 42 at node 0"), "{message}");
            assert!(message.contains(expected), "{message}");
        };
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("interval");
        node["fields"] = json!({
            "interval_frames": 1,
            "on_interval": {"$": "Plug", "links": []},
        });

        let graph = load(&data).unwrap();
        let interval = graph.graph(42).unwrap().state(0).unwrap();
        assert_eq!(interval.class, "StateInterval");
        assert!(interval.is_state() && !interval.is_networked());
        assert_eq!(interval.interval_frames().map(NonZeroU32::get), Some(1));

        for period in [
            Value::Null,
            json!(0),
            json!(-1),
            json!(1.0),
            json!(true),
            json!("1"),
            json!(u64::from(u32::MAX) + 1),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"]["interval_frames"] = period;
            rejects(&bad, "interval_frames");
        }
        for plug in [
            Value::Null,
            json!({"$": "Plug"}),
            json!({"$": "Plug", "links": [[2, 3]]}),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"]["on_interval"] = plug;
            rejects(&bad, "on_interval");
        }
        let mut missing_plug = data.clone();
        missing_plug["graphs"][0]["nodes"][0]["fields"]
            .as_object_mut()
            .unwrap()
            .remove("on_interval");
        rejects(&missing_plug, "on_interval");
        for (field, invalid) in [
            ("server_only", json!(false)),
            ("client_only", json!(true)),
            ("state", Value::Null),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0][field] = invalid;
            rejects(&bad, "server-only and occupy its state slot exactly once");
        }
        let mut bad_lifecycle = data.clone();
        bad_lifecycle["graphs"][0]["nodes"][0]["fields"]["m_onBeginPlug"] = json!(3);
        rejects(&bad_lifecycle, "m_onBeginPlug");

        let node = &mut data["graphs"][0]["nodes"][0];
        node["state_behavior"] = Value::Null;
        node["class"] = json!("StateInterval");
        let graph = load(&data).unwrap();
        assert!(
            graph
                .graph(42)
                .unwrap()
                .state(0)
                .unwrap()
                .interval_frames()
                .is_none()
        );

        data["authored_classes"] =
            json!([{"class": "authored.timer", "behavior": "interval_state"}]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.timer");
        node["server_only"] = json!(false);
        node["fields"]["interval_frames"] = json!(u32::MAX);
        let graph = load(&data).unwrap();
        let interval = graph.graph(42).unwrap().state(0).unwrap();
        assert_eq!(interval.class, "StateInterval");
        assert!(interval.server_only && !interval.client_only);
        assert_eq!(
            interval.interval_frames().map(NonZeroU32::get),
            Some(u32::MAX)
        );
    }

    fn held_input_fixture() -> Value {
        let node = |position, class, state, server_only, fields| {
            json!({
                "pos":position,"rid":position,"uid":position,"remote":-1,"state":state,
                "class":class,"server_only":server_only,"client_only":false,"fields":fields,
            })
        };
        let mut input = node(
            0,
            "authored.aim_input",
            0,
            true,
            json!({"button":29,"held_states":[1],"idle_states":[2]}),
        );
        input["state_behavior"] = json!("held_input");
        json!({"version":1,"bodies":{},"missing":[],"graphs":[{
            "index":42,"items":4,"containers":{},"unknown_nodes":[],
            "states":[0,1,2,3],"entries":[],"remote":[],"sync_vars":[],
            "header":{"m_nodesBitCount":3,"m_statesBitCount":3,
                "m_remoteSyncNodesBitCount":3,"m_syncVarsBitCount":3},
            "fields":{"m_680A2CB2":[3,3,null,"ignored",-1,65536]},
            "nodes":[input,
                node(1,"STUStatescriptState",1,false,json!({"m_2BBEEAB8":true})),
                node(2,"STUStatescriptState",2,true,json!({"m_2BBEEAB8":true})),
                node(3,"STUStatescriptStateLogicalButton",3,false,json!({
                    "m_2BBEEAB8":true,"m_logicalButton":20}))
            ]
        }]})
    }

    #[test]
    fn authored_held_input_lowers_owned_stages_without_rewriting_legacy_dispatch() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let data = held_input_fixture();
        let graphs = load(&data).unwrap();
        let graph = graphs.graph(42).unwrap();
        assert_eq!(graph.fields, data["graphs"][0]["fields"]);
        assert_eq!(graph.logical_button_slots().collect::<Vec<_>>(), [3, 3, 0]);
        let input = graph.state(0).unwrap();
        assert_eq!(input.class, "STUStatescriptStateLogicalButton");
        assert!(input.is_state() && input.server_only && !input.is_networked());
        assert_eq!(
            input.field("m_logicalButton"),
            Some(&json!({
            "$":"STUConfigVarLogicalButton","m_logicalButton":29}))
        );
        assert_eq!(
            input.field("m_80AF45FB"),
            Some(&json!({
            "$":"Plug","links":[[1,"m_beginPlug"]],"m_FF3DAF1E":[1]}))
        );
        assert_eq!(
            input.field("m_1C9EF058"),
            Some(&json!({
            "$":"Plug","links":[[2,"m_beginPlug"]],"m_FF3DAF1E":[2]}))
        );
        assert_eq!(input.field("m_5AE05408"), Some(&json!(false)));
        for name in ["m_onGoingDownPlug", "m_onComingUpPlug"] {
            assert_eq!(input.field(name), Some(&json!({"$":"Plug","links":[]})));
        }

        let mut registered = data.clone();
        registered["graphs"][0]["fields"]["m_680A2CB2"] = json!([3, 0, 3, 0]);
        let graphs = load(&registered).unwrap();
        assert_eq!(
            graphs
                .graph(42)
                .unwrap()
                .logical_button_slots()
                .collect::<Vec<_>>(),
            [3, 0, 3, 0]
        );
        let mut automatic = data.clone();
        automatic["graphs"][0]["fields"] = json!({});
        let graphs = load(&automatic).unwrap();
        assert_eq!(
            graphs
                .graph(42)
                .unwrap()
                .logical_button_slots()
                .collect::<Vec<_>>(),
            [0]
        );
        let mut multiple = automatic;
        let second = &mut multiple["graphs"][0]["nodes"][3];
        second["server_only"] = json!(true);
        second["state_behavior"] = json!("held_input");
        second["fields"] = json!({"button":20,"held_states":[2]});
        multiple["graphs"][0]["nodes"]
            .as_array_mut()
            .unwrap()
            .reverse();
        let graphs = load(&multiple).unwrap();
        assert_eq!(
            graphs
                .graph(42)
                .unwrap()
                .logical_button_slots()
                .collect::<Vec<_>>(),
            [0, 3]
        );

        let mut alias = data;
        alias["authored_classes"] = json!([
            {"class":"authored.aim_input","behavior":"held_input_state"}]);
        let input = &mut alias["graphs"][0]["nodes"][0];
        input["state_behavior"] = Value::Null;
        input["server_only"] = json!(false);
        input["fields"]
            .as_object_mut()
            .unwrap()
            .remove("idle_states");
        input["fields"]["allow_dead"] = json!(true);
        input["fields"]["on_press"] = json!({"$":"Plug","links":[[3,"m_beginPlug"]]});
        input["fields"]["on_release"] = json!({"$":"Plug","links":[]});
        let graphs = load(&alias).unwrap();
        let input = graphs.graph(42).unwrap().state(0).unwrap();
        assert!(input.server_only && !input.client_only);
        assert_eq!(input.field("m_5AE05408"), Some(&json!(true)));
        assert_eq!(
            input.field("m_onGoingDownPlug"),
            Some(&json!({
            "$":"Plug","links":[[3,"m_beginPlug"]]}))
        );
        assert_eq!(
            input.field("m_1C9EF058"),
            Some(&json!({
            "$":"Plug","links":[],"m_FF3DAF1E":[]}))
        );
    }

    #[test]
    fn authored_held_input_rejects_malformed_or_conflicting_configuration() {
        let source = held_input_fixture();
        let rejects = |data: &Value| {
            let error = GraphStore::from_json(&serde_json::to_vec(data).unwrap()).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(
                error
                    .to_string()
                    .contains("held input state in graph 42 at node 0"),
                "{error}"
            );
        };
        for (name, invalid) in [
            ("button", json!(null)),
            ("button", json!(0)),
            ("button", json!(256)),
            ("button", json!(-1)),
            ("button", json!(29.0)),
            ("button", json!(true)),
            ("button", json!("29")),
            ("held_states", json!(null)),
            ("held_states", json!([])),
            ("held_states", json!({})),
            ("held_states", json!([null])),
            ("held_states", json!([1.0])),
            ("held_states", json!([-1])),
            ("held_states", json!([65536])),
            ("held_states", json!([0])),
            ("held_states", json!([1, 1])),
            ("held_states", json!([9])),
            ("idle_states", json!(null)),
            ("idle_states", json!([1])),
            ("allow_dead", json!(null)),
            ("allow_dead", json!(1)),
            ("allow_dead", json!("false")),
            ("on_press", json!(null)),
            ("on_press", json!({"$":"Plug"})),
            ("on_release", json!({"$":"Plug","links":[[1,7]]})),
        ] {
            let mut invalid_data = source.clone();
            invalid_data["graphs"][0]["nodes"][0]["fields"][name] = invalid;
            rejects(&invalid_data);
        }
        for name in ["button", "held_states"] {
            let mut missing = source.clone();
            missing["graphs"][0]["nodes"][0]["fields"]
                .as_object_mut()
                .unwrap()
                .remove(name);
            rejects(&missing);
        }
        for name in [
            "m_logicalButton",
            "m_80AF45FB",
            "m_1C9EF058",
            "m_onGoingDownPlug",
            "m_onComingUpPlug",
            "m_5AE05408",
            "m_53C4B6A6",
            "m_246438AD",
        ] {
            let mut conflicting = source.clone();
            conflicting["graphs"][0]["nodes"][0]["fields"][name] = Value::Null;
            rejects(&conflicting);
        }
        for (name, invalid) in [
            ("server_only", json!(false)),
            ("client_only", json!(true)),
            ("state", json!(null)),
        ] {
            let mut invalid_data = source.clone();
            invalid_data["graphs"][0]["nodes"][0][name] = invalid;
            rejects(&invalid_data);
        }
        let mut conflict = source;
        conflict["authored_classes"] = json!([
            {"class":"authored.aim_input","behavior":"plain_state"}]);
        assert!(
            GraphStore::from_json(&serde_json::to_vec(&conflict).unwrap())
                .unwrap_err()
                .to_string()
                .contains("conflicts with scope or behavior")
        );
    }

    #[test]
    fn authored_held_input_requires_real_unambiguous_stage_targets() {
        let source = held_input_fixture();
        let rejects = |data: &Value| {
            let error = GraphStore::from_json(&serde_json::to_vec(data).unwrap()).unwrap_err();
            assert!(error.to_string().contains("held_states slot 1"), "{error}");
        };
        for (name, invalid) in [
            ("client_only", json!(true)),
            ("state", json!(2)),
            ("fields", json!({"m_2BBEEAB8":false})),
            ("fields", json!({"m_2BBEEAB8":true,"m_ADEB6E05":true})),
        ] {
            let mut invalid_data = source.clone();
            invalid_data["graphs"][0]["nodes"][1][name] = invalid;
            rejects(&invalid_data);
        }
        for position in [json!(null), json!(99)] {
            let mut invalid_data = source.clone();
            invalid_data["graphs"][0]["states"][1] = position;
            rejects(&invalid_data);
        }
        let mut duplicated = source.clone();
        duplicated["graphs"][0]["states"]
            .as_array_mut()
            .unwrap()
            .push(json!(1));
        rejects(&duplicated);
        let mut duplicate_own = source.clone();
        duplicate_own["graphs"][0]["states"]
            .as_array_mut()
            .unwrap()
            .push(json!(0));
        assert!(
            GraphStore::from_json(&serde_json::to_vec(&duplicate_own).unwrap())
                .unwrap_err()
                .to_string()
                .contains("occupy its state slot exactly once")
        );

        // A runnable legacy state is permitted; network visibility is independent
        // of stage ownership, and no engine-specific class list is imposed here.
        let mut legacy_target = source;
        legacy_target["graphs"][0]["nodes"][0]["fields"]["held_states"] = json!([3]);
        let graphs = GraphStore::from_json(&serde_json::to_vec(&legacy_target).unwrap()).unwrap();
        assert_eq!(
            graphs
                .graph(42)
                .unwrap()
                .state(0)
                .unwrap()
                .field("m_80AF45FB"),
            Some(&json!({"$":"Plug","links":[[3,"m_beginPlug"]],"m_FF3DAF1E":[3]}))
        );
    }

    #[test]
    fn authored_button_lock_requires_server_scope_and_valid_config() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("button_lock");
        node["fields"] = json!({"button": 255});

        let graphs = load(&data).unwrap();
        let lock = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(lock.class, "StateButtonLock");
        assert_eq!(lock.button_lock(), Some((255, None)));
        assert!(lock.is_state() && !lock.is_networked());

        for (field, invalid) in [
            ("button", json!(0)),
            ("button", json!(256)),
            ("button", json!(-1)),
            ("button", json!(1.0)),
            ("duration_frames", Value::Null),
            ("duration_frames", json!(0)),
            ("duration_frames", json!(1.0)),
            ("duration_frames", json!(u64::from(u32::MAX) + 1)),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"][field] = invalid;
            let error = load(&bad).unwrap_err().to_string();
            assert!(
                error.contains("button lock state in graph 42 at node 0"),
                "{error}"
            );
            assert!(error.contains(field), "{error}");
        }
        for (field, invalid) in [("server_only", false), ("client_only", true)] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0][field] = json!(invalid);
            assert!(load(&bad).unwrap_err().to_string().contains("server-only"));
        }

        data["authored_classes"] =
            json!([{"class": "authored.lock", "behavior": "button_lock_state"}]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.lock");
        node["state_behavior"] = Value::Null;
        node["server_only"] = json!(false);
        node["fields"] = json!({"button": 1, "duration_frames": u32::MAX});
        let graphs = load(&data).unwrap();
        let lock = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(lock.class, "StateButtonLock");
        assert_eq!(lock.button_lock(), Some((1, NonZeroU32::new(u32::MAX))));
        assert!(lock.server_only && !lock.client_only);
    }

    #[test]
    fn authored_movement_scale_validates_scope_factor_duration_and_alias() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("movement_scale");
        node["fields"] = json!({"factor": 0});

        let graphs = load(&data).unwrap();
        let scale = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(scale.class, "StateMovementScale");
        assert_eq!(scale.movement_scale(), Some((0.0, None)));
        assert!(scale.is_state() && !scale.is_networked());

        for fields in [
            json!({}),
            json!({"factor":-0.01}),
            json!({"factor":1.01}),
            json!({"factor":"0.5"}),
            json!({"factor":true}),
            json!({"factor":null}),
            json!({"factor":0.5,"duration_frames":null}),
            json!({"factor":0.5,"duration_frames":0}),
            json!({"factor":0.5,"duration_frames":1.0}),
            json!({"factor":0.5,"duration_frames":u64::from(u32::MAX) + 1}),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"] = fields;
            let error = load(&bad).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(
                error
                    .to_string()
                    .contains("movement scale state in graph 42 at node 0"),
                "{error}"
            );
        }
        let mut bad = data.clone();
        bad["graphs"][0]["nodes"][0]["server_only"] = json!(false);
        assert!(load(&bad).unwrap_err().to_string().contains("server-only"));

        data["authored_classes"] =
            json!([{"class": "authored.scale", "behavior": "movement_scale_state"}]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.scale");
        node["state_behavior"] = Value::Null;
        node["server_only"] = json!(false);
        node["fields"] = json!({"factor": 1.0, "duration_frames": u32::MAX});
        let graphs = load(&data).unwrap();
        let scale = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(scale.class, "StateMovementScale");
        assert_eq!(
            scale.movement_scale(),
            Some((1.0, NonZeroU32::new(u32::MAX)))
        );
        assert!(scale.server_only && !scale.client_only);
    }

    #[test]
    fn authored_facing_claim_requires_direction_and_explicit_priority() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("facing_claim");
        node["fields"] = json!({
            "direction":{"$":"EntityDirection","entity":null},
            "priority":{"level":-3,"above_equals":true}
        });
        let graphs = load(&data).unwrap();
        let claim = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(claim.class, "StateFacingClaim");
        assert!(claim.declares_facing_claim() && claim.is_state() && !claim.is_networked());
        assert_eq!(
            claim.facing_claim_priority(),
            Some(StackPriority {
                level: -3,
                above_equals: true
            })
        );

        for (field, value) in [
            ("direction", Value::Null),
            ("priority", Value::Null),
            ("priority", json!({"level":128,"above_equals":true})),
            ("priority", json!({"level":-129,"above_equals":true})),
            ("priority", json!({"level":1.5,"above_equals":true})),
            ("priority", json!({"level":1,"above_equals":1})),
            ("priority", json!({"level":1})),
            (
                "priority",
                json!({"level":1,"above_equals":true,"extra":false}),
            ),
            ("m_onAbortPlug", json!({"$":"Plug","links":[[0,false]]})),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"][field] = value;
            let error = load(&bad).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(
                error
                    .to_string()
                    .contains("facing claim state in graph 42 at node 0")
            );
        }
        for field in ["direction", "priority"] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"]
                .as_object_mut()
                .unwrap()
                .remove(field);
            assert_eq!(load(&bad).unwrap_err().kind(), io::ErrorKind::InvalidData);
        }
        let mut bad = data.clone();
        bad["graphs"][0]["nodes"][0]["server_only"] = json!(false);
        assert!(load(&bad).unwrap_err().to_string().contains("server-only"));

        data["authored_classes"] =
            json!([{"class":"authored.facing","behavior":"facing_claim_state"}]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.facing");
        node["state_behavior"] = Value::Null;
        node["server_only"] = json!(false);
        let graphs = load(&data).unwrap();
        let claim = graphs.graph(42).unwrap().state(0).unwrap();
        assert!(claim.declares_facing_claim() && claim.server_only && !claim.client_only);
        assert_eq!(claim.class, "StateFacingClaim");
    }

    #[test]
    fn authored_stack_claim_validates_binding_and_keeps_null_as_a_value() {
        use crate::script_expr::Scope;

        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let mut data = fixture();
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("stack_claim");
        node["fields"] = json!({
            "variable":{"scope":"entity","id":9},
            "priority":{"level":-3,"above_equals":true},
            "value":null,
            "duration_frames":3,
            "m_topSubgraphPlug":{"$":"Plug","links":[]},
            "m_underSubgraphPlug":null
        });
        let graphs = load(&data).unwrap();
        let claim = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(claim.class, "StateStackClaim");
        assert!(claim.is_state() && !claim.is_networked());
        assert_eq!(claim.stack_value(), Some(&Value::Null));
        assert_eq!(
            claim.stack_claim(),
            Some((
                Variable {
                    scope: Scope::Entity,
                    id: 9
                },
                StackPriority {
                    level: -3,
                    above_equals: true
                },
                NonZeroU32::new(3),
            ))
        );
        for (field, bad_value) in [
            ("variable", json!({"scope":"entity","id":0})),
            ("priority", json!({"level":128,"above_equals":true})),
            ("duration_frames", json!(0)),
            ("m_topSubgraphPlug", json!({"$":"Plug","links":[[0,false]]})),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"][field] = bad_value;
            let error = load(&bad).unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("stack claim state in graph 42 at node 0")
            );
        }
        let mut missing = data.clone();
        missing["graphs"][0]["nodes"][0]["fields"]
            .as_object_mut()
            .unwrap()
            .remove("value");
        assert_eq!(
            load(&missing).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );

        data["authored_classes"] =
            json!([{"class":"authored.claim","behavior":"stack_claim_state"}]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.claim");
        node["server_only"] = json!(false);
        node["state_behavior"] = Value::Null;
        node["fields"]["variable"]["scope"] = json!("instance");
        let graphs = load(&data).unwrap();
        let claim = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(claim.class, "StateStackClaim");
        assert!(claim.server_only && !claim.client_only);
        assert_eq!(claim.stack_claim().unwrap().0.scope, Scope::Instance);
    }

    #[test]
    fn track_entities_state_validates_child_bindings_after_loading_all_graphs() {
        let load = |data: &Value| GraphStore::from_json(&serde_json::to_vec(data).unwrap());
        let rejects = |data: &Value, expected: &str| {
            let error = load(data).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            let message = error.to_string();
            assert!(message.contains("graph 42 at node 0"), "{message}");
            assert!(message.contains(expected), "{message}");
        };

        let mut data = fixture();
        let mut child = data["graphs"][0].clone();
        child["index"] = json!(0);
        data["graphs"][0]["states"][3] = Value::Null;
        let node = &mut data["graphs"][0]["nodes"][0];
        node["state"] = json!(0);
        node["server_only"] = json!(true);
        node["state_behavior"] = json!("track_entities");
        node["fields"] = json!({
            "targets": {"$": "STUConfigVarDynamic", "m_identifier": 3, "m_60DB8F99": false},
            "child_graph": 0,
            "target_variable": u32::MAX,
        });
        rejects(&data, "references missing child_graph 0");
        data["graphs"].as_array_mut().unwrap().push(child);

        let graphs = load(&data).unwrap();
        let node = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(node.class, "StateTrackEntities");
        assert_eq!(node.target_child_binding(), Some((0, u32::MAX)));
        assert!(node.is_state() && !node.is_networked());
        assert!(!node.tracks_room_actor_identity());
        assert_eq!(graphs.room_spatial_needs(), RoomSpatialNeeds::None);

        let mut room_actor = data.clone();
        room_actor["graphs"][0]["nodes"][0]["fields"]["identity"] = json!("room_actor");
        let graphs = load(&room_actor).unwrap();
        assert!(
            graphs
                .graph(42)
                .unwrap()
                .state(0)
                .unwrap()
                .tracks_room_actor_identity()
        );
        assert_eq!(graphs.room_spatial_needs(), RoomSpatialNeeds::Queries);

        for (field, invalid) in [
            ("targets", Value::Null),
            ("child_graph", json!(-1)),
            ("child_graph", json!(0.0)),
            ("child_graph", json!(u64::from(u32::MAX) + 1)),
            ("target_variable", json!(0)),
            ("target_variable", json!(true)),
            ("target_variable", json!(u64::from(u32::MAX) + 1)),
            ("identity", Value::Null),
            ("identity", json!(true)),
            ("identity", json!("unknown")),
        ] {
            let mut bad = data.clone();
            bad["graphs"][0]["nodes"][0]["fields"][field] = invalid;
            rejects(&bad, field);
        }
        let mut bad_scope = data.clone();
        bad_scope["graphs"][0]["nodes"][0]["client_only"] = json!(true);
        rejects(
            &bad_scope,
            "server-only and occupy its state slot exactly once",
        );

        let node = &mut data["graphs"][0]["nodes"][0];
        node["state_behavior"] = Value::Null;
        node["class"] = json!("StateTrackEntities");
        let graphs = load(&data).unwrap();
        assert!(
            graphs
                .graph(42)
                .unwrap()
                .state(0)
                .unwrap()
                .target_child_binding()
                .is_none()
        );

        data["authored_classes"] =
            json!([{"class": "authored.members", "behavior": "track_entities_state"}]);
        let node = &mut data["graphs"][0]["nodes"][0];
        node["class"] = json!("authored.members");
        node["server_only"] = json!(false);
        let graphs = load(&data).unwrap();
        let node = graphs.graph(42).unwrap().state(0).unwrap();
        assert_eq!(node.class, "StateTrackEntities");
        assert_eq!(node.target_child_binding(), Some((0, u32::MAX)));
        assert!(node.server_only && !node.client_only);
    }

    #[test]
    fn dangling_link_report_preserves_unresolved_links_and_data_order() {
        let mut data = fixture();
        let links = &mut data["graphs"][0]["nodes"][0]["fields"]["entries"][0]["trigger"]["links"];
        *links = json!([
            [2, "input"],
            [null, "unresolved"],
            [3, "container"],
            [4, "empty slot"],
            [9, "outside table"],
        ]);
        let store = GraphStore::from_json(&serde_json::to_vec(&data).unwrap()).unwrap();
        let plug = "entries[0].trigger";
        assert_eq!(
            store.dangling_links().collect::<Vec<_>>(),
            [(3, 4), (4, 9)].map(|(link_index, target)| DanglingLink {
                graph: 42,
                source: 0,
                plug: plug.into(),
                link_index,
                target,
            })
        );
        let graph = store.graph(42).unwrap();
        assert_eq!(
            graph.node(0).unwrap().links(plug).collect::<Vec<_>>(),
            [
                (Some(2), "input"),
                (None, "unresolved"),
                (Some(3), "container"),
                (Some(4), "empty slot"),
                (Some(9), "outside table"),
            ]
        );
        assert_eq!(graph.owner_positions(), [0, 0]);
    }

    #[test]
    fn builds_wire_slots_and_keeps_data_order_through_holes() {
        let store = GraphStore::from_json(&serde_json::to_vec(&fixture()).unwrap()).unwrap();
        let graph = store.graph(42).unwrap();
        assert_eq!(graph.owner_positions(), [0, 0]);
        assert_eq!(graph.remote_positions(), [0]);
        assert_eq!(graph.owner_slot(0), Some(0));
        assert_eq!(graph.owner_slot(3), Some(0));
        assert_eq!(graph.owner_slot(1), None);
        assert_eq!(graph.remote_slot(0), None);
        assert_eq!(graph.remote_slot(3), Some(0));
        assert_eq!(graph.presence_bit(42), Some(1));
        assert_eq!(graph.presence_bit(12), None);
        assert_eq!(graph.graph_refs(), BTreeSet::from([26, 43]));
        assert_eq!(graph.by_rid(6).unwrap().pos, 2);
        assert_eq!(graph.node_ref(&json!({"node": 2})).unwrap().rid, 6);
        assert!(graph.node(3).is_none());

        let node = graph.node(0).unwrap();
        let plug_path = "entries[0].trigger";
        assert_eq!(node.plugs().keys().next().unwrap(), plug_path);
        assert_eq!(
            node.links(plug_path).collect::<Vec<_>>(),
            [(Some(2), "input"), (None, "unknown")]
        );
        for invalid in [
            "entries[-1].trigger",
            "entries[0]x",
            ".entries[0]",
            "entries[1]",
        ] {
            assert_eq!(node.field(invalid), None);
        }
        assert_eq!(store.missing(), [43]);
        assert_eq!(
            store.bodies()[&0x0400_0000_0000_0001].graphs[0].graph,
            Some(42)
        );
        assert_eq!(
            store.entity_body(0x0400_0000_0000_0002).unwrap().name,
            "Fixture"
        );
        assert_eq!(
            store.mode_variables(0x0230_0000_0000_0018).unwrap(),
            &BTreeMap::from([
                (1, ModeValue::Bool(false)),
                (2, ModeValue::Integer(3)),
                (3, ModeValue::Asset(0x0d80_0000_0000_195f)),
            ])
        );
    }

    #[test]
    fn reads_gzip_and_rejects_truncated_or_inconsistent_data() {
        use flate2::{Compression, write::GzEncoder};
        use std::io::Write;

        let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
        encoder
            .write_all(&serde_json::to_vec(&fixture()).unwrap())
            .unwrap();
        let bytes = encoder.finish().unwrap();
        assert!(
            GraphStore::from_gzip(bytes.as_slice())
                .unwrap()
                .graph(42)
                .is_some()
        );
        assert!(GraphStore::from_gzip(&bytes[..bytes.len() - 4]).is_err());
        let mut bad = fixture();
        bad["graphs"][0]["nodes"][0]["pos"] = json!(6);
        assert!(GraphStore::from_json(&serde_json::to_vec(&bad).unwrap()).is_err());
        bad["version"] = json!(2);
        assert!(GraphStore::from_json(&serde_json::to_vec(&bad).unwrap()).is_err());
    }

    #[test]
    #[ignore = "requires a prepared 104319 state graph bundle"]
    fn whole_retail_wire_index_matches_the_python_reference() {
        use sha2::{Digest, Sha256};
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let store = GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap();
        let mut digest = Sha256::new();
        fn sequence(digest: &mut Sha256, items: impl IntoIterator<Item = u64>) {
            let items: Vec<_> = items.into_iter().collect();
            digest.update((items.len() as u64).to_le_bytes());
            for item in items {
                digest.update(item.to_le_bytes());
            }
        }
        for index in store.graph_indexes() {
            let graph = store.graph(index).unwrap();
            sequence(
                &mut digest,
                [
                    u64::from(index),
                    u64::from(graph.items),
                    u64::from(graph.header.nodes_bits),
                    u64::from(graph.header.states_bits),
                    u64::from(graph.header.remote_bits),
                    u64::from(graph.header.sync_vars_bits),
                    graph.nodes.len() as u64,
                ],
            );
            sequence(
                &mut digest,
                graph.owner_positions().iter().copied().map(u64::from),
            );
            sequence(
                &mut digest,
                graph.remote_positions().iter().copied().map(u64::from),
            );
            sequence(
                &mut digest,
                graph.presence_vars().flat_map(|(index, var)| {
                    [
                        index as u64,
                        u64::from(var.0.unwrap()),
                        u64::from(var.1),
                        u64::from(var.2),
                    ]
                }),
            );
            sequence(&mut digest, graph.graph_refs());
            sequence(
                &mut digest,
                graph.unknown_rids.iter().copied().map(u64::from),
            );
        }
        assert_eq!(store.bodies().len(), 32);
        let expected = match store.graphs.len() {
            373 => "f54fb85e91d33b669c73239ddc77fb0f8f7fa6e6d0f04b83532bc1f953a4ac7c",
            405 => {
                assert_eq!(store.entities.len(), 8);
                assert_eq!(store.mode_variables.len(), 25);
                "eab09343964a53d44a53baa8d990ff9c2a3eea8922d9eb1927136fb7b661fc94"
            }
            699 => {
                assert_eq!(store.entities.len(), 6);
                assert_eq!(store.mode_variables.len(), 25);
                "585ab3b69f4eb0966dd755171b33b227f8eac7de595f6f5ec7392939c92229df"
            }
            700 => {
                assert_eq!(store.entities.len(), 6);
                assert_eq!(store.mode_variables.len(), 25);
                assert!(store.graph(0x0b92).is_some());
                assert_eq!(store.enemies.pairs.len(), 8);
                assert_eq!(store.enemies.entities.len(), 19);
                assert_eq!(
                    store.enemy_pair(0x02e0_0000_0000_0175),
                    Some(EnemyPair {
                        body: 0x0400_0000_0000_16be,
                        controller: 0x0400_0000_0000_16c0,
                    })
                );
                for (definition, expected) in [
                    (0x0400_0000_0000_16be, vec![0x0be2, 0x0c89, 0x0cb8, 0x0cbf]),
                    (0x0400_0000_0000_16c0, vec![0x0c87, 0x0cf3]),
                ] {
                    let roots =
                        crate::script_init::body_roots(store.enemy_body(definition).unwrap())
                            .unwrap();
                    assert_eq!(
                        roots.iter().map(|root| root.graph).collect::<Vec<_>>(),
                        expected
                    );
                    assert!(roots.iter().all(|root| store.graph(root.graph).is_some()));
                }
                "8834709ac62f67287938b1eb747a0039131abc73f6fd6568b008d8689c2d9c22"
            }
            count => panic!("no Python reference vector for the {count}-graph bundle"),
        };
        assert_eq!(format!("{:x}", digest.finalize()), expected);
    }
}
