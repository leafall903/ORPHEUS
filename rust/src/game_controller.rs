//! Owner controller and remote team-entry frames for the 104319 hero select.

use std::{collections::BTreeMap, io};

use crate::{
    bits::BitWriter,
    game_content::{PRACTICE_RANGE_MODE, is_junkenstein_mode},
    script_graph::{Graph, GraphStore},
    script_wire::{
        Instance, InstanceVariableDelta, Layout, Value, Variables, WireError, owner_full,
        owner_variable_delta, remote_full, subscript,
    },
};

const PRACTICE: u32 = 0x13c1;
const PVP: u32 = 0x0c90;
const HUD: u32 = 0x20e4;
const HERO_SELECT_HOST: u32 = 0x288a;
const HERO_SELECT: u32 = 0x288b;
const TEAM_ENTRY: u32 = 0x288d;

const ALLOW_SELECT: u32 = 8419;
const ASSEMBLING: u32 = 25377;
const HIDE_ASSEMBLE: u32 = 8535;
const OPEN_SELECT: u32 = 2438;
const SELECT_SHOWN: u32 = 31435;
const CURRENT_HERO: u32 = 940;
const PICKED: u32 = 2114;
const PICKED_HERO: u32 = 13161;
const OBJECTIVE_TEXT: u32 = 8975;
const OBJECTIVE_COLOR_TEAM: u32 = 6999;

#[derive(Clone, Copy, Debug)]
struct StateSlot {
    index: u16,
    wire: usize,
}

#[derive(Clone, Copy, Debug)]
struct Hud {
    layout: Layout,
    root_state: StateSlot,
    shared_vars: usize,
}

/// Loaded layouts keep filtered owner positions separate from raw parent state IDs.
#[derive(Clone, Copy, Debug)]
pub struct Controller {
    root: Layout,
    host: Layout,
    screen: Layout,
    entry: Layout,
    hud: Option<Hud>,
    parent: Option<(Layout, StateSlot)>,
    allow: usize,
    hero_select: usize,
    start_host: StateSlot,
    start_screen: StateSlot,
    start_entry: StateSlot,
    assembling: usize,
    screen_sync: usize,
    screen_states: usize,
    assembling_sync: usize,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ControllerFrame<'a> {
    pub select_open: bool,
    pub skins: bool,
    pub hide_assemble: bool,
    /// Extra presence bits of the root controller, such as notice switches.
    pub presence: Option<&'a BTreeMap<usize, Value>>,
    pub hero: Option<u64>,
    pub confirmed_hero: Option<u64>,
    pub send_selection: bool,
    pub objective: Option<(u64, u8)>,
}

impl Controller {
    #[cfg(test)]
    pub(crate) fn fixture_for_owner_admission() -> Self {
        let layout = |graph, owner_slots| Layout {
            graph,
            owner_slots,
            remote_slots: 0,
            presence_slots: 1,
        };
        let first = StateSlot { index: 0, wire: 0 };
        let second = StateSlot { index: 1, wire: 1 };
        Self {
            root: layout(720, 2),
            host: layout(721, 2),
            screen: layout(722, 0),
            entry: layout(723, 0),
            hud: None,
            parent: None,
            allow: 0,
            hero_select: first.wire,
            start_host: second,
            start_screen: first,
            start_entry: second,
            assembling: 0,
            screen_sync: 1,
            screen_states: 0,
            assembling_sync: 0,
        }
    }

    #[cfg(test)]
    pub(crate) fn fixture_for_test() -> Self {
        let empty_layout = Layout {
            graph: 0,
            owner_slots: 0,
            remote_slots: 0,
            presence_slots: 0,
        };
        let slot = StateSlot { index: 0, wire: 0 };
        Self {
            root: empty_layout,
            host: empty_layout,
            screen: empty_layout,
            entry: empty_layout,
            hud: None,
            parent: None,
            allow: 0,
            hero_select: 0,
            start_host: slot,
            start_screen: slot,
            start_entry: slot,
            assembling: 0,
            screen_sync: 1,
            screen_states: 0,
            assembling_sync: 0,
        }
    }

