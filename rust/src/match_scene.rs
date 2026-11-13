//! Match entity baselines and the shared mode presentation frame.

use std::{collections::HashMap, io};

use crate::{
    bits::BitWriter,
    game_body::yaw_rotation,
    game_bots::Bot,
    game_content::GameMap,
    game_instance::FREE_FOR_ALL_TEAM,
    game_stats::GMP_STATS,
    script_graph::GraphStore,
    script_init::{Snapshot, StateKind},
    script_wire::{self, Instance, Layout, Variables},
    world::{
        Component, ComponentValue, Create, EntityUpdate, GAME_MODE_ENTITY, Operation, PLAYER_PATH,
        SkinTheme, WITH_PARTICIPANT, WITH_SCALE, WITH_TRANSFORM, health,
    },
};

const PLAYER_DEFINITION: u64 = 0x0400_0000_0000_000e;
const GAME_MODE_DEFINITION: u64 = 0x0400_0000_0000_16b2;
const PLAYER_FILTER: u64 = 0x0004_0001;
const TEAM_BIT: u64 = 0x0080_0000;
const MODE_UI: u32 = 0x0cb6;
const MODE_UI_STATE: usize = 21;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Possession {
    pub body: u32,
    pub hero: u64,
    pub skin: Option<u64>,
}

impl Possession {
    pub fn components(self) -> [Component; 2] {
        [
            fields(44, vec![Some(ComponentValue::Entity(self.body)); 3]),
            fields(
                75,
                vec![
                    Some(ComponentValue::Id(self.hero)),
                    self.skin.map(ComponentValue::Id),
                    None,
                    Some(ComponentValue::Id(self.hero)),
                ],
            ),
        ]
    }
}

pub struct PlayerAppearance<'a> {
    pub entity: u32,
    pub slot: i8,
    pub team: u8,
    pub card: &'a PlayerCard,
}

#[derive(Debug, Default)]
pub struct PlayerCard {
    pub account: [u64; 2],
    pub battle_tag: String,
    pub level: Option<u32>,
    pub portrait_frame: Option<u64>,
    pub ranked: Option<(i16, i16, i8)>,
    pub role: Option<i8>,
    pub skins: HashMap<u64, SkinTheme>,
}

pub fn player_create(
    map: &GameMap,
    player: &PlayerAppearance<'_>,
    possession: Option<Possession>,
    owner_board: Option<Component>,
) -> io::Result<EntityUpdate> {
    use ComponentValue::*;
    let card = player.card;
    let team = if map.free_for_all {
        FREE_FOR_ALL_TEAM
    } else {
        player.team
    };
    let mut components = vec![
        fields(26, vec![Some(Id(PLAYER_FILTER | team_bit(team)?))]),
        fields(
            29,
            vec![
                Some(DbId(card.account[0], card.account[1])),
                (!card.battle_tag.is_empty()).then(|| Text(card.battle_tag.clone())),
                card.level.map(U32),
                None,
                None,
                None,
                None,
                card.ranked.map(|rank| I16(rank.0)),
                card.ranked.map(|rank| I16(rank.1)),
                card.ranked.map(|rank| I8(rank.2)),
            ],
        ),
        fields(
            74,
            vec![
                card.portrait_frame.filter(|&guid| guid != 0).map(Id),
                None,
                Some(I8(player.slot)),
                None,
                None,
                card.role.filter(|&role| role != 0).map(I8),
                Some(U8(1)),
                Some(U8(1)),
            ],
        ),
    ];
    if let Some(possession) = possession {
        components.extend(possession.components());
    }
    if let Some(board) = owner_board {
        if board.index != GMP_STATS {
            return Err(invalid("the player board requires component 64"));
        }
        components.push(board);
    }
    let mut definition = Create::new(PLAYER_DEFINITION, components);
    definition.flags = WITH_PARTICIPANT | PLAYER_PATH | WITH_TRANSFORM | WITH_SCALE;
    Ok(create(player.entity, definition))
}

