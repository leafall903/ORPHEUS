# TrackTargets: the target search behind Guardian Angel, the staff and Hack

Build 1.74.0.0.104319. Read from data and from code; see the [index](README.md) for the evidence
labels and the address convention. Every code claim names its function by image offset. What is
not decoded is listed under [Not decoded](#not-decoded).

## Why

`STUStatescriptStateTrackTargets` answers "which entities am I aiming at". The communication wheel
uses it for the aimed teammate, and [its notes](../COMMUNICATION_WHEEL.txt) decode most of the
native search for the wheel's two nodes: the composite score, the two body rays and their order,
the `.097` collision profiles. Mercy's Guardian Angel and staff and Sombra's Hack use the same
state with other options. The server answered only the wheel's nodes and one hostile
configuration, so those abilities found no target.

## The nodes compared

Read from data. Nodes are named by graph and node position. Only the fields that differ are
listed; the wheel's two nodes are the decoded baseline.

| Field | Wheel `05AD` 113, 115 | Guardian Angel `006E` 15 | Staff `006B` 6, 121 | Staff `006B` 7 | Hack `0B1B` 50, 68 | Hack `0B1B` 52 |
| --- | --- | --- | --- | --- | --- | --- |
| `m_8DCA77B6` source entity | the owner | empty | empty | empty | empty | empty |
| `m_05BE62DD` source point | Vec3 (0, 1, 0) | entity position (`STU_21A13145`) of the owner | same | same | same | same |
| `m_radius` | 40 | `#1648` | `#1648` | `#1648` | `#1648` | `#1648` + 1 |
| `m_A30B383E` aperture | 15 | expression: `#9582`, 30 | 6: expression: 360, 40, `#9583`; 121: 40 | 360 | expression: 45, 30, `#23689`, `#9584` | 90 |
| `m_D45E22FA` yaw | empty | owner's heading (`STU_C8C4C20D`) | same | empty | same | same |
| `m_E46A19A3` pitch | empty | empty | minus the owner's pitch (`STU_40AC5AE4`) | empty | empty | empty |
| `m_3D667CE3` profile | `0111.097`, `0079.097` | `002C.097` | `002C.097` | `002C.097` | empty | empty |
| `m_filter` any `m_84065FD1` | 0x8001 | 0 | 1 | 1 | 0x3FFFFFFF | 0x3FFFFFFF |
| `m_filter` all `m_1E14CCA8` | 0 | 1 | 0 | 0 | 0 | 0 |
| `m_filter` excluded `m_4EA686DB` | 0x110 | 0x200000 | 0x200010 | 0x200010 | 0x94 | 0x14 |
| `m_filter` relations any, required, excluded (`m_27CF71D5`, `m_66747E7D`, `m_89831602`) | 0, 0, 4 | 1, 0, 4 | 0, 1, 4 | 1, 0, 0 | 0, 0, 4 | 0, 0, 0 |
| `m_filter` `m_condition` | empty | set | 6: set; 121: empty | set | set | set |
| `m_84E5DE4E` score | screen [0, 1] x 1, distance [0, 40] x 0.01 | screen [0, 1] x 1, distance [2, 25] x 0.01 | screen [0, 1] x 1, distance [0, 15] x 0.01 | empty | screen [0, 1] x 1, distance [0, `#1648`] x 0.1 | empty |
| `m_C39691D9` most targets | 1 | 1 | empty | empty | 1 | empty |
| `m_DD792FF0` search rate | 2 | 0 | 3 | 3 | 3 | 0 |
| `m_43478CAD` order | 3 | 3 | 3 | 0 | 3 | 0 |
| `m_08285CD6`, `m_0297060F` | 1, 1 | 1, 1 | 0, 0 | 1, 1 | 1, 1 | 1, 1 |
| `m_93AB8C13` | empty | empty | empty | 1 | empty | empty |
| `m_8A2905A0` output | `#375` | `#375` | 6: `#375`; 121: `#1182` | `#468` | 50: `#375`; 68: `#1691` | `#468` |

`m_95FFC3C5`, `m_32AFE9AA` and `m_E4A295CE` are 1 on every node. `m_8D13DD07`, `m_2F5FAF65` and
`m_2A0FB8BB` are empty on every node. Guardian Angel's node 30 repeats node 15 with search rate 2,
the distance term from 0, `m_1C2A9945` off and output `#1691`. Hack's nodes 50 and 68 also set the
hardpoints `m_229DD034` and `m_48837057`.

## Field layout

Read from code. The class's field registry ends at image offset `0x31640B0`, 0x38 bytes a row. A
row holds a field's type and offset and then the hash of the next field, so each hash pairs with
the offset one row up. The registering code confirms the rule for the filter class: at `0x10B2640`
it stores offset 0x20 and then loads the hash `84065FD1`.

Two named fields appear under their hashes in the registry: `m_radius` is `m_ED61D926` (+0x150)
and `m_filter` is `m_F8274C10` (+0x1F0). The refresh takes its filter from node +0x1F0
(`0xCAF05B`) and the volume builder its radius from node +0x150 (`0xC9BA41`).

| Offset | Field | Offset | Field |
| --- | --- | --- | --- |
| 0xE8 | `m_47AB1887` | 0x1B0 | `m_C1B724B2` |
| 0xF8 | `m_E46A19A3` | 0x1B8 | `m_93AB8C13` |
| 0x100 | `m_A30B383E` | 0x1C0 | `m_C39691D9` |
| 0x108 | `m_3119AD61` | 0x1C8 | `m_8A2905A0` |
| 0x110 | `m_B9BEAC7F` | 0x1D0 | `m_CEE20F6F` |
| 0x118 | `m_D45E22FA` | 0x1D8 | `m_24A4AB8A` |
| 0x120 | `m_2F88C5F2` | 0x1E0 | `m_1B89EA79` |
| 0x128 | `m_544C3E6F` | 0x1E8 | `m_84E5DE4E` |
| 0x130 | `m_8DCA77B6` | 0x1F0 | `m_filter` |
| 0x138 | `m_762A56C0` | 0x1F8 | `m_4A6E0895` |
| 0x140 | `m_05BE62DD` | 0x200 | `m_8CBE558B` |
| 0x148 | `m_B3A77CD0` | 0x208 | `m_2A0FB8BB` |
| 0x150 | `m_radius` | 0x210 | `m_43478CAD` (enum) |
| 0x158 | `m_16BDE607` | 0x214 | `m_DD792FF0` (enum) |
| 0x160 | `m_8862A75D` | 0x218 | `m_08285CD6` |
| 0x168 | `m_E474FFD6` | 0x219 | `m_0297060F` |
| 0x170 | `m_D59188D5` | 0x21A | `m_95FFC3C5` |
| 0x178 | `m_48837057` | 0x21B | `m_8D13DD07` |
| 0x180 | `m_294660A6` | 0x21C | `m_2F5FAF65` |
| 0x188 | `m_3D667CE3` | 0x21D | `m_32AFE9AA` |
| 0x190 | `m_229DD034` | 0x21E | `m_E4A295CE` |
| 0x198 | `m_BA6C9F75` | 0x21F | `m_1C2A9945` |
| 0x1A0 | `m_9E1426F6` | | |
| 0x1A8 | `m_E8474EE8` | | |

`STUConfigVarFilter`'s registry ends at `0x3004B20`, read the same way:

| Offset | Field | Offset | Field |
| --- | --- | --- | --- |
| 0x10 | `m_93FA1642` | 0x50 | `m_condition` (`m_BAA74493`) |
| 0x20 | `m_84065FD1` | 0x58 | `m_9C7BD8B8` |
| 0x28 | `m_1E14CCA8` | 0x60 | `m_B486603E` |
| 0x30 | `m_4EA686DB` | 0x68 | `m_8B2A4DF3` |
| 0x38 | `m_27CF71D5` | 0x70 | `m_C1903771` |
| 0x40 | `m_66747E7D` | 0x78 | `m_F61726FE` |
| 0x48 | `m_89831602` | 0x80 | `m_B40362FC` |

## The search, step by step

Read from code. `0xCAE3B0` is the state's refresh. Hex-Rays does not decompile these functions;
they were read as disassembly of the decrypted image.

### 1. Whether to search this frame: `m_DD792FF0`

`0xCA9460`. Never twice in one frame (the last search's frame is kept at state +0x4B0). Rate 0
searches every frame. Rates 1, 2, 3 and 4 search every 3, 6, 12 and 30 frames. The frame is
offset by a hash so that states do not all search together: FNV-1a over the four bytes of (the
node's value at +0x6C xor an id of the owner). The search runs when (hash + frame) mod N is 0, or
when N frames have passed since the last one. `0xCB0540` gives the same rates as periods: 0.05,
0.1, 0.2 and 0.5 s, and 0.016 s for rate 0.

So Guardian Angel's node 15 and Hack's node 52 search every frame, the wheel ten times a second,
and the staff and Hack's nodes 50 and 68 five times a second.

### 2. The search volume

`0xC9A430` (state, output, node) fills a 0x50-byte volume: the source point at +0x00, a direction
at +0x10, the cone's limits from +0x20, the radius at +0x40.

1. **Blend.** `m_2F88C5F2` is a duration. When set, t = time since the state began over that
   duration, held to [0, 1]; else t = 0.
2. **Source point.** `m_05BE62DD` and `m_B3A77CD0` are each read as a point, and the source is the
   first moved toward the second by t.
3. **Source entity.** `m_8DCA77B6` names an entity. With it empty, `0xC9AFF9` takes the state's
   owner (`0xCC6EF0`). The entity's aim pitch is read with `0xC61F70`: the signed 16-bit pitch of
   its movement state (+0x42) times 2 pi / 65536. That is the command's own pitch, positive
   looking down.
4. **`m_0297060F`: pivot.** When set, the source point is taken into the entity's space, turned
   by that pitch around the first-person pivot and taken back: with h the pivot's height
   (`0xD9F3F0`, 1.55 by default) and p the pitch, y' = cos p (y - h) - sin p z + h and
   z' = sin p (y - h) + cos p z (`0xC9A92F` on the path with a named entity, `0xC9B80E` on the
   other).
5. **Named entity** (`0xC9A658`, the wheel's path). The source point is a local offset: after the
   pivot it goes through the entity's matrix (`0xC9AF66`, `0xC9AFB0`), and the entity's three
   axes stay loaded for the direction (the jump to `0xC9B8A1` skips the world axes). The wheel's
   (0, 1, 0) is a metre above the entity's root, swung around its eye by the pitch.
6. **No named entity.** The point is a world point and the axes are the world's (`0xC9B889`).
7. **Direction**, `0xC9B8CF`. Two angles in degrees: yaw = `m_D45E22FA`, 0 when empty;
   pitch = `m_E46A19A3`, 0 when empty, less the entity's aim pitch when `m_08285CD6` is set. The
   direction is (sin yaw cos pitch, sin pitch, cos yaw cos pitch) on the loaded axes.
8. **Cone**, `0xC9B9AB`. half = `m_A30B383E` / 2, 360 when empty. `m_B9BEAC7F` and `m_3119AD61`
   scale it sideways and upward, 1 when empty. The volume keeps cos(half x sideways) at +0x20 and
   cos(half x upward) at +0x24, each angle held to [0, pi]. `m_544C3E6F` is a second, inner angle
   kept the same way at +0x38 and +0x3C; empty here, so cos 0.
9. **Radius.** +0x40 = `m_radius` moved toward `m_8862A75D` by t. +0x44 = 0, a least distance.
   +0x48 = `m_16BDE607`, added to a candidate's radius. +0x4C = `m_E474FFD6` as a flag; when
   set, +0x28 to +0x34 hold tan(angle) x the radius for the two angles and two scales. +0x4D = 0.
   `m_8862A75D`, `m_16BDE607` and `m_E474FFD6` are empty on the nodes here.

What the values on Mercy's and Sombra's nodes give:

- **`STU_21A13145`, the source point** (`0xCE47D0`, worker `0xD1A410`). The entity's transform
  comes from its movement state when it has one (`0xC5E3C0`: rotation and translation of the
  mover), and the result is translation + up axis x the first-person pivot height (`0xD1ABA4`,
  the same `0xD9F3F0`, 1.55 by default). For a player body that is the eye. The pivot step then
  leaves the point where it is, since the point is the pivot.
- **`STU_C8C4C20D`, the yaw**: the owner's heading in degrees.
- **`STU_40AC5AE4`, the staff's pitch.** [The config notes](../CONFIG_EVALUATION.txt) read its
  evaluator (`0xCC8200`, worker `0xD1C220`): the same signed pitch from `0xC61F70`, in degrees.
  The staff's expression (opcodes 14, 0, 29, 0) is that value negated, and `m_08285CD6` is off.
- So Guardian Angel (pitch empty, `m_08285CD6` on) and the staff's nodes 6 and 121 (minus the
  pitch, `m_08285CD6` off) come to the same thing: **from the owner's eye, along the owner's
  aim.** Nodes without a yaw (`006B` 7) look along world +Z, and their aperture is 360.

### 3. Candidates: where they come from

`0xCAACF0` (state, search context, world). The search context is the block the refresh keeps at
its frame +0x1A0: the filter at +0x00, the filter's evaluator at +0x08, the volume at +0x20, the
candidate list at +0x70 with its count at +0x78, the spatial query at +0x290, the profile at
+0x2A8.

1. `0xC72D50` reduces the filter to a kind from its two entity masks. `m_1E14CCA8`, when not zero,
   gives 1 for bit 0, 0x40000 for bit 18, 0x40 for bits 6 or 7, 0x10000 for bit 16. Otherwise
   `m_84065FD1` with exactly one bit set gives 1, 0x40000 or 0x40 the same way. Else 0.
2. Kinds 1, 0x40, 0x10000 and 0x40000 are gathered from lists the world keeps for those bits
   (`0xCAAB40`: a sphere of the search radius plus `m_16BDE607` around the source point handed to
   `0xC778A0`, then `0xCA7940` for each entity found).
3. Kind 0: with any of 0x400C1 in `m_84065FD1` those lists are gathered too (`0xCAAB40` with
   0x150041). If other bits remain, a sphere query runs around the source point with the same
   radius, and `0xCAB8A0` is called for each entity found.

Guardian Angel (`m_1E14CCA8` = 1) and the staff (`m_84065FD1` = 1) are kind 1: their candidates
come from the list of bit 0. Hack and the wheel take the lists and the sphere query.

### 4. Candidates: the test for each

`0xCAB8A0` only resolves the entity and calls `0xCABB20`; `0xCA7940` is the same test for a listed
entity. In order:

1. An entity already on the candidate list is skipped.
2. **Filter.** The evaluator's method at +0x150 is called with the entity's id and the node's
   filter. It is `0xD0DA20`, described [below](#the-filter).
3. **Volume.** `0xDB1AA0` gives the entity's anchor: position, radius (+0x10) and height (+0x14).
   `0xC8E660` (volume, anchor) decides:
   - The body is a vertical capsule: sphere centres at min(radius, height / 2) and
     max(height - radius, height / 2) above the anchor.
   - **Range.** P is the point of that axis nearest the source's height. The entity is out when
     |P - source| > radius of the search + the body's radius, or nearer than the least distance
     (+0x44, less the body's larger half extent).
   - With no cone (both cosines at -0.9999 or less) and no inner cone the entity is in.
   - **Cone.** `0xDC8720` gives the nearest points of the body's capsule (its radius plus
     `m_16BDE607`) and the aim segment, source - radius x direction to source + radius x direction.
     When they touch and the body's point is ahead of the source, the entity is in. Otherwise
     n = the unit vector from the source to the body's point, and the entity is in when
     n . direction >= the cosine kept at +0x20, moved toward the one at +0x24 by |n.y|.
     The same path with the inner angle puts an entity out when it is inside the inner cone.
4. `0xC95840` is a further test for entities tagged `067B.0D6` only: one of their parts must be
   within the radius. Others pass.
5. The entity joins the candidate list, with its distance from the source less its radius kept
   beside it (context +0x180).

### The filter

`0xD0DA20` (evaluator, instance, watcher, entity id, filter). An empty filter passes everything.
The entity must exist. Then:

1. **Masks**, `0xBCAAE0`. The entity's bits are its filter component's (+0x58; component 25, with
   the team in bits 23 to 27 and the spectator in bit 28, as `script/entity_team.py` reads them).
   Three results are formed and the entity passes when `any` and `all` hold and `excluded` does
   not:
   - `any`: bits & `m_84065FD1` is not zero, or `m_84065FD1` is 0x3FFFFFFF.
   - `all`: `m_1E14CCA8` is zero, or bits & `m_1E14CCA8` equals it.
   - `excluded`: bits & `m_4EA686DB` is not zero.
   - With an owner, three relation masks adjust them. The relation is hostile when `0xC1CF60`
     (owner, entity) says so and the entity is not the owner (for a spectator bit, `0xC1C690`
     decides instead). Each mask has bit 0 for "not hostile", bit 1 for "hostile", bit 2 for
     "is the owner". `m_27CF71D5`: a matching bit makes `any` hold. `m_66747E7D`: a set bit whose
     relation does not match makes `all` fail. `m_89831602`: a matching bit makes `excluded` hold.
   - An entity without the filter component fails any filter with a mask set, unless
     `m_84065FD1` is 0x3FFFFFFF (`0xD0DBEE`).
2. **Condition**, `0x1B8D970`: `m_condition` is evaluated with the candidate as parameter 8 and
   must be true.
3. **Tags**, `0xD0EC70`: `m_9C7BD8B8`, `m_B486603E` and `m_8B2A4DF3` are the any, all and excluded
   tag lists (as `SceneWorld._filter_entity` already reads them).
4. `0xDA8BD0` and, with `m_F61726FE` set, `0x90BFC0` are two more tests, not read; `m_F61726FE`
   is empty on every node here.

Read with that, the authored filters say:

- **Guardian Angel**: bit 0 required, bit 21 excluded, not hostile, not the owner.
- **Staff**, nodes 6 and 121: bit 0, neither bit 4 nor bit 21, not hostile, not the owner.
- **Staff**, node 7: bit 0 or not hostile, neither bit 4 nor bit 21; its condition does the rest.
- **Hack**: anything but bits 2, 4 and 7, not the owner; its condition does the rest.
- **Wheel**: bit 0 or bit 15, neither bit 4 nor bit 8, not the owner.
- The hostile queries the server already answers agree: (any 0x40 or 1, excluded 0x10 or 0x14,
  relation excluded 1) is "an enemy".

### 5. Sight, the limit and the result

`0xCAE3B0` from `0xCAF14B`:

1. The volume is built and the candidates gathered (steps 2 to 4).
2. The collision profile is `m_3D667CE3`, or `002D.097` when empty (`0xCAF1BD`): Hack's nodes
   use that default.
3. The sight origin is `m_D59188D5`, or the volume's source point when empty (`0xCAF254`).
4. The output variable's present entities are read as the targets held from the last search.
5. The limit is `m_C39691D9`, none when empty. Only with a limit are the candidates put in order
   first (`0xC89F60`, described [below](#the-order)).
6. For each candidate in order (`0xCAF4D0`):
   - **Not held.** With `m_95FFC3C5` clear it is taken. With it set it is taken when fewer new
     targets than the limit were taken so far and the body rays reach it (`0xCA3570` answers
     "blocked").
   - **Held.** With `m_32AFE9AA` and `m_95FFC3C5` both set the rays are cast again, and a blocked
     one is lost.
7. With `m_32AFE9AA` set, a held target that is no longer a candidate is lost (`0xCAB830`).
8. `m_93AB8C13` is a time in seconds: the state keeps each target's last time (state +0x1C0) and
   a held target older than that is lost too (`0xCAF8C2`).
9. The result (`0xCAFA5C`): the held targets less the lost ones, then the new ones. With an order
   or a score and more than one entry they are put in order again (`0xC8A0C0`). Entries past the
   limit are dropped (`0xCAFC2D`). If the list changed it is written to `m_8A2905A0`.
   `m_1B89EA79`, when set, receives the count.

So a node with a limit of 1 ends each search with the best candidate it can see, held or new, and
a node without a limit with every candidate it can see.

The body rays are the wheel's (`0xCA3570`; [its notes](../COMMUNICATION_WHEEL.txt) have the
points and their order: the primary hardpoint, 30 % of the body's height, the secondary hardpoint
or 60 %). `m_8D13DD07` is 0 on every node, so no lateral rays.

### The order

`0xC89F60` returns at once when `m_43478CAD` is 0. Otherwise `0xCA9570`:

- With `m_84E5DE4E` set, each candidate's score is the composite's (`0xC96E10`, read in the wheel
  notes), whatever the value of `m_43478CAD`.
- Without it, `m_43478CAD` 1, 2 and 3 pick three built-in orders (`0xCAA70A`, `0xCA9F14`,
  `0xCA96C1`), not read: no node here is ordered without a score.
- The candidates are sorted by rising score (`0xC8A5E0`): the lowest is the best.

A term of the composite has a low bound `m_ABC68B33`, a high bound `m_2AFC083F` and a weight. Its
value is held to [0, 1] over that range and multiplied by the weight (`0xC96F1C`: low, then
high - low). For the distance term (`0xC9763C`) that is
weight x clamp((distance - low) / (high - low), 0, 1), the distance being from the source point to
the candidate's root. Guardian Angel's low bound of 2 makes every ally within 2 m equal on
distance.

## The collision profiles

Read from data: the four `.097` assets are one instance of `STU_8EFC337B` each.

- `002C.097` has the same flag fields as `0079.097` (`m_2C54AEAF` = 1, 0, 0, 0, 1;
  `m_CBCB2AB9` = 0, 0, 0, 0, 1, 1; `m_0220680A`, `m_5CE47069` and `m_BC69BB40` alike). The wheel
  notes give `0079.097` the masks 0x1FDF (static) and 0x1FDF (dynamic), so `002C.097` has them
  too. Its rules `m_BC131C03` are `0079.097`'s one rule (the one that turns away category 0x10)
  and a second on category 1 with flags 0x80.
- `002D.097` has the same fields less `m_2C54AEAF`, and the same two rules with another header.
- `0x17A8FC0` packs a row of 13 flag bytes into the mask (read from code). Byte 6 is bit 0, then
  bytes 8, 0, 9 and 10; bit 5 is always clear; then bytes 4, 3, 1, 7, 11, 12 and 2.

## The conditions

Read from data. A filter's condition reads the candidate as parameter 8; `c.#478` below is the
candidate's own variable `#478`.

- **Guardian Angel**: when `c.#478`, then `c.#653` and not `c.#11264`; otherwise not `c.#11264`.
- **Staff**, node 6: true for a player (`STU_D3EB8D87` is the test for an AI's controller,
  `0xCDB700`); for an AI the candidate must be `STU_A37A910D`'s entity.
- **Staff**, node 7: the candidate is the target already in `#375`. So this node only says whether
  the present target is still within the radius and in sight, and keeps it one second longer
  (`m_93AB8C13`).
- **Hack**, nodes 50 and 68: the owner's `#2426`, not `c.#11264`, not the owner's `#61`, then a
  choice between kinds of target made with `STU_AC829876` (does an entity pass a filter,
  `0xCF4340`) on five filters, four of them with a required tag (`029C.0D6`, `0034.0D6`,
  `03A6.0D6`, `06E6.0D6`) and one with a condition of its own on `STU_53F4CA65` (`0xCEAD90`);
  with `STU_7C005F8F` (`0xCCBF90`), `STU_1A3FA5B0` (`0xCC99A0`) compared with the entity
  definition `0216.004`, and `c.#7982`, `c.#842` and `c.#14679`. Those evaluators are not read.

The cone's width on Guardian Angel, the staff and Hack is the authored angle times `#9582`, `#9583`
and `#9584`, entity variables that no graph writes.

## What the server implementation takes on trust

`combat.track_targets` plays the search for the nodes whose source is the owner's eye and whose
direction is the owner's aim, in Mercy's two graphs: Guardian Angel's and the staff's. Zarya's,
Zenyatta's and Ana's ally searches have the same shape and are left unanswered until they are
checked. Where the decode stops, it does this, and each point is a reconstruction:

1. **Filter bits.** Every player's and bot's body has bit 0, and bit 4 while dead. Nothing has
   bit 21.
2. **Candidates** are the match's player and bot bodies, not the world's lists.
3. **Other bodies' variables.** A condition reads the candidate's variables as unset, so
   Guardian Angel's `c.#478`, `c.#653` and `c.#11264` rules do not turn anyone away.
4. **`#9582` to `#9584`** are taken to be the player's target sensitivity options and played at
   1, the options' 100 %.
5. **Sight** uses the static mask 0x1FDF against the map only. Other bodies and barriers do not
   block, and the rays go to the wheel's points: the body's hardpoints when its pose is known,
   else 30 % and 60 % of its height.
6. **The search frame.** The period of `m_DD792FF0` is played; the hash that offsets each state's
   frame is not.
7. **`m_93AB8C13`** is not played: the staff's node 7 drops its target the refresh it is lost.

Hack's nodes are not answered: their sight goes through two hardpoint fields and their condition
through evaluators that are not read.

## Seen live against ORPHEUS

2026-11-12, the original client in the Practice Range as Mercy, driven by a virtual gamepad, one
run.

- **Seen.** With a Friendly Bot about 15 m ahead at the crosshair, the Guardian Angel button flew
  Mercy to it: the next picture, 0.7 s later, has the bot filling the view. The server's log has
  the flight's own nodes running in that second.
- **Not seen.** No target mark on the bot before the press, so the client's own search (node 30)
  did not pick it. Holding primary fire at the same bot from 3 m and from 10 m drew no beam. The
  bot was at full health, so healing could not show either.
- **Not tried.** An ally outside the cone or behind a wall, an enemy, a second player.

A likely reason for the missing mark and beam, not checked: the client runs the same filters on
the filter bits the server gave the bot's body, which are the team bit alone, and both filters ask
for bit 0.

## Not decoded

The server implementation states which of these it had to assume.

- **Which entities carry which filter bits.** Bits 23 to 28 are known. A player entity is created
  with 0x40001. Bit 4 fits "dead" (the staff excludes it, Guardian Angel does not, and
  `junkenstein_ai.py` already takes it so) and bit 0 fits "a hero's body", but no code that sets
  either on a body was found: `0xB8BDF0` and `0xB8AF90` set bits, with few callers, none of them
  with these values. Nothing in the packed graphs sets bit 21.
- **The world's lists** behind kinds 1, 0x40, 0x10000 and 0x40000 (`0xC778A0`, `0xC73900`).
- **`002D.097`'s masks**: the default of the flag field it leaves out is not known. The other
  three profiles all have 0x1FDF as their static mask.
- **How a profile's rules are applied to dynamic shapes** beyond what the wheel notes read.
- `0xCA7F20`, called for a listed entity before the volume test, and `0xC95840`'s part test.
- `0xDA8BD0` in the filter, `m_E4A295CE`, `m_1C2A9945` (it reaches the gathering as context
  +0x340 together with a component of the owner) and `m_47AB1887` (a list of segments on Hack's
  node 52).
- Hack's hardpoint fields `m_229DD034` and `m_48837057` inside `0xCA3570`.
- The built-in orders without a score.
