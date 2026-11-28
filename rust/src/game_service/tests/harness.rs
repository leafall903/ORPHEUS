use super::*;

pub const MATCH: MatchId = [5, 8];

pub fn local() -> SocketAddr {
    "127.0.0.1:0".parse().unwrap()
}

pub fn player() -> Vec<MatchPlayer> {
    vec![MatchPlayer {
        name: "Protocol fixture".into(),
        team: 0,
        tournament: false,
        ranked: false,
    }]
}

pub fn fixture() -> Schemas {
    Schemas::from_json(
        r#"{
            "C77F6403": {
                "20300": [{"type":8,"off":1192,"size":8,"count":0,"array":false}],
                "20301": [], "20302": [], "20304": [],
                "20306": [
                    {"type":6,"off":120,"size":4,"count":0,"array":false},
                    {"type":8,"off":128,"size":8,"count":-1,"array":true}
                ]
            },
            "A7FBBD0C": {
                "21601": [], "21602": [],
                "21610": [
                    {"type":6,"off":120,"size":4,"count":0,"array":false},
                    {"type":8,"off":128,"size":8,"count":-1,"array":true}
                ]
            }
        }"#,
    )
    .unwrap()
}

pub struct Client {
    pub socket: ClientSocket,
    pub cipher: LinkCipher,
    pub connection: u32,
    pub next: u32,
    pub window: ReceiveWindow,
}

impl Client {
    pub fn new(address: SocketAddr, ticket: &Handoff) -> Self {
        let socket = ClientSocket::bind(local()).unwrap();
        socket.connect(address).unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        Self {
            socket,
            cipher: LinkCipher::new(ticket.key_out, ticket.key_in),
            connection: ticket.conn,
            next: 0,
            window: ReceiveWindow::default(),
        }
    }

    pub fn send(&mut self, flags: u8, payload: Vec<u8>) {
        let packet = Packet {
            conn: self.connection,
            kind: if payload.is_empty() {
                KIND_CONTROL
            } else {
                KIND_FRAME
            },
            seq: self.next,
            ack: self.window.expected,
            ack_bits: self.window.bits,
            flags,
            payload,
        };
        self.next += 1;
        self.socket
            .send(&self.cipher.seal(&packet).unwrap())
            .unwrap();
    }

    pub fn receive(&self) -> Packet {
        let mut bytes = [0; MAX_DATAGRAM];
        let count = self.socket.recv(&mut bytes).unwrap();
        let packet = self.cipher.open(&bytes[..count]).unwrap();
        assert_eq!(packet.conn, self.connection);
        packet
    }

    pub fn handshake(&mut self) {
        self.send(SYN, Vec::new());
        let response = self.receive();
        assert_eq!((response.kind, response.flags), (KIND_CONTROL, SYN | ACK));
        assert!(self.window.accept(response.seq));
        self.send(ACK, Vec::new());
    }
}

pub fn messages(schemas: &Schemas, packet: &Packet) -> Vec<crate::game_messages::ReliableMessage> {
    assert_eq!(packet.kind, KIND_FRAME);
    let mut reader = BitReader::new(&packet.payload);
    reader.var_a().unwrap();
    read_channel(&mut reader, schemas).unwrap().reliable
}

