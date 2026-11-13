//! Validated external combat-body definitions, matching Python npc.Definition.

use crate::{accounts::parse_guid, game_pools::HealthPools};
use serde::Deserialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    path::Path,
};

pub(crate) fn is_junkenstein_boss(hero: u64) -> bool {
    matches!(
        hero,
        0x02e0_0000_0000_0002
            | 0x02e0_0000_0000_0004
            | 0x02e0_0000_0000_0016
            | 0x02e0_0000_0000_0040
            | 0x02e0_0000_0000_0065
    )
}

/// Use the encounter body rather than a playable hero's cached pool values.
pub(crate) fn encounter_definition(
    catalog: Option<&NpcCatalog>,
    graphs: &crate::script_graph::GraphStore,
    heroes: &crate::game_heroes::HeroCatalog,
    hero: u64,
) -> io::Result<NpcDefinition> {
    let pair = graphs
        .enemy_pair(hero)
        .ok_or_else(|| invalid("missing authored enemy pair"))?;
    let body = graphs
        .enemy_body(pair.body)
        .ok_or_else(|| invalid("missing authored enemy body"))?;
    graphs
        .enemy_body(pair.controller)
        .ok_or_else(|| invalid("missing authored enemy controller"))?;
    if let Some(definition) = catalog.and_then(|catalog| catalog.get(hero)) {
        if definition.body != pair.body {
            return Err(invalid("NPC definition differs from authored enemy body"));
        }
        return Ok(definition.clone());
    }
    if !is_junkenstein_boss(hero) {
        return Err(invalid("missing prepared NPC definition"));
    }
    let hero_data = heroes
        .get(hero)
        .filter(|data| data.body == pair.body)
        .ok_or_else(|| invalid("boss hero differs from authored enemy body"))?;
    let pools = crate::game_pools::authored_pools(graphs, body)?
        .ok_or_else(|| invalid("boss body has no authored health pools"))?;
    if pools.health <= 0.0
        || [pools.health, pools.armour, pools.shields, pools.total()]
            .iter()
            .any(|value| !value.is_finite() || *value < 0.0 || *value > f64::from(f32::MAX))
    {
        return Err(invalid("boss body has invalid authored health pools"));
    }
    Ok(NpcDefinition {
        hero,
        body: pair.body,
        name: hero_data.name.clone(),
        pools,
    })
}

#[derive(Clone, Debug, PartialEq)]
pub struct NpcDefinition {
    pub hero: u64,
    pub body: u64,
    pub name: String,
    pub pools: HealthPools,
}

#[derive(Debug, Default)]
pub struct NpcCatalog {
    definitions: BTreeMap<u64, NpcDefinition>,
}

#[derive(Deserialize)]
struct Catalog {
    format_version: u32,
    definitions: Vec<Row>,
}

#[derive(Deserialize)]
struct Row {
    key: String,
    hero: String,
    body: String,
    name: String,
    health: f64,
    #[serde(default)]
    armour: f64,
    #[serde(default)]
    shields: f64,
}

impl NpcCatalog {
    pub fn load(path: impl AsRef<Path>) -> io::Result<Self> {
        Self::from_json(&fs::read_to_string(path)?)
    }

