//! Native destination paths and mutable identifier-map bucket storage.

use crate::script_wire::Value;

#[derive(Clone, Debug, PartialEq)]
pub struct IdentifierMap {
    buckets: Vec<Option<(u32, Value)>>,
}

impl Default for IdentifierMap {
    fn default() -> Self {
        Self {
            buckets: vec![None; 8],
        }
    }
}

impl IdentifierMap {
    #[cfg(test)]
    pub(crate) fn capacity(&self) -> usize {
        self.buckets.len()
    }
    pub(crate) fn entries(&self) -> impl Iterator<Item = (u32, &Value)> {
        self.buckets
            .iter()
            .flatten()
            .map(|(key, value)| (*key, value))
    }

    pub(crate) fn pairs(&self) -> Vec<(Value, Value)> {
        self.entries()
            .map(|(key, value)| (Value::Integer(i64::from(key)), value.clone()))
            .collect()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.buckets.iter().all(Option::is_none)
    }

    fn slot(&self, key: u32, cost: &mut usize) -> Option<usize> {
        for offset in 0..self.buckets.len() {
            *cost += 1;
            let slot = (key as usize + offset) % self.buckets.len();
            if self.buckets[slot]
                .as_ref()
                .is_none_or(|(existing, _)| *existing == key)
            {
                return Some(slot);
            }
        }
        None
    }

    fn field(&mut self, key: u32, cost: &mut usize) -> Result<&mut Value, &'static str> {
        if key == 0 {
            return Err("zero destination field");
        }
        let slot = if let Some(slot) = self.slot(key, cost) {
            slot
        } else {
            let width = self
                .buckets
                .len()
                .checked_mul(2)
                .filter(|n| *n <= 65536)
                .ok_or("destination map exceeds bounded storage")?;
            *cost += width;
            let previous = std::mem::replace(&mut self.buckets, vec![None; width]);
            // F68480 replays occupied physical buckets when growing.
            for (key, value) in previous.into_iter().flatten() {
                let slot = self.slot(key, cost).expect("doubled map capacity");
                self.buckets[slot] = Some((key, value));
            }
            self.slot(key, cost).expect("new map slot")
        };
        Ok(&mut self.buckets[slot].get_or_insert((key, Value::Integer(0))).1)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Step {
    Index(u16),
    Field(u16),
}

/// Mutate owned destination storage; return whether a final value slot existed.
/// Invalid array indexes still coerce the containing value before failing,
/// matching F65340. Work is charged by the caller before publishing the root.
pub(crate) fn assign(
    root: &mut Value,
    path: &[Step],
    value: Value,
    cost: &mut usize,
) -> Result<bool, &'static str> {
    if path.len() > 64 {
        return Err("destination path exceeds bounded depth");
    }
    let Some((step, tail)) = path.split_first() else {
        *root = value;
        return Ok(true);
    };
    *cost += 1;
    match *step {
        Step::Index(index) => {
            if !matches!(root, Value::Array(_)) {
                *root = Value::Array(Vec::new());
            }
            if index >= 1000 {
                return Ok(false);
            }
            let Value::Array(items) = root else {
                unreachable!()
            };
            let index = usize::from(index);
            if items.len() <= index {
                *cost += index + 1 - items.len();
                items.resize(index + 1, Value::Integer(0));
            }
            assign(&mut items[index], tail, value, cost)
        }
        Step::Field(field) => {
            // A legacy logical map has lost its native capacity/empty buckets.
            // Do not invent a bucket layout when mutating one.
            if matches!(root, Value::Map(_)) {
                return Err("destination map has no native bucket layout");
            }
            if !matches!(root, Value::IdentifierMap(_)) {
                *root = Value::IdentifierMap(IdentifierMap::default());
                *cost += 8;
            }
            let Value::IdentifierMap(map) = root else {
                unreachable!()
            };
            assign(map.field(u32::from(field), cost)?, tail, value, cost)
        }
    }
}
