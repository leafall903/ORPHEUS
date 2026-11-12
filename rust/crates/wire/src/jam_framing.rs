//! Lobby stream framing. Each payload starts with a protocol index and message offset.

use std::io::{self, Write};

use crate::jam_cipher::Jam;

pub const CONTROL_WIRE: u8 = 0;
pub const ANNOUNCE: u8 = 0;
pub const PING: u8 = 3;
pub const CONNECTED: [u8; 2] = [CONTROL_WIRE, 2];
pub const PONG: [u8; 2] = [CONTROL_WIRE, PING];
const LENGTH_BYTES: usize = 3;
const MAX_PAYLOAD_LEN: usize = 0xff_ffff;

/// Write a complete encrypted frame to a lobby stream.
pub fn send_frame(writer: &mut impl Write, cipher: &mut Jam, payload: &[u8]) -> io::Result<()> {
    if payload.len() > MAX_PAYLOAD_LEN {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "JAM frame exceeds u24 length",
        ));
    }

    let mut frame = Vec::with_capacity(LENGTH_BYTES + payload.len());
    frame.extend_from_slice(&(payload.len() as u32).to_be_bytes()[1..]);
    frame.extend_from_slice(payload);
    cipher.crypt_in_place(&mut frame);
    writer.write_all(&frame)
}

/// Read the announced protocol CRCs in wire order, starting at wire index one.
pub fn parse_announcement(frame: &[u8]) -> io::Result<Vec<u32>> {
    let truncated = || io::Error::new(io::ErrorKind::UnexpectedEof, "truncated JAM announcement");
    let count = frame.get(2..6).ok_or_else(truncated)?;
    let count = u32::from_le_bytes(count.try_into().unwrap()) as usize;
    let groups = &frame[6..];
    if count > groups.len() / 4 {
        return Err(truncated());
    }
    Ok(groups
        .chunks_exact(4)
        .take(count)
        .map(|group| u32::from_le_bytes(group.try_into().unwrap()))
        .collect())
}

/// Decrypt and split the lobby byte stream across arbitrary receive boundaries.
pub struct FrameReader {
    cipher: Jam,
    buffer: Vec<u8>,
}

impl FrameReader {
    pub fn new(cipher: Jam) -> Self {
        Self {
            cipher,
            buffer: Vec::new(),
        }
    }

    pub fn feed(&mut self, data: &[u8]) -> Vec<Vec<u8>> {
        let buffered = self.buffer.len();
        self.buffer.extend_from_slice(data);
        self.cipher.crypt_in_place(&mut self.buffer[buffered..]);

        let mut frames = Vec::new();
        let mut consumed = 0;
        while let Some(header) = self.buffer.get(consumed..consumed + LENGTH_BYTES) {
            let size = u32::from_be_bytes([0, header[0], header[1], header[2]]) as usize;
            let start = consumed + LENGTH_BYTES;
            let Some(frame) = self.buffer.get(start..start + size) else {
                break;
            };
            frames.push(frame.to_vec());
            consumed = start + size;
        }
        self.buffer.drain(..consumed);
        frames
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_framing_contract_survives_every_fragmentation() {
        let contract: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/jam-framing-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "jam-framing.synthetic-stream.v1");
        assert_eq!(contract["target_build"], 104319);
        assert_eq!(contract["evidence"], "synthetic-reference");
        let key: [u8; 32] = hex_bytes(&contract["input"]["key_hex"]).try_into().unwrap();
        let payloads: Vec<_> = contract["input"]["payloads_hex"]
            .as_array()
            .unwrap()
            .iter()
            .map(hex_bytes)
            .collect();
        let expected = hex_bytes(&contract["expected"]["wire_hex"]);
        let expected_payloads: Vec<_> = contract["expected"]["payloads_hex"]
            .as_array()
            .unwrap()
            .iter()
            .map(hex_bytes)
            .collect();
        let mut wire = Vec::new();
        let mut cipher = Jam::new(&key);
        for payload in &payloads {
            send_frame(&mut wire, &mut cipher, payload).unwrap();
        }
        assert_eq!(wire, expected);

        for split in 0..=wire.len() {
            let mut reader = FrameReader::new(Jam::new(&key));
            let mut frames = reader.feed(&wire[..split]);
            frames.extend(reader.feed(&wire[split..]));
            assert_eq!(frames, expected_payloads, "stream split at {split}");
        }
        let mut reader = FrameReader::new(Jam::new(&key));
        let frames: Vec<_> = wire
            .chunks(1)
            .flat_map(|chunk| reader.feed(chunk))
            .collect();
        assert_eq!(frames, expected_payloads);
    }

    fn hex_bytes(value: &serde_json::Value) -> Vec<u8> {
        let text = value.as_str().unwrap();
        assert!(text.is_ascii() && text.len() % 2 == 0);
        (0..text.len())
            .step_by(2)
            .map(|offset| u8::from_str_radix(&text[offset..offset + 2], 16).unwrap())
            .collect()
    }

    #[test]
    fn announcement_rejects_incomplete_count_and_groups() {
        let bytes = [0, 0, 2, 0, 0, 0, 0xef, 0xcd, 0xab, 0x89, 4, 3, 2, 1, 0xff];
        assert_eq!(
            parse_announcement(&bytes).unwrap(),
            [0x89ab_cdef, 0x0102_0304]
        );
        for length in 0..14 {
            assert_eq!(
                parse_announcement(&bytes[..length]).unwrap_err().kind(),
                io::ErrorKind::UnexpectedEof
            );
        }
        let overflow = [0, 0, 0xff, 0xff, 0xff, 0xff];
        assert!(parse_announcement(&overflow).is_err());
    }
}