pub fn exercise_loading(schemas: Schemas) {
    let schemas = Arc::new(schemas);
    let mut server = GameService::bind(local(), Arc::clone(&schemas)).unwrap();
    let mut tickets = server
        .open_match(MATCH, GameMap::practice_range(), player())
        .unwrap();
    let ticket = tickets.pop().unwrap();
    assert!(server.handoff_pending(ticket.conn).unwrap());
    let mut client = Client::new(server.local_addr(), &ticket);
    client.handshake();
    assert!(!server.handoff_pending(ticket.conn).unwrap());

    let first = client.receive();
    let initial = messages(&schemas, &first);
    assert_eq!((initial[0].sequence, initial[0].message.id), (0, 20300));
    assert_eq!(
        initial[0].message.fields[&0x4a8],
        Value::Unsigned(PRACTICE_RANGE_MAP)
    );
    // Drop the first instance datagram, then ACK the following heartbeat.
    let heartbeat = client.receive();
    assert!(messages(&schemas, &heartbeat).is_empty());
    assert!(client.window.accept(heartbeat.seq));
    client.send(ACK, Vec::new());
    let until = Instant::now() + Duration::from_secs(2);
    let retry = loop {
        let packet = client.receive();
        if !messages(&schemas, &packet).is_empty() {
            break packet;
        }
        assert!(Instant::now() < until, "waiting for retransmitted instance");
    };
    assert_eq!(messages(&schemas, &retry), initial);
    assert!(client.window.accept(retry.seq));

    let loaded = encode(&schemas, 21602, &Fields::new()).unwrap();
    let ping = encode(
        &schemas,
        21610,
        &Fields::from([
            (0x78, Value::Unsigned(17)),
            (0x80, Value::Array(vec![Value::Unsigned(25)])),
        ]),
    )
    .unwrap();
    let mut payload = BitWriter::new();
    write_channel(&mut payload, &[(0, loaded)], &[ping]).unwrap();
    let start = Instant::now();
    client.send(ACK, payload.into_bytes());
    let mut received = Vec::new();
    while received.len() < 3 && start.elapsed() < Duration::from_secs(2) {
        let packet = client.receive();
        assert!(client.window.accept(packet.seq));
        received.extend(messages(&schemas, &packet));
        client.send(ACK, Vec::new());
    }
    assert_eq!(
        received
            .iter()
            .map(|message| (message.sequence, message.message.id))
            .collect::<Vec<_>>(),
        [(1, 20306), (2, 20302), (3, 20301)]
    );
    assert!(start.elapsed() >= Duration::from_millis(400));
    assert_eq!(received[0].message.fields[&0x78], Value::Unsigned(17));
    let Value::Array(stamps) = &received[0].message.fields[&0x80] else {
        panic!("expected ping timestamps");
    };
    assert_eq!(stamps.len(), 3);
    assert_eq!(stamps[0], Value::Unsigned(25));

    client.send(FIN | ACK, Vec::new());
    let until = Instant::now() + Duration::from_secs(2);
    loop {
        let packet = client.receive();
        if packet.kind == KIND_CONTROL {
            assert_eq!(packet.flags, FIN | ACK);
            break;
        }
        assert!(Instant::now() < until, "waiting for FIN acknowledgement");
    }
    let address = server.local_addr();
    server.stop().unwrap();
    assert!(ClientSocket::bind(address).is_ok());
    assert_eq!(
        server
            .open_match(MATCH, GameMap::practice_range(), player())
            .err()
            .unwrap()
            .kind(),
        io::ErrorKind::BrokenPipe
    );
}

#[derive(Debug)]
pub struct WorldRecord {
    pub entity: u32,
    pub operation: Option<u8>,
    pub definition: Option<u32>,
    pub create_position: Option<[f32; 3]>,
    pub create_filter: Option<u64>,
    pub movement: bool,
    pub movement_frame: Option<u32>,
    pub skin: Option<(u32, bool)>,
    pub script: Option<ReceivedChunk>,
    pub has_projectile_launch: bool,
    pub launched_projectiles: Vec<u32>,
    pub projectile_release: Option<u32>,
    pub base_health: Option<f32>,
    pub total_health: Option<f32>,
    pub possessed_body: Option<u32>,
}

#[derive(Eq, PartialEq)]
pub struct ReceivedChunk {
    pub first: u32,
    pub span: u32,
    pub width: usize,
    pub data: Vec<u8>,
    pub wire_width: usize,
    pub wire: Vec<u8>,
}

