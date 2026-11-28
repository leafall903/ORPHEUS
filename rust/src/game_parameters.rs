//! Client game messages and their typed request parameters.

use crate::{
    game_heroes::{Hero, HeroCatalog},
    game_messages::Message,
    jam_codec::Value,
    script_init::RoomMessage,
    script_wire::{Value as ScriptValue, Variables},
};

pub const CLIENT_GAME_MESSAGE: u32 = 21616;
pub(crate) const CLIENT_WHEEL_ACTION: u32 = 21608;
const WHEEL_USE: u64 = 0x0240_0000_0000_1313;
pub const PICK_HERO: u64 = 0x0240_0000_0000_1361;
pub const OPEN_OR_CLOSE: u64 = 0x0240_0000_0000_1360;
const HERO_ASSET_TYPE: u16 = 0x02e0;
const WHEEL_ACTION_PREFIX: u64 = 0x0a90_0000_0000_0000;
const WHEEL_SELECTION: u64 = 0x0240_0000_0000_02a3;

/// Retail 1.74 wheel action GUIDs (matches `script_expr/wheel_slots.json`).
#[cfg(feature = "lua-mods")]
pub(crate) const EMOTE_WHEEL_ACTIONS: [u64; 4] = [
    0x0a90_0000_0000_0172,
    0x0a90_0000_0000_01a1,
    0x0a90_0000_0000_01a0,
    0x0a90_0000_0000_019f,
];
#[cfg(feature = "lua-mods")]
pub(crate) const VOICELINE_WHEEL_ACTIONS: [u64; 4] = [
    0x0a90_0000_0000_016f,
    0x0a90_0000_0000_019d,
    0x0a90_0000_0000_019c,
    0x0a90_0000_0000_019b,
];
#[cfg(feature = "lua-mods")]
pub(crate) const SPRAY_WHEEL_ACTIONS: [u64; 4] = [
    0x0a90_0000_0000_0173,
    0x0a90_0000_0000_01a5,
    0x0a90_0000_0000_01a4,
    0x0a90_0000_0000_01a3,
];
#[cfg(feature = "lua-mods")]
pub(crate) const GREETING_WHEEL_ACTION: u64 = 0x0a90_0000_0000_016b;
#[cfg(feature = "lua-mods")]
pub(crate) const NEED_HEALING_WHEEL_ACTION: u64 = 0x0a90_0000_0000_016c;
#[cfg(feature = "lua-mods")]
pub(crate) const THANKS_WHEEL_ACTION: u64 = 0x0a90_0000_0000_0171;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ClientWheelAction {
    action: u64,
    confirmed: bool,
    target: Option<u32>,
}

impl ClientWheelAction {
    #[cfg(feature = "lua-mods")]
    pub(crate) fn synthetic(action: u64) -> Self {
        Self {
            action,
            confirmed: true,
            target: None,
        }
    }
    pub(crate) fn read(message: &Message) -> Option<Self> {
        if message.id != CLIENT_WHEEL_ACTION {
            return None;
        }
        let Value::Unsigned(action) = *message.fields.get(&0x78)? else {
            return None;
        };
        let Value::Bool(confirmed) = *message.fields.get(&0x80)? else {
            return None;
        };
        if action & !u64::from(u32::MAX) != WHEEL_ACTION_PREFIX || action == WHEEL_ACTION_PREFIX {
            return None;
        }
        let target = match message.fields.get(&0x84) {
            None => None,
            Some(Value::Struct(marker)) => match marker.get(&0) {
                None | Some(Value::Unsigned(0)) => None,
                Some(Value::Unsigned(entity)) => Some(u32::try_from(*entity).ok()?),
                _ => return None,
            },
            _ => return None,
        };
        Some(Self {
            action,
            confirmed,
            target,
        })
    }

    pub(crate) fn confirmed(self) -> bool {
        self.confirmed
    }

    pub(crate) fn asset(self) -> u64 {
        self.action
    }

    pub(crate) fn target(self) -> Option<u32> {
        self.target
    }