    pub fn load(graphs: &GraphStore, mode_guid: u64) -> io::Result<Self> {
        let (root_id, hero_select, start_host, start_hud) = if mode_guid == PRACTICE_RANGE_MODE {
            (PRACTICE, 29, 43, Some(42))
        } else {
            (PVP, 1, 119, None)
        };
        let root = graph(graphs, root_id)?;
        let host = graph(graphs, HERO_SELECT_HOST)?;
        let screen = graph(graphs, HERO_SELECT)?;
        let entry = graph(graphs, TEAM_ENTRY)?;
        let parent = if is_junkenstein_mode(mode_guid) {
            let team = graphs
                .mode_scripts(mode_guid)
                .and_then(|mode| mode.teams.get(&0))
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        "missing Junkenstein defender team scripts",
                    )
                })?;
            let wrapper = graph(
                graphs,
                team.controller.ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        "missing Junkenstein defender controller",
                    )
                })?,
            )?;
            let state = wrapper.state(0).filter(|node| {
                node.class == "STU_37D754C8"
                    && node
                        .field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph")
                        .and_then(serde_json::Value::as_str)
                        .and_then(crate::accounts::parse_guid)
                        == Some(0x0580_0000_0000_0c90)
            });
            if state.is_none() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "missing Junkenstein common controller SubScript",
                ));
            }
            Some((layout(wrapper)?, owner_slot(wrapper, 0)?))
        } else {
            None
        };
        let hud = start_hud
            .map(|index| {
                let hud = graph(graphs, HUD)?;
                Ok::<_, io::Error>(Hud {
                    layout: layout(hud)?,
                    root_state: owner_slot(root, index)?,
                    // 20E4's EntryClientOnlySharedVars initializes raw state 25.
                    shared_vars: owner_slot(hud, 25)?.wire,
                })
            })
            .transpose()?;
        let assembling = presence_bit(screen, ASSEMBLING)?;
        let assembling_sync = screen
            .presence_vars()
            .nth(assembling)
            .expect("validated screen slot")
            .0;
        Ok(Self {
            root: layout(root)?,
            host: layout(host)?,
            screen: layout(screen)?,
            entry: layout(entry)?,
            hud,
            parent,
            allow: presence_bit(root, ALLOW_SELECT)?,
            hero_select: owner_slot(root, hero_select)?.wire,
            start_host: owner_slot(root, start_host)?,
            start_screen: owner_slot(host, 0)?,
            start_entry: owner_slot(host, 1)?,
            assembling,
            screen_sync: screen.sync_vars.len(),
            screen_states: screen.states.len(),
            assembling_sync,
        })
    }

    /// Owner full frame: root controller, 288A, 288B, optional HUD, and team entry.
    pub fn controller_frame(
        &self,
        command_frame: u32,
        input: ControllerFrame<'_>,
    ) -> Result<BitWriter, WireError> {
        let mut instances = Vec::with_capacity(5);
        let root_id = if let Some((layout, slot)) = self.parent {
            let mut parent = Instance::new(1, layout, None);
            parent.states.insert(slot.wire, subscript(2));
            instances.push(parent);
            2
        } else {
            1
        };
        let mut root = Instance::new(
            root_id,
            self.root,
            self.parent.map(|(_, slot)| (1, slot.index)),
        );
        root.presence.insert(self.allow, Value::Bool(true));
        if let Some(extra) = input.presence {
            root.presence
                .extend(extra.iter().map(|(&slot, value)| (slot, value.clone())));
        }
        root.states.insert(self.hero_select, BitWriter::new());
        let host_id = root_id + 1;
        let screen_id = root_id + 2;
        root.states.insert(self.start_host.wire, subscript(host_id));

        let entry_id = root_id + if self.hud.is_some() { 4 } else { 3 };
        let mut host = Instance::new(host_id, self.host, Some((root_id, self.start_host.index)));
        host.states
            .insert(self.start_screen.wire, subscript(screen_id));
        host.states
            .insert(self.start_entry.wire, subscript(entry_id));
        let mut screen = Instance::new(
            screen_id,
            self.screen,
            Some((host_id, self.start_screen.index)),
        );
        if input.skins {
            screen.presence.insert(self.assembling, Value::Bool(true));
        }
        screen.variables = screen_variables(input, false);
        let entry = Instance::new(
            entry_id,
            self.entry,
            Some((host_id, self.start_entry.index)),
        );

        let hud = self.hud.map(|hud| {
            let id = root_id + 3;
            root.states.insert(hud.root_state.wire, subscript(id));
            let mut instance = Instance::new(id, hud.layout, Some((root_id, hud.root_state.index)));
            instance.states.insert(hud.shared_vars, BitWriter::new());
            instance
        });
        instances.extend([root, host, screen]);
        instances.extend(hud);
        instances.push(entry);

        owner_full(command_frame, &instances, &selection_variables(input))
    }

    pub(crate) fn selection_delta(
        &self,
        command_frame: u32,
        input: ControllerFrame<'_>,
        update_assembling: bool,
    ) -> Result<BitWriter, WireError> {
        let value = Value::Bool(input.skins);
        owner_variable_delta(
            command_frame,
            &selection_variables(input),
            Some(InstanceVariableDelta {
                id: if self.parent.is_some() { 4 } else { 3 },
                sync_slots: self.screen_sync,
                state_slots: self.screen_states,
                sync: update_assembling.then_some((self.assembling_sync, &value)),
                variables: &screen_variables(input, true),
            }),
        )
    }

    /// A remote 288D root and the three entity variables used by team selection.
    pub fn team_entry_frame(&self, hero: Option<u64>) -> Result<BitWriter, WireError> {
        team_entry(self.entry, hero)
    }
}