impl std::fmt::Debug for ReceivedChunk {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ReceivedChunk")
            .field("first", &self.first)
            .field("span", &self.span)
            .field("width", &self.width)
            .field("wire_width", &self.wire_width)
            .finish_non_exhaustive()
    }
}

#[test]
fn received_chunk_debug_redacts_bytes_without_changing_equality() {
    let chunk = ReceivedChunk {
        first: 2,
        span: 3,
        width: 8,
        data: vec![222],
        wire_width: 16,
        wire: vec![173, 239],
    };
    assert_eq!(
        format!("{chunk:?}"),
        "ReceivedChunk { first: 2, span: 3, width: 8, wire_width: 16, .. }"
    );
    let make = |data, wire| ReceivedChunk {
        first: 2,
        span: 3,
        width: 8,
        data: vec![data],
        wire_width: 16,
        wire: vec![wire, 239],
    };
    assert_eq!(chunk, make(222, 173));
    assert_ne!(chunk, make(221, 173));
    assert_ne!(chunk, make(222, 172));
}

impl ReceivedChunk {
    pub fn bits(&self) -> BitReader<'_> {
        BitReader::with_range(&self.data, 0, self.width).unwrap()
    }
}

impl WorldRecord {
    pub fn is_create(&self) -> bool {
        self.operation == Some(1)
    }
}

fn read_projectile_launch(reader: &mut BitReader<'_>, entities: &mut Vec<u32>) -> bool {
    if !reader.bit().unwrap() {
        return false;
    }
    let mut first = true;
    while reader.bit().unwrap() {
        if first {
            reader.var_b().unwrap();
            first = false;
        } else {
            let width = if !reader.bit().unwrap() {
                4
            } else if !reader.bit().unwrap() {
                8
            } else {
                32
            };
            reader.advance(width).unwrap();
        }
        read_projectile_escape(reader, 4, 8);
        assert_eq!(reader.bits(2).unwrap(), 0); // no spread, volley form
        reader.w_u16().unwrap(); // instance
        reader.w_u16().unwrap(); // state
        reader.advance(6).unwrap(); // activation
        if !reader.bit().unwrap() {
            reader.w_u16().unwrap(); // volley count
        }
        reader.w_u16().unwrap(); // counter
        reader.advance(96 + 32).unwrap(); // origin and direction
        if reader.bit().unwrap() {
            let count = reader.w_u16().unwrap();
            assert_ne!(count, 0);
            let namespace = reader.bits(2).unwrap() as u32;
            let mut entity =
                0x8000_0000 | (namespace << 29) | read_projectile_escape(reader, 8, 16);
            entities.push(entity);
            for _ in 1..count {
                entity = if reader.bit().unwrap() {
                    entity.checked_add(1)
                } else {
                    let negative = reader.bit().unwrap();
                    let step = read_projectile_escape(reader, 4, 8);
                    if negative {
                        entity.checked_sub(step)
                    } else {
                        entity.checked_add(step)
                    }
                }
                .unwrap();
                entities.push(entity);
            }
        }
    }
    !first
}

fn read_projectile_escape(reader: &mut BitReader<'_>, short: usize, middle: usize) -> u32 {
    if !reader.bit().unwrap() {
        return reader.bits(short).unwrap() as u32;
    }
    let value = reader.bits(middle).unwrap() as u32;
    if value == (1 << middle) - 1 {
        reader.bits(32).unwrap() as u32
    } else {
        value
    }
}

