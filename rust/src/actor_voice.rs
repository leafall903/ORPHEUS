//! Private actor voice selection data. Audio delivery stays in the room queue.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{self, Read},
    path::Path,
};

use crate::runtime::format::parse_guid;
use serde::{Deserialize, Deserializer, de::Error};

const MAX_CATALOG_BYTES: u64 = 16 * 1024 * 1024;
const DEFAULT_VOICE_DURATION_SECS: f64 = 1.8;

// Named .078 stimuli and .0D8 criteria from the 104319 voice catalog.
pub(crate) const STIMULUS_HELLO: u64 = 0x0ee0_0000_0000_01cf;
pub(crate) const STIMULUS_THANKS: u64 = 0x0ee0_0000_0000_01d0;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct Guid(u64);

impl<'de> Deserialize<'de> for Guid {
    fn deserialize<D: Deserializer<'de>>(input: D) -> Result<Self, D::Error> {
        let text = String::deserialize(input)?;
        parse_guid(&text)
            .filter(|&id| id != 0)
            .map(Self)
            .ok_or_else(|| D::Error::custom("invalid actor voice GUID"))
    }
}

#[derive(Clone, Debug, Default, Deserialize)]
pub(crate) struct VoiceConditions {
    #[serde(default)]
    pub map: Option<u64>,
    #[serde(default)]
    pub target_hero: Option<u64>,
    #[serde(default)]
    pub required_ally_hero: Option<u64>,
    #[serde(default)]
    pub deprecated: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct VoiceLineMeta {
    guid: Guid,
    #[serde(default = "default_weight")]
    weight: f64,
    #[serde(default)]
    duration_secs: Option<f64>,
    #[serde(default)]
    conditions: VoiceConditions,
}

fn default_weight() -> f64 {
    1.0
}

#[derive(Clone, Debug, Deserialize)]
#[serde(untagged)]
enum RawVoiceLine {
    Simple(Guid),
    Rich(VoiceLineMeta),
}

impl RawVoiceLine {
    fn guid(&self) -> Guid {
        match self {
            Self::Simple(g) => *g,
            Self::Rich(m) => m.guid,
        }
    }

    fn weight(&self) -> f64 {
        match self {
            Self::Simple(_) => 1.0,
            Self::Rich(m) => {
                if m.weight.is_finite() && m.weight > 0.0 {
                    m.weight
                } else {
                    0.0
                }
            }
        }
    }

    fn duration_secs(&self) -> Option<f64> {
        match self {
            Self::Simple(_) => None,
            Self::Rich(m) => m.duration_secs.filter(|d| d.is_finite() && *d > 0.0),
        }
    }

    fn has_specific_condition(&self) -> bool {
        let Self::Rich(meta) = self else {
            return false;
        };
        meta.conditions.map.is_some()
            || meta.conditions.target_hero.is_some()
            || meta.conditions.required_ally_hero.is_some()
    }

    fn matches(&self, ctx: &VoiceContext<'_>, deprecated: &BTreeSet<Guid>) -> bool {
        if deprecated.contains(&self.guid()) {
            return false;
        }
        let Self::Rich(meta) = self else {
            return true;
        };
        if meta.conditions.deprecated || self.weight() <= 0.0 {
            return false;
        }
        if let Some(req_map) = meta.conditions.map {
            if ctx.map != Some(req_map) {
                return false;
            }
        }
        if let Some(req_target) = meta.conditions.target_hero {
            if ctx.target_hero != Some(req_target) {
                return false;
            }
        }
        if let Some(req_ally) = meta.conditions.required_ally_hero {
            if !ctx.ally_heroes.contains(&req_ally) {
                return false;
            }
        }
        true
    }
}

/// Runtime evaluation context for conditional & easter-egg voice selection.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct VoiceContext<'a> {
    pub map: Option<u64>,
    pub target_hero: Option<u64>,
    pub ally_heroes: &'a [u64],
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct VoiceConversationTurn {
    pub hero: u64,
    pub stimulus: u64,
    pub line: u64,
    #[serde(default)]
    pub delay_after_secs: f64,
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct VoiceConversation {
    #[serde(default)]
    pub map: Option<u64>,
    pub turns: Vec<VoiceConversationTurn>,
}

type VoiceSet = BTreeMap<Guid, Vec<RawVoiceLine>>;

pub(crate) struct VoiceOptions<'a> {
    stimulus: Guid,
    lines: &'a [RawVoiceLine],
    deprecated: &'a BTreeSet<Guid>,
}

impl VoiceOptions<'_> {
    pub(crate) fn choose(&self, choose: &mut dyn FnMut(usize) -> usize) -> Option<(u64, u64)> {
        self.choose_with_context(&VoiceContext::default(), choose)
    }

