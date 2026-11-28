//! Match entry and roster messages shared by team modes and free-for-all.

use crate::jam_codec::{
    Fields,
    Value::{Array, String, Struct, Unsigned},
};
use crate::{game_content::GameMap, game_messages::Message, room_primitives::SPECTATOR_TEAM};

pub const RETAIL_QUANTA: u32 = 16_000;
pub const TOURNAMENT_QUANTA: u32 = 7_000;
pub const PARTICIPANTS: usize = 16;
pub(crate) const FREE_FOR_ALL_TEAM: u8 = 4;

/// Membership of the admitted player roster, independent of spawned bodies.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct TeamMemberCounts([u32; FREE_FOR_ALL_TEAM as usize + 1]);

impl TeamMemberCounts {
    pub(crate) fn from_teams(free_for_all: bool, teams: impl IntoIterator<Item = u8>) -> Self {
        let mut counts = [0_u32; FREE_FOR_ALL_TEAM as usize + 1];
        for team in teams {
            let slot = if free_for_all {
                FREE_FOR_ALL_TEAM
            } else {
                team
            };
            if let Some(count) = counts.get_mut(usize::from(slot)) {
                *count = count.saturating_add(1);
            }
        }
        Self(counts)
    }

    pub(crate) fn count(self, team: u8) -> Option<u32> {
        self.0.get(usize::from(team)).copied()
    }
}

pub struct Member<'a> {
    pub entity: u32,
    pub name: &'a str,
    pub team: u8,
    pub gone: bool,
    pub ranked: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SeatedBot<'a> {
    pub slot: u8,
    pub controller: u32,
    pub name: &'a str,
    pub team: u8,
}

pub struct Instance<'a> {
    pub map: &'a GameMap,
    pub members: &'a [Member<'a>],
    pub bots: &'a [SeatedBot<'a>],
}

impl Instance<'_> {
    fn roster(&self, viewer: u32) -> Fields {
        let playing = || self.members.iter().filter(|member| !member.gone);
        let mut records: Vec<_> = playing()
            .map(|member| Struct(Fields::from([(0x50, String(member.name.into()))])))
            .collect();
        let player_count = records.len();
        let mut seated: Vec<_> = if self.map.free_for_all {
            Vec::new()
        } else {
            self.bots
                .iter()
                .copied()
                .filter(|bot| usize::from(bot.slot) >= player_count)
                .collect()
        };
        seated.sort_by_key(|bot| bot.slot);
        for bot in &seated {
            let seat = usize::from(bot.slot);
            if seat >= records.len() {
                records.resize_with(seat, || Struct(Fields::new()));
                records.push(Struct(Fields::from([(0x50, String(bot.name.into()))])));
            }
        }
        let team = |number, size| {
            let mut slots: Vec<_> = playing()
                .filter(|member| {
                    member.team != SPECTATOR_TEAM
                        && (self.map.free_for_all || member.team == number)
                })
                .map(|member| entity(member.entity))
                .collect();
            if !self.map.free_for_all {
                slots.extend(
                    seated
                        .iter()
                        .filter(|bot| bot.team == number)
                        .map(|bot| entity(bot.controller)),
                );
            }
            Struct(Fields::from([
                (0x18, Array(slots)),
                (0x60, Unsigned(u64::from(number))),
                (0x61, Unsigned(u64::from(size))),
            ]))
        };
        let capacity = |team| self.map.team_sizes.get(team).copied().unwrap_or(0);
        let teams = if self.map.free_for_all {
            vec![team(FREE_FOR_ALL_TEAM, capacity(0))]
        } else {
            vec![team(0, capacity(0)), team(1, capacity(1))]
        };
        let mut roster = Fields::from([
            (0, Struct(Fields::from([(0, Array(records))]))),
            (0xb0, Unsigned(self.map.mode_guid)),
            (0xc0, Array(teams)),
            (0xd8, Array(vec![entity(viewer)])),
        ]);
        if self.map.preset_guid != 0 {
            roster.insert(0xa8, Unsigned(self.map.preset_guid));
        }
        Fields::from([(
            0,
            Struct(Fields::from([
                (0, Struct(roster)),
                (0x274, Unsigned(PARTICIPANTS as u64)),
            ])),
        )])
    }

    pub fn load(&self, viewer: u32, quanta: u32) -> Message {
        // A competitive roster keeps its instance type after a ranked player leaves.
        let ranked = self.members.iter().any(|member| member.ranked);
        Message {
            id: 20300,
            fields: Fields::from([
                (0x78, Unsigned(if ranked { 9 } else { 1 })),
                (0x80, Struct(self.roster(viewer))),
                (0x4a8, Unsigned(self.map.map_guid)),
                (0x500, Unsigned(u64::from(quanta))),
                (
                    0x508,
                    Struct(Fields::from([(0, Unsigned(1)), (8, Unsigned(1))])),
                ),
            ]),
        }
    }

    pub fn update(&self, viewer: u32) -> Message {
        Message {
            id: 20301,
            fields: Fields::from([(0x78, Struct(self.roster(viewer)))]),
        }
    }
}