pub fn mode_create(mode: u64) -> EntityUpdate {
    create(
        GAME_MODE_ENTITY,
        Create::new(
            GAME_MODE_DEFINITION,
            vec![fields(114, vec![None, Some(ComponentValue::Id(mode))])],
        ),
    )
}

/// The match freezes each bot's pose and pools when it queues the create.
pub fn bot_create(bot: &Bot, vitals: Option<&crate::vitals::Vitals>) -> io::Result<EntityUpdate> {
    let pools = vitals.map_or_else(
        || health(bot.spawn.health as f32, bot.spawn.health as f32, 0.0, 0.0),
        crate::vitals::Vitals::component,
    );
    let team = fields(
        26,
        vec![Some(ComponentValue::Id(team_bit(bot.spawn.team)?))],
    );
    let mut definition = Create::new(bot.spawn.body, vec![pools, team]);
    definition.position = bot.position.map(|position| position as f32);
    definition.rotation = yaw_rotation(bot.yaw.round_ties_even());
    Ok(create(bot.entity, definition))
}

/// Build the initial world before the local player picks a hero.
pub fn initial_world<'a>(
    map: &GameMap,
    player: &PlayerAppearance<'_>,
    board: Component,
    bots: impl IntoIterator<Item = &'a Bot>,
) -> io::Result<Vec<EntityUpdate>> {
    let bots = bots.into_iter();
    let mut updates = Vec::with_capacity(map.placeables.len() + 2 + bots.size_hint().0);
    updates.extend(map.placeables.iter().map(|&index| EntityUpdate {
        entity: 0x8000_0000 | index,
        operation: Some(Operation::Placeable(index)),
        ..EntityUpdate::default()
    }));
    updates.push(player_create(map, player, None, Some(board))?);
    updates.push(mode_create(map.mode_guid));
    for bot in bots {
        updates.push(bot_create(bot, None)?);
    }
    Ok(updates)
}

/// H=0 game mode graph with its client-only UI entry dependencies retained.
pub fn mode_ui(graphs: &GraphStore, variables: &Variables) -> io::Result<BitWriter> {
    mode_presentation(graphs, variables, None)
}

pub(crate) fn mode_presentation(
    graphs: &GraphStore,
    variables: &Variables,
    encounter: Option<u32>,
) -> io::Result<BitWriter> {
    let instances = mode_instances(graphs, encounter)?;
    script_wire::remote_full(&instances, variables).map_err(invalid)
}

pub(crate) fn junkenstein_presentation(
    graphs: &GraphStore,
    encounter: &Snapshot,
) -> io::Result<BitWriter> {
    let instances = junkenstein_presentation_instances(graphs, encounter)?;
    script_wire::remote_full(&instances, &Variables::new()).map_err(invalid)
}

