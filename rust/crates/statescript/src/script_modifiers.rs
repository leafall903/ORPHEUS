//! Core movement modifier operations and slot keys shared between StateScript and movement.

pub const SPEED_SLOT: u8 = 0;

pub fn narrow(value: f64) -> f64 {
    f64::from(value as f32)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Operation {
    Add,
    Multiply,
    Set,
    Minimum,
    Maximum,
}

impl Operation {
    pub fn apply(self, value: f64, operand: f64) -> f64 {
        match self {
            Self::Add => narrow(value + operand),
            Self::Multiply => narrow(value * operand),
            Self::Set => operand,
            Self::Minimum if operand < value => operand,
            Self::Maximum if operand > value => operand,
            Self::Minimum | Self::Maximum => value,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Modifier {
    pub index: u8,
    pub operation: Operation,
    pub value: f64,
    pub priority: f64,
}

impl Modifier {
    pub fn apply(self, value: f64) -> f64 {
        self.operation.apply(value, self.value)
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ModifierKey {
    pub instance: u16,
    pub state: u16,
    pub slot: usize,
}

/// Complete inline `STUCharacterMoverComponent` override authored inside
/// `STU_ED2B22BD` (e.g. Wrecking Ball Roll form `1203:STU_316CFEF2_1`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MoverProfile {
    pub stand_height: f64,
    pub stand_radius: f64,
    pub stand_pogo: f64,
    pub crouch_height: f64,
    pub crouch_radius: f64,
    pub crouch_pogo: f64,
    pub ground_circle_radius: f64,
    pub ground_circle_rays: u32,
    pub pogo_frequency: f64,
    pub pogo_damping: f64,
    pub max_slope: f64,
    pub step_down: f64,
    pub run_forward: f64,
    pub run_backward: f64,
    pub run_strafe: f64,
    pub crouch_forward: f64,
    pub crouch_backward: f64,
    pub crouch_strafe: f64,
    pub accel_ground: f64,
    pub accel_air: f64,
    pub accel_air_touching: f64,
    pub decel_ground_base: f64,
    pub decel_ground_base_speed: f64,
    pub decel_ground_per_speed: f64,
    pub decel_air_base: f64,
    pub decel_air_base_speed: f64,
    pub decel_air_per_speed: f64,
    pub jump_speed: f64,
    pub gravity_scale: f64,
    pub terminal_velocity: f64,
    pub terminal_velocity_up: f64,
    pub ground_gravity_accel: f64,
    pub ground_gravity_decel: f64,
    pub character_collision: u32,
}

impl MoverProfile {
    /// Parse a complete 34-field retail `STUCharacterMoverComponent` JSON object.
    pub fn from_stu_component(component: &serde_json::Value) -> Option<Self> {
        if component.get("$").and_then(serde_json::Value::as_str)
            != Some("STUCharacterMoverComponent")
        {
            return None;
        }
        let float = |name: &str| {
            component
                .get(name)
                .and_then(serde_json::Value::as_f64)
                .filter(|v| v.is_finite())
        };
        let uint = |name: &str| {
            component
                .get(name)
                .and_then(serde_json::Value::as_u64)
                .and_then(|v| u32::try_from(v).ok())
        };
        Some(Self {
            stand_height: float("m_1972C687")?,
            stand_radius: float("m_7A70E3E3")?,
            stand_pogo: float("m_4A576BC2")?,
            crouch_height: float("m_03E04168")?,
            crouch_radius: float("m_915FF368")?,
            crouch_pogo: float("m_D97B3735")?,
            ground_circle_radius: float("m_D4AE9D05")?,
            ground_circle_rays: uint("m_5C6B4602")?,
            pogo_frequency: float("m_6B7FDB90")?,
            pogo_damping: float("m_E54438F7")?,
            max_slope: float("m_AEBAD1B0")?,
            step_down: float("m_08FFAA0A")?,
            run_forward: float("m_CFD3E635")?,
            run_backward: float("m_61D6B4E9")?,
            run_strafe: float("m_19452F15")?,
            crouch_forward: float("m_B470B8DA")?,
            crouch_backward: float("m_AA6F4CE1")?,
            crouch_strafe: float("m_0DDD18C3")?,
            accel_ground: float("m_DAA0618B")?,
            accel_air: float("m_E631BA84")?,
            accel_air_touching: float("m_D24BF4E6")?,
            decel_ground_base: float("m_E0A0FE25")?,
            decel_ground_base_speed: float("m_6F25AA23")?,
            decel_ground_per_speed: float("m_4E03E892")?,
            decel_air_base: float("m_142DA8D4")?,
            decel_air_base_speed: float("m_031FE7EE")?,
            decel_air_per_speed: float("m_8895A9C8")?,
            jump_speed: float("m_EC289987")?,
            gravity_scale: float("m_C2C40969")?,
            terminal_velocity: float("m_B74245E8")?,
            terminal_velocity_up: float("m_975F21A5")?,
            ground_gravity_accel: float("m_B99EC983")?,
            ground_gravity_decel: float("m_CEF4898C")?,
            character_collision: uint("m_D4A3C694")?,
        })
    }
}
