"""Extract lobby and game-link JAM schemas offline from a local 104319 Overwatch.exe PE image."""

import collections
import json
from pathlib import Path
import struct

# Fixed protocol CRC table for the 104319 game-server UDP link.
GAME_LINK_CRCS: tuple[int, ...] = (
    0xC77F6403,
    0xA7FBBD0C,
    0x8F89E0DF,
    0x82A2F991,
    0x9F0A9F88,
    0xA9385A52,
    0x398E145E,
    0x741EC7B2,
    0x716AD5D9,
    0x145CE2DA,
    0xCF043764,
)

# Announced 104319 lobby protocol CRCs (pure 32-bit protocol identifiers).
LOBBY_CRCS: tuple[int, ...] = (
    0x00A8654E, 0x074DAD18, 0x0BFB00A8, 0x0DFEEFEF, 0x11D82194, 0x158BBD52,
    0x1732EBF2, 0x17CCBFB2, 0x1A264832, 0x1A9879A4, 0x1C6EC712, 0x1CA9B786,
    0x1CFB43CD, 0x1D80BD86, 0x1EC25650, 0x1F8A43DD, 0x21286B49, 0x2372A2E8,
    0x2411DE56, 0x2558BD8D, 0x262EF305, 0x267FDE9E, 0x273FF626, 0x28A2A1CD,
    0x34BB385D, 0x3C7E3468, 0x4116E22C, 0x46DC9706, 0x48151833, 0x49963FA6,
    0x4BAD7A7E, 0x5217E4CD, 0x5635CB1C, 0x5894D085, 0x5F8C3ADD, 0x5F913F6B,
    0x6099BC5F, 0x692C511B, 0x70065EF5, 0x70E5A247, 0x713D7589, 0x75D32AE2,
    0x779A8581, 0x783EE77A, 0x79BFDDBE, 0x7F4F46CB, 0x8133E1F5, 0x872E4FB9,
    0x888716D3, 0x8B3DC153, 0x8B77C823, 0x928CFB44, 0x9529F0ED, 0x981D6A52,
    0xA1498A6A, 0xA287DF29, 0xA59B4EEF, 0xA6E53896, 0xA743DF58, 0xA830FDA2,
    0xAA91BE18, 0xAD1C34BA, 0xAFBE5037, 0xB2FF5A5E, 0xB4F8BC62, 0xB68870B8,
    0xB689E024, 0xB9DCA497, 0xBAA07D0C, 0xBCD57A46, 0xBDDBF58A, 0xC64B397E,
    0xC81200AF, 0xCAE25D1B, 0xCBF59220, 0xCEA98618, 0xD5550262, 0xDDA583EF,
    0xDE3CBAE7, 0xE54AAACD, 0xEB45AD29, 0xEB59E676, 0xF470E2AB, 0xF5FF548A,
    0xFB2E5CAC, 0xFDEC4D9E, 0xFFEB57B9,
)


def map_pe_image(exe_path: Path) -> tuple[int, bytes, list[tuple[int, int]]]:
    """Map a PE64 executable's sections into a virtual-address byte buffer."""
    raw = exe_path.read_bytes()
    if len(raw) < 0x100 or raw[:2] != b"MZ":
        raise ValueError(f"Not a valid PE executable: {exe_path}")
    pe_off = struct.unpack_from("<I", raw, 0x3C)[0]
    if pe_off + 0x108 > len(raw) or raw[pe_off : pe_off + 4] != b"PE\x00\x00":
        raise ValueError(f"Missing PE header in {exe_path}")
    num_sections = struct.unpack_from("<H", raw, pe_off + 6)[0]
    opt_size = struct.unpack_from("<H", raw, pe_off + 20)[0]
    magic = struct.unpack_from("<H", raw, pe_off + 24)[0]
    if magic != 0x20B:
        raise ValueError(f"Expected PE32+ (x64) executable: {exe_path}")
    image_base = struct.unpack_from("<Q", raw, pe_off + 24 + 24)[0]
    image_size = struct.unpack_from("<I", raw, pe_off + 24 + 56)[0]
    sec_off = pe_off + 24 + opt_size
    image = bytearray(image_size)
    hdr_copy = min(0x1000, len(raw), image_size)
    image[:hdr_copy] = raw[:hdr_copy]
    scan_ranges: list[tuple[int, int]] = []
    for i in range(num_sections):
        entry = sec_off + i * 40
        if entry + 40 > len(raw):
            break
        name, vsz, rva, rsz, rptr = struct.unpack_from("<8sIIII", raw, entry)
        copy_len = min(vsz, rsz, max(0, image_size - rva), max(0, len(raw) - rptr))
        if copy_len > 0:
            image[rva : rva + copy_len] = raw[rptr : rptr + copy_len]
            sname = name.rstrip(b"\x00")
            if sname in (b".data", b".rdata"):
                scan_ranges.append((rva, rva + copy_len))
    return image_base, bytes(image), scan_ranges