fn junkenstein_presentation_instances(
    graphs: &GraphStore,
    encounter: &Snapshot,
) -> io::Result<Vec<Instance>> {
    let root = encounter
        .instances
        .get(&2)
        .filter(|instance| instance.parent.is_none())
        .ok_or_else(|| invalid("missing Junkenstein presentation root"))?;
    let mut instances = mode_instances(graphs, Some(root.graph))?;
    // The outcome voice entries and the post-match screen manager are
    // separate children of the common HUD in the authored mode tree.
    let mut mount = |index, parent, listener| {
        let (&id, instance) = encounter
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == index && instance.parent == Some(parent))
            .ok_or_else(|| invalid(format!("missing Junkenstein HUD graph {index:04X}")))?;
        let graph = graphs
            .graph(index)
            .ok_or_else(|| invalid(format!("missing graph {index:04X}")))?;
        let mut wire = Instance::new(id, Layout::try_from(graph).map_err(invalid)?, Some(parent));
        if let Some(raw) = listener {
            let node = graph
                .state(usize::from(raw))
                .filter(|node| {
                    node.class == "STUStatescriptStateGameMessageEntry"
                        && node
                            .field("m_gameMessage.m_gameMessage")
                            .and_then(crate::script_graph::asset_guid)
                            == Some(crate::junkenstein_messages::POST_MATCH_PHASE)
                })
                .ok_or_else(|| invalid("missing Junkenstein post-match listener"))?;
            if !instance
                .states
                .get(&raw)
                .is_some_and(|state| state.active && state.kind == StateKind::MessageIdle)
            {
                return Err(invalid("Junkenstein post-match listener is inactive"));
            }
            let slot = graph
                .remote_positions()
                .iter()
                .position(|&pos| pos == node.pos)
                .ok_or_else(|| invalid("Junkenstein post-match listener is local"))?;
            // The verified remote-full message entry has X, A and stacked bits.
            let mut bits = BitWriter::new();
            bits.bits(3, 3).map_err(invalid)?;
            wire.states.insert(slot, bits);
        }
        instances.push(wire);
        Ok::<u16, io::Error>(id)
    };
    let hud = mount(0x0cbb, (2, 6), Some(0))?;
    mount(0x03f2, (hud, 44), None)?;
    mount(0x180a, (hud, 45), Some(2))?;
    // This sibling owns the authored mission notifications. Its client-only
    // presenters start locally, while the two server message entries must be
    // present before they can drive those presenters.
    let notifications = mount(0x1188, (2, 68), None)?;
    let instance = &encounter.instances[&notifications];
    let graph = graphs
        .graph(instance.graph)
        .expect("mounted notification graph");
    let wire = instances.last_mut().expect("mounted notification instance");
    for (&raw, state) in &instance.states {
        if !(state.active && state.kind == StateKind::MessageIdle) {
            continue;
        }
        let node = graph
            .state(usize::from(raw))
            .filter(|node| node.class == "STUStatescriptStateGameMessageEntry")
            .ok_or_else(|| invalid("invalid Junkenstein notification listener"))?;
        let slot = graph
            .remote_positions()
            .iter()
            .position(|&pos| pos == node.pos)
            .ok_or_else(|| invalid("Junkenstein notification listener is local"))?;
        let mut bits = BitWriter::new();
        bits.bits(3, 3).map_err(invalid)?;
        wire.states.insert(slot, bits);
    }
    Ok(instances)
}

fn mode_instances(graphs: &GraphStore, encounter: Option<u32>) -> io::Result<Vec<Instance>> {
    let graph = graphs
        .graph(MODE_UI)
        .ok_or_else(|| invalid("missing game mode UI graph 0CB6"))?;
    let state = graph
        .state(MODE_UI_STATE)
        .filter(|node| node.class == "STU_9D7BF987")
        .ok_or_else(|| invalid("missing verified mode UI shared-variable state"))?;
    let slot = graph
        .remote_positions()
        .iter()
        .position(|&pos| pos == state.pos)
        .ok_or_else(|| invalid("mode UI shared-variable state has no remote slot"))?;
    let mut instance = Instance::new(1, Layout::try_from(graph).map_err(invalid)?, None);
    let mut active = BitWriter::new();
    // The verified 9D7BF987 remote-full layout has X/A bits and no payload.
    active.bits(3, 2).map_err(invalid)?;
    instance.states.insert(slot, active);
    let mut instances = vec![instance];
    if let Some(encounter) = encounter {
        // Keep the retail entrance's UI root and mount the authored mission root.
        // The list scopes are owned by the server simulation, outside this bootstrap.
        let encounter = graphs
            .graph(encounter)
            .ok_or_else(|| invalid("missing Junkenstein encounter graph"))?;
        let ui = encounter.state(67).filter(|node| {
            node.class == "STU_37D754C8"
                && node
                    .field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph")
                    .and_then(serde_json::Value::as_str)
                    .and_then(crate::accounts::parse_guid)
                    == Some(0x0580_0000_0000_0cb6)
        });
        if ui.is_none() {
            return Err(invalid("missing Junkenstein common UI SubScript"));
        }
        instances.push(Instance::new(
            2,
            Layout::try_from(encounter).map_err(invalid)?,
            None,
        ));
    }
    Ok(instances)
}

