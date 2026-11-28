//! Reliable combat notices shared by weapons and damage sources.
//!
//! The field layout follows ow174/game/combat.py for build 104319. Each
//! notice exposes the identity a recipient must know before it is queued.

use crate::{
    game_messages::{self, ChannelError, EncodedMessage},
    jam_codec::{Fields, Schemas, Value},
    static_ray::Vec3,
};

pub(crate) const DAMAGE_TAKEN_MESSAGE: u64 = 0x0240_0000_0000_0011;

pub(crate) fn damage_taken_parameters(
    dealt: f64,
    direction: Vec3,
    source: u32,
    tags: &crate::script_init::HitTags,
) -> crate::script_wire::Variables {
    use crate::script_wire::{Value, Variables};
    Variables::from([
        (0x12, Value::Entity(source)),
        (0x13, Value::Float(f64::from(dealt as f32))),
        (
            0x3d,
            Value::Array(tags.as_slice().iter().copied().map(Value::Asset).collect()),
        ),
        (0x65, Value::Vector(direction)),
    ])
}

const RECEIVED_DAMAGE: u32 = 25000;
const HIT: u32 = 25001;
const HEAL_CONFIRM: u32 = 25002;
const HEAL_EVENT: u32 = 25003;
const DEATH: u32 = 25004;
const ELIMINATION: u32 = 25005;
const SCORE_POPUP: u32 = 25007;
const HIGHLIGHT_HEADER: u32 = 25011;
const COMMENDATION_CARD: u32 = 25012;
const CARD_VOTE_UPDATE: u32 = 25013;
const VOICE: u32 = 27500;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum CombatNotice {
    Hit {
        target: u32,
        dealt: f64,
        critical: bool,
        killed: bool,
        shot_frame: u32,
    },
    HealConfirm {
        target: u32,
        amount: f64,
        shot_frame: u32,
    },
    /// Retail `25003` -> client StateScript `0x02400000000008CB` heal feedback/audio event.
    HealEvent {
        healer: u32,
        target: u32,
        amount: f64,
        full: bool,
        loadout: u64,
    },
    DamageReceived {
        source: u32,
        victim: u32,
        dealt: f32,
        origin: Option<Vec3>,
        impact_point: Option<Vec3>,
        normal: Option<Vec3>,
        contact_set: u16,
        critical: bool,
        armor_or_shield: bool,
        special_vfx: bool,
    },
    Death {
        source: u32,
        killed_by: bool,
    },
    Elimination {
        killer: u32,
        victim: u32,
        loadout: u64,
        fire: u8,
        critical: bool,
    },
    ScorePopup {
        score: u16,
        label: u64,
    },
    /// Killcam / Play of the Game highlight header (`25011`).
    HighlightHeader {
        star_entity: u32,
        target_entity: u32,
        category: u32,
        star_hero: u64,
        start_tick: u32,
        end_tick: u32,
    },
    /// End-of-match commendation card (`25012`).
    CommendationCardNotice {
        card_index: u32,
        owner: u32,
        hero: u64,
        stat_guid: u64,
        value: f32,
    },
    /// End-of-match card vote tally update (`25013`).
    CardVoteUpdate {
        card_index: u32,
        votes: u32,
        epic: bool,
    },
    Voice {
        anchor: u32,
        speaker: u32,
        listener: u32,
        stimulus: u64,
        line: u64,
    },
}

impl CombatNotice {
    pub(crate) fn needs_mode_ui(self) -> bool {
        matches!(
            self,
            Self::Death { .. } | Self::Elimination { .. } | Self::ScorePopup { .. }
        )
    }

    pub fn required_identity(self) -> Option<u32> {
        match self {
            Self::Hit { target, .. }
            | Self::HealConfirm { target, .. }
            | Self::HealEvent { target, .. } => Some(target),
            Self::DamageReceived { source, .. } => Some(source),
            Self::Death { source, killed_by } => killed_by.then_some(source),
            Self::Elimination { victim, .. } => Some(victim),
            Self::ScorePopup { .. }
            | Self::HighlightHeader { .. }
            | Self::CommendationCardNotice { .. }
            | Self::CardVoteUpdate { .. } => None,
            Self::Voice { anchor, .. } => Some(anchor),
        }
    }