    pub fn from_json(text: &str) -> io::Result<Self> {
        let catalog: Catalog = serde_json::from_str(text).map_err(invalid)?;
        if catalog.format_version != 1 || catalog.definitions.is_empty() {
            return Err(invalid("unsupported or empty NPC catalog"));
        }
        let mut keys = BTreeSet::new();
        let mut definitions = BTreeMap::new();
        let asset = |text: &str, kind| {
            parse_guid(text)
                .filter(|guid| guid >> 48 == kind && guid & 0x0000_ffff_ffff_ffff != 0)
                .ok_or_else(|| invalid("NPC definition requires full hero and body GUIDs"))
        };
        for row in catalog.definitions {
            if row.key.len() != 1
                || !matches!(row.key.as_bytes()[0], b'1'..=b'9')
                || !keys.insert(row.key)
            {
                return Err(invalid("NPC catalog requires unique selection keys 1..9"));
            }
            let definition = NpcDefinition {
                hero: asset(&row.hero, 0x02e0)?,
                body: asset(&row.body, 0x0400)?,
                name: row.name,
                pools: HealthPools {
                    health: row.health,
                    armour: row.armour,
                    shields: row.shields,
                },
            };
            if definition.name.trim().is_empty()
                || definition.pools.health <= 0.0
                || [row.health, row.armour, row.shields]
                    .iter()
                    .any(|value| !value.is_finite() || *value < 0.0)
                || !definition.pools.total().is_finite()
            {
                return Err(invalid(
                    "NPC definition requires a name and finite positive health pools",
                ));
            }
            if definitions.insert(definition.hero, definition).is_some() {
                return Err(invalid(
                    "NPC enemy catalog requires unique hero definitions",
                ));
            }
        }
        Ok(Self { definitions })
    }

    pub fn get(&self, hero: u64) -> Option<&NpcDefinition> {
        self.definitions.get(&hero)
    }
}