fn screen_variables(input: ControllerFrame<'_>, delta: bool) -> Variables {
    let mut variables = Variables::new();
    if delta || input.hide_assemble || input.send_selection {
        variables.insert(HIDE_ASSEMBLE, Value::Bool(input.hide_assemble));
    }
    if let Some((text, team)) = input.objective {
        variables.insert(OBJECTIVE_TEXT, Value::Asset(text));
        variables.insert(OBJECTIVE_COLOR_TEAM, Value::Integer(i64::from(team)));
    } else if delta && input.send_selection {
        variables.insert(OBJECTIVE_TEXT, Value::Null);
        variables.insert(OBJECTIVE_COLOR_TEAM, Value::Null);
    }
    variables
}

fn selection_variables(input: ControllerFrame<'_>) -> Variables {
    let mut variables = Variables::from([
        (OPEN_SELECT, Value::Bool(input.select_open)),
        (SELECT_SHOWN, Value::Bool(input.select_open)),
    ]);
    if let Some(hero) = input.hero.filter(|&hero| hero != 0) {
        variables.insert(CURRENT_HERO, Value::Asset(hero));
    } else if input.send_selection {
        variables.insert(CURRENT_HERO, Value::Asset(0));
    }
    if input.send_selection {
        variables.insert(PICKED, Value::Bool(input.confirmed_hero.is_some()));
        variables.insert(PICKED_HERO, Value::Asset(input.confirmed_hero.unwrap_or(0)));
    }
    variables
}

fn team_entry(layout: Layout, hero: Option<u64>) -> Result<BitWriter, WireError> {
    let entry = Instance::new(1, layout, None);
    let hero = hero.filter(|&hero| hero != 0);
    let mut variables = Variables::new();
    if let Some(hero) = hero {
        variables.insert(CURRENT_HERO, Value::Asset(hero));
    }
    variables.insert(PICKED, Value::Bool(hero.is_some()));
    if let Some(hero) = hero {
        variables.insert(PICKED_HERO, Value::Asset(hero));
    }
    // Bool and Asset have the same representation in compact and explicit frames.
    remote_full(&[entry], &variables)
}

fn graph(graphs: &GraphStore, index: u32) -> io::Result<&Graph> {
    graphs.graph(index).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("missing controller graph {index:04X}"),
        )
    })
}

fn layout(graph: &Graph) -> io::Result<Layout> {
    Layout::try_from(graph).map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn owner_slot(graph: &Graph, index: u16) -> io::Result<StateSlot> {
    let wire = graph.owner_slot(usize::from(index)).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "controller graph {:04X} lacks owner state {index}",
                graph.index
            ),
        )
    })?;
    Ok(StateSlot { index, wire })
}

