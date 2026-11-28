//! Summarize only selected, successfully delivered owner records for the two observed bodies.
use super::*;
use crate::world::{ComponentValue, Operation, POOLS};
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;

pub(crate) fn body_script_receipts(
    receipt: &DeliveryReport<Arc<Record>>,
    body: BodyId,
) -> Vec<(u32, bool, u32)> {
    receipt
        .packets
        .iter()
        .flat_map(|packet| {
            packet
                .records
                .iter()
                .filter_map(move |record| match record.delivery {
                    Delivery::BodyScript { id, last, .. } if id == body => {
                        Some((packet.sequence, packet.arrived, last))
                    }
                    _ => None,
                })
        })
        .collect()
}

/// Count body-script packets actually received before the schedule. Possession
/// and script baselines have separate acknowledgements.
pub(crate) fn script_receipts(receipt: &DeliveryReport<Arc<Record>>, owner_body: u32) -> [u32; 2] {
    let mut counts = [0; 2];
    for packet in receipt.packets.iter().filter(|p| p.arrived) {
        for r in &packet.records {
            if let Delivery::BodyScript { id, delta, .. } = r.delivery {
                if id.entity == owner_body {
                    counts[usize::from(delta)] += 1;
                }
            }
        }
    }
    counts
}

pub(crate) fn selected(
    receipt: &DeliveryReport<Arc<Record>>,
    names: &BTreeMap<u32, String>,
) -> Vec<Vec<Json>> {
    let mut groups = Vec::new();
    for packet in &receipt.packets {
        assert!(
            packet.arrived,
            "the comparison schedule requires acknowledged deliveries"
        );
        let mut group = Vec::new();
        for r in &packet.records {
            let Some(actor) = names.get(&r.update.entity) else {
                continue;
            };
            if let Delivery::BodyScript {
                rifle_ammo_written: Some(ammo),
                ..
            } = r.delivery
            {
                group.push(json!(["owner", "ammo", actor, ammo]));
            }
            match &r.update.operation {
                Some(Operation::Create(_)) => group.push(json!(["owner", "create", actor, null])),
                Some(Operation::Destroy) => group.push(json!(["owner", "retire", actor, null])),
                Some(Operation::Update(parts)) if matches!(r.delivery, Delivery::Health { .. }) => {
                    for part in parts.iter().filter(|c| c.index == 51) {
                        // Match the reference's component-51 projection on this exact update.
                        let mut plain = [0.0_f64; 3];
                        let mut over = [0.0_f64; 3];
                        for (index, field) in part.fields.iter().enumerate() {
                            let Some(field) = field else { continue };
                            let ComponentValue::Blob20(data) = field else {
                                panic!("unexpected health field")
                            };
                            if u32::from_le_bytes(data[..4].try_into().unwrap()) == 0 {
                                continue;
                            }
                            let pools = if data[17] != 0 && data[18] == 0 {
                                &mut plain
                            } else {
                                &mut over
                            };
                            pools[index / POOLS] +=
                                f64::from(f32::from_le_bytes(data[8..12].try_into().unwrap()));
                        }
                        let current =
                            plain.into_iter().sum::<f64>() + over.into_iter().sum::<f64>();
                        assert!(current.is_finite());
                        group.push(json!([
                            "owner",
                            "health",
                            actor,
                            format!("{:016x}", current.to_bits())
                        ]));
                    }
                }
                _ => {}
            }
        }
        if !group.is_empty() {
            groups.push(group);
        }
    }
    groups
}

#[test]
fn only_identified_committed_records_produce_ordered_owner_effects() {
    use crate::game_egress::PacketDelivery;
    let record = |wire, operation| {
        Arc::new(Record::baseline(EntityUpdate {
            entity: wire,
            operation,
            ..EntityUpdate::default()
        }))
    };
    let receipts = DeliveryReport {
        requeued_reliable: 0,
        owner_correction: None,
        packets: vec![
            PacketDelivery {
                sequence: 1,
                arrived: true,
                records: vec![record(17, Some(Operation::Destroy))],
            },
            PacketDelivery {
                sequence: 2,
                arrived: true,
                records: vec![
                    record(19, Some(Operation::Destroy)),
                    record(19, None),
                    record(
                        19,
                        Some(Operation::Create(Box::new(crate::world::Create::new(
                            1,
                            vec![],
                        )))),
                    ),
                ],
            },
        ],
    };
    let labels = BTreeMap::from([(19, "bot/0/body/1".to_owned())]);
    assert_eq!(
        selected(&receipts, &labels),
        vec![vec![
            json!(["owner", "retire", "bot/0/body/1", null]),
            json!(["owner", "create", "bot/0/body/1", null])
        ]]
    );
}

#[test]
fn controller_owner_ack_waits_for_data_and_supersedes_unsent_acks() {
    let mut world = WorldDelivery::new(OWNER);
    world.started = Some(FIRST_TICK);
    world
        .controller
        .full_owner(FIRST_TICK, |_| Ok(crate::bits::BitWriter::new()))
        .unwrap();
    world.input.processed = Some(FIRST_TICK + 10);

    // Held back while initial controller data is still in flight.
    world
        .advance_controller_ack(Duration::from_millis(100))
        .unwrap();
    assert!(world.queued.is_empty());

    assert!(world.controller.arrived(1));
    world
        .advance_controller_ack(Duration::from_millis(150))
        .unwrap();
    assert!(world.queued.is_empty());

    world
        .advance_controller_ack(Duration::from_millis(200))
        .unwrap();
    assert_eq!(world.controller.command_frame(), FIRST_TICK + 10);
    assert_eq!(
        world
            .queued
            .iter()
            .filter_map(|record| match record.delivery {
                Delivery::Script {
                    last,
                    close: false,
                    resend: false,
                } if record.update.entity == OWNER => Some(last),
                _ => None,
            })
            .collect::<Vec<_>>(),
        [2]
    );

    world.input.processed = Some(FIRST_TICK + 20);
    world
        .advance_controller_ack(Duration::from_millis(300))
        .unwrap();
    assert_eq!(world.controller.command_frame(), FIRST_TICK + 20);
    assert_eq!(
        world
            .queued
            .iter()
            .filter_map(|record| match record.delivery {
                Delivery::Script {
                    last,
                    close: false,
                    resend: false,
                } if record.update.entity == OWNER => Some(last),
                _ => None,
            })
            .collect::<Vec<_>>(),
        [3]
    );
}