pub fn ownership(viewer: u32) -> Message {
    Message {
        id: 20302,
        fields: Fields::from([
            (0x78, entity(viewer)),
            (0x7c, crate::jam_codec::Value::Bool(false)),
            (0x80, Unsigned(0)),
        ]),
    }
}

fn entity(id: u32) -> crate::jam_codec::Value {
    Struct(Fields::from([(0, Unsigned(u64::from(id)))]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn members() -> [Member<'static>; 3] {
        [
            Member {
                entity: 0x1000,
                name: "Blue",
                team: 0,
                gone: false,
                ranked: false,
            },
            Member {
                entity: 0x2000,
                name: "Retired",
                team: 1,
                gone: true,
                ranked: true,
            },
            Member {
                entity: 0x3000,
                name: "Red",
                team: 1,
                gone: false,
                ranked: false,
            },
        ]
    }

    #[test]
    fn instance_preserves_ranked_type_after_a_member_leaves() {
        let members = members();
        let map = GameMap::practice_range();
        let instance = Instance {
            map: &map,
            members: &members,
            bots: &[],
        };
        let initial = instance.load(members[0].entity, TOURNAMENT_QUANTA);
        assert_eq!(initial.fields[&0x78], Unsigned(9));
        assert_eq!(initial.fields[&0x500], Unsigned(7000));
        assert_eq!(
            instance.update(members[0].entity).fields[&0x78],
            initial.fields[&0x80]
        );
        let active_only = Instance {
            members: &members[..1],
            ..instance
        };
        assert_eq!(
            active_only.load(members[0].entity, RETAIL_QUANTA).fields[&0x78],
            Unsigned(1)
        );
    }

    #[test]
    fn instance_roster_seats_bots_in_team_slots_without_changing_team_sizes() {
        let members = members();
        let map = GameMap::practice_range();
        let bots = [
            SeatedBot {
                slot: 2,
                controller: 0xa000_8200,
                name: "Training Bot",
                team: 1,
            },
            SeatedBot {
                slot: 4,
                controller: 0xa000_8400,
                name: "Ally Bot",
                team: 0,
            },
        ];
        let instance = Instance {
            map: &map,
            members: &members[..1],
            bots: &bots,
        };
        let load = instance.load(members[0].entity, RETAIL_QUANTA);
        let Struct(outer) = &load.fields[&0x80] else {
            panic!("missing outer roster struct");
        };
        let Struct(inner) = &outer[&0] else {
            panic!("missing inner roster struct");
        };
        let Struct(roster) = &inner[&0] else {
            panic!("missing roster");
        };
        let Struct(record_box) = &roster[&0] else {
            panic!("missing records");
        };
        let Array(records) = &record_box[&0] else {
            panic!("missing record array");
        };
        assert_eq!(records.len(), 5);
        assert_eq!(
            records[0],
            Struct(Fields::from([(0x50, String("Blue".into()))]))
        );
        assert_eq!(records[1], Struct(Fields::new()));
        assert_eq!(
            records[2],
            Struct(Fields::from([(0x50, String("Training Bot".into()))]))
        );
        assert_eq!(records[3], Struct(Fields::new()));
        assert_eq!(
            records[4],
            Struct(Fields::from([(0x50, String("Ally Bot".into()))]))
        );

        let Array(teams) = &roster[&0xc0] else {
            panic!("missing teams");
        };
        let Struct(team0) = &teams[0] else {
            panic!("missing team 0");
        };
        let Struct(team1) = &teams[1] else {
            panic!("missing team 1");
        };
        assert_eq!(
            team0[&0x18],
            Array(vec![entity(members[0].entity), entity(0xa000_8400)])
        );
        assert_eq!(team0[&0x61], Unsigned(1));
        assert_eq!(team1[&0x18], Array(vec![entity(0xa000_8200)]));
        assert_eq!(team1[&0x61], Unsigned(0));

        let ffa_map = GameMap {
            free_for_all: true,
            team_sizes: vec![8],
            ..GameMap::practice_range()
        };
        let ffa = Instance {
            map: &ffa_map,
            members: &members[..1],
            bots: &bots,
        };
        let ffa_load = ffa.load(members[0].entity, RETAIL_QUANTA);
        let Struct(ffa_outer) = &ffa_load.fields[&0x80] else {
            panic!();
        };
        let Struct(ffa_inner) = &ffa_outer[&0] else {
            panic!();
        };
        let Struct(ffa_roster) = &ffa_inner[&0] else {
            panic!();
        };
        let Struct(ffa_record_box) = &ffa_roster[&0] else {
            panic!();
        };
        assert_eq!(
            ffa_record_box[&0],
            Array(vec![Struct(Fields::from([(0x50, String("Blue".into()))]))])
        );
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 game schemas"]
    fn map_load_and_roster_updates_match_python_for_teams_and_free_for_all() {
        use crate::{game_messages::encode, jam_codec::Schemas};
        use sha2::{Digest, Sha256};

        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let schemas = Schemas::load(root.join("data/game_schemas_174.json")).unwrap();
        let members = members();
        let practice_range = GameMap::practice_range();
        let free_for_all = GameMap {
            map_guid: 12,
            mode_guid: 34,
            team_sizes: vec![8],
            free_for_all: true,
            ..GameMap::practice_range()
        };
        let cases = [
            (
                Instance {
                    map: &practice_range,
                    members: &members,
                    bots: &[],
                },
                [
                    (
                        513,
                        "96c8620fdd4430e82bdf363c9f1d07dd405581ad7ffd68bda00e13926a9374c3",
                    ),
                    (
                        438,
                        "8b2d19c3c9379c478ce04590af1633e8882ddde0fe26ced16112d1a5dcb62ee9",
                    ),
                ],
            ),
            (
                Instance {
                    map: &free_for_all,
                    members: &members,
                    bots: &[],
                },
                [
                    (
                        494,
                        "4dbcdc55773344208c6cb95ea98d4d8efda57add102a373e4314b4547babe923",
                    ),
                    (
                        419,
                        "59330c72bf73f50b987fe7fe59cb4607e486f4582add1704cec5f035df24c0bf",
                    ),
                ],
            ),
        ];
        for (instance, expected) in cases {
            let messages = [
                instance.load(members[0].entity, RETAIL_QUANTA),
                instance.update(members[2].entity),
            ];
            for (message, (length, digest)) in messages.into_iter().zip(expected) {
                let encoded = encode(&schemas, message.id, &message.fields).unwrap();
                assert_eq!(encoded.as_bytes().len(), length);
                assert_eq!(format!("{:x}", Sha256::digest(encoded.as_bytes())), digest);
            }
        }
    }
}
