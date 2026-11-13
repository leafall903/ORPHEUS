# Reinhardt entity affiliation on build104319

The shield's gameplay source and Pet owner are different identities. Original
0095/node51/state17 evaluates `m_D749D7D8` as the primary possessor of the
casting body. In a normal player session that is the controller entity. Capture
that provider at allocation, then send it in component72 alongside the caster's
component26 team bits. Keep the011A startup/0096 VM source bound to the original
body. Neither the owner nor the team bits depend on which client receives them.

| Entity or effect | Original ownership path | Change or bounded finding |
| --- | --- | --- |
| Barrier011A and child0262 effect02AA | 0095 Pet17; controller Pet owner, body VM/combat source | Fix omitted component72; retain team filter, health, movement and original model/look |
| Fire Strike01EA and child0211 effect025F | 00E2 volley0; Shot is carried on the casting body with original instance/state/activation | No Pet component in the original definition; retain Shot ownership and original effect resources |
| Hammer0061 and raised weapon01F8 | 0048 CosmeticEntity0 and0095 CosmeticEntity9 on the casting body | Client-created body cosmetics; no separate gameplay owner or team entity added |
| Hammer children0285/2D33/2075 | Original0061 child definitions, including effects02C4/2860 | Preserve the weapon/child chain |
| Charge and victim effects | Original007D nodes and referenced224E victim graph | Preserve authored body/target selectors and source; no material or palette override |
| Earthshatter shockwave and impact effects | Original00BE shockwave0 and action effects; shared224E target graph | Preserve original source and target selectors |
| Other shield/ability effects | Original0095/25B4/0096 and ability effects on00E2 | Preserve their original graph context and resource selectors |
| Autonomous summon or stationary turret | None found in the five weapon/ability graphs and their referenced graph closure | No applicable producer; the shield Pet and Fire Strike are covered separately |

The synthetic contract is
`fixtures/behavior/reinhardt-entity-affiliation-v1.json`. Its runtime consumer
decodes actual encrypted world packets for self, teammate and enemy. It also
connects a teammate and, separately, an enemy while the same shield is already
live. All receive the same captured controller and caster team bits; their
shield VMs retain the casting body source. Each later Fire Strike retains one
original01EA launch on that source body and retires through the existing path.

The target image's GetTeam leaf at RVA5D9570 derives its team index from filter
bits23..27. This establishes team input decoding, not final visual color.
Palette selection, effect-internal instigator resolution and original-client
rendered colors remain unverified. Protocol checks must not close native color
acceptance. The new Soldier Biotic Field and Mei Cryo enemy-color reports remain
open in their owning lanes regardless of any older color result.
