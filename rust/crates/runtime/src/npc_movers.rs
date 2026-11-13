//! Selected NPC character movers from authorized local DataTool XML.
use super::{RuntimeError, at};
use roxmltree::Document;
use serde_json::{Map, Value, json};
use std::{fs, io, io::Read, path::Path};

const FIELDS: &[(&str, &str)] = &[
    ("m_1972C687", "stand_height"),
    ("m_7A70E3E3", "stand_radius"),
    ("m_4A576BC2", "stand_pogo"),
    ("m_03E04168", "crouch_height"),
    ("m_915FF368", "crouch_radius"),
    ("m_D97B3735", "crouch_pogo"),
    ("m_D4AE9D05", "ground_circle_radius"),
    ("m_5C6B4602", "ground_circle_rays"),
    ("m_6B7FDB90", "pogo_frequency"),
    ("m_E54438F7", "pogo_damping"),
    ("m_AEBAD1B0", "max_slope"),
    ("m_08FFAA0A", "step_down"),
    ("m_CFD3E635", "run_forward"),
    ("m_61D6B4E9", "run_backward"),
    ("m_19452F15", "run_strafe"),
    ("m_B470B8DA", "crouch_forward"),
    ("m_AA6F4CE1", "crouch_backward"),
    ("m_0DDD18C3", "crouch_strafe"),
    ("m_DAA0618B", "accel_ground"),
    ("m_E631BA84", "accel_air"),
    ("m_D24BF4E6", "accel_air_touching"),
    ("m_E0A0FE25", "decel_ground_base"),
    ("m_6F25AA23", "decel_ground_base_speed"),
    ("m_4E03E892", "decel_ground_per_speed"),
    ("m_142DA8D4", "decel_air_base"),
    ("m_031FE7EE", "decel_air_base_speed"),
    ("m_8895A9C8", "decel_air_per_speed"),
    ("m_EC289987", "jump_speed"),
    ("m_C2C40969", "gravity_scale"),
    ("m_B74245E8", "terminal_velocity"),
    ("m_975F21A5", "terminal_velocity_up"),
    ("m_B99EC983", "ground_gravity_accel"),
    ("m_CEF4898C", "ground_gravity_decel"),
    ("m_D4A3C694", "character_collision"),
];
const DRAGON: &str = "https://yretenai.com/dragonml/v1";