    pub(crate) fn choose_with_context(
        &self,
        ctx: &VoiceContext<'_>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) -> Option<(u64, u64)> {
        let eligible: Vec<&RawVoiceLine> = self
            .lines
            .iter()
            .filter(|entry| entry.matches(ctx, self.deprecated))
            .collect();
        if eligible.is_empty() {
            return None;
        }
        // Prefer conditional easter-egg lines when their specific map/target/ally
        // condition matched, while preserving weighted selection across candidates.
        let specific: Vec<&RawVoiceLine> = eligible
            .iter()
            .copied()
            .filter(|entry| entry.has_specific_condition())
            .collect();
        let pool = if !specific.is_empty() {
            &specific
        } else {
            &eligible
        };
        self.pick_weighted(pool, choose)
    }

    fn pick_weighted(
        &self,
        eligible: &[&RawVoiceLine],
        choose: &mut dyn FnMut(usize) -> usize,
    ) -> Option<(u64, u64)> {
        let total_weight: f64 = eligible.iter().map(|entry| entry.weight()).sum();
        if eligible.len() == 1 || total_weight <= 0.0 {
            return Some((self.stimulus.0, eligible[0].guid().0));
        }
        let uniform = eligible
            .iter()
            .all(|entry| (entry.weight() - 1.0).abs() <= 1e-6);
        if uniform {
            let index = choose(eligible.len());
            return Some((self.stimulus.0, eligible.get(index)?.guid().0));
        }
        let buckets = 1000usize;
        let roll = (choose(buckets) as f64 / buckets as f64) * total_weight;
        let mut acc = 0.0;
        for entry in eligible {
            acc += entry.weight();
            if roll <= acc {
                return Some((self.stimulus.0, entry.guid().0));
            }
        }
        Some((self.stimulus.0, eligible.last()?.guid().0))
    }
}

#[derive(Deserialize)]
pub(crate) struct ActorVoiceCatalog {
    criteria: BTreeMap<Guid, Vec<Guid>>,
    heroes: BTreeMap<Guid, Guid>,
    #[serde(default)]
    skins: BTreeMap<Guid, BTreeMap<Guid, Guid>>,
    sets: BTreeMap<Guid, VoiceSet>,
    #[serde(default)]
    equipped: BTreeMap<Guid, Guid>,
    #[serde(default)]
    durations: BTreeMap<Guid, f64>,
    #[serde(default)]
    deprecated_lines: BTreeSet<Guid>,
    #[serde(default)]
    conversations: Vec<VoiceConversation>,
}

