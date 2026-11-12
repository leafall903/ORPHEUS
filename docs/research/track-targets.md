# TrackTargets: the target search behind Guardian Angel, the staff and Hack

Build 1.74.0.0.104319. Read from data and from code; see the [index](README.md) for the evidence
labels and the address convention. Work in progress: the sections under Open are not decoded yet.

## Why

`STUStatescriptStateTrackTargets` answers "which entities am I aiming at". The communication wheel
uses it for the aimed teammate, and [its notes](../COMMUNICATION_WHEEL.txt) decode most of the
native search for the wheel's two nodes: the composite score, the two body rays and their order,
the `.097` collision profiles. Mercy's Guardian Angel and staff and Sombra's Hack use the same
state with other options. The server answered only the wheel's nodes and one hostile
configuration, so those abilities found no target.

## The nodes compared

Read from data. Only the fields that differ are listed; the wheel's two nodes are the decoded
baseline.

| Field | Wheel `05AD` 113, 115 | Guardian Angel `006E` st5 | Staff `006B` st4, st51 | Staff `006B` st5 | Hack `0B1B` st25 | Hack `0B1B` st26 |
| --- | --- | --- | --- | --- | --- | --- |
| `m_8DCA77B6` source entity | the owner | empty | empty | empty | empty | empty |
| `m_05BE62DD` source point | Vec3 (0, 1, 0) | entity position (`STU_21A13145`) | same | same | same | same |
| `m_radius` | 40 | `#1648` | `#1648` | `#1648` | `#1648` | `#1648` + 1 |
| `m_A30B383E` aperture | 15 | expression: `#9582`, 30 | st4 expression: 360, 40, `#9583`; st51 40 | 360 | expression: 45, 30, `#23689`, `#9584` | 90 |
| `m_D45E22FA` yaw | empty | owner's heading (`STU_C8C4C20D`) | same | empty | same | empty |
| `m_E46A19A3` pitch | empty | empty | expression on `STU_40AC5AE4` | empty | empty | empty |
| `m_3D667CE3` profile | `0111.097`, `0079.097` | `002C.097` | `002C.097` | `002C.097` | empty | empty |
| `m_filter` (`m_4EA686DB`, `m_84065FD1`, `m_89831602`) | 0x110, 0x8001, 4 | 0x200000, 0, 4, plus `m_1E14CCA8`, `m_27CF71D5` and a condition | 0x200010, 1, 4, plus `m_66747E7D` | 0x200010, 1, 0, plus `m_27CF71D5` | 0x94, 0x3FFFFFFF, 4 | 0x14, 0x3FFFFFFF, 0 |
| `m_84E5DE4E` score | screen [0, 1] x 1, distance [0, 40] | screen [0, 1] x 1, distance [2, 25] | screen [0, 1] x 1, distance [0, 15] | empty | screen [0, 1] x 1, distance [0, `#1648`] | empty |
| `m_C39691D9` most targets | 1 | 1 | empty | empty | 1 | empty |
| `m_DD792FF0` search rate | 2 | 0 | 3 | 3 | 3 | 0 |
| `m_43478CAD` | 3 | 3 | 3 | 0 | 3 | 0 |
| `m_08285CD6`, `m_0297060F` | 1, 1 | 1, 1 | 0, 0 | 1, 1 | 1, 1 | 1, 1 |
| `m_93AB8C13` | empty | empty | empty | 1 | empty | empty |
| `m_8A2905A0` output | `#375` | `#375` | `#375` | `#468` | | |

`m_95FFC3C5`, `m_32AFE9AA` and `m_E4A295CE` are 1 on every node. `m_8D13DD07`, `m_2F5FAF65` and
`m_2A0FB8BB` are empty on every node. Guardian Angel's client-only state 14 repeats state 5 with
search rate 2, the distance term from 0 and `m_1C2A9945` off.

## Field layout

Read from code. The class's field registry ends at image offset `0x31640B0`, 0x38 bytes a row. A
row holds a field's type and offset and then the hash of the next field, so each hash pairs with
the offset one row up. The pairing below agrees with the wheel notes (`m_9E1426F6` at +0x1A0) and
with each field's type: two enums, then eight flags.