fn presence_bit(graph: &Graph, id: u32) -> io::Result<usize> {
    graph.presence_bit(id).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "controller graph {:04X} lacks presence variable {id}",
                graph.index
            ),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    const SOLDIER: u64 = 0x02e0_0000_0000_006e;
    const REAPER: u64 = 0x02e0_0000_0000_0002;

    fn assert_frame(frame: &BitWriter, bits: usize, hex: &str) {
        assert_eq!(frame.bit_len(), bits);
        let actual: String = frame
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(actual, hex);
    }

    #[test]
    fn team_entry_exact_wire_bytes_with_and_without_pick() {
        let layout = Layout {
            graph: TEAM_ENTRY as u16,
            owner_slots: 0,
            remote_slots: 0,
            presence_slots: 0,
        };
        let selected = team_entry(layout, Some(SOLDIER)).unwrap();
        assert_frame(&selected, 167, "88461400631dc8e002dc248400953693c105b80100");
        let choosing = team_entry(layout, None).unwrap();
        assert_frame(&choosing, 69, "884614001342000000");
        assert_eq!(team_entry(layout, Some(0)).unwrap(), choosing);
    }

    #[test]
    fn picker_variable_deltas_match_python_without_remounting_the_screen() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/hero-picker-variables-v1.json"
        ))
        .unwrap();
        let number = |key: &str| fixture[key].as_u64().unwrap();
        let controller = Controller {
            screen_sync: number("screen_sync_slots") as usize,
            screen_states: number("screen_state_slots") as usize,
            assembling_sync: number("assembling_sync_slot") as usize,
            ..Controller::fixture_for_test()
        };
        let hero = crate::accounts::parse_guid(fixture["hero"].as_str().unwrap());
        for case in fixture["cases"].as_array().unwrap() {
            let frame = controller
                .selection_delta(
                    number("command_frame") as u32,
                    ControllerFrame {
                        select_open: case["open"].as_bool().unwrap(),
                        skins: case["assembling"].as_bool().unwrap(),
                        hide_assemble: true,
                        hero,
                        ..ControllerFrame::default()
                    },
                    case["update_assembling"].as_bool().unwrap(),
                )
                .unwrap();
            assert_frame(
                &frame,
                case["bits"].as_u64().unwrap() as usize,
                case["hex"].as_str().unwrap(),
            );
        }
    }

    #[test]
    #[ignore = "requires an authorized 104319 runtime in ORPHEUS_RUNTIME_DIR"]
    fn junkenstein_controller_matches_reference_parent_tree() {
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap();
        for mode in [
            crate::game_content::JUNKENSTEIN_REVENGE,
            crate::game_content::JUNKENSTEIN_ENDLESS,
        ] {
            let controller = Controller::load(&graphs, mode).unwrap();
            let frame = controller
                .controller_frame(
                    200,
                    ControllerFrame {
                        select_open: true,
                        ..ControllerFrame::default()
                    },
                )
                .unwrap();
            assert_frame(
                &frame,
                543,
                "822c441940403250000042118504dc118b283400806844a10101c00c13a096f5200000090000003000004200080000000000000000000000001a00c0221518000000001b",
            );
        }
    }

    #[test]
    #[ignore = "requires an authorized 104319 runtime in ORPHEUS_RUNTIME_DIR"]
    fn practice_and_pvp_controller_frames_match_reference_graphs_and_bytes() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            GraphStore::load(runtime.join("data").join("game_graphs_174.json.gz")).unwrap();
        let practice = Controller::load(&graphs, PRACTICE_RANGE_MODE).unwrap();
        let pvp = Controller::load(&graphs, 0).unwrap();
        let open = ControllerFrame {
            select_open: true,
            skins: true,
            ..ControllerFrame::default()
        };
        let extra = BTreeMap::from([(19, Value::Bool(false)), (0, Value::Bool(true))]);
        for (controller, command_frame, input, bits, expected) in [
            (
                practice,
                123456,
                open,
                446,
                "1a483c0040044f805044a1002bc4220a090020c84128800ad188420202801926402deb410040080000002000910458a3028700000000d80d",
            ),
            (
                practice,
                123456,
                ControllerFrame {
                    hide_assemble: true,
                    hero: Some(SOLDIER),
                    ..open
                },
                528,
                "1a483c0040044f805044a1002bc4220a090020c84128800ad188420202801926402deb41b10e6470016e00801000000040002209b046050e61572110000000006037",
            ),
            (
                practice,
                123457,
                ControllerFrame {
                    hero: Some(REAPER),
                    ..ControllerFrame::default()
                },
                489,
                "3a483c0040044f805044a1002bc4220a090020c84128800ad188420202801926002deb01b10e6470010200801000000040002209b0460506000000c06e00",
            ),
            (
                pvp,
                123456,
                open,
                485,
                "1a483c00404032805044a10077c4220a0900201a5148400030c304a8653d080000004200080000000000000000000000001200c01a113804000000c006",
            ),
            (
                pvp,
                123458,
                ControllerFrame {
                    presence: Some(&extra),
                    hero: Some(SOLDIER),
                    ..ControllerFrame::default()
                },
                534,
                "5a483c00404032805044a10077c4220a0900201a5148400030c304a0653d20d6810c2ec00d801000000100040000000000000000000000000900608d080c000000800d",
            ),
        ] {
            let frame = controller.controller_frame(command_frame, input).unwrap();
            assert_frame(&frame, bits, expected);
        }
        assert_frame(
            &practice.team_entry_frame(Some(SOLDIER)).unwrap(),
            167,
            "88461400631dc8e002dc248400953693c105b80100",
        );
        assert_eq!(
            pvp.controller_frame(0, open),
            Err(WireError::ZeroCommandFrame)
        );
    }
}