class _PeSchemaReader:
    def __init__(self, base: int, image: bytes, scan_ranges: list[tuple[int, int]]):
        self.base = base
        self.image = image
        self.scan_ranges = scan_ranges or [(0, len(image))]

    def _q(self, rva: int) -> int:
        return struct.unpack_from("<Q", self.image, rva)[0]

    def _rva(self, ptr: int) -> int | None:
        return ptr - self.base if self.base <= ptr < self.base + len(self.image) else None

    def _valid(self, items_rva: int, depth: int = 0) -> bool:
        r = items_rva
        for _ in range(300):
            if r + 0x30 > len(self.image):
                return False
            typ, off, size, cnt = struct.unpack_from("<IIIi", self.image, r + 8)
            if typ == 17:
                return True
            if typ > 17 or off > 0x10000 or size > 0x10000 or cnt < -1 or cnt > 0x1000:
                return False
            if typ == 14:
                inner = self._rva(self._q(r + 0x18))
                if inner is None or depth > 16 or not self._valid(inner, depth + 1):
                    return False
            r += 0x30
        return False

    def _fields(self, items_rva: int, depth: int = 0) -> list[dict]:
        out = []
        r = items_rva
        for _ in range(300):
            typ, off, size, cnt = struct.unpack_from("<IIIi", self.image, r + 8)
            if typ == 17:
                break
            field = {
                "type": typ,
                "off": off,
                "size": size,
                "count": cnt,
                "array": bool(self._q(r + 0x20)),
            }
            inner = self._rva(self._q(r + 0x18))
            if typ == 14 and inner is not None and depth < 16:
                field["fields"] = self._fields(inner, depth + 1)
            out.append(field)
            r += 0x30
        return out

    def extract(self, crcs: set[int]) -> dict[str, dict[str, list[dict]]]:
        infos: dict[tuple[int, int], int] = {}
        for start, end in self.scan_ranges:
            lo = max(0x18, (start + 7) & ~7)
            hi = min(len(self.image) - 8, end - 8)
            for off in range(lo, hi, 8):
                crc, msg_id = struct.unpack_from("<II", self.image, off)
                if crc in crcs and 0 < msg_id < 70000:
                    items = self._rva(self._q(off - 8))
                    if items is not None and self._valid(items):
                        infos.setdefault((crc, msg_id), items)
        groups: dict[int, dict[int, list[dict]]] = collections.defaultdict(dict)
        for (crc, msg_id), items in infos.items():
            groups[crc][msg_id] = self._fields(items)
        return {
            f"{crc:08X}": {str(mid): fields for mid, fields in sorted(msgs.items())}
            for crc, msgs in sorted(groups.items())
        }


def locate_client_executable(game_dir: Path) -> Path:
    for candidate in (game_dir / "_retail_/Overwatch.exe", game_dir / "Overwatch.exe"):
        if candidate.is_file():
            return candidate
    raise ValueError(f"Expected Overwatch.exe inside {game_dir}")


def extract_pe_schemas(exe_path: Path) -> tuple[dict, dict]:
    """Return (lobby_schemas, game_schemas) extracted offline from Overwatch.exe."""
    base, image, ranges = map_pe_image(exe_path)
    reader = _PeSchemaReader(base, image, ranges)
    lobby = reader.extract(set(LOBBY_CRCS))
    game = reader.extract(set(GAME_LINK_CRCS))
    missing_lobby = set(LOBBY_CRCS) - {int(c, 16) for c in lobby}
    missing_game = set(GAME_LINK_CRCS) - {int(c, 16) for c in game}
    if missing_lobby or missing_game:
        raise ValueError(
            f"Incomplete JAM schema extraction from {exe_path.name}: "
            f"missing {len(missing_lobby)} lobby and {len(missing_game)} game-link protocols"
        )
    return lobby, game


def write_pe_schemas(game_dir: Path, out_dir: Path) -> tuple[int, int]:
    exe = locate_client_executable(game_dir)
    lobby, game = extract_pe_schemas(exe)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "schemas_174.json").write_text(json.dumps(lobby), encoding="utf-8")
    (out_dir / "game_schemas_174.json").write_text(json.dumps(game), encoding="utf-8")
    return len(lobby), len(game)
