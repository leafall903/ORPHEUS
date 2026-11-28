//! A body's authoritative health, armour and shield pools.
//!
//! The damage and healing order follows the Python reference. Original-client
//! validation of the armour reduction and healing order remains open.

use crate::world::{Component, POOLS, pool, pool_component};
pub use orpheus_statescript::script_init::NamedPoolLoss;
pub use orpheus_statescript::script_runtime::PoolKind;
use orpheus_statescript::script_runtime::{HEALTH_POOL, SHIELDS_POOL};

const ARMOUR_BLOCK: f64 = 5.0;
const DEATH_EPSILON: f64 = 1e-9;

const DAMAGE_ORDER: [PoolKind; 3] = [PoolKind::Shields, PoolKind::Armour, PoolKind::Health];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Death {
    pub time: f64,
    pub frame: u32,
    pub killer: u32,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Damage {
    pub dealt: f64,
    pub killed: bool,
    pub named_pool_losses: Vec<NamedPoolLoss>,
}

#[derive(Clone, Debug, PartialEq)]
struct Pool {
    field: usize,
    id: u32,
    maximum: f64,
    current: f64,
}

impl Pool {
    fn kind(&self) -> usize {
        self.field / POOLS
    }

    fn wire(&self) -> [u8; 20] {
        pool(
            self.id,
            self.kind() as u32,
            self.maximum as f32,
            self.current as f32,
        )
    }
}

#[derive(Clone, Debug, PartialEq)]
struct ShieldGrant {
    handles: Vec<u32>,
    maximum: f64,
    current: f64,
    stacked: bool,
}

#[derive(Clone, Debug, PartialEq)]
struct TemporaryShield {
    identifier: u64,
    field: usize,
    grants: Vec<ShieldGrant>,
}

impl TemporaryShield {
    fn amount(&self, maximum: bool) -> f64 {
        self.grants
            .iter()
            .map(|grant| {
                // The state can still observe a depleted grant before cleanup.
                if !maximum || (grant.stacked && grant.current == 0.0) {
                    grant.current
                } else {
                    grant.maximum
                }
            })
            .sum()
    }

    fn drain(&mut self, left: &mut f64) -> f64 {
        let mut taken = 0.0;
        for grant in &mut self.grants {
            if *left <= 0.0 {
                break;
            }
            let drain = (*left).min(grant.current);
            grant.current -= drain;
            *left -= drain;
            taken += drain;
        }
        taken
    }

    fn wire(&self) -> [u8; 20] {
        let maximum = self.amount(true);
        let id = if maximum > 0.0 {
            self.identifier as u32
        } else {
            0
        };
        pool(
            id,
            SHIELDS_POOL as u32,
            maximum as f32,
            self.amount(false) as f32,
        )
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Vitals {
    pools: Vec<Pool>,
    temporary_shields: Vec<TemporaryShield>,
    // Preserve explicit zero rows for previously advertised named shields.
    temporary_fields: u64,
    wave_health: Option<u32>,
    last_id: u32,
    used: u64,
    changed: u64,
    death: Option<Death>,
}

impl Vitals {
    pub fn new(health: f64, armour: f64, shields: f64) -> Self {
        Self::with_current_health(health, armour, shields, health)
    }

    pub(crate) fn with_current_health(
        health: f64,
        armour: f64,
        shields: f64,
        current: f64,
    ) -> Self {
        let mut vitals = Self::empty();
        let amounts = [health, armour, shields];
        for kind in DAMAGE_ORDER {
            let amount = amounts[kind as usize];
            if kind == PoolKind::Health || amount > 0.0 {
                let current = if kind == PoolKind::Health {
                    current
                } else {
                    amount
                };
                let _ = vitals.add_pool(kind, amount, current);
            }
        }
        vitals.clear_changed();
        vitals
    }

    pub(crate) fn empty() -> Self {
        Self {
            pools: Vec::new(),
            temporary_shields: Vec::new(),
            temporary_fields: 0,
            wave_health: None,
            last_id: 0,
            used: 0,
            changed: 0,
            death: None,
        }
    }

    pub(crate) fn add_pool(&mut self, kind: PoolKind, maximum: f64, current: f64) -> Option<u32> {
        if !(0.0..=f64::from(f32::MAX)).contains(&maximum) || !(0.0..=maximum).contains(&current) {
            return None;
        }
        let first = kind as usize * POOLS;
        let field = (first..first + POOLS).find(|&field| !self.field_occupied(field))?;
        let index = self.pools.partition_point(|pool| pool.field < field);
        let id = self.last_id.checked_add(1)?;
        self.pools.insert(
            index,
            Pool {
                field,
                id,
                maximum,
                current,
            },
        );
        self.last_id = id;
        self.temporary_fields &= !(1 << field);
        self.used |= 1 << field;
        self.changed |= 1 << field;
        Some(id)
    }

    fn field_occupied(&self, field: usize) -> bool {
        self.pools
            .binary_search_by_key(&field, |pool| pool.field)
            .is_ok()
            || self
                .temporary_shields
                .iter()
                .any(|shield| shield.field == field)
    }

    pub(crate) fn add_temporary_shield(
        &mut self,
        identifier: u64,
        amount: f64,
        stacked: bool,
    ) -> Option<u32> {
        if self.death.is_some()
            || identifier >> 52 != 0x0d8
            || identifier as u32 == 0
            || !(0.0 < amount && amount <= f64::from(f32::MAX))
        {
            return None;
        }
        let existing = self
            .temporary_shields
            .iter()
            .position(|shield| shield.identifier == identifier);
        let id = self.last_id.checked_add(1)?;
        if let Some(index) = existing {
            let shield = &mut self.temporary_shields[index];
            if !stacked {
                if let Some(grant) = shield.grants.iter_mut().find(|grant| !grant.stacked) {
                    // Each scope gets its own lifetime handle for the shared
                    // balance without changing its capacity or current value.
                    grant.handles.push(id);
                    self.last_id = id;
                    return Some(id);
                }
            }
            if shield.amount(true) + amount > f64::from(f32::MAX) {
                return None;
            }
        }
        let index = if let Some(index) = existing {
            index
        } else {
            if self
                .temporary_shields
                .iter()
                .any(|shield| shield.identifier as u32 == identifier as u32)
            {
                return None;
            }
            let first = SHIELDS_POOL * POOLS;
            let field = (first + 1..first + POOLS).find(|&field| !self.field_occupied(field))?;
            let index = self
                .temporary_shields
                .partition_point(|shield| shield.field < field);
            self.temporary_shields.insert(
                index,
                TemporaryShield {
                    identifier,
                    field,
                    grants: Vec::new(),
                },
            );
            self.temporary_fields |= 1 << field;
            self.used |= 1 << field;
            index
        };
        let shield = &mut self.temporary_shields[index];
        shield.grants.push(ShieldGrant {
            handles: vec![id],
            maximum: amount,
            current: amount,
            stacked,
        });
        self.last_id = id;
        self.changed |= 1 << shield.field;
        Some(id)
    }

    pub(crate) fn temporary_shield_current(&self, identifier: u64) -> f64 {
        self.temporary_shields
            .iter()
            .find(|shield| shield.identifier == identifier)
            .map_or(0.0, |shield| shield.amount(false))
    }

    /// Return the named pool's remaining shields after a pool-local drain.
    pub(crate) fn drain_temporary_shield(&mut self, identifier: u64, mut amount: f64) -> f64 {
        let Some(shield) = self
            .temporary_shields
            .iter_mut()
            .find(|shield| shield.identifier == identifier)
        else {
            return 0.0;
        };
        if amount.is_finite() && amount > 0.0 && shield.drain(&mut amount) > 0.0 {
            self.changed |= 1 << shield.field;
        }
        shield.amount(false)
    }

    /// End all named grants and retain the slot history needed to publish clears.
    pub(crate) fn clear_temporary_shields(&mut self) {
        for shield in self.temporary_shields.drain(..) {
            self.changed |= 1 << shield.field;
        }
    }

    pub(crate) fn pool_current(&self, id: u32) -> Option<f64> {
        self.pools
            .iter()
            .find(|pool| pool.id == id)
            .map(|pool| pool.current)
            .or_else(|| {
                self.temporary_shields
                    .iter()
                    .flat_map(|shield| &shield.grants)
                    .find(|grant| grant.handles.contains(&id))
                    .map(|grant| grant.current)
            })
    }

    pub(crate) fn add_wave_health(&mut self, amount: f64) -> Option<u32> {
        if self.death.is_some() || self.wave_health.is_some() || amount <= 0.0 {
            return None;
        }
        let id = self.add_pool(PoolKind::Health, amount, amount)?;
        self.wave_health = Some(id);
        Some(id)
    }

    pub(crate) fn remove_pool(&mut self, id: u32) -> Option<f64> {
        if let Some(index) = self.pools.iter().position(|pool| pool.id == id) {
            if self.wave_health == Some(id) {
                self.wave_health = None;
            }
            let pool = self.pools.remove(index);
            self.changed |= 1 << pool.field;
            return Some(pool.current);
        }
        let (shield_index, grant_index, handle_index) = self
            .temporary_shields
            .iter()
            .enumerate()
            .find_map(|(shield_index, shield)| {
            shield
                .grants
                .iter()
                .enumerate()
                .find_map(|(grant_index, grant)| {
                    grant
                        .handles
                        .iter()
                        .position(|&handle| handle == id)
                        .map(|handle_index| (shield_index, grant_index, handle_index))
                })
        })?;
        let shield = &mut self.temporary_shields[shield_index];
        let grant = &mut shield.grants[grant_index];
        grant.handles.swap_remove(handle_index);
        let current = grant.current;
        if !grant.handles.is_empty() {
            return Some(current);
        }
        shield.grants.remove(grant_index);
        self.changed |= 1 << shield.field;
        if shield.grants.is_empty() {
            self.temporary_shields.remove(shield_index);
        }
        Some(current)
    }

    pub fn total(&self) -> f64 {
        self.total_amount(false)
    }

    pub fn maximum(&self) -> f64 {
        self.total_amount(true)
    }

    fn total_amount(&self, maximum: bool) -> f64 {
        self.amount(None, maximum)
            + self
                .temporary_shields
                .iter()
                .map(|shield| shield.amount(maximum))
                .sum::<f64>()
    }

    pub(crate) fn amount(&self, kind: Option<PoolKind>, maximum: bool) -> f64 {
        self.pools
            .iter()
            .filter(|pool| kind.is_none_or(|kind| pool.kind() == kind as usize))
            .map(|pool| if maximum { pool.maximum } else { pool.current })
            .sum()
    }

    pub fn death(&self) -> Option<Death> {
        self.death
    }

    pub fn damage(&mut self, amount: f64, source: Death) -> Damage {
        if self.death.is_some() || !amount.is_finite() || amount <= 0.0 {
            return Damage::default();
        }
        let mut left = amount;
        let mut dealt = 0.0;
        let mut named_pool_losses = Vec::new();
        for shield in &mut self.temporary_shields {
            let taken = shield.drain(&mut left);
            if taken > 0.0 {
                self.changed |= 1 << shield.field;
                dealt += taken;
                named_pool_losses.push(NamedPoolLoss {
                    identifier: shield.identifier,
                    amount: taken,
                });
            }
            if left <= 0.0 {
                break;
            }
        }
        'damage: for kind in DAMAGE_ORDER {
            if kind == PoolKind::Health {
                if let Some(id) = self.wave_health {
                    if let Some(bonus) = self.pools.iter_mut().find(|pool| pool.id == id) {
                        let taken = left.min(bonus.current);
                        if taken > 0.0 {
                            bonus.current -= taken;
                            self.changed |= 1 << bonus.field;
                            left -= taken;
                            dealt += taken;
                        }
                    }
                }
            }
            let mut reduce_armour = kind == PoolKind::Armour;
            for pool in self
                .pools
                .iter_mut()
                .filter(|pool| pool.kind() == kind as usize)
            {
                if left <= 0.0 {
                    break 'damage;
                }
                if pool.current == 0.0 {
                    continue;
                }
                if reduce_armour {
                    left = (left - ARMOUR_BLOCK).max(left / 2.0);
                    reduce_armour = false;
                }
                let taken = left.min(pool.current);
                if taken > 0.0 {
                    pool.current -= taken;
                    self.changed |= 1 << pool.field;
                    left -= taken;
                    dealt += taken;
                }
            }
        }
        let killed = dealt > 0.0 && self.amount(Some(PoolKind::Health), false) <= DEATH_EPSILON;
        if killed {
            for pool in self
                .pools
                .iter_mut()
                .take_while(|pool| pool.kind() == HEALTH_POOL)
            {
                if pool.current > 0.0 {
                    pool.current = 0.0;
                    self.changed |= 1 << pool.field;
                }
            }
            self.clear_temporary_shields();
            self.death = Some(source);
        }
        Damage {
            dealt,
            killed,
            named_pool_losses,
        }
    }

    pub fn heal(&mut self, amount: f64) -> f64 {
        if self.death.is_some() || !amount.is_finite() || amount <= 0.0 {
            return 0.0;
        }
        let mut left = amount;
        let mut healed = 0.0;
        for pool in &mut self.pools {
            if left <= 0.0 {
                break;
            }
            let given = left.min(pool.maximum - pool.current);
            if given > 0.0 {
                pool.current += given;
                self.changed |= 1 << pool.field;
                left -= given;
                healed += given;
            }
        }
        healed
    }

    pub(crate) fn recharge_shields(&mut self, mut amount: f64) {
        if self.death.is_some() || !amount.is_finite() || amount <= 0.0 {
            return;
        }
        for pool in self
            .pools
            .iter_mut()
            .filter(|pool| pool.kind() == SHIELDS_POOL)
        {
            let current = pool.current;
            let restored = (current + amount).min(pool.maximum);
            pool.current = f64::from(restored as f32).clamp(current, pool.maximum);
            if pool.current > current {
                self.changed |= 1 << pool.field;
                amount = (amount - (pool.current - current)).max(0.0);
            }
        }
    }

    pub fn component(&self) -> Component {
        let fields = self
            .pools
            .iter()
            .fold(self.temporary_fields, |fields, pool| {
                fields | 1 << pool.field
            });
        self.encode(fields)
    }

    /// Include previously occupied slots so a replacement update still clears them.
    pub(crate) fn cumulative_component(&self) -> Component {
        self.encode(self.used)
    }

    pub(crate) fn take_cumulative_update(&mut self) -> Option<Component> {
        if !self.has_changed() {
            return None;
        }
        self.clear_changed();
        Some(self.cumulative_component())
    }

    pub(crate) fn has_changed(&self) -> bool {
        self.changed != 0
    }

    pub fn changed_component(&self) -> Option<Component> {
        self.has_changed().then(|| self.encode(self.changed))
    }

    pub fn clear_changed(&mut self) {
        self.changed = 0;
    }

    fn encode(&self, mut fields: u64) -> Component {
        pool_component(std::iter::from_fn(|| {
            if fields == 0 {
                return None;
            }
            let field = fields.trailing_zeros() as usize;
            fields &= fields - 1;
            let bytes =
                if let Ok(index) = self.pools.binary_search_by_key(&field, |pool| pool.field) {
                    self.pools[index].wire()
                } else if self.temporary_fields & (1 << field) != 0 {
                    self.temporary_shields
                        .iter()
                        .find(|shield| shield.field == field)
                        .map_or_else(
                            || pool(0, SHIELDS_POOL as u32, 0.0, 0.0),
                            TemporaryShield::wire,
                        )
                } else {
                    [0; 20]
                };
            Some((field, bytes))
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::world::{ComponentValue, health};

    const HIT: Death = Death {
        time: 12.0,
        frame: 1042,
        killer: 9,
    };

    #[test]
    fn wave_health_absorbs_hits_before_base_health_and_retires_independently() {
        let mut vitals = Vitals::new(250.0, 0.0, 0.0);
        let bonus = vitals.add_wave_health(750.0).unwrap();
        assert!(vitals.add_wave_health(1.0).is_none());
        assert_eq!(vitals.maximum(), 1000.0);
        assert_eq!(vitals.damage(800.0, HIT).dealt, 800.0);
        assert_eq!(vitals.pool_current(bonus), Some(0.0));
        assert_eq!(vitals.total(), 200.0);
        assert!(vitals.death().is_none());
        assert_eq!(vitals.heal(100.0), 100.0);
        assert_eq!(vitals.pool_current(bonus), Some(50.0));
        assert_eq!(vitals.remove_pool(bonus), Some(50.0));
        assert_eq!(vitals.maximum(), 250.0);
        assert_eq!(vitals.total(), 250.0);
        let next = vitals.add_wave_health(750.0).unwrap();
        assert_ne!(next, bonus);
        assert!(vitals.damage(1000.0, HIT).killed);
        assert!(vitals.add_wave_health(750.0).is_none());
    }

    #[test]
    fn shield_armour_health_order_and_partial_updates_match_reference() {
        let mut vitals = Vitals::new(200.0, 50.0, 25.0);
        let currents = |vitals: &Vitals| {
            [PoolKind::Health, PoolKind::Armour, PoolKind::Shields]
                .map(|kind| vitals.amount(Some(kind), false))
        };
        assert_eq!(vitals.component(), health(200.0, 200.0, 50.0, 25.0));
        assert_eq!(vitals.damage(30.0, HIT).dealt, 27.5);
        assert_eq!(currents(&vitals), [200.0, 47.5, 0.0]);
        let changed = vitals.changed_component().unwrap();
        assert_eq!(changed.fields[0], None);
        assert_eq!(
            changed.fields[POOLS],
            Some(ComponentValue::Blob20(pool(2, 1, 50.0, 47.5)))
        );
        assert_eq!(
            changed.fields[2 * POOLS],
            Some(ComponentValue::Blob20(pool(1, 2, 25.0, 0.0)))
        );
        vitals.clear_changed();
        assert!(vitals.changed_component().is_none());
        assert_eq!(vitals.damage(100.0, HIT).dealt, 95.0);
        assert_eq!(currents(&vitals), [152.5, 0.0, 0.0]);
        assert_eq!(vitals.heal(49.0), 49.0);
        assert_eq!(currents(&vitals), [200.0, 1.5, 0.0]);
        assert_eq!(vitals.heal(1000.0), 73.5);
        assert_eq!(vitals.total(), vitals.maximum());
    }

    #[test]
    fn named_shields_report_actual_drains_and_replicate_shared_slots() {
        const FIRST: u64 = 0x0d80_0000_0044_0123;
        const SECOND: u64 = 0x0d80_0000_0044_0124;
        const SLOT: usize = SHIELDS_POOL * POOLS + 1;
        let mut vitals = Vitals::new(100.0, 10.0, 20.0);
        let shared = vitals.add_temporary_shield(FIRST, 20.0, false).unwrap();
        vitals.clear_changed();
        let other_holder = vitals.add_temporary_shield(FIRST, 50.0, false).unwrap();
        assert_ne!(shared, other_holder);
        assert_eq!(vitals.pool_current(other_holder), Some(20.0));
        assert!(vitals.changed_component().is_none());
        let stacked = vitals.add_temporary_shield(FIRST, 15.0, true).unwrap();
        let other = vitals.add_temporary_shield(SECOND, 25.0, true).unwrap();
        assert_eq!(
            vitals.component().fields[SLOT],
            Some(ComponentValue::Blob20(pool(FIRST as u32, 2, 35.0, 35.0)))
        );
        assert_eq!(vitals.total(), 190.0);
        assert_eq!(vitals.maximum(), 190.0);
        assert_eq!(vitals.amount(None, false), 130.0);
        assert_eq!(vitals.amount(Some(PoolKind::Shields), true), 20.0);

        let first = vitals.damage(28.0, HIT);
        assert_eq!(first.dealt, 28.0);
        assert_eq!(
            first.named_pool_losses,
            [NamedPoolLoss {
                identifier: FIRST,
                amount: 28.0
            }]
        );
        assert_eq!(vitals.pool_current(shared), Some(0.0));
        assert_eq!(vitals.pool_current(stacked), Some(7.0));
        assert_eq!(vitals.temporary_shield_current(FIRST), 7.0);
        assert_eq!(vitals.temporary_shield_current(SECOND), 25.0);
        assert_eq!(vitals.total(), 162.0);
        assert_eq!(vitals.maximum(), 190.0);

        vitals.clear_changed();
        let second = vitals.damage(40.0, HIT);
        assert_eq!(second.dealt, 40.0);
        assert_eq!(
            second.named_pool_losses,
            [
                NamedPoolLoss {
                    identifier: FIRST,
                    amount: 7.0
                },
                NamedPoolLoss {
                    identifier: SECOND,
                    amount: 25.0
                },
            ]
        );
        assert_eq!(vitals.pool_current(other), Some(0.0));
        assert_eq!(vitals.total(), 122.0);
        assert_eq!(vitals.maximum(), 150.0);
        let first_wire = Some(ComponentValue::Blob20(pool(FIRST as u32, 2, 20.0, 0.0)));
        let clear = Some(ComponentValue::Blob20(pool(0, 2, 0.0, 0.0)));
        let changed = vitals.changed_component().unwrap();
        assert_eq!(changed.fields[SLOT], first_wire);
        assert_eq!(changed.fields[SLOT + 1], clear);
        assert_eq!(vitals.heal(100.0), 8.0);
        vitals.recharge_shields(100.0);
        assert_eq!(vitals.total(), 130.0);
        assert_eq!(vitals.temporary_shield_current(FIRST), 0.0);

        vitals.clear_changed();
        assert_eq!(vitals.remove_pool(shared), Some(0.0));
        assert_eq!(vitals.remove_pool(shared), None);
        assert_eq!(vitals.pool_current(other_holder), Some(0.0));
        assert!(vitals.changed_component().is_none());
        assert_eq!(vitals.remove_pool(other_holder), Some(0.0));
        assert_eq!(vitals.remove_pool(stacked), Some(0.0));
        assert_eq!(vitals.remove_pool(other), Some(0.0));
        assert_eq!(vitals.changed_component().unwrap().fields[SLOT], clear);
        vitals.clear_changed();
        assert_eq!(vitals.component().fields[SLOT], clear);
        assert_eq!(vitals.cumulative_component().fields[SLOT + 1], clear);
        assert!(vitals.damage(5.0, HIT).named_pool_losses.is_empty());
    }

    #[test]
    fn named_shield_without_base_shield_reserves_the_base_slot_and_clears_on_death() {
        const ID: u64 = 0x0d80_0000_0044_0123;
        const BASE_SLOT: usize = SHIELDS_POOL * POOLS;
        let mut vitals = Vitals::new(10.0, 0.0, 0.0);
        assert!(vitals.add_temporary_shield(1, 1.0, false).is_none());
        assert!(vitals.add_temporary_shield(ID, f64::NAN, false).is_none());
        let grant = vitals.add_temporary_shield(ID, 7.0, true).unwrap();
        assert_eq!(vitals.component().fields[BASE_SLOT], None);
        let base = vitals.add_pool(PoolKind::Shields, 2.0, 2.0).unwrap();
        let damage = vitals.damage(30.0, HIT);
        assert!(damage.killed);
        assert_eq!(damage.dealt, 19.0);
        assert_eq!(
            damage.named_pool_losses,
            [NamedPoolLoss {
                identifier: ID,
                amount: 7.0
            }]
        );
        assert_eq!(vitals.pool_current(base), Some(0.0));
        assert_eq!(vitals.pool_current(grant), None);
        assert_eq!(
            vitals.component().fields[BASE_SLOT + 1],
            Some(ComponentValue::Blob20(pool(0, 2, 0.0, 0.0)))
        );
    }

    #[test]
    fn local_named_shield_drain_does_not_count_as_the_next_hit() {
        const DECAYING: u64 = 0x0d80_0000_0044_0123;
        const OTHER: u64 = 0x0d80_0000_0044_0124;
        const BASE_SLOT: usize = SHIELDS_POOL * POOLS;
        let mut vitals = Vitals::new(100.0, 0.0, 20.0);
        let first = vitals.add_temporary_shield(DECAYING, 5.0, false).unwrap();
        let second = vitals.add_temporary_shield(DECAYING, 7.0, true).unwrap();
        vitals.add_temporary_shield(OTHER, 3.0, false).unwrap();
        vitals.clear_changed();

        assert_eq!(vitals.drain_temporary_shield(DECAYING, 6.25), 5.75);
        assert_eq!(vitals.pool_current(first), Some(0.0));
        assert_eq!(vitals.pool_current(second), Some(5.75));
        assert_eq!(vitals.temporary_shield_current(OTHER), 3.0);
        assert_eq!(vitals.amount(Some(PoolKind::Shields), false), 20.0);
        let changed = vitals.changed_component().unwrap();
        assert_eq!(changed.fields[BASE_SLOT], None);
        assert_eq!(
            changed.fields[BASE_SLOT + 1],
            Some(ComponentValue::Blob20(pool(DECAYING as u32, 2, 12.0, 5.75)))
        );
        assert_eq!(changed.fields[BASE_SLOT + 2], None);

        vitals.clear_changed();
        assert_eq!(vitals.drain_temporary_shield(DECAYING, f64::NAN), 5.75);
        assert_eq!(vitals.drain_temporary_shield(DECAYING, -1.0), 5.75);
        assert_eq!(
            vitals.drain_temporary_shield(0x0d80_0000_0044_0125, 10.0),
            0.0
        );
        assert!(!vitals.has_changed());

        let damage = vitals.damage(10.0, HIT);
        assert_eq!(damage.dealt, 10.0);
        assert_eq!(
            damage.named_pool_losses,
            [
                NamedPoolLoss {
                    identifier: DECAYING,
                    amount: 5.75
                },
                NamedPoolLoss {
                    identifier: OTHER,
                    amount: 3.0
                },
            ]
        );
        assert_eq!(vitals.amount(Some(PoolKind::Shields), false), 18.75);
    }

    #[test]
    fn lethal_transition_keeps_original_attribution_and_stops_healing() {
        let mut vitals = Vitals::new(10.0, 0.0, 0.0);
        assert_eq!(
            vitals.damage(10.0 - DEATH_EPSILON / 2.0, HIT),
            Damage {
                dealt: 10.0 - DEATH_EPSILON / 2.0,
                killed: true,
                named_pool_losses: Vec::new(),
            }
        );
        assert_eq!(vitals.total(), 0.0);
        assert_eq!(vitals.death(), Some(HIT));
        assert_eq!(
            vitals.damage(100.0, Death { killer: 50, ..HIT }),
            Damage::default()
        );
        assert_eq!(vitals.heal(50.0), 0.0);
        assert_eq!(
            vitals.changed_component().unwrap().fields[0],
            Some(ComponentValue::Blob20(pool(1, 0, 10.0, 0.0)))
        );
    }

    #[test]
    fn shield_recharge_only_restores_shields_and_respects_death() {
        let mut vitals = Vitals::new(100.0, 0.0, 25.0);
        vitals.damage(45.0, HIT);
        vitals.clear_changed();
        vitals.recharge_shields(0.48);
        assert_eq!(
            vitals.amount(Some(PoolKind::Shields), false),
            f64::from(0.48_f32)
        );
        assert_eq!(vitals.amount(Some(PoolKind::Health), false), 80.0);
        let changed = vitals.changed_component().unwrap();
        assert_eq!(changed.fields[0], None);
        assert!(changed.fields[SHIELDS_POOL * POOLS].is_some());
        vitals.recharge_shields(50.0);
        assert_eq!(vitals.amount(Some(PoolKind::Shields), false), 25.0);
        assert!(vitals.damage(105.0, HIT).killed);
        vitals.clear_changed();
        vitals.recharge_shields(50.0);
        assert_eq!(vitals.total(), 0.0);
        assert!(vitals.changed_component().is_none());
    }

    #[test]
    fn pool_removal_survives_cumulative_delivery_and_reuse_gets_a_new_id() {
        let mut vitals = Vitals::new(200.0, 0.0, 0.0);
        let field = SHIELDS_POOL * POOLS;
        assert!(!vitals.has_changed());
        assert_eq!(vitals.cumulative_component(), vitals.component());
        let old = vitals.add_pool(PoolKind::Shields, 40.0, 40.0).unwrap();
        assert_eq!(old, 2);
        assert_eq!(vitals.damage(40.0, HIT).dealt, 40.0);
        assert_eq!(vitals.pool_current(old), Some(0.0));
        assert_eq!(vitals.amount(Some(PoolKind::Shields), true), 40.0);
        assert_eq!(
            vitals.changed_component().unwrap().fields[field],
            Some(ComponentValue::Blob20(pool(
                old,
                SHIELDS_POOL as u32,
                40.0,
                0.0
            )))
        );

        vitals.clear_changed();
        assert_eq!(vitals.remove_pool(old), Some(0.0));
        assert_eq!(vitals.pool_current(old), None);
        assert_eq!(vitals.component().fields[field], None);
        assert_eq!(
            vitals.changed_component().unwrap().fields[field],
            Some(ComponentValue::Blob20([0; 20]))
        );
        vitals.clear_changed();
        let cumulative = vitals.cumulative_component();
        assert_eq!(
            cumulative.fields[field],
            Some(ComponentValue::Blob20([0; 20]))
        );
        assert_eq!(cumulative.fields[field + 1], None);

        let fresh = vitals.add_pool(PoolKind::Shields, 20.0, 10.0).unwrap();
        assert_eq!(fresh, 3);
        assert_eq!(vitals.remove_pool(old), None);
        assert_eq!(vitals.pool_current(fresh), Some(10.0));
        assert_eq!(vitals.cumulative_component(), vitals.component());
    }

    #[test]
    fn pool_limits_reject_invalid_values_full_kinds_and_id_wrap() {
        let mut vitals = Vitals::empty();
        for (maximum, current) in [
            (f64::NAN, 0.0),
            (f64::INFINITY, 1.0),
            (f64::MAX, 1.0),
            (-1.0, 0.0),
            (1.0, f64::NEG_INFINITY),
            (1.0, f64::NAN),
            (1.0, 2.0),
        ] {
            assert_eq!(vitals.add_pool(PoolKind::Health, maximum, current), None);
        }
        assert!(!vitals.has_changed());
        for slot in 0..POOLS {
            assert_eq!(
                vitals.add_pool(PoolKind::Health, 10.0, 10.0),
                Some(slot as u32 + 1)
            );
        }
        assert_eq!(vitals.add_pool(PoolKind::Health, 10.0, 10.0), None);
        vitals.last_id = u32::MAX - 1;
        assert_eq!(
            vitals.add_pool(PoolKind::Armour, 10.0, 10.0),
            Some(u32::MAX)
        );
        assert_eq!(vitals.add_pool(PoolKind::Shields, 10.0, 10.0), None);
        assert_eq!(vitals.total(), 170.0);
        assert_eq!(
            Vitals::new(0.0, 0.0, 0.0).component(),
            health(0.0, 0.0, 0.0, 0.0)
        );
    }

    #[test]
    fn same_kind_pools_damage_and_heal_in_slot_order() {
        let mut vitals = Vitals::new(200.0, 10.0, 0.0);
        let extra_armour = vitals.add_pool(PoolKind::Armour, 30.0, 30.0).unwrap();
        assert_eq!(vitals.damage(f64::INFINITY, HIT), Damage::default());
        assert_eq!(vitals.heal(f64::NAN), 0.0);
        assert_eq!(vitals.damage(30.0, HIT).dealt, 25.0);
        assert_eq!(vitals.pool_current(1), Some(0.0));
        assert_eq!(vitals.pool_current(extra_armour), Some(15.0));
        assert_eq!(vitals.heal(11.0), 11.0);
        assert_eq!(vitals.pool_current(1), Some(10.0));
        assert_eq!(vitals.pool_current(extra_armour), Some(16.0));
    }
}