impl ActorVoiceCatalog {
    pub(crate) fn load(path: &Path) -> io::Result<Option<Self>> {
        let file = match File::open(path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        let mut bytes = Vec::new();
        file.take(MAX_CATALOG_BYTES + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_CATALOG_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "actor voice catalog exceeds the size limit",
            ));
        }
        serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
    }

    pub(crate) fn line_duration(&self, line: u64) -> f64 {
        if let Some(&secs) = self.durations.get(&Guid(line)) {
            if secs.is_finite() && secs > 0.0 {
                return secs;
            }
        }
        for set in self.sets.values() {
            for entries in set.values() {
                for entry in entries {
                    if entry.guid().0 == line {
                        if let Some(secs) = entry.duration_secs() {
                            return secs;
                        }
                    }
                }
            }
        }
        DEFAULT_VOICE_DURATION_SECS
    }

    pub(crate) fn matching_conversation(
        &self,
        map: Option<u64>,
        active_heroes: &[u64],
    ) -> Option<&VoiceConversation> {
        self.conversations.iter().find(|conv| {
            conv.map.is_none_or(|m| map == Some(m))
                && !conv.turns.is_empty()
                && conv
                    .turns
                    .iter()
                    .all(|turn| active_heroes.contains(&turn.hero))
        })
    }

    pub(crate) fn options(&self, hero: u64, skin: u64, criterion: u64) -> Option<VoiceOptions<'_>> {
        self.options_for(hero, skin, self.criteria.get(&Guid(criterion))?)
    }

    pub(crate) fn stimulus_options(
        &self,
        hero: u64,
        skin: u64,
        stimulus: u64,
    ) -> Option<VoiceOptions<'_>> {
        self.options_for(hero, skin, &[Guid(stimulus)])
    }

    pub(crate) fn equipped_options(
        &self,
        hero: u64,
        skin: u64,
        effect: u64,
    ) -> Option<VoiceOptions<'_>> {
        self.options_for(
            hero,
            skin,
            std::slice::from_ref(self.equipped.get(&Guid(effect))?),
        )
    }

    fn options_for(&self, hero: u64, skin: u64, requested: &[Guid]) -> Option<VoiceOptions<'_>> {
        let base = self.heroes.get(&Guid(hero))?;
        let selected = self
            .skins
            .get(&Guid(skin))
            .and_then(|map| map.get(base))
            .unwrap_or(base);
        let lines_by_stimulus = self.sets.get(selected)?;
        requested.iter().find_map(|&stimulus| {
            let lines = lines_by_stimulus.get(&stimulus)?;
            (!lines.is_empty()).then_some(VoiceOptions {
                stimulus,
                lines,
                deprecated: &self.deprecated_lines,
            })
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_skin_voice_set_ordered_stimuli_and_variant() {
        use serde_json::{Value, json};

        let fixture: Value =
            serde_json::from_str(include_str!("../../fixtures/behavior/player-voice-v1.json"))
                .unwrap();
        let selection = &fixture["rust_selection"];
        let input = &fixture["input"];
        let mut data = input["fixtures"].clone();
        let text = |key| selection[key].as_str().unwrap();
        let key = text("criterion");
        let hero = input["hero"].as_str().unwrap();
        let skin = input["skin"].as_str().unwrap();
        let base = data["heroes"][hero].as_str().unwrap().to_owned();
        let selected = data["skins"][skin][&base].as_str().unwrap().to_owned();
        let stimulus = data["criteria"][key][0].as_str().unwrap().to_owned();
        data["criteria"][key]
            .as_array_mut()
            .unwrap()
            .insert(0, json!(text("unavailable_stimulus")));
        data["sets"][&selected][&stimulus]
            .as_array_mut()
            .unwrap()
            .push(json!(text("alternate_line")));
        data["sets"][&base] = json!({(stimulus.clone()): [text("stock_line")]});
        data["equipped"] = json!({(text("equipped_effect")): stimulus});
        let catalog: ActorVoiceCatalog = serde_json::from_value(data).unwrap();
        let guid = |value| parse_guid(value).unwrap();
        assert_eq!(
            catalog
                .options(guid(hero), guid(skin), guid(key))
                .unwrap()
                .choose(&mut |count| {
                    assert_eq!(count, 2);
                    1
                }),
            Some((guid(&stimulus), guid(text("alternate_line"))))
        );
        assert_eq!(
            catalog
                .options(guid(hero), 0, guid(key))
                .unwrap()
                .choose(&mut |_| panic!("one line needs no draw")),
            Some((guid(&stimulus), guid(text("stock_line"))))
        );
        assert!(catalog.options(1, guid(skin), guid(key)).is_none());
        assert_eq!(
            catalog
                .equipped_options(guid(hero), guid(skin), guid(text("equipped_effect")))
                .unwrap()
                .choose(&mut |_| 1),
            Some((guid(&stimulus), guid(text("alternate_line"))))
        );
        assert!(
            catalog
                .equipped_options(guid(hero), guid(skin), 1)
                .is_none()
        );
        assert_eq!(
            catalog
                .stimulus_options(guid(hero), guid(skin), guid(&stimulus))
                .unwrap()
                .choose(&mut |_| 1),
            Some((guid(&stimulus), guid(text("alternate_line"))))
        );
        assert!(
            catalog
                .stimulus_options(guid(hero), guid(skin), 1)
                .is_none()
        );
    }

    #[test]
    #[ignore = "requires the private 104319 actor voice catalog"]
    fn prepared_actor_voice_catalog_contains_playable_actor_criteria() {
        use std::path::PathBuf;

        let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let catalog = ActorVoiceCatalog::load(&root.join(crate::runtime::ACTOR_VOICE_CATALOG))
            .unwrap()
            .unwrap();
        let playable = |hero: Guid| {
            catalog
                .criteria
                .keys()
                .any(|criterion| catalog.options(hero.0, 0, criterion.0).is_some())
        };
        assert!(
            catalog
                .heroes
                .keys()
                .copied()
                .filter(|&hero| playable(hero))
                .count()
                > 1
        );
    }

    #[test]
    fn prefers_contextual_map_and_rival_easter_eggs_and_reads_durations() {
        let data = serde_json::json!({
            "criteria": {},
            "heroes": { "0x02E0000000000029": "0x07A0000000000054" },
            "sets": {
                "0x07A0000000000054": {
                    "0x0EE000000000000B": [
                        "0x0760000000000001",
                        {
                            "guid": "0x0760000000000002",
                            "weight": 1.0,
                            "conditions": { "target_hero": 0x02E0000000000005_u64 }
                        },
                        {
                            "guid": "0x0760000000000003",
                            "weight": 1.0,
                            "conditions": { "map": 0x08000000000000D4_u64 }
                        }
                    ]
                }
            },
            "durations": {
                "0x0760000000000002": 2.415
            }
        });
        let catalog: ActorVoiceCatalog = serde_json::from_value(data).unwrap();
        let opts = catalog
            .stimulus_options(0x02E0_0000_0000_0029, 0, 0x0EE0_0000_0000_000B)
            .unwrap();
        // Generic context falls back to unconditional line.
        let generic = opts.choose_with_context(&VoiceContext::default(), &mut |_| 0);
        assert_eq!(
            generic,
            Some((0x0EE0_0000_0000_000B, 0x0760_0000_0000_0001))
        );
        // Rival target_hero matches easter egg specifically.
        let rival_ctx = VoiceContext {
            map: None,
            target_hero: Some(0x02E0_0000_0000_0005),
            ally_heroes: &[],
        };
        assert_eq!(
            opts.choose_with_context(&rival_ctx, &mut |_| 0),
            Some((0x0EE0_0000_0000_000B, 0x0760_0000_0000_0002))
        );
        // Map context prefers the King's Row line over the generic line.
        let map_ctx = VoiceContext {
            map: Some(0x0800_0000_0000_00D4),
            target_hero: None,
            ally_heroes: &[],
        };
        assert_eq!(
            opts.choose_with_context(&map_ctx, &mut |_| 0),
            Some((0x0EE0_0000_0000_000B, 0x0760_0000_0000_0003))
        );
        assert!((catalog.line_duration(0x0760_0000_0000_0002) - 2.415).abs() < 1e-9);
    }
}
