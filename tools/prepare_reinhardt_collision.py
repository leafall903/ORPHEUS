"""Prepare selected authorized104319 barrier geometry outside the source tree."""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from ow174.game import props
from ow174.game.script import graph


def model_nodes(raw):
    payload = props.chunks(raw)["MSTU"]
    offset, size = struct.unpack_from("<qq", payload)
    return props.nodes(props.Structured(payload[offset:offset + size]))


def prepare(owner, shield):
    expected = ("813b7bbad2e34189307284c06bd28675", "8c0dd67b8f133f9396ea7d9bf9a98bf1")
    if tuple(hashlib.md5(blob).hexdigest() for blob in (owner, shield)) != expected:
        raise ValueError("models do not match the manifest-verified104319 owner/shield identities")
    shapes = props.model_shapes(shield, 0xFFFF, static_only=False)
    triangles = [tuple(axis for v in t[:3] for axis in v)
                 for shape in shapes for t in props.shape_triangles(shape)]
    owner_anchor = props.node_frame(model_nodes(owner), 0x29)[0]
    child_anchor = props.node_frame(model_nodes(shield), 0x8C)[0]
    definition = graph.graph(0x95)
    maximum = definition.node(2).field("m_value")["m_value"]
    regen = definition.state(4).field("m_9B0385B4")["m_expression"]["m_D99EF254"][0]
    delay = definition.state(5).field("m_timeout")["m_value"]
    # Default break cooldown is initialized from the authored graph's schema/entries.
    from ow174.game.script.driver import BodyScript
    script = BodyScript(0x02E0000000000007, 0xA0000101, 1000)
    instance = next(i for i in script.component.instances.values() if i.graph.index == 0x95)
    cooldown = instance.find_var(0, 472).value()
    return {
        "schema": "orpheus-reinhardt-barrier-v1", "build": 104319,
        "triangles": triangles, "owner_anchor": owner_anchor, "child_anchor": child_anchor,
        "forward_offset": definition.state(17).field("m_position")["m_expression"]["m_D99EF254"][0],
        "maximum": maximum, "regen_rate": regen, "regen_delay": delay,
        "break_delay": cooldown,
        "fingerprint": hashlib.sha256(owner + shield).hexdigest(),
        "pose_policy": ("reconstructed aim alignment at bind hardpoint29 minus shieldbone8C; "
                        "clear owner capsule"),
        "resource_evidence": ("0095:587 maximum,ChaseVar4 rate,Wait5 regen delay,"
                              "472 break cooldown;Pet17 offset0.2"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", type=Path, required=True)
    parser.add_argument("--shield", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    source = Path(__file__).resolve().parents[1]
    if output.is_relative_to(source):
        parser.error("prepared game geometry must remain outside source")
    data = prepare(args.owner.read_bytes(), args.shield.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data), encoding="utf-8")
    print(f"prepared{len(data['triangles'])} triangles; "
          f"maximum{data['maximum']}, regen{data['regen_rate']}/s")


if __name__ == "__main__":
    main()