pub fn world_records(schemas: &Schemas, packet: &Packet) -> Vec<WorldRecord> {
    let mut reader = BitReader::new(&packet.payload);
    let tick = reader.var_a().unwrap();
    read_channel(&mut reader, schemas).unwrap();
    reader.var_a().unwrap();
    assert_eq!(reader.bits(7).unwrap(), 0b010_0000);
    let count = reader.bits(7).unwrap();
    (0..count)
        .map(|_| {
            let mut record = WorldRecord {
                entity: reader.entity_id().unwrap().unwrap(),
                operation: None,
                definition: None,
                create_position: None,
                create_filter: None,
                movement: false,
                movement_frame: None,
                skin: None,
                script: None,
                has_projectile_launch: false,
                launched_projectiles: Vec::new(),
                projectile_release: None,
                base_health: None,
                total_health: None,
                possessed_body: None,
            };
            assert!(reader.bit().unwrap());
            if !reader.bit().unwrap() {
                return record;
            }
            if reader.bit().unwrap() {
                assert!(!reader.bit().unwrap());
                let mut wire = reader;
                let start = reader.position();
                let first = reader.w_u32().unwrap();
                let span = reader.w_var().unwrap();
                assert!(!reader.bit().unwrap());
                let width = reader.w_var().unwrap() as usize;
                let wire_width = reader.position() - start + width;
                record.script = Some(ReceivedChunk {
                    first,
                    span,
                    width,
                    data: reader.raw_bits(width).unwrap(),
                    wire_width,
                    wire: wire.raw_bits(wire_width).unwrap(),
                });
            }
            if !reader.bit().unwrap() {
                return record;
            }
            record.has_projectile_launch =
                read_projectile_launch(&mut reader, &mut record.launched_projectiles);
            if reader.bit().unwrap() {
                assert_eq!(reader.var_b().unwrap(), u32::MAX);
                record.projectile_release = Some(reader.var_b().unwrap());
                assert_eq!(reader.bits(2).unwrap(), 0);
                // A release may carry the authoritative impact position or a
                // hidden sentinel for predicted projectiles.
                assert!((0..3).all(|_| reader.f32().unwrap().is_finite()));
                assert_eq!(reader.bits(5).unwrap(), 0);
                assert!(reader.bit().unwrap()); // impact count is present
                let count_width = if reader.bit().unwrap() {
                    4
                } else if reader.bit().unwrap() {
                    8
                } else {
                    32
                };
                assert!(reader.bits(count_width).unwrap() > 0);
                assert_eq!(reader.bits(6).unwrap(), 0);
            }
            assert!(reader.bit().unwrap());
            record.movement = reader.bit().unwrap();
            if record.movement {
                let length = reader.bits(12).unwrap() as usize;
                let mut movement = reader;
                assert!(!movement.bit().unwrap()); // movement header
                if movement.bit().unwrap() {
                    for _ in 0..4 {
                        if movement.bit().unwrap() {
                            movement.advance(8).unwrap();
                        }
                    }
                }
                assert!(!movement.bit().unwrap()); // fixed-precision grid
                let frame_back = if movement.bit().unwrap() {
                    let width = if movement.bit().unwrap() {
                        4
                    } else if movement.bit().unwrap() {
                        10
                    } else {
                        32
                    };
                    movement.bits(width).unwrap() as u32
                } else {
                    0
                };
                record.movement_frame = Some(tick.checked_sub(frame_back).unwrap());
                reader.advance(length - 12).unwrap();
            }
            if reader.bit().unwrap() {
                let operation = reader.bits(2).unwrap() as u8;
                record.operation = Some(operation);
                if operation != 3 {
                    let length = reader.bits(14).unwrap() as usize;
                    let start = reader.position();
                    if operation == 1 && !reader.bit().unwrap() {
                        record.definition = Some(reader.guid_index().unwrap());
                        let flags = reader.bits(8).unwrap() as u8;
                        if flags & crate::world::SKIN_THEME != 0 {
                            assert_ne!(flags & crate::world::WITH_SKIN, 0);
                            record.skin =
                                Some((reader.bits(32).unwrap() as u32, reader.bit().unwrap()));
                        }
                        if flags & crate::world::WITH_TRANSFORM != 0 {
                            record.create_position =
                                Some(std::array::from_fn(|_| reader.f32().unwrap()));
                            reader.advance(4 * 32).unwrap(); // rotation
                        }
                        if flags & (crate::world::WITH_TRANSFORM | crate::world::WITH_SCALE) != 0 {
                            reader.advance(3 * 32).unwrap();
                        }
                        if flags & crate::world::WITH_MOVEMENT == 0 {
                            let mut data = reader;
                            if data.bits(8).unwrap() != 0 && data.bits(8).unwrap() == 26 {
                                data.align();
                                assert_eq!(data.bits(8).unwrap(), 1);
                                assert_eq!(data.bits(8).unwrap(), 1);
                                let present = data.bits(8).unwrap();
                                let mut filter = 0;
                                for byte in 0..8 {
                                    if present & (1 << byte) != 0 {
                                        filter |= data.bits(8).unwrap() << (byte * 8);
                                    }
                                }
                                record.create_filter = Some(filter);
                            }
                        }
                    } else if operation == 2 {
                        let mut components = reader;
                        let count = components.bits(8).unwrap();
                        let index = components.bits(8).unwrap();
                        if count == 1 && index == 51 {
                            components.align();
                            assert_eq!(components.bits(8).unwrap(), 7);
                            let mut fields = components.bits(56).unwrap();
                            let mut total = 0.0;
                            while fields != 0 {
                                let field = fields.trailing_zeros();
                                fields &= fields - 1;
                                components.advance(64).unwrap(); // ID and maximum
                                let current = components.f32().unwrap();
                                components.advance(64).unwrap(); // pool kind and padding
                                if field == 0 {
                                    record.base_health = Some(current);
                                }
                                total += current;
                            }
                            record.total_health = Some(total);
                        } else if index == 44 {
                            components.align();
                            if components.bits(8).unwrap() == 1
                                && (components.bits(8).unwrap() & 1) != 0
                            {
                                record.possessed_body = Some(components.bits(32).unwrap() as u32);
                            }
                        }
                    }
                    reader
                        .advance(length - (reader.position() - start))
                        .unwrap();
                }
                assert!(!reader.bit().unwrap());
            }
            record
        })
        .collect()
}