| Offset | Field | Offset | Field |
| --- | --- | --- | --- |
| 0xF8 | `m_E46A19A3` | 0x1C0 | `m_C39691D9` |
| 0x100 | `m_A30B383E` | 0x1C8 | `m_8A2905A0` |
| 0x108 | `m_3119AD61` | 0x1D0 | `m_CEE20F6F` |
| 0x110 | `m_B9BEAC7F` | 0x1E0 | `m_1B89EA79` |
| 0x118 | `m_D45E22FA` | 0x1E8 | `m_84E5DE4E` |
| 0x120 | `m_2F88C5F2` | 0x1F8 | `m_4A6E0895` |
| 0x128 | `m_544C3E6F` | 0x208 | `m_2A0FB8BB` |
| 0x130 | `m_8DCA77B6` | 0x210 | `m_43478CAD` (enum) |
| 0x138 | `m_762A56C0` | 0x214 | `m_DD792FF0` (enum) |
| 0x140 | `m_05BE62DD` | 0x218 | `m_08285CD6` |
| 0x148 | `m_B3A77CD0` | 0x219 | `m_0297060F` |
| 0x150 | `m_ED61D926` | 0x21A | `m_95FFC3C5` |
| 0x158 | `m_16BDE607` | 0x21B | `m_8D13DD07` |
| 0x160 | `m_8862A75D` | 0x21C | `m_2F5FAF65` |
| 0x168 | `m_E474FFD6` | 0x21D | `m_32AFE9AA` |
| 0x170 | `m_D59188D5` | 0x21E | `m_E4A295CE` |
| 0x178 | `m_48837057` | 0x21F | `m_1C2A9945` |
| 0x180 | `m_294660A6` | | |
| 0x188 | `m_3D667CE3` | | |
| 0x190 | `m_229DD034` | | |
| 0x198 | `m_BA6C9F75` | | |
| 0x1A0 | `m_9E1426F6` | | |
| 0x1A8 | `m_E8474EE8` | | |
| 0x1B0 | `m_C1B724B2` | | |
| 0x1B8 | `m_93AB8C13` | | |

## The search, step by step

Read from code. `0xCAE3B0` is the state's refresh. Hex-Rays does not decompile it or `0xC9A430`;
both were read as disassembly of the decrypted image.

### 1. Whether to search this frame: `m_DD792FF0`