    pub(crate) fn referenced_entities(self) -> impl Iterator<Item = u32> {
        let references = match self {
            Self::Hit { target, .. } | Self::HealConfirm { target, .. } => {
                [Some(target), None, None]
            }
            Self::HealEvent { healer, target, .. } => [Some(healer), Some(target), None],
            Self::DamageReceived { source, victim, .. } => [Some(source), Some(victim), None],
            Self::Death { source, killed_by } => [killed_by.then_some(source), None, None],
            Self::Elimination { killer, victim, .. } => [Some(killer), Some(victim), None],
            Self::HighlightHeader {
                star_entity,
                target_entity,
                ..
            } => [Some(star_entity), Some(target_entity), None],
            Self::CommendationCardNotice { owner, .. } => [Some(owner), None, None],
            Self::ScorePopup { .. } | Self::CardVoteUpdate { .. } => [None, None, None],
            Self::Voice {
                anchor,
                speaker,
                listener,
                ..
            } => [Some(anchor), Some(speaker), Some(listener)],
        };
        references
            .into_iter()
            .flatten()
            .filter(|&entity| entity != 0 && entity != u32::MAX)
    }

    pub fn encode(self, schemas: &Schemas) -> Result<EncodedMessage, ChannelError> {
        let entity = |id: u32| Value::Struct(Fields::from([(0, Value::Unsigned(u64::from(id)))]));
        let (id, fields) = match self {
            Self::Hit {
                target,
                dealt,
                critical,
                killed,
                shot_frame,
            } => (
                HIT,
                Fields::from([
                    (
                        0x78,
                        Value::Struct(Fields::from([
                            (0, entity(target)),
                            (
                                4,
                                Value::Unsigned(u64::from(dealt.round_ties_even().max(1.0) as u32)),
                            ),
                            (
                                8,
                                Value::Unsigned(u64::from(critical) | (u64::from(killed) << 1)),
                            ),
                        ])),
                    ),
                    (0x84, Value::Unsigned(u64::from(shot_frame))),
                ]),
            ),
            Self::HealConfirm {
                target,
                amount,
                shot_frame,
            } => (
                HEAL_CONFIRM,
                Fields::from([
                    (
                        0x78,
                        Value::Struct(Fields::from([
                            (0, entity(target)),
                            (
                                4,
                                Value::Unsigned(
                                    u64::from(amount.round_ties_even().max(1.0) as u32),
                                ),
                            ),
                            (8, Value::Unsigned(0)),
                        ])),
                    ),
                    (0x84, Value::Unsigned(u64::from(shot_frame))),
                ]),
            ),
            Self::HealEvent {
                healer,
                target,
                amount,
                full,
                loadout,
            } => (
                HEAL_EVENT,
                Fields::from([
                    (0x78, entity(healer)),
                    (
                        0x7c,
                        Value::Struct(Fields::from([
                            (0, entity(target)),
                            (
                                4,
                                Value::Unsigned(
                                    u64::from(amount.round_ties_even().max(1.0) as u32),
                                ),
                            ),
                            (8, Value::Unsigned(u64::from(full))),
                        ])),
                    ),
                    (0x88, Value::Unsigned(loadout)),
                ]),
            ),
            Self::DamageReceived {
                source,
                victim,
                dealt,
                origin,
                impact_point,
                normal,
                contact_set,
                critical,
                armor_or_shield,
                special_vfx,
            } => {
                let vector = |xyz: Option<Vec3>| {
                    let [x, y, z] = xyz
                        .map(|xyz| xyz.map(|value| value as f32))
                        .filter(|xyz| xyz.iter().all(|value| value.is_finite()))
                        .unwrap_or_default()
                        .map(Value::Float32);
                    Value::Struct(Fields::from([(0, x), (4, y), (8, z)]))
                };
                (
                    RECEIVED_DAMAGE,
                    Fields::from([
                        (0x78, Value::Signed(1)),
                        (0x7c, entity(source)),
                        (0x80, entity(victim)),
                        // The contact effect turns the outward normal inward.
                        (0x84, vector(normal)),
                        (0x90, vector(impact_point)),
                        // The direction indicator subtracts the view position.
                        (0x9c, vector(origin)),
                        (
                            0xa8,
                            Value::Array(vec![
                                Value::Unsigned(u64::from(critical)),
                                Value::Unsigned(u64::from(armor_or_shield)),
                                Value::Unsigned(u64::from(special_vfx)),
                            ]),
                        ),
                        (0xc0, Value::Float32(dealt)),
                        (0xc4, Value::Unsigned(u64::from(armor_or_shield))),
                        (0xc8, Value::Unsigned(0)),
                        (0xca, Value::Unsigned(u64::from(contact_set))),
                        (0xcc, Value::Float32(dealt.clamp(0.0, 300.0))),
                        (0xd0, Value::Bool(critical || special_vfx)),
                        (
                            0xd8,
                            Value::Array(vec![Value::Float32(dealt), Value::Float32(1.0)]),
                        ),
                        (0xf0, Value::Array(Vec::new())),
                        (0x108, Value::Bool(armor_or_shield)),
                    ]),
                )
            }
            Self::Death { source, killed_by } => (
                DEATH,
                Fields::from([
                    (0x78, Value::Unsigned(2 * u64::from(killed_by))),
                    (0x7c, entity(source)),
                ]),
            ),
            Self::Elimination {
                killer,
                victim,
                loadout,
                fire,
                critical,
            } => (
                ELIMINATION,
                Fields::from([
                    (0x78, Value::Unsigned(loadout)),
                    (0x80, entity(killer)),
                    (0x84, entity(victim)),
                    (0x88, Value::Unsigned(u64::from(fire))),
                    (0x89, Value::Bool(critical)),
                ]),
            ),
            Self::ScorePopup { score, label } => (
                SCORE_POPUP,
                Fields::from([
                    (0x78, Value::Unsigned(u64::from(score))),
                    (0x80, Value::Unsigned(label)),
                ]),
            ),
            Self::HighlightHeader {
                star_entity,
                target_entity,
                category,
                star_hero,
                start_tick,
                end_tick,
            } => (
                HIGHLIGHT_HEADER,
                Fields::from([
                    (0x78, entity(star_entity)),
                    (0x7c, entity(target_entity)),
                    (0x80, Value::String(String::new())),
                    (0xa8, Value::Unsigned(u64::from(category))),
                    (0xb0, Value::Unsigned(star_hero)),
                    (0xb8, Value::Unsigned(u64::from(start_tick))),
                    (0xc0, Value::Array(Vec::new())),
                    (
                        0xd8,
                        Value::Signed(i64::from(end_tick.saturating_sub(start_tick))),
                    ),
                    (0xdc, entity(star_entity)),
                    (0xe0, Value::Unsigned(u64::from(end_tick))),
                ]),
            ),
            Self::CommendationCardNotice {
                card_index,
                owner,
                hero,
                stat_guid,
                value,
            } => (
                COMMENDATION_CARD,
                Fields::from([
                    (
                        0x78,
                        Value::Struct(Fields::from([
                            (0x10, Value::Unsigned(u64::from(owner))),
                            (0x18, Value::Unsigned(hero)),
                            (0x20, Value::Unsigned(stat_guid)),
                            (0x38, Value::Unsigned(u64::from(value.to_bits()))),
                        ])),
                    ),
                    (0x158, Value::Signed(i64::from(card_index))),
                ]),
            ),
            Self::CardVoteUpdate {
                card_index,
                votes,
                epic,
            } => (
                CARD_VOTE_UPDATE,
                Fields::from([
                    (
                        0x78,
                        Value::Struct(Fields::from([
                            (0x00, Value::Unsigned(u64::from(card_index))),
                            (0x08, Value::Unsigned(u64::from(votes))),
                        ])),
                    ),
                    (0xe0, Value::Bool(epic)),
                    (0xe4, Value::Float32(votes as f32)),
                ]),
            ),
            Self::Voice {
                speaker,
                listener,
                stimulus,
                line,
                ..
            } => (
                VOICE,
                Fields::from([
                    (0x78, entity(speaker)),
                    (0x7c, entity(listener)),
                    (0x80, entity(u32::MAX)),
                    (0x88, Value::Unsigned(stimulus)),
                    (0x90, Value::Unsigned(line)),
                    (0x98, Value::Unsigned(0)),
                    (0xa0, Value::Unsigned(0)),
                ]),
            ),
        };
        game_messages::encode(schemas, id, &fields)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn damage_taken_forwards_only_this_hits_tags_and_attacker() {
        use crate::script_wire::Value;
        let mut tags = crate::script_init::HitTags::EMPTY;
        assert!(tags.push(101));
        assert!(tags.push(205));
        let params = damage_taken_parameters(2.5, [0.0, 0.0, 1.0], 80, &tags);
        assert_eq!(
            params[&0x3d],
            Value::Array(vec![Value::Asset(101), Value::Asset(205)])
        );
        assert_eq!(params[&0x12], Value::Entity(80));
        let next = damage_taken_parameters(
            1.0,
            [1.0, 0.0, 0.0],
            81,
            &crate::script_init::HitTags::EMPTY,
        );
        assert_eq!(next[&0x3d], Value::Array(vec![]));
        assert_eq!(next[&0x12], Value::Entity(81));
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 game schemas"]
    fn combat_notice_messages_match_python() {
        const KILLER: u32 = 0xa000_0101;
        const TARGET: u32 = 0xa004_0010;
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let schemas = Schemas::load(root.join("data/game_schemas_174.json")).unwrap();
        let cases: [(CombatNotice, Option<u32>, &[u8], usize); 4] = [
            (
                CombatNotice::Hit {
                    target: TARGET,
                    dealt: 12.5,
                    critical: true,
                    killed: true,
                    shot_frame: 1042,
                },
                Some(TARGET),
                b"\x02\x01\x10\x00\x04\xa0\x0c\x00\x00\x00\x03\x12\x04\x00\x00",
                120,
            ),
            (
                CombatNotice::Elimination {
                    killer: KILLER,
                    victim: TARGET,
                    loadout: 0,
                    fire: 0,
                    critical: false,
                },
                Some(TARGET),
                b"\x02\x05\x00\x00\x00\x00\x00\x00\x00\x00\x01\x01\x00\xa0\x10\x00\x04\xa0\x00\x00",
                153,
            ),
            (
                CombatNotice::Death {
                    source: KILLER,
                    killed_by: true,
                },
                Some(KILLER),
                b"\x02\x04\x02\x01\x01\x00\xa0",
                56,
            ),
            (
                CombatNotice::Death {
                    source: TARGET,
                    killed_by: false,
                },
                None,
                b"\x02\x04\x00\x10\x00\x04\xa0",
                56,
            ),
        ];
        for (notice, identity, expected, expected_bits) in cases {
            assert_eq!(notice.required_identity(), identity);
            let encoded = notice.encode(&schemas).unwrap();
            assert_eq!(encoded.as_bytes(), expected);
            assert_eq!(encoded.bit_len(), expected_bits);
        }
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 game schemas"]
    fn elimination_frame_resumes_at_exact_bit() {
        use crate::{
            bits::BitReader,
            game_messages::read_channel,
            world::{EntityUpdate, Operation, frame},
        };

        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let schemas = Schemas::load(root.join("data/game_schemas_174.json")).unwrap();
        let elim = CombatNotice::Elimination {
            killer: 0xa000_0101,
            victim: 0xc000_1005,
            loadout: 0,
            fire: 0,
            critical: false,
        }
        .encode(&schemas)
        .unwrap();
        assert_eq!(elim.bit_len(), 153);
        let destroy = EntityUpdate {
            entity: 0xc000_1005,
            operation: Some(Operation::Destroy),
            ..EntityUpdate::default()
        };
        let payload = frame(6906, 6905, &[(0, elim)], &[], &[&destroy]).unwrap();
        let mut reader = BitReader::new(&payload);
        assert_eq!(reader.var_a().unwrap(), 6906);
        let channel = read_channel(&mut reader, &schemas).unwrap();
        assert_eq!(channel.reliable.len(), 1);
        assert_eq!(channel.reliable[0].message.id, ELIMINATION);
        assert_eq!(reader.position(), 217);
        assert_eq!(reader.var_a().unwrap(), 6905);
        assert_eq!(reader.bits(4).unwrap(), 0);
        assert!(!reader.bit().unwrap());
        assert!(reader.bit().unwrap());
        assert!(!reader.bit().unwrap());
        assert_eq!(reader.bits(7).unwrap(), 1);
        assert_eq!(reader.entity_id().unwrap(), Some(0xc000_1005));
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 game schemas"]
    fn boss_intro_voice_notices_match_python() {
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let schemas = Schemas::load(root.join("data/game_schemas_174.json")).unwrap();
        let anchor = 0xc000_1000;
        for (speaker, stimulus, line, expected) in [
            (
                crate::world::GAME_MODE_ENTITY,
                0x0ee0_0000_0000_06b5,
                0x0760_0000_0000_8cb1,
                "0300100000d0010100a0ffffffffb50600000000e00eb18c000000006007000000000000000000000000",
            ),
            (
                anchor,
                0x0ee0_0000_0000_06bd,
                0x0760_0000_0000_8a2b,
                "0300001000c0010100a0ffffffffbd0600000000e00e2b8a000000006007000000000000000000000000",
            ),
        ] {
            let notice = CombatNotice::Voice {
                anchor,
                speaker,
                listener: 0xa000_0101,
                stimulus,
                line,
            };
            assert_eq!(notice.required_identity(), Some(anchor));
            assert!(!notice.needs_mode_ui());
            let encoded = notice.encode(&schemas).unwrap();
            assert_eq!(encoded.bit_len(), 336);
            let encoded: String = encoded
                .as_bytes()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            assert_eq!(encoded, expected);
        }
    }
}