pub fn input_batch(first: u32, count: u8, yaw: i16) -> Vec<u8> {
    command_batch(first, count, yaw, 0, true)
}

pub fn command_batch(first: u32, count: u8, yaw: i16, buttons: u32, forward: bool) -> Vec<u8> {
    command_batch_with_pitch(first, count, yaw, 0, buttons, forward)
}

pub fn command_batch_with_pitch(
    first: u32,
    count: u8,
    yaw: i16,
    pitch: i16,
    buttons: u32,
    forward: bool,
) -> Vec<u8> {
    let mut input = BitWriter::new();
    input.bits(0, 24).unwrap(); // latency and key flags
    input.bits(u64::from(count), 8).unwrap();
    input.bits(u64::from(first), 32).unwrap();
    input.bit(forward); // new throttles
    if forward {
        input.bit(true); // compact throttle direction
        input.bit(true); // moving
        input.bits(2, 3).unwrap();
    }
    input.signed(i64::from(yaw), 16).unwrap();
    input.signed(i64::from(pitch), 16).unwrap();
    input.bits(u64::from(buttons), 24).unwrap();
    input.bits(0, 8).unwrap(); // action
    input.bits(0, 16).unwrap(); // extra
    for _ in 1..count {
        input.bit(false); // next consecutive frame
        input.bit(false); // no changed fields
    }
    let mut payload = BitWriter::new();
    write_channel(&mut payload, &[], &[]).unwrap();
    payload.bit(true);
    payload.bits(input.bit_len() as u64, 32).unwrap();
    payload.raw_bits(input.as_bytes(), input.bit_len()).unwrap();
    payload.into_bytes()
}

pub fn input_ack(schemas: &Schemas, packet: &Packet) -> u32 {
    let mut reader = BitReader::new(&packet.payload);
    reader.var_a().unwrap();
    read_channel(&mut reader, schemas).unwrap();
    reader.var_a().unwrap()
}