fn invalid(message: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn boss_profiles_require_matching_prepared_pair_and_exact_body_pools() {
        use crate::{game_heroes::HeroCatalog, script_graph::GraphStore};
        let entry = |id: u64, value| json!({"m_0D09D2D9":format!("0x{:016X}",0x0d80_0000_0000_0000|id), "m_value":{"m_value":value}});
        let body = |health| {
            json!({"hero":"0x02E0000000000002","name":"Body", "manager":null,"weapons":[],
            "graphs":[{"graph":51,"m_1EB5A024":[entry(199,health)],"m_75408640":0}]})
        };
        let bundle = json!({"version":1,"graphs":[{"index":51,"header":{"m_nodesBitCount":1,"m_statesBitCount":1,
            "m_remoteSyncNodesBitCount":1,"m_syncVarsBitCount":1},"fields":{"m_publicSchema":{"m_entries":[entry(199,1000),entry(1736,25),entry(221,50)]}},
            "items":0,"nodes":[],"states":[],"entries":[],"remote":[],"sync_vars":[],"containers":{},"unknown_nodes":[]}],
            "bodies":{"0x0400000000000022":body(999)},"missing":[],
            "junkenstein":{"pairs":{"0x02E0000000000002":{"body":"0x0400000000000022","controller":"0x0400000000001958"}},
                "entities":{"0x0400000000000022":body(250),"0x0400000000001958":body(1)}}});
        let heroes = HeroCatalog::from_json(&json!({"0x02E0000000000002":{"name":"Reaper","class":"Damage","body":"0x0400000000000022"}}).to_string()).unwrap();
        let load = |data: &serde_json::Value| {
            GraphStore::from_json(&serde_json::to_vec(data).unwrap()).unwrap()
        };
        let graphs = load(&bundle);
        let profile = encounter_definition(None, &graphs, &heroes, 0x02e0_0000_0000_0002).unwrap();
        assert_eq!(profile.name, "Reaper");
        assert_eq!(
            profile.pools,
            HealthPools {
                health: 250.0,
                armour: 25.0,
                shields: 50.0
            }
        );
        assert_eq!(
            crate::game_pools::PoolCatalog::from_graphs(&graphs)
                .unwrap()
                .hero_pools(profile.hero)
                .unwrap()
                .health,
            999.0
        );
        for mutate in 0..5 {
            let mut bad = bundle.clone();
            match mutate {
                0 => {
                    bad["junkenstein"]["entities"]
                        .as_object_mut()
                        .unwrap()
                        .remove("0x0400000000001958");
                }
                1 => bad["junkenstein"]["entities"]["0x0400000000000022"]["graphs"] = json!([]),
                2 => {
                    bad["junkenstein"]["entities"]["0x0400000000000022"]["graphs"][0]["m_1EB5A024"] =
                        json!([entry(199, -1)])
                }
                3 => {
                    bad["junkenstein"]["entities"]["0x0400000000000022"]["graphs"][0]["m_1EB5A024"] =
                        json!([entry(199, 0)])
                }
                _ => {
                    bad["junkenstein"]["pairs"].as_object_mut().unwrap().clear();
                }
            }
            assert!(
                GraphStore::from_json(&serde_json::to_vec(&bad).unwrap())
                    .map(
                        |graphs| encounter_definition(None, &graphs, &heroes, profile.hero)
                            .is_err()
                    )
                    .unwrap_or(true),
                "case {mutate}"
            );
        }
        let wrong_heroes = HeroCatalog::from_json(&json!({"0x02E0000000000002":{"name":"Reaper","class":"Damage","body":"0x0400000000000023"}}).to_string()).unwrap();
        assert!(encounter_definition(None, &graphs, &wrong_heroes, profile.hero).is_err());
        let wrong_catalog = NpcCatalog::from_json(&json!({"format_version":1,"definitions":[{"key":"1","hero":"0x02E0000000000002","body":"0x0400000000000023","name":"Wrong","health":100}]}).to_string()).unwrap();
        assert!(
            encounter_definition(Some(&wrong_catalog), &graphs, &heroes, profile.hero).is_err()
        );
    }

    #[test]
    #[ignore = "requires external 104319 encounter body definitions"]
    fn authored_boss_profiles_use_exact_encounter_bodies_and_pools() {
        use crate::{game_heroes::HeroCatalog, script_graph::GraphStore};
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap();
        let heroes = HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
        let catalog =
            NpcCatalog::load(root.join("data/junkenstein/junkenstein-npcs.json")).unwrap();
        for (index, body, health, shields) in [
            (2, 0x22, 250.0, 0.0),
            (4, 0xe4, 200.0, 0.0),
            (0x16, 0x26f0, 100.0, 125.0),
            (0x40, 0x28f, 600.0, 0.0),
            (0x65, 0x380, 200.0, 0.0),
        ] {
            let hero = 0x02e0_0000_0000_0000 | index;
            assert!(catalog.get(hero).is_none());
            let profile = encounter_definition(Some(&catalog), &graphs, &heroes, hero).unwrap();
            assert_eq!(profile.body, 0x0400_0000_0000_0000 | body);
            assert_eq!(profile.hero, hero);
            assert_eq!(
                profile.pools,
                HealthPools {
                    health,
                    armour: 0.0,
                    shields
                }
            );
            assert_eq!(
                profile,
                encounter_definition(None, &graphs, &heroes, hero).unwrap()
            );
        }
        let zomnic = 0x02e0_0000_0000_0175;
        assert_eq!(
            encounter_definition(Some(&catalog), &graphs, &heroes, zomnic).unwrap(),
            *catalog.get(zomnic).unwrap()
        );
        assert!(encounter_definition(None, &graphs, &heroes, zomnic).is_err());
        assert!(encounter_definition(None, &graphs, &heroes, crate::game_heroes::SOLDIER).is_err());
    }

    #[test]
    fn catalog_rejects_ambiguous_identity_and_invalid_capacity() {
        let row = json!({"key":"1","hero":"0x02E0000000000175","body":"0x04000000000016BE",
            "name":"Fixture", "health":200,"armour":50,"shields":25});
        let load = |rows| {
            NpcCatalog::from_json(&json!({"format_version":1,"definitions":rows}).to_string())
        };
        let catalog = load(json!([row])).unwrap();
        assert_eq!(
            catalog.get(0x02e0_0000_0000_0175).unwrap().pools.total(),
            275.0
        );
        for (key, value) in [
            ("key", json!("0")),
            ("name", json!(" ")),
            ("health", json!(0)),
            ("armour", json!(-1)),
            ("hero", json!("0x0400000000000175")),
        ] {
            let mut bad = row.clone();
            bad[key] = value;
            assert!(load(json!([bad])).is_err());
        }
        let mut duplicate = row.clone();
        duplicate["key"] = json!("2");
        assert!(load(json!([row, duplicate])).is_err());
        assert!(load(json!([])).is_err());
    }
}