fn invalid(message: impl Into<Box<dyn std::error::Error + Send + Sync>>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn character_mover(xml: &str) -> io::Result<Value> {
    let doc = Document::parse(xml).map_err(invalid)?;
    let root = doc.root_element();
    if !root.has_tag_name("STUEntityDefinition") {
        return Err(invalid("expected entity definition"));
    }
    let mut maps = root
        .children()
        .filter(|n| n.is_element() && n.attribute((DRAGON, "name")) == Some("m_componentMap"));
    let map = maps
        .next()
        .ok_or_else(|| invalid("missing component map"))?;
    if maps.next().is_some() || !map.has_tag_name((DRAGON, "map")) {
        return Err(invalid("invalid component map"));
    }
    let mut entries = map
        .children()
        .filter(|n| n.has_tag_name("STUCharacterMoverComponent"));
    let entry = entries
        .next()
        .ok_or_else(|| invalid("missing NPC character mover"))?;
    if entries.next().is_some() {
        return Err(invalid("duplicate character mover"));
    }
    let mut values = entry.children().filter(|n| {
        n.has_tag_name("STUCharacterMoverComponent")
            && n.attribute((DRAGON, "name")) == Some("dragon:value")
    });
    let value = values
        .next()
        .ok_or_else(|| invalid("missing character mover value"))?;
    if values.next().is_some() {
        return Err(invalid("duplicate character mover value"));
    }
    let mut result = Map::new();
    for &(hash, name) in FIELDS {
        let default = match name {
            "ground_circle_radius" => Some(0.25),
            "ground_circle_rays" => Some(4.0),
            "accel_air_touching" => Some(0.5),
            "character_collision" => Some(1.0),
            _ => None,
        };
        let number = value
            .attribute(hash)
            .map(|s| s.replace(',', ".").parse::<f64>().map_err(invalid))
            .transpose()?
            .or(default)
            .ok_or_else(|| invalid(format!("missing mover field {hash}")))?;
        if !number.is_finite() || number < 0.0 {
            return Err(invalid(format!("invalid mover field {hash}")));
        }
        let json = if matches!(name, "ground_circle_rays" | "character_collision") {
            if number.fract() != 0.0 || number > f64::from(u32::MAX) {
                return Err(invalid("invalid integer mover field"));
            }
            json!(number as u32)
        } else {
            json!(number)
        };
        result.insert(name.into(), json);
    }
    for (height, radius) in [
        ("stand_height", "stand_radius"),
        ("crouch_height", "crouch_radius"),
    ] {
        if result[radius].as_f64().unwrap() <= 0.0
            || result[height].as_f64().unwrap() < 2.0 * result[radius].as_f64().unwrap()
        {
            return Err(invalid("invalid mover capsule"));
        }
    }
    Ok(Value::Object(result))
}

pub(super) fn enrich(
    table: &Path,
    catalog: &Path,
    xml_dir: &Path,
) -> Result<Vec<u8>, RuntimeError> {
    let mut table_value: Value = at(
        table,
        serde_json::from_reader(fs::File::open(table).map_err(|e| RuntimeError::Io {
            path: table.into(),
            source: e,
        })?)
        .map_err(invalid),
    )?;
    let records = at(
        table,
        table_value
            .as_object_mut()
            .ok_or_else(|| invalid("expected mover table")),
    )?;
    let catalog_value: Value = at(
        catalog,
        serde_json::from_reader(fs::File::open(catalog).map_err(|e| RuntimeError::Io {
            path: catalog.into(),
            source: e,
        })?)
        .map_err(invalid),
    )?;
    if catalog_value["format_version"].as_u64() != Some(1) {
        return at(catalog, Err(invalid("unsupported NPC catalog")));
    }
    let definitions = at(
        catalog,
        catalog_value["definitions"]
            .as_array()
            .filter(|a| !a.is_empty())
            .ok_or_else(|| invalid("missing NPC definitions")),
    )?;
    let mut seen = std::collections::BTreeSet::new();
    for row in definitions {
        let body = at(
            catalog,
            row["body"]
                .as_str()
                .and_then(super::format::parse_guid)
                .filter(|g| g >> 48 == 0x0400 && g & 0x0000_ffff_ffff_ffff != 0)
                .ok_or_else(|| invalid("invalid NPC body")),
        )?;
        if !seen.insert(body) {
            return at(catalog, Err(invalid("duplicate NPC body")));
        }
        let path = xml_dir.join(format!("{:012X}.003.xml", body & 0x0000_ffff_ffff_ffff));
        let mut xml = String::new();
        at(
            &path,
            at(&path, fs::File::open(&path))?
                .take(16 * 1024 * 1024 + 1)
                .read_to_string(&mut xml),
        )?;
        if xml.len() > 16 * 1024 * 1024 {
            return at(&path, Err(invalid("entity XML exceeds size limit")));
        }
        let mut mover = at(&path, character_mover(&xml))?;
        mover["name"] = row["name"].clone();
        records.insert(format!("0x{body:016X}"), mover);
    }
    serde_json::to_vec_pretty(&table_value).map_err(RuntimeError::Manifest)
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;
    pub(crate) fn fixture() -> String {
        let fields = FIELDS
            .iter()
            .filter(|(_, n)| {
                !matches!(
                    *n,
                    "ground_circle_radius"
                        | "ground_circle_rays"
                        | "accel_air_touching"
                        | "character_collision"
                )
            })
            .map(|(h, n)| format!("{h}=\"{}\"", if n.ends_with("height") { 4.0 } else { 1.0 }))
            .collect::<Vec<_>>()
            .join(" ");
        format!(
            "<STUEntityDefinition xmlns:dragon=\"{DRAGON}\"><dragon:map dragon:name=\"m_componentMap\"><STUCharacterMoverComponent><STUCharacterMoverComponent dragon:name=\"dragon:value\" {fields}/></STUCharacterMoverComponent></dragon:map></STUEntityDefinition>"
        )
    }
    #[test]
    fn reads_only_the_selected_mover_and_native_field_defaults() {
        let xml = fixture();
        let mover = character_mover(&xml).unwrap();
        assert_eq!(mover["run_forward"], json!(1.0));
        assert_eq!(mover["ground_circle_rays"], json!(4));
        assert_eq!(mover["accel_air_touching"], json!(0.5));
        assert!(character_mover(&xml.replace("m_CFD3E635=\"1\"", "m_CFD3E635=\"NaN\"")).is_err());
        assert!(character_mover(&xml.replace("m_CFD3E635=\"1\"", "")).is_err());
        assert!(character_mover(&xml.replace("m_7A70E3E3=\"1\"", "m_7A70E3E3=\"3\"")).is_err());
    }
}