`0xCA9460`. Never twice in one frame (the last search's frame is kept at state +0x4B0). Rate 0
searches every frame. Rates 1, 2, 3 and 4 search every 3, 6, 12 and 30 frames. The frame is
offset by a hash so that states do not all search together: FNV-1a over the four bytes of (the
node's value at +0x6C xor an id of the owner). The search runs when (hash + frame) mod N is 0, or
when N frames have passed since the last one. `0xCB0540` gives the same rates as periods: 0.05,
0.1, 0.2 and 0.5 s, and 0.016 s for rate 0.

So Guardian Angel's state 5 and Hack's state 26 search every frame, the wheel ten times a second,
and the staff and Hack's state 25 five times a second.

### 2. The search volume

`0xC9A430` (state, output, node) fills a 0x50-byte volume: the source point at +0x00, a direction
at +0x10 and the cone's limits from +0x20.

1. **Blend.** `m_2F88C5F2` is a duration. When set, t = time since the state began over that
   duration, held to [0, 1]; else t = 0.
2. **Source point.** `m_05BE62DD` and `m_B3A77CD0` are each read as a point, and the source is the
   first moved toward the second by t.
3. **Source entity.** `m_8DCA77B6` names an entity. With it empty, `0xC9AFF9` takes the state's
   owner (`0xCC6EF0`). The entity's signed aim pitch is read with `0xC61F70`.
4. **`m_0297060F`: pivot.** When set, the source point is moved into the entity's space, turned
   by that pitch around the first-person pivot (its height from `0xD9F3F0`, 1.55 by default) and
   moved back. The wheel notes describe this rotation at `0xC9A92F`.
5. **Direction**, `0xC9B8CF`, on the path without a named entity. Two angles in degrees:
   yaw = `m_D45E22FA`, 0 when empty; pitch = `m_E46A19A3`, 0 when empty, less the entity's aim
   pitch when `m_08285CD6` is set. The direction is (sin yaw cos pitch, sin pitch,
   cos yaw cos pitch) on the world's axes. Guardian Angel and Hack's state 25 give the owner's
   heading as the yaw and set `m_08285CD6`: the direction is the owner's aim. The staff's states 4
   and 51 give the heading and a pitch expression and leave `m_08285CD6` off.
6. **Cone**, `0xC9B9AB`. half = `m_A30B383E` / 2, 360 when empty. `m_B9BEAC7F` and `m_3119AD61`
   scale it sideways and upward, 1 when empty. The volume keeps cos(half x sideways) at +0x20 and
   cos(half x upward) at +0x24, each angle held to [0, pi]. `m_544C3E6F` is a second, inner angle
   kept the same way at +0x38 and +0x3C; empty here, so cos 0.
7. **Other members.** +0x48 = `m_16BDE607`. +0x40 = `m_ED61D926` moved toward `m_8862A75D` by t.
   +0x4C = `m_E474FFD6` as a flag; when set, +0x28 to +0x34 hold tan(angle) x the value at +0x40
   for the two angles and two scales. All empty on the nodes here.

Not read yet: the path with a named entity (`0xC9A658`, the wheel's), which has its own copy of
steps 4 and 5. The wheel leaves the yaw empty, so that copy must take the entity's axes.

### 3. Candidates, sight and the result

`0xCAE3B0` from `0xCAF14B`:

1. The volume is built and a spatial query is made from it (`0xD8E3F0`, `0xDA9BA0`).
2. The collision profile is `m_3D667CE3`, or `002D.097` when empty (`0xCAF1BD`): Hack's states
   use that default.
3. The sight origin is `m_D59188D5`, or the volume's source point when empty (`0xCAF254`).
4. The output variable's present entities are read as the targets held from the last search.
5. `m_C39691D9` is the most targets, without limit when empty. Only with a limit are the
   candidates ranked: `0xC89F60` takes the volume, the score `m_84E5DE4E` and `m_43478CAD`. So
   Guardian Angel, the wheel and Hack's state 25 keep the one best candidate, and the staff's
   states take every candidate in the cone.
6. For each candidate not already held: with `m_95FFC3C5` set, and the new list still under the
   limit, the body rays are cast (`0xCA3570`) and a blocked candidate is dropped. With it clear the
   candidate is taken unseen.
7. For each candidate already held: with `m_32AFE9AA` set, the rays are cast again and a blocked
   one goes on the list of lost targets.

`0xCA3570` also takes `m_229DD034`, `m_BA6C9F75`, `m_9E1426F6`, `m_E8474EE8`, the flags
`m_8D13DD07` and `m_2F5FAF65`, and per candidate `m_294660A6`, `m_C1B724B2` and the point
`m_48837057`. Hack's states 25 and 38 set `m_229DD034` and `m_48837057` to hardpoints.

## Open

- Where the cone and the entity filter are applied to the candidates: `0xD8E3F0`, `0xDA9BA0`,
  `0xCAACF0` and `0xC89F60` are not read.
- `STUConfigVarFilter`: `m_4EA686DB`, `m_84065FD1`, `m_89831602`, `m_1E14CCA8`, `m_27CF71D5`,
  `m_66747E7D` and the conditions.
- `m_43478CAD` inside the ranking, and the distance term's lower bound (Guardian Angel's 2).
- `m_93AB8C13`, `m_E4A295CE`, `m_1C2A9945`.
- The sign of `0xC61F70`'s pitch, and what the staff's pitch expression (opcodes 14, 0, 29, 0)
  yields.
- What the entity-position value `STU_21A13145` gives for a player body: feet or eye.
- Profile `002C.097`'s and `002D.097`'s masks and rules.