    pub(crate) fn into_local_message(self) -> RoomMessage {
        RoomMessage {
            id: WHEEL_SELECTION,
            parameters: Variables::from([
                (0x08fc, ScriptValue::Asset(self.action)),
                (0x03e1, ScriptValue::Bool(self.confirmed)),
            ]),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ClientWheelUse {
    pub entity: u32,
    pub instance: u16,
    pub unlock: u64,
}

impl ClientWheelUse {
    pub(crate) fn read(message: ClientGameMessage<'_>) -> Option<Self> {
        if message.guid != WHEEL_USE {
            return None;
        }
        // Default header and one compact 053B Asset parameter, followed by
        // the original sender and target placeholders.
        const PREFIX: &[u8] = &[0, 0, 0, 0, 1, 0xc3, 0x3b, 0x05, 0x40, 0x0a, 4];
        let (body, [0, 0]) = message.parameters.split_entity_tail()? else {
            return None;
        };
        let unlock = u64::from_le_bytes(body.strip_prefix(PREFIX)?.try_into().ok()?);
        (unlock >> 48 == 0x0250 && unlock & 0x0000_ffff_ffff_ffff != 0).then_some(Self {
            entity: message.entity,
            instance: u16::try_from(message.instance).ok()?,
            unlock,
        })
    }

    pub(crate) fn into_local_message(self) -> RoomMessage {
        RoomMessage {
            id: WHEEL_USE,
            parameters: Variables::from([(0x053b, ScriptValue::Asset(self.unlock))]),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ClientGameMessage<'a> {
    pub entity: u32,
    pub guid: u64,
    pub instance: i16,
    pub parameters: Parameters<'a>,
}

impl<'a> ClientGameMessage<'a> {
    /// Borrow the typed 21616 envelope from an already decoded game message.
    pub fn read(message: &'a Message) -> Option<Self> {
        if message.id != CLIENT_GAME_MESSAGE {
            return None;
        }
        let fields = &message.fields;
        let Value::Struct(entity) = fields.get(&0x78)? else {
            return None;
        };
        let Value::Unsigned(entity) = *entity.get(&0)? else {
            return None;
        };
        let Value::Blob(parameters) = fields.get(&0x80)? else {
            return None;
        };
        let Value::Unsigned(guid) = *fields.get(&0xa8)? else {
            return None;
        };
        let Value::Signed(instance) = *fields.get(&0xb0)? else {
            return None;
        };
        Some(Self {
            entity: u32::try_from(entity).ok()?,
            guid,
            instance: i16::try_from(instance).ok()?,
            parameters: Parameters::new(parameters),
        })
    }

    pub fn hero_select<'catalog>(
        self,
        heroes: &'catalog HeroCatalog,
    ) -> Option<HeroSelectRequest<'catalog>> {
        match self.guid {
            PICK_HERO => {
                let bytes = self.parameters.as_bytes();
                // One typed hero parameter (005D), followed by two entity words.
                const HERO_PARAMETER: [u8; 13] =
                    [0, 0, 0, 0, 1, 0xc1, 0x5d, 0x40, 0x0a, 0x0c, 1, 0xff, 4];
                if bytes.len() == 29 && bytes.starts_with(&HERO_PARAMETER) {
                    let guid =
                        u64::from_le_bytes(bytes[13..21].try_into().expect("hero parameter"));
                    return Some(if guid == 0 {
                        HeroSelectRequest::ReopenAssembly
                    } else {
                        HeroSelectRequest::Pick(heroes.get(guid))
                    });
                }
                Some(HeroSelectRequest::Pick(
                    self.parameters
                        .find_asset(HERO_ASSET_TYPE, |guid| heroes.get(guid)),
                ))
            }
            OPEN_OR_CLOSE => Some(HeroSelectRequest::OpenOrClose(
                self.parameters.bool_before_entities(),
            )),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HeroSelectRequest<'a> {
    /// A recognized pick without a known hero has no gameplay effect.
    Pick(Option<&'a Hero>),
    ReopenAssembly,
    /// Without a readable bool, the client request toggles the current screen.
    OpenOrClose(Option<bool>),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Parameters<'a>(&'a [u8]);

impl<'a> Parameters<'a> {
    pub fn new(bytes: &'a [u8]) -> Self {
        Self(bytes)
    }

    pub fn as_bytes(self) -> &'a [u8] {
        self.0
    }

    /// Split the two little-endian entity words found at the end of the
    /// observed parameter blobs. Their meaning depends on the message.
    pub fn split_entity_tail(self) -> Option<(&'a [u8], [u32; 2])> {
        let (body, tail) = self.0.split_last_chunk::<8>()?;
        let &[a, b, c, d, e, f, g, h] = tail;
        Some((
            body,
            [
                u32::from_le_bytes([a, b, c, d]),
                u32::from_le_bytes([e, f, g, h]),
            ],
        ))
    }

    pub fn bool_before_entities(self) -> Option<bool> {
        let (body, _) = self.split_entity_tail()?;
        match body {
            [.., 1, 0] => Some(false),
            [.., 1, 1] => Some(true),
            _ => None,
        }
    }

    /// Find the first known unaligned asset of this type. This matches the
    /// reference pick reader without assuming the rest of the blob grammar.
    pub fn find_asset<T>(
        self,
        asset_type: u16,
        mut known: impl FnMut(u64) -> Option<T>,
    ) -> Option<T> {
        self.0.windows(8).find_map(|bytes| {
            let guid = u64::from_le_bytes(bytes.try_into().expect("eight-byte window"));
            if guid >> 48 == u64::from(asset_type) {
                known(guid)
            } else {
                None
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    use crate::{
        bits::BitReader,
        game_heroes::SOLDIER,
        game_messages::read_channel,
        jam_codec::{Fields, Schemas},
    };

    #[test]
    fn spray_use_report_requires_the_exact_asset_parameter() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/wheel-spray-use-report-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let parse = |value: &serde_json::Value| {
            crate::accounts::parse_guid(value.as_str().unwrap()).unwrap()
        };
        let expected = ClientWheelUse {
            entity: u32::try_from(parse(&input["entity"])).unwrap(),
            instance: u16::try_from(input["instance"].as_u64().unwrap()).unwrap(),
            unlock: parse(&input["unlock"]),
        };
        let raw = input["parameters_hex"].as_str().unwrap().as_bytes();
        let mut parameters: Vec<_> = raw
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect();
        let read = |parameters: &[u8], instance| {
            ClientWheelUse::read(ClientGameMessage {
                entity: expected.entity,
                guid: WHEEL_USE,
                instance,
                parameters: Parameters::new(parameters),
            })
        };
        let accepted = read(&parameters, expected.instance as i16).unwrap();
        assert_eq!(accepted, expected);
        assert_eq!(
            accepted.into_local_message().parameters[&0x053b],
            ScriptValue::Asset(expected.unlock)
        );
        assert_eq!(
            read(
                &parameters[..parameters.len() - 1],
                expected.instance as i16
            ),
            None
        );
        assert_eq!(read(&parameters, -1), None);
        for change in input["invalid_byte_changes"].as_array().unwrap() {
            let index = usize::try_from(change[0].as_u64().unwrap()).unwrap();
            let original = parameters[index];
            parameters[index] = u8::try_from(change[1].as_u64().unwrap()).unwrap();
            assert_eq!(
                read(&parameters, expected.instance as i16),
                None,
                "byte {index}"
            );
            parameters[index] = original;
        }
    }

    #[test]
    fn envelope_uses_the_client_message_id_and_fixed_width_fields() {
        let mut message = Message {
            id: CLIENT_GAME_MESSAGE,
            fields: Fields::from([
                (
                    0x78,
                    Value::Struct(Fields::from([(0, Value::Unsigned(0xa000_0100))])),
                ),
                (0x80, Value::Blob(vec![1, 2])),
                (0xa8, Value::Unsigned(OPEN_OR_CLOSE)),
                (0xb0, Value::Signed(-1)),
            ]),
        };
        let decoded = ClientGameMessage::read(&message).unwrap();
        assert_eq!(decoded.entity, 0xa000_0100);
        assert_eq!(decoded.guid, OPEN_OR_CLOSE);
        assert_eq!(decoded.instance, -1);
        assert_eq!(decoded.parameters.as_bytes(), [1, 2]);
        message.id -= 1;
        assert!(ClientGameMessage::read(&message).is_none());
        message.id += 1;
        message.fields.insert(0xb0, Value::Signed(32_768));
        assert!(ClientGameMessage::read(&message).is_none());
    }

    #[test]
    fn pick_scans_unaligned_asset_words_and_skips_unknown_candidates() {
        let heroes = HeroCatalog::from_json(
            r#"{"0x02E000000000006E":{"name":"Local fixture","class":"Damage","body":"0x0800000000000001"}}"#,
        )
        .unwrap();
        let mut bytes = vec![0xfa, 0xb0, 0x01];
        bytes.extend_from_slice(&0x02e0_0000_ffff_ffffu64.to_le_bytes());
        let pick = ClientGameMessage {
            entity: 1,
            guid: PICK_HERO,
            instance: 3,
            parameters: Parameters::new(&[]),
        };
        let unknown = ClientGameMessage {
            parameters: Parameters::new(&bytes),
            ..pick
        };
        assert_eq!(
            unknown.hero_select(&heroes),
            Some(HeroSelectRequest::Pick(None))
        );
        bytes.extend_from_slice(&SOLDIER.to_le_bytes());
        let known = ClientGameMessage {
            parameters: Parameters::new(&bytes),
            ..pick
        };
        assert_eq!(
            known.hero_select(&heroes),
            Some(HeroSelectRequest::Pick(Some(heroes.get(SOLDIER).unwrap())))
        );
    }

    #[test]
    fn only_a_typed_null_hero_reopens_assembly() {
        let case: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/escort-assembly-seat-pose-v1.json"
        ))
        .unwrap();
        let hex = case["input"]["reopen_pick_parameters"].as_str().unwrap();
        let bytes: Vec<_> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect();
        let heroes = HeroCatalog::from_json("{}").unwrap();
        let request = |parameters| {
            ClientGameMessage {
                entity: 1,
                guid: PICK_HERO,
                instance: 3,
                parameters: Parameters::new(parameters),
            }
            .hero_select(&heroes)
        };
        assert_eq!(request(&bytes), Some(HeroSelectRequest::ReopenAssembly));
        assert_eq!(request(&bytes[..28]), Some(HeroSelectRequest::Pick(None)));
        assert_eq!(request(&[0; 29]), Some(HeroSelectRequest::Pick(None)));
    }

    #[test]
    fn wheel_action_parses_optional_contextual_target_marker() {
        use crate::jam_codec::Value as WireValue;

        let make = |marker| Message {
            id: CLIENT_WHEEL_ACTION,
            fields: Fields::from([
                (0x78, WireValue::Unsigned(0x0a90_0000_0000_016b)),
                (0x80, WireValue::Bool(true)),
                (
                    0x84,
                    WireValue::Struct(Fields::from([(0, WireValue::Unsigned(marker))])),
                ),
            ]),
        };
        assert_eq!(ClientWheelAction::read(&make(0)).unwrap().target(), None);
        assert_eq!(
            ClientWheelAction::read(&make(0xc000_1005))
                .unwrap()
                .target(),
            Some(0xc000_1005)
        );
    }

    #[test]
    fn only_the_bool_tag_before_the_complete_entity_tail_sets_the_screen() {
        let mut bytes = vec![0x05, 0x01, 0x01];
        bytes.extend_from_slice(&0xa000_0100u32.to_le_bytes());
        bytes.extend_from_slice(&0xa000_0200u32.to_le_bytes());
        let parameters = Parameters::new(&bytes);
        assert_eq!(parameters.as_bytes(), bytes);
        assert_eq!(
            parameters.split_entity_tail(),
            Some((&bytes[..3], [0xa000_0100, 0xa000_0200]))
        );
        assert_eq!(parameters.bool_before_entities(), Some(true));
        bytes[2] = 0;
        assert_eq!(Parameters::new(&bytes).bool_before_entities(), Some(false));
        bytes[2] = 2;
        assert_eq!(Parameters::new(&bytes).bool_before_entities(), None);
        bytes[1] = 2;
        bytes[2] = 1;
        assert_eq!(Parameters::new(&bytes).bool_before_entities(), None);
        assert_eq!(Parameters::new(&bytes[..7]).split_entity_tail(), None);
        assert_eq!(Parameters::new(&bytes[3..]).bool_before_entities(), None);
    }

    #[test]
    #[ignore = "requires authorized 104319 game schemas and hero catalog in ORPHEUS_RUNTIME_DIR"]
    fn reliable_21616_messages_match_the_python_reader_and_hero_select_requests() {
        let data = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        )
        .join("data");
        let schemas = Schemas::load(data.join("game_schemas_174.json")).unwrap();
        let heroes = HeroCatalog::load(data.join("game_heroes_174.json")).unwrap();
        // Python game.messages.write_channel with synthetic entity IDs and the
        // known pick/open parameter bytes from tests/test_game_statescript.py.
        let bytes = concat!(
            "0b00070000000110000100a01d0000000000000001c15d400a0c01ff046e0000000000e002000100a0000200a061130000000040020300",
            "080000000110000100a00e00000001c3a2050101000100a0000200a060130000000040020300",
            "090000000110000100a00e00000001c3a2050100000100a0000200a060130000000040020300",
            "0a0000000110000100a00a0000000102000100a0000200a060130000000040020300",
            "0b0000000110000100a0000000006213000000004002ffff",
        )
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect::<Vec<_>>();
        let mut reader = BitReader::new(&bytes);
        let channel = read_channel(&mut reader, &schemas).unwrap();
        assert!(channel.unreliable.is_empty());
        assert_eq!(reader.position(), 1512);
        let expected = [
            Some(HeroSelectRequest::Pick(Some(heroes.get(SOLDIER).unwrap()))),
            Some(HeroSelectRequest::OpenOrClose(Some(true))),
            Some(HeroSelectRequest::OpenOrClose(Some(false))),
            Some(HeroSelectRequest::OpenOrClose(None)),
            None,
        ];
        assert_eq!(channel.reliable.len(), expected.len());
        for (index, (item, wanted)) in channel.reliable.iter().zip(expected).enumerate() {
            assert_eq!(item.sequence, 7 + index as u32);
            let message = ClientGameMessage::read(&item.message).unwrap();
            assert_eq!(message.entity, 0xa000_0100);
            assert_eq!(message.instance, if wanted.is_some() { 3 } else { -1 });
            assert_eq!(message.hero_select(&heroes), wanted);
            if wanted.is_some() {
                assert_eq!(
                    message.parameters.split_entity_tail().unwrap().1,
                    [0xa000_0100, 0xa000_0200],
                );
            }
        }
    }
}
