//! Explicit local startup settings for the requested Normal Cassidy acceptance runs.

use std::{fs::File, io, path::Path};

use serde::Deserialize;

use crate::{game_content::JunkensteinCard, game_heroes::CASSIDY};

#[derive(Clone, Copy, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct JunkensteinValidation {
    #[serde(default)]
    pub(crate) invulnerable_cassidy: bool,
    pub(crate) gate_capacity: Option<f64>,
}

impl JunkensteinValidation {
    pub(crate) fn load(path: &Path) -> io::Result<Self> {
        let settings: Self = serde_json::from_reader(File::open(path)?)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        if settings.gate_capacity.is_some_and(|value| {
            let value = value as f32;
            !value.is_finite() || value <= 0.0
        }) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "validation gate capacity must be positive and representable as a finite f32",
            ));
        }
        Ok(settings)
    }

    pub(crate) fn for_card(self, card: JunkensteinCard) -> Option<Self> {
        (Some(card) == JunkensteinCard::from_index(0x3c)).then_some(self)
    }

    pub(crate) fn protects(self, hero: Option<u64>) -> bool {
        self.invulnerable_cassidy && hero == Some(CASSIDY)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_junkenstein_validation_contract() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/junkenstein-validation-v1.json"
        ))
        .unwrap();
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("validation.json");
        for case in fixture["cases"].as_array().unwrap() {
            std::fs::write(&path, serde_json::to_vec(&case["settings"]).unwrap()).unwrap();
            let settings = JunkensteinValidation::load(&path).unwrap();
            let card = JunkensteinCard::from_index(case["card"].as_u64().unwrap() as u32).unwrap();
            let applied = settings.for_card(card);
            let hero = case["hero"]
                .as_str()
                .map(|value| u64::from_str_radix(value.trim_start_matches("0x"), 16).unwrap());
            assert_eq!(
                applied.is_some_and(|settings| settings.protects(hero)),
                case["protected"].as_bool().unwrap()
            );
            assert_eq!(
                applied.and_then(|settings| settings.gate_capacity),
                case["gate_capacity"].as_f64()
            );
        }
        for settings in fixture["invalid_settings"].as_array().unwrap() {
            std::fs::write(&path, serde_json::to_vec(settings).unwrap()).unwrap();
            assert!(JunkensteinValidation::load(&path).is_err());
        }
    }
}
