//! Data-driven hero ability, deployable, posture, and status-effect rule catalog.
//!
//! Centralizes retail 1.74 hero-specific gameplay bindings so simulation modules
//! do not scatter raw entity/body/effect GUIDs or magic tuning constants.

use std::{
    collections::HashMap,
    fs, io,
    path::Path,
    sync::{Arc, LazyLock},
};

use serde::Deserialize;

use crate::accounts::parse_guid;

const DEFAULT_RULES_JSON: &str = include_str!("hero_ability_rules_defaults.json");
const MAX_RULES_BYTES: usize = 256 * 1024;

static DEFAULT_RULES: LazyLock<Arc<HeroAbilityRules>> = LazyLock::new(|| {
    Arc::new(
        HeroAbilityRules::from_json(DEFAULT_RULES_JSON)
            .expect("embedded hero ability rules defaults must be valid"),
    )
});

#[derive(Clone, Debug)]
pub(crate) struct IceWallRule {
    pub entity: u64,
    pub pillar_health: f64,
    pub pillar_spacing: f64,
    pub flank_offsets: Vec<i32>,
}

#[derive(Clone, Debug)]
pub(crate) struct JunkratTrapRule {
    pub entities: Vec<u64>,
    pub trigger_radius: f64,
    pub trigger_height: f64,
    pub damage: f64,
    pub root_ticks: u32,
}

