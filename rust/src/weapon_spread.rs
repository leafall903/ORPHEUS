//! Deterministic rotation of the 104319 authored concentric pellet rings.

use std::{f64::consts::TAU, sync::OnceLock};

use crate::static_ray::{Vec3, norm};

const TABLE_SIZE: usize = 16_384;
const TABLE_MASK: usize = TABLE_SIZE - 1;

fn uniform_table() -> &'static [u32] {
    static TABLE: OnceLock<Vec<u32>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut words = [1u32; 624];
        for index in 1..words.len() {
            let last = words[index - 1];
            words[index] = 1_812_433_253u32
                .wrapping_mul(last ^ (last >> 30))
                .wrapping_add(index as u32);
        }
        if (words[0] ^ (words[3] & 0x13c9_e684)).count_ones() % 2 == 0 {
            words[0] ^= 1;
        }
        let mut state = [0u128; 156];
        for (block, lanes) in state.iter_mut().zip(words.chunks_exact(4)) {
            *block = lanes.iter().enumerate().fold(0, |block, (lane, &word)| {
                block | (u128::from(word) << (lane * 32))
            });
        }
        let masks = [0xdfff_ffef, 0xddfe_cb7f, 0xbffa_ffff, 0xbfff_fff6];
        let mut table = Vec::with_capacity(TABLE_SIZE);
        while table.len() < TABLE_SIZE {
            let [mut previous, mut last] = [state[154], state[155]];
            for index in 0..state.len() {
                let current = state[index];
                let ahead = state[(index + 122) % state.len()];
                let (right, left) =
                    masks
                        .into_iter()
                        .enumerate()
                        .fold((0, 0), |(right, left), (lane, mask)| {
                            let shift = lane * 32;
                            let next_right = ((ahead >> shift) as u32 >> 11) & mask;
                            let next_left = ((last >> shift) as u32) << 18;
                            (
                                right | (u128::from(next_right) << shift),
                                left | (u128::from(next_left) << shift),
                            )
                        });
                let result = current ^ (current << 8) ^ (previous >> 8) ^ right ^ left;
                state[index] = result;
                previous = last;
                last = result;
            }
            let remaining = TABLE_SIZE - table.len();
            table.extend(
                state
                    .iter()
                    .rev()
                    .flat_map(|block| (0..4).rev().map(move |lane| (block >> (lane * 32)) as u32))
                    .take(remaining),
            );
        }
        table
    })
}

fn cross([ax, ay, az]: Vec3, [bx, by, bz]: Vec3) -> Vec3 {
    [ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx]
}

pub(crate) fn ring_directions<'a>(
    forward: Vec3,
    cone: f64,
    rings: &'a [(f64, usize)],
    shot: &crate::script_init::ShotEvent,
    node: u16,
) -> impl Iterator<Item = Vec3> + use<'a> {
    let table = uniform_table();
    let seed = (shot.entity as u16)
        .wrapping_add(shot.instance.wrapping_mul(23))
        .wrapping_add(node.wrapping_mul(7))
        .wrapping_add(shot.activation & 63);
    let key = (usize::from(seed) * 131 + shot.ordinal as usize * 23) & TABLE_MASK;
    let phase = f64::from(table[table[key] as usize & TABLE_MASK]) * TAU / 4_294_967_296.0;
    let right = cross(forward, [0.0, 1.0, 0.0]);
    let length = norm(right);
    let right = if length == 0.0 {
        [1.0, 0.0, 0.0]
    } else {
        right.map(|axis| axis / length)
    };
    let up = cross(right, forward);
    rings.iter().flat_map(move |&(radius, count)| {
        let (sin, cos) = (cone * radius).sin_cos();
        (0..count).map(move |pellet| {
            let (phase_sin, phase_cos) = (phase + TAU * pellet as f64 / count as f64).sin_cos();
            std::array::from_fn(|axis| {
                f64::from(
                    (forward[axis] * cos + (up[axis] * phase_cos + right[axis] * phase_sin) * sin)
                        as f32,
                )
            })
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::script_init::ShotEvent;
    use sha2::{Digest, Sha256};

    #[test]
    fn reaper_rings_match_the_reference_table_and_directions() {
        let mut digest = Sha256::new();
        for word in uniform_table() {
            digest.update(word.to_le_bytes());
        }
        assert_eq!(
            format!("{:x}", digest.finalize()),
            "4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6"
        );
        let shot = ShotEvent {
            entity: 0xc000_1000,
            instance: 10,
            graph: 0x0019,
            raw_state: 6,
            frame: 0,
            time_ms: 0,
            activation: 1,
            activation_ms: 0,
            ordinal: 0,
        };
        let directions: Vec<_> = ring_directions(
            [0.0, 0.0, 1.0],
            6.0_f64.to_radians(),
            &[(0.0, 1), (0.25, 5), (0.5, 6), (1.0, 8)],
            &shot,
            10,
        )
        .collect();
        assert_eq!(directions.len(), 20);
        for (index, expected) in [
            (0, [0, 0, 0x3f80_0000]),
            (1, [0x3ca8_4a87, 0xbc84_e73d, 0x3f7f_e98b]),
            (6, [0x3d28_3bc4, 0xbd04_db95, 0x3f7f_a62f]),
            (12, [0x3da8_00be, 0xbd84_acf8, 0x3f7e_98fd]),
            (19, [0x3c47_d74d, 0xbdd4_9ca2, 0x3f7e_98fd]),
        ] {
            assert_eq!(
                directions[index].map(|axis| (axis as f32).to_bits()),
                expected
            );
        }
    }
}