fn create(entity: u32, definition: Create) -> EntityUpdate {
    EntityUpdate {
        entity,
        operation: Some(Operation::Create(Box::new(definition))),
        ..EntityUpdate::default()
    }
}

fn fields(index: u8, fields: Vec<Option<ComponentValue>>) -> Component {
    Component { index, fields }
}

pub(crate) fn team_bit(team: u8) -> io::Result<u64> {
    if team > FREE_FOR_ALL_TEAM {
        return Err(invalid("invalid team for a player or bot"));
    }
    Ok(TEAM_BIT << team)
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        game_bots::{BOT_ENTITY, BotCatalog, BotWalk},
        game_stats::{PlayerStats, StatsCatalog},
        mover_settings::MoverCatalog,
        script_wire::Value,
        world::{FrameCandidates, MAX_PAYLOAD, frame, select_frame},
    };
    use sha2::{Digest, Sha256};

    fn runtime() -> std::path::PathBuf {
        std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap()).join("data")
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 spawn and stat catalogs"]
    fn matches_the_python_practice_range_create_frame_and_budget() {
        let data = runtime();
        let movers = MoverCatalog::load(data.join("game_movers_174.json")).unwrap();
        let bots = BotCatalog::load(data.join("ai_spawners_174.json")).unwrap();
        let stats = StatsCatalog::load(data.join("game_stats_174.json")).unwrap();
        let map = GameMap::practice_range();
        let bots = bots.make(map.map_guid, |body| {
            let mover = movers.for_body(Some(body));
            BotWalk::from_mover(
                mover.run_forward,
                mover.accel_ground,
                mover.decel_ground_base,
            )
        });
        let card = PlayerCard {
            account: [0x1234_5678, 0x0100_0000_0000_0000],
            battle_tag: "rust#2345".into(),
            level: Some(3000),
            portrait_frame: Some(0x0250_0000_0000_0f97),
            ranked: Some((3200, 120, 4)),
            role: Some(1),
            ..PlayerCard::default()
        };
        let player = PlayerAppearance {
            entity: 0xa000_0100,
            slot: 0,
            team: 1,
            card: &card,
        };
        let board = stats.component(&PlayerStats::default(), map.mode_guid, None, None);
        let updates = initial_world(&map, &player, board, &bots).unwrap();
        let expected: Vec<_> = [
            0x8000_0088,
            0x8000_022e,
            0x8000_0234,
            0x8000_0289,
            player.entity,
            GAME_MODE_ENTITY,
        ]
        .into_iter()
        .chain(BOT_ENTITY..BOT_ENTITY + 15)
        .collect();
        assert_eq!(
            updates
                .iter()
                .map(|update| update.entity)
                .collect::<Vec<_>>(),
            expected
        );
        let encoded = frame(1234, 1234, &[], &[], &updates.iter().collect::<Vec<_>>()).unwrap();
        assert_eq!(encoded.len(), 1674);
        assert_eq!(
            format!("{:x}", Sha256::digest(&encoded)),
            "b82987cb93e2e93851a16452fbb2230637b89edd70cf9f07b9a34c054689d66f",
        );
        let mut remaining = updates.as_slice();
        let mut transmitted = Vec::new();
        while !remaining.is_empty() {
            let selected = select_frame(
                FrameCandidates {
                    tick: 1234,
                    reliable: &[],
                    unreliable: &[],
                    queued: remaining,
                    movements: &[],
                    owner_correction: false,
                    reliable_only: false,
                },
                |_| 1234,
            )
            .unwrap();
            assert!(selected.payload.len() <= MAX_PAYLOAD && !selected.entities.is_empty());
            transmitted.extend(selected.entities.iter().map(|update| update.entity));
            remaining = &remaining[selected.entities.len()..];
        }
        assert_eq!(transmitted, expected);
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 Junkenstein runtime"]
    fn junkenstein_entrance_mounts_mission_notification_receivers() {
        use crate::{
            game_content::{JUNKENSTEIN_MAP, JunkensteinCard, MapCatalog},
            scene_assets::SceneAssets,
        };
        let runtime = runtime().parent().unwrap().to_path_buf();
        let assets = SceneAssets::load(&runtime).unwrap();
        let maps = MapCatalog::load(runtime.join("data/game_maps_174.json")).unwrap();
        for index in [
            0x3c, 0x3d, 0x3e, 0x4a, 0x43, 0x44, 0x45, 0x49, 0x16b, 0x16c, 0x16d, 0x16e, 0x16f,
            0x170,
        ] {
            let card = JunkensteinCard::from_index(index).unwrap();
            let map = maps
                .game_map(
                    JUNKENSTEIN_MAP,
                    card.mode,
                    "Junkenstein's Revenge",
                    false,
                    &[card.players, 0],
                )
                .unwrap();
            let scene = assets.prepare_junkenstein(map, card, 0).unwrap();
            let snapshot = scene.encounter_snapshot().unwrap();
            let instances = junkenstein_presentation_instances(assets.graphs(), snapshot).unwrap();
            let notifications = instances.iter().find(|i| i.layout.graph == 0x1188).unwrap();
            assert_eq!(notifications.parent, Some((2, 68)));
            let graph = assets.graphs().graph(0x1188).unwrap();
            let messages: Vec<_> = notifications
                .states
                .keys()
                .map(|&slot| {
                    let node = graph.node(graph.remote_positions()[slot]).unwrap();
                    crate::script_graph::asset_guid(
                        node.field("m_gameMessage.m_gameMessage").unwrap(),
                    )
                    .unwrap()
                })
                .collect();
            assert!(messages.contains(&0x0240_0000_0000_0694));
            assert!(messages.contains(&0x0240_0000_0000_06a8));
            assert!(instances.iter().any(|i| i.layout.graph == 0x180a));
            assert!(instances.iter().any(|i| i.layout.graph == 0x03f2));
            assert!(
                junkenstein_presentation(assets.graphs(), snapshot)
                    .unwrap()
                    .bit_len()
                    > 112
            );
        }
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 graph bundle"]
    fn junkenstein_mode_entrance_matches_reference_frame() {
        let graphs = GraphStore::load(runtime().join("game_graphs_174.json.gz")).unwrap();
        for mode in [
            crate::game_content::JUNKENSTEIN_REVENGE,
            crate::game_content::JUNKENSTEIN_ENDLESS,
        ] {
            let encounter = graphs.mode_scripts(mode).unwrap().graph.unwrap();
            let frame = mode_presentation(&graphs, &Variables::new(), Some(encounter)).unwrap();
            assert_eq!(frame.bit_len(), 112);
            assert_eq!(
                frame.as_bytes(),
                [8, 91, 6, 16, 140, 12, 0, 24, 0, 0, 0, 0, 0, 0]
            );
        }
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 graph bundle"]
    fn mode_ui_preserves_its_shared_variable_state_with_or_without_countdown() {
        let graphs = GraphStore::load(runtime().join("game_graphs_174.json.gz")).unwrap();
        let cases = [
            (Variables::new(), 38, "085b06000c"),
            (
                Variables::from([(15545, Value::Float(3.0))]),
                74,
                "085b0600cbe5a901c000",
            ),
        ];
        for (vars, length, expected) in cases {
            let frame = mode_ui(&graphs, &vars).unwrap();
            let actual: String = frame
                .as_bytes()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            assert_eq!((frame.bit_len(), actual.as_str()), (length, expected));
        }
    }
}