impl JunkratTrapRule {
    pub(crate) fn matches_entity(&self, definition: u64) -> bool {
        self.entities.contains(&definition)
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct JunkratMineRule {
    pub entity: u64,
    pub blast_radius: f64,
    pub max_damage: f64,
    pub min_damage_ratio: f64,
    pub self_horizontal_impulse: f64,
    pub self_vertical_impulse: f64,
    pub enemy_horizontal_impulse: f64,
    pub enemy_vertical_impulse: f64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct BeamScalingRule {
    pub max_base_tick_damage: f64,
    pub symmetra_body: u64,
    pub symmetra_charge_per_tick: f32,
    pub symmetra_max_charge: f32,
    pub echo_body: u64,
    pub echo_execute_hp_ratio: f64,
    pub echo_execute_multiplier: f64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct RoadhogHookRule {
    pub body: u64,
    pub min_distance: f64,
    pub max_distance: f64,
    pub pull_stop_distance: f64,
    pub pull_ticks: u32,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct GenjiDashResetRule {
    pub body: u64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct EchoDuplicateRule {
    pub duration_ticks: u32,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DvaMechRule {
    pub meka_body: u64,
    pub pilot_body: u64,
    pub self_destruct_min_speed: f64,
    pub self_destruct_fuse_ticks: u32,
    pub call_mech_crush_damage: f64,
    pub call_mech_crush_radius: f64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct PostureBodiesRule {
    pub bastion_body: u64,
    pub bastion_sentry_max_speed: f64,
    pub wrecking_ball_body: u64,
    pub wrecking_ball_roll_min_speed: f64,
    pub wrecking_ball_roll_max_height: f64,
    pub wrecking_ball_roll_min_radius: f64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct SombraHackRule {
    pub effect: u64,
    pub interrupt_ticks: u32,
    pub health_pack_range: f64,
    pub health_pack_duration_ticks: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum ProjectileStatusEffectBehavior {
    Stun {
        wrapper_graph: u32,
        fallback_secs: f64,
    },
    Sleep {
        wrapper_graph: u32,
        fallback_secs: f64,
    },
    ChillFreeze {
        wrapper_graph: u32,
        stacks: u8,
        fallback_secs: f64,
    },
    HealModifier {
        duration_secs: f64,
    },
    HoT {
        duration_secs: f64,
        total_heal: f64,
    },
}

#[derive(Clone, Debug)]
pub(crate) struct HeroAbilityRules {
    pub ice_wall: IceWallRule,
    pub junkrat_trap: JunkratTrapRule,
    pub junkrat_mine: JunkratMineRule,
    pub beam_scaling: BeamScalingRule,
    pub roadhog_hook: RoadhogHookRule,
    pub genji_dash_reset: GenjiDashResetRule,
    pub echo_duplicate: EchoDuplicateRule,
    pub dva_mech: DvaMechRule,
    pub postures: PostureBodiesRule,
    pub sombra_hack: SombraHackRule,
    heal_aura_effects: Vec<u64>,
    projectile_status_effects: HashMap<u64, ProjectileStatusEffectBehavior>,
    status_scripts: HashMap<u64, u32>,
}

impl Default for HeroAbilityRules {
    fn default() -> Self {
        DEFAULT_RULES.as_ref().clone()
    }
}

impl HeroAbilityRules {
    /// Returns the embedded retail 1.74 rule catalog without allocating.
    pub(crate) fn defaults() -> &'static Self {
        DEFAULT_RULES.as_ref()
    }

    /// Returns a cheaply cloned `Arc` handle to the embedded default rule catalog.
    pub(crate) fn default_shared() -> Arc<Self> {
        Arc::clone(&DEFAULT_RULES)
    }

    /// Loads an optional runtime override file (falling back to embedded defaults if missing).
    pub(crate) fn load(path: impl AsRef<Path>) -> io::Result<Arc<Self>> {
        let path = path.as_ref();
        match fs::read(path) {
            Ok(bytes) => {
                if bytes.len() > MAX_RULES_BYTES {
                    return Err(invalid_data(format!(
                        "hero ability rules catalog exceeds {MAX_RULES_BYTES} bytes"
                    )));
                }
                let text = String::from_utf8(bytes)
                    .map_err(|_| invalid_data("hero ability rules catalog is not valid UTF-8"))?;
                Self::from_json(&text).map(Arc::new)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Self::default_shared()),
            Err(error) => Err(error),
        }
    }

    pub(crate) fn from_json(text: &str) -> io::Result<Self> {
        let raw: RawHeroAbilityRules = serde_json::from_str(text)
            .map_err(|error| invalid_data(format!("invalid hero ability rules JSON: {error}")))?;
        if raw.version != 1 {
            return Err(invalid_data(format!(
                "unsupported hero ability rules version {}",
                raw.version
            )));
        }

        let ice_wall = IceWallRule {
            entity: parse_rule_guid(&raw.ice_wall.entity, "ice_wall.entity")?,
            pillar_health: require_positive(raw.ice_wall.pillar_health, "ice_wall.pillar_health")?,
            pillar_spacing: require_positive(
                raw.ice_wall.pillar_spacing,
                "ice_wall.pillar_spacing",
            )?,
            flank_offsets: raw.ice_wall.flank_offsets,
        };

        let mut trap_entities = Vec::with_capacity(raw.junkrat_trap.entities.len());
        for s in &raw.junkrat_trap.entities {
            trap_entities.push(parse_rule_guid(s, "junkrat_trap.entities")?);
        }
        let junkrat_trap = JunkratTrapRule {
            entities: trap_entities,
            trigger_radius: require_positive(
                raw.junkrat_trap.trigger_radius,
                "junkrat_trap.trigger_radius",
            )?,
            trigger_height: require_positive(
                raw.junkrat_trap.trigger_height,
                "junkrat_trap.trigger_height",
            )?,
            damage: require_positive(raw.junkrat_trap.damage, "junkrat_trap.damage")?,
            root_ticks: raw.junkrat_trap.root_ticks,
        };

        let junkrat_mine = JunkratMineRule {
            entity: parse_rule_guid(&raw.junkrat_mine.entity, "junkrat_mine.entity")?,
            blast_radius: require_positive(
                raw.junkrat_mine.blast_radius,
                "junkrat_mine.blast_radius",
            )?,
            max_damage: require_positive(raw.junkrat_mine.max_damage, "junkrat_mine.max_damage")?,
            min_damage_ratio: require_unit_ratio(
                raw.junkrat_mine.min_damage_ratio,
                "junkrat_mine.min_damage_ratio",
            )?,
            self_horizontal_impulse: require_finite(
                raw.junkrat_mine.self_horizontal_impulse,
                "junkrat_mine.self_horizontal_impulse",
            )?,
            self_vertical_impulse: require_finite(
                raw.junkrat_mine.self_vertical_impulse,
                "junkrat_mine.self_vertical_impulse",
            )?,
            enemy_horizontal_impulse: require_finite(
                raw.junkrat_mine.enemy_horizontal_impulse,
                "junkrat_mine.enemy_horizontal_impulse",
            )?,
            enemy_vertical_impulse: require_finite(
                raw.junkrat_mine.enemy_vertical_impulse,
                "junkrat_mine.enemy_vertical_impulse",
            )?,
        };

        let beam_scaling = BeamScalingRule {
            max_base_tick_damage: require_positive(
                raw.beam_scaling.max_base_tick_damage,
                "beam_scaling.max_base_tick_damage",
            )?,
            symmetra_body: parse_rule_guid(
                &raw.beam_scaling.symmetra_body,
                "beam_scaling.symmetra_body",
            )?,
            symmetra_charge_per_tick: require_positive(
                f64::from(raw.beam_scaling.symmetra_charge_per_tick),
                "beam_scaling.symmetra_charge_per_tick",
            )? as f32,
            symmetra_max_charge: require_positive(
                f64::from(raw.beam_scaling.symmetra_max_charge),
                "beam_scaling.symmetra_max_charge",
            )? as f32,
            echo_body: parse_rule_guid(&raw.beam_scaling.echo_body, "beam_scaling.echo_body")?,
            echo_execute_hp_ratio: require_unit_ratio(
                raw.beam_scaling.echo_execute_hp_ratio,
                "beam_scaling.echo_execute_hp_ratio",
            )?,
            echo_execute_multiplier: require_positive(
                raw.beam_scaling.echo_execute_multiplier,
                "beam_scaling.echo_execute_multiplier",
            )?,
        };

        let roadhog_hook = RoadhogHookRule {
            body: parse_rule_guid(&raw.roadhog_hook.body, "roadhog_hook.body")?,
            min_distance: require_positive(
                raw.roadhog_hook.min_distance,
                "roadhog_hook.min_distance",
            )?,
            max_distance: require_positive(
                raw.roadhog_hook.max_distance,
                "roadhog_hook.max_distance",
            )?,
            pull_stop_distance: require_positive(
                raw.roadhog_hook.pull_stop_distance,
                "roadhog_hook.pull_stop_distance",
            )?,
            pull_ticks: raw.roadhog_hook.pull_ticks,
        };
        if roadhog_hook.min_distance >= roadhog_hook.max_distance {
            return Err(invalid_data(
                "roadhog_hook.min_distance must be < roadhog_hook.max_distance",
            ));
        }

        let genji_dash_reset = GenjiDashResetRule {
            body: parse_rule_guid(&raw.genji_dash_reset.body, "genji_dash_reset.body")?,
        };

        let echo_duplicate = EchoDuplicateRule {
            duration_ticks: raw.echo_duplicate.duration_ticks,
        };

        let dva_mech = DvaMechRule {
            meka_body: parse_rule_guid(&raw.dva_mech.meka_body, "dva_mech.meka_body")?,
            pilot_body: parse_rule_guid(&raw.dva_mech.pilot_body, "dva_mech.pilot_body")?,
            self_destruct_min_speed: require_positive(
                raw.dva_mech.self_destruct_min_speed,
                "dva_mech.self_destruct_min_speed",
            )?,
            self_destruct_fuse_ticks: raw.dva_mech.self_destruct_fuse_ticks,
            call_mech_crush_damage: require_positive(
                raw.dva_mech.call_mech_crush_damage,
                "dva_mech.call_mech_crush_damage",
            )?,
            call_mech_crush_radius: require_positive(
                raw.dva_mech.call_mech_crush_radius,
                "dva_mech.call_mech_crush_radius",
            )?,
        };

        let postures = PostureBodiesRule {
            bastion_body: parse_rule_guid(&raw.postures.bastion_body, "postures.bastion_body")?,
            bastion_sentry_max_speed: require_positive(
                raw.postures.bastion_sentry_max_speed,
                "postures.bastion_sentry_max_speed",
            )?,
            wrecking_ball_body: parse_rule_guid(
                &raw.postures.wrecking_ball_body,
                "postures.wrecking_ball_body",
            )?,
            wrecking_ball_roll_min_speed: require_positive(
                raw.postures.wrecking_ball_roll_min_speed,
                "postures.wrecking_ball_roll_min_speed",
            )?,
            wrecking_ball_roll_max_height: require_positive(
                raw.postures.wrecking_ball_roll_max_height,
                "postures.wrecking_ball_roll_max_height",
            )?,
            wrecking_ball_roll_min_radius: require_positive(
                raw.postures.wrecking_ball_roll_min_radius,
                "postures.wrecking_ball_roll_min_radius",
            )?,
        };

        let sombra_hack = SombraHackRule {
            effect: parse_rule_guid(&raw.sombra_hack.effect, "sombra_hack.effect")?,
            interrupt_ticks: raw.sombra_hack.interrupt_ticks,
            health_pack_range: require_positive(
                raw.sombra_hack.health_pack_range,
                "sombra_hack.health_pack_range",
            )?,
            health_pack_duration_ticks: raw.sombra_hack.health_pack_duration_ticks,
        };

        let mut heal_aura_effects = Vec::with_capacity(raw.heal_aura_effects.len());
        for s in &raw.heal_aura_effects {
            heal_aura_effects.push(parse_rule_guid(s, "heal_aura_effects")?);
        }

        let mut projectile_status_effects =
            HashMap::with_capacity(raw.projectile_status_effects.len());
        for entry in raw.projectile_status_effects {
            let guid = parse_rule_guid(&entry.effect, "projectile_status_effects.effect")?;
            let behavior = match entry.behavior {
                RawProjectileStatusBehavior::Stun {
                    wrapper_graph,
                    fallback_secs,
                } => ProjectileStatusEffectBehavior::Stun {
                    wrapper_graph,
                    fallback_secs: require_positive(fallback_secs, "stun.fallback_secs")?,
                },
                RawProjectileStatusBehavior::Sleep {
                    wrapper_graph,
                    fallback_secs,
                } => ProjectileStatusEffectBehavior::Sleep {
                    wrapper_graph,
                    fallback_secs: require_positive(fallback_secs, "sleep.fallback_secs")?,
                },
                RawProjectileStatusBehavior::ChillFreeze {
                    wrapper_graph,
                    stacks,
                    fallback_secs,
                } => ProjectileStatusEffectBehavior::ChillFreeze {
                    wrapper_graph,
                    stacks,
                    fallback_secs: require_positive(fallback_secs, "chill_freeze.fallback_secs")?,
                },
                RawProjectileStatusBehavior::HealModifier { duration_secs } => {
                    ProjectileStatusEffectBehavior::HealModifier {
                        duration_secs: require_positive(
                            duration_secs,
                            "heal_modifier.duration_secs",
                        )?,
                    }
                }
                RawProjectileStatusBehavior::HoT {
                    duration_secs,
                    total_heal,
                } => ProjectileStatusEffectBehavior::HoT {
                    duration_secs: require_positive(duration_secs, "hot.duration_secs")?,
                    total_heal: require_positive(total_heal, "hot.total_heal")?,
                },
            };
            projectile_status_effects.insert(guid, behavior);
        }

        Ok(Self {
            ice_wall,
            junkrat_trap,
            junkrat_mine,
            beam_scaling,
            roadhog_hook,
            genji_dash_reset,
            echo_duplicate,
            dva_mech,
            postures,
            sombra_hack,
            heal_aura_effects,
            projectile_status_effects,
            status_scripts: HashMap::new(),
        })
    }

    pub(crate) fn is_heal_aura_effect(&self, effect: u64) -> bool {
        effect == 0 || self.heal_aura_effects.contains(&effect)
    }

    pub(crate) fn status_script(&self, effect: u64) -> Option<u32> {
        self.status_scripts.get(&effect).copied()
    }

    pub(crate) fn load_with_status_scripts(
        rules_path: impl AsRef<Path>,
        scripts_path: impl AsRef<Path>,
    ) -> io::Result<Arc<Self>> {
        #[derive(Deserialize)]
        struct StatusScripts {
            version: u32,
            on_apply: HashMap<String, u32>,
        }

        let mut rules = Self::load(rules_path)?;
        let bytes = match fs::read(scripts_path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(rules),
            Err(error) => return Err(error),
        };
        if bytes.len() > MAX_RULES_BYTES {
            return Err(invalid_data("status script catalog exceeds size limit"));
        }
        let catalog: StatusScripts = serde_json::from_slice(&bytes)
            .map_err(|e| invalid_data(format!("invalid status script catalog: {e}")))?;
        if catalog.version != 1 {
            return Err(invalid_data(format!(
                "unsupported status script catalog version {}",
                catalog.version
            )));
        }
        let bindings = &mut Arc::make_mut(&mut rules).status_scripts;
        for (effect, graph) in catalog.on_apply {
            let key = parse_rule_guid(&effect, "on_apply")?;
            if key >> 48 != 0x04a0 || graph == 0 || bindings.insert(key, graph).is_some() {
                return Err(invalid_data(format!(
                    "invalid status effect binding {effect} / {graph}"
                )));
            }
        }
        Ok(rules)
    }

    pub(crate) fn projectile_status_effect(
        &self,
        effect: u64,
    ) -> Option<ProjectileStatusEffectBehavior> {
        self.projectile_status_effects.get(&effect).copied()
    }
}

fn parse_rule_guid(value: &str, field: &str) -> io::Result<u64> {
    parse_guid(value).ok_or_else(|| invalid_data(format!("invalid GUID in {field}: {value}")))
}

fn require_finite(value: f64, field: &str) -> io::Result<f64> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(invalid_data(format!("{field} must be finite")))
    }
}

fn require_positive(value: f64, field: &str) -> io::Result<f64> {
    if value.is_finite() && value > 0.0 {
        Ok(value)
    } else {
        Err(invalid_data(format!("{field} must be finite and > 0")))
    }
}

fn require_unit_ratio(value: f64, field: &str) -> io::Result<f64> {
    if value.is_finite() && (0.0..=1.0).contains(&value) {
        Ok(value)
    } else {
        Err(invalid_data(format!("{field} must be in 0.0..=1.0")))
    }
}

fn invalid_data(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawHeroAbilityRules {
    version: u32,
    ice_wall: RawIceWallRule,
    junkrat_trap: RawJunkratTrapRule,
    junkrat_mine: RawJunkratMineRule,
    beam_scaling: RawBeamScalingRule,
    roadhog_hook: RawRoadhogHookRule,
    genji_dash_reset: RawGenjiDashResetRule,
    echo_duplicate: RawEchoDuplicateRule,
    dva_mech: RawDvaMechRule,
    postures: RawPostureBodiesRule,
    sombra_hack: RawSombraHackRule,
    heal_aura_effects: Vec<String>,
    projectile_status_effects: Vec<RawProjectileStatusEffect>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIceWallRule {
    entity: String,
    pillar_health: f64,
    pillar_spacing: f64,
    flank_offsets: Vec<i32>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawJunkratTrapRule {
    entities: Vec<String>,
    trigger_radius: f64,
    trigger_height: f64,
    damage: f64,
    root_ticks: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawJunkratMineRule {
    entity: String,
    blast_radius: f64,
    max_damage: f64,
    min_damage_ratio: f64,
    self_horizontal_impulse: f64,
    self_vertical_impulse: f64,
    enemy_horizontal_impulse: f64,
    enemy_vertical_impulse: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawBeamScalingRule {
    max_base_tick_damage: f64,
    symmetra_body: String,
    symmetra_charge_per_tick: f32,
    symmetra_max_charge: f32,
    echo_body: String,
    echo_execute_hp_ratio: f64,
    echo_execute_multiplier: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRoadhogHookRule {
    body: String,
    min_distance: f64,
    max_distance: f64,
    pull_stop_distance: f64,
    pull_ticks: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawGenjiDashResetRule {
    body: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEchoDuplicateRule {
    duration_ticks: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawDvaMechRule {
    meka_body: String,
    pilot_body: String,
    self_destruct_min_speed: f64,
    self_destruct_fuse_ticks: u32,
    call_mech_crush_damage: f64,
    call_mech_crush_radius: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawPostureBodiesRule {
    bastion_body: String,
    bastion_sentry_max_speed: f64,
    wrecking_ball_body: String,
    wrecking_ball_roll_min_speed: f64,
    wrecking_ball_roll_max_height: f64,
    wrecking_ball_roll_min_radius: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawSombraHackRule {
    effect: String,
    interrupt_ticks: u32,
    health_pack_range: f64,
    health_pack_duration_ticks: u32,
}

#[derive(Deserialize)]
struct RawProjectileStatusEffect {
    effect: String,
    #[serde(flatten)]
    behavior: RawProjectileStatusBehavior,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum RawProjectileStatusBehavior {
    Stun {
        wrapper_graph: u32,
        fallback_secs: f64,
    },
    Sleep {
        wrapper_graph: u32,
        fallback_secs: f64,
    },
    ChillFreeze {
        wrapper_graph: u32,
        stacks: u8,
        fallback_secs: f64,
    },
    HealModifier {
        duration_secs: f64,
    },
    #[serde(rename = "hot")]
    HoT {
        duration_secs: f64,
        total_heal: f64,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_defaults_parse_and_cover_retail_bindings() {
        let rules = HeroAbilityRules::defaults();
        assert_eq!(rules.ice_wall.entity, 0x0400_0000_0000_090b);
        assert_eq!(rules.ice_wall.flank_offsets, vec![-2, -1, 1, 2]);
        assert!(rules.junkrat_trap.matches_entity(0x0400_0000_0000_0385));
        assert!(rules.junkrat_trap.matches_entity(0x0400_0000_0000_11a9));
        assert_eq!(rules.junkrat_mine.entity, 0x0400_0000_0000_103a);
        assert_eq!(rules.dva_mech.meka_body, 0x0400_0000_0000_0407);
        assert_eq!(rules.dva_mech.pilot_body, 0x0400_0000_0000_040a);
        assert_eq!(rules.postures.bastion_body, 0x0400_0000_0000_01fd);
        assert_eq!(rules.postures.wrecking_ball_body, 0x0400_0000_0000_200b);
        assert_eq!(rules.sombra_hack.effect, 0x04a0_0000_0000_0286);
        assert!(rules.is_heal_aura_effect(0x04a0_0000_0000_0019));
        assert!(!rules.is_heal_aura_effect(0x04a0_0000_0000_0286));
        assert!(matches!(
            rules.projectile_status_effect(0x04a0_0000_0000_026f),
            Some(ProjectileStatusEffectBehavior::Sleep {
                wrapper_graph: 0x0bde,
                ..
            })
        ));
        assert!(matches!(
            rules.projectile_status_effect(0x04a0_0000_0000_0169),
            Some(ProjectileStatusEffectBehavior::ChillFreeze { stacks: 20, .. })
        ));
    }

    #[test]
    fn rejects_unknown_fields_and_invalid_version() {
        let bad_version = DEFAULT_RULES_JSON.replace("\"version\": 1", "\"version\": 99");
        assert!(HeroAbilityRules::from_json(&bad_version).is_err());

        let unknown_field =
            DEFAULT_RULES_JSON.replace("\"version\": 1,", "\"version\": 1, \"rogue\": true,");
        assert!(HeroAbilityRules::from_json(&unknown_field).is_err());
    }

    #[test]
    fn missing_runtime_override_falls_back_to_embedded_defaults() {
        let rules = HeroAbilityRules::load("nonexistent_hero_ability_rules_174.json").unwrap();
        assert_eq!(rules.roadhog_hook.body, 0x0400_0000_0000_028f);
    }
}
