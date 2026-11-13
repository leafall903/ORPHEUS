//! Strict 104319 voice-state lifecycle. Playback services remain explicit host inputs.

use crate::script_expr::{Value, valid_entity};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Stimulus {
    pub guid: u64,
    pub playable: bool,
    pub mode: bool,
}

pub(crate) trait Host {
    type Error;
    fn context(&mut self) -> Result<bool, Self::Error>;
    fn manager(&mut self) -> Result<bool, Self::Error>;
    fn clock(&mut self) -> Result<f64, Self::Error>;
    fn scalar(&mut self, field: &'static str, initial: Value) -> Result<Value, Self::Error>;
    fn load(&mut self, guid: u64) -> Result<Option<Stimulus>, Self::Error>;
    fn allow(&mut self, stimulus: Stimulus) -> Result<bool, Self::Error>;
    fn targets(&mut self) -> Result<Vec<u32>, Self::Error>;
    fn submit(&mut self, stimulus: Stimulus, targets: &[u32]) -> Result<u32, Self::Error>;
    fn duration(&mut self, handle: u32, guid: u64, mode: bool) -> Result<f64, Self::Error>;
    fn part(&mut self, number: u16) -> Result<f64, Self::Error>;
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct State {
    pub handle: u32,
    pub attempts: u8,
    pub duration: f64,
    pub part: u8,
    pub started_at: f64,
    pub delay: f64,
    pub cooldown: f64,
    pub retry_at: f64,
    pub submitted: bool,
    pub failed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Action {
    Timer(u8),
    Finish,
}

fn f32_value(value: f64) -> f64 {
    f64::from(value as f32)
}

fn at_least(value: f64, bound: f64) -> bool {
    value >= bound
}

fn milliseconds(value: f64) -> i32 {
    let value = f32_value(f32_value(value) * 1000.0);
    let value = f32_value(value + if value < 0.0 { -0.5 } else { 0.5 });
    if !value.is_finite() || !(-2147483648.0..2147483648.0).contains(&value) {
        i32::MIN
    } else {
        value.trunc() as i32
    }
}

pub(crate) fn begin<H: Host>(host: &mut H) -> Result<Option<State>, H::Error> {
    if !host.context()? {
        return Ok(None);
    }
    host.manager()?; // Begin validates the lookup even when no manager is present.
    let started_at = host.clock()?;
    let delay = f32_value(crate::script_expr::to_float(
        &host.scalar("m_B0263D2D", Value::Float(0.0))?,
    ));
    let cooldown = f32_value(crate::script_expr::to_float(
        &host.scalar("m_E0E02BD4", Value::Float(0.0))?,
    ));
    Ok(Some(State {
        started_at,
        delay,
        cooldown,
        part: 1,
        ..State::default()
    }))
}

fn stimulus<H: Host>(host: &mut H) -> Result<(u64, Option<Stimulus>), H::Error> {
    let value = host.scalar("m_stimulus", Value::Integer(0))?;
    let guid = crate::script_expr::asset_scalar(&value);
    if guid == 0 {
        Ok((0, None))
    } else {
        Ok((guid, host.load(guid)?))
    }
}

pub(crate) fn poll<H: Host>(state: &mut State, host: &mut H) -> Result<Vec<Action>, H::Error> {
    if !host.context()? || !host.manager()? {
        return Ok(Vec::new());
    }
    if !state.submitted {
        if !at_least(host.clock()? - state.started_at, state.delay) {
            return Ok(Vec::new());
        }
        let (_, stimulus) = stimulus(host)?;
        let allowed = match stimulus {
            Some(stimulus) if stimulus.playable => host.allow(stimulus)?,
            _ => false,
        };
        if allowed {
            let targets = host.targets()?;
            state.handle = host.submit(stimulus.expect("allowed loaded stimulus"), &targets)?;
            if valid_entity(state.handle) {
                state.submitted = true;
            } else {
                state.failed = true;
            }
        } else {
            state.failed = true;
        }
    }
    if state.failed {
        return Ok(
            if host.clock()? - state.started_at > state.delay + state.cooldown {
                vec![Action::Finish]
            } else {
                Vec::new()
            },
        );
    }
    if state.retry_at > 0.0 {
        if !at_least(host.clock()?, state.retry_at) {
            return Ok(Vec::new());
        }
        let next = f32_value(host.part(u16::from(state.part) + 1)?);
        if next == 0.0 || next.is_nan() {
            let previous = f32_value(host.part(u16::from(state.part))?);
            return Ok(if previous > 0.0 {
                Vec::new()
            } else {
                vec![Action::Finish]
            });
        }
        state.part = state.part.wrapping_add(1);
        state.retry_at = host.clock()? + next;
        return Ok(vec![Action::Timer(2)]);
    }
    if !valid_entity(state.handle) {
        return Ok(Vec::new());
    }
    let (guid, stimulus) = stimulus(host)?;
    if guid == 0 {
        return Ok(Vec::new());
    }
    let duration =
        f32_value(host.duration(state.handle, guid, stimulus.is_some_and(|s| s.mode))?);
    state.attempts = state.attempts.wrapping_add(1);
    if state.attempts > 5 {
        state.failed = true;
        return Ok(Vec::new());
    }
    if duration > 0.0 {
        let total = i64::from(milliseconds(duration)) + i64::from(milliseconds(state.cooldown));
        state.duration = f32_value(f32_value(total as f64) * f32_value(0.001));
        state.handle = 0;
        state.retry_at = host.clock()? + duration + state.cooldown;
        return Ok(vec![Action::Timer(1)]);
    }
    Ok(Vec::new())
}
