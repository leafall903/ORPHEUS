# Runtime Data Guide (`rust`)

Public Rust tests use synthetic JSON fixtures and run without game data. Starting `orpheus_server` or running `--ignored` prepared-runtime tests requires a unified **1.74.0.0.104319** runtime pack (`ORPHEUS_RUNTIME_DIR`) sourced strictly from your own local game installation.

For one-click packing, preflight, and persistent-save launch, use [`scripts/setup_and_play.ps1`](../scripts/setup_and_play.ps1) (or double-click [`scripts/PLAY_ORPHEUS.cmd`](../scripts/PLAY_ORPHEUS.cmd)).

Keep extracted game assets, XML dumps, keys, and generated packs out of Git.

---

## Preflight & Packing

Validate any existing pack before starting the server:

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\authorized-base-runtime'
cargo run --locked --bin orpheus_server -- --check-runtime
```

> [!IMPORTANT]
> `--check-runtime` only verifies the 22 required filenames, map header metadata, and the 64-byte `data/lobby_state_blob.key`. It does **not** fail if `data/game_graphs_174.json.gz` was exported without mode scripts/variables (`0C5`), hero `target_tags` (`003`), or `mode_rulesets`.
>
> A bare graph bundle (e.g. ~388 graphs, `0/32` bodies with `target_tags`, no `mode_scripts`/`mode_variables`) will pass `--check-runtime` **but will reject hero spawns with StateScript initialization faults and stick clients on the "Assemble Your Team" / hero-select screen.**

### Recommended Complete Pack Command

Export `game_graphs_174.json.gz` with the `python` branch extractor (`tools/extract_game_graphs.py`) **including `--mode-xml` (`0C5` mode XMLs) and entity/bot definitions**, then pack with **`--game-graphs`**, **`--target-tags-xml` (`003` entity XMLs)**, and **`--mode-rulesets`**:

```powershell
cargo run --locked -p orpheus-runtime --bin prepare_server_runtime -- `
    --source 'C:\path\to\prepared-source' `
    --destination 'C:\path\to\orpheus-runtime-104319' `
    --map 0688 --map 06C9 --map 0CD3 `
    --game-graphs 'C:\path\to\game_graphs_174.json.gz' `
    --target-tags-xml 'C:\path\to\xml\003' `
    --mode-rulesets 'C:\path\to\mode-rulesets.json'
```

- `--game-graphs`: supplies the full graph bundle exported from `tools/extract_game_graphs.py` on the [`python`](https://github.com/leafall903/ORPHEUS/tree/python/tools) branch with `--mode-xml` (Practice Range / PvP / Junkenstein `0C5` mode scripts & variables) and `--entity` bot/NPC bodies (typically ~950 graphs, not ~388).
- `--target-tags-xml`: **required for playable hero spawning** — enriches all 32 hero bodies and entity records with `target_tags`, component 92 metadata, and Junkenstein NPC mover tables from local `003` XML dumps.
- `--mode-rulesets`: embeds local mode rulesets (JSON array of mode profiles; see [mode profile schema](../docs/STATESCRIPT_DEVELOPMENT.md#set-mode-and-actor-rules)) into the graph bundle and rejects duplicate mode IDs.

Minimal Practice-Range-only repack (only when `--source` **already** contains an enriched `data/game_graphs_174.json.gz` with `target_tags`, `mode_scripts`, `mode_variables`, and `mode_rulesets`):

```powershell
cargo run --locked -p orpheus-runtime --bin prepare_server_runtime -- --source 'C:\path\to\already-enriched-source' --destination 'C:\path\to\orpheus-runtime' --map 0688
```

### Verify Graph Bundle Completeness

After packing, verify that `data/game_graphs_174.json.gz` actually contains mode data, all 32 hero `target_tags`, and `mode_rulesets`:

```powershell
python -c "import gzip, json; g=json.load(gzip.open(r'C:\path\to\orpheus-runtime-104319\data\game_graphs_174.json.gz','rt',encoding='utf-8')); b=g.get('bodies',{}); print('graphs:',len(g.get('graphs',[])),'bodies:',len(b),'with_target_tags:',sum(bool(v.get('target_tags')) for v in b.values()),'mode_scripts:',len(g.get('mode_scripts',{})),'mode_variables:',len(g.get('mode_variables',{})),'mode_rulesets:',len(g.get('mode_rulesets',[])))"
```

Expected for a complete Practice Range + 32-hero pack: `graphs` ~950, `bodies` 32, `with_target_tags` **32**, `mode_scripts` > 0, `mode_variables` > 0, `mode_rulesets` > 0.

The packer also preserves optional enriched files present in `--source` and hashes them into `manifest.json`: `data/combat_hurt_volumes.json`, `data/combat_voice_174.json`, `data/player_voice_174.json`, `data/voice_effects_174.json`, `data/emote_aliases_174.json`, `data/emote_blend_trees_174.json`, `data/emote_durations_174.json`, `data/emote_scalar_tracks_174.json`, `data/greeting_durations_174.json`, `data/map_environment_174.json`, and `data/statescript_names_174.json`.

---

## Input Provenance (Three Categories)

### 1. Tool Outputs from Local 104319 Game Files / XML (Generated via tools on [`python`](https://github.com/leafall903/ORPHEUS/tree/python/tools))

| Runtime File | Generator (`python` branch) |
| --- | --- |
| `data/extracted_{items,general_unlocks,heroes,maps}.json` | `tools/prepare_client_catalogs.py` (pinned OWLib DataTool) |
| `data/arcade_cards_174.json`, `data/game_{heroes,maps,movers,queues,stats}_174.json` | `tools/extract_*.py` from local XML exports |
| `data/ai_spawners_174.json`, `data/game_graphs_174.json.gz` | `tools/extract_ai_spawners.py`, `tools/extract_game_graphs.py` (pass `--mode-xml` for `0C5` + `--entity` for bots/NPCs) |
| `data/collision_keys_174.json`, `collision/0688.col` (and optional `*.rays.col` / extra maps `005B`, `00D4`, `0165`, `0CD3`) | `tools/export_casc_records.ps1`, `tools/build_collision.py` |

Rust uses the optional map-specific `*.rays.col` for hitscan, scripted
sight, and both point and swept projectile terrain queries. The runtime
checks its map, collision key and applicable prop provenance against the
catalogs. An expanded cache can specify `weapon_terrain_category` as a
nonzero 16-bit integer in its JSON header. Use a category verified for the
target build. The room uses it for hitscan and both point and swept projectile
terrain queries. `category_mask` continues to describe the cache's coverage.
Character movement uses the movement cache. Caches without a weapon category
use the legacy mover category. Missing or rejected expanded geometry uses
movement-based probes and sweeps.

### 2. Prepared Research Data (Must Be Supplied in Source Pack)

| Runtime File | Role |
| --- | --- |
| `data/schemas_174.json`, `data/game_schemas_174.json` | 707 lobby schemas and game-link message layouts |
| `data/statescript_codecs_174.json` | StateScript owner/observer frame field codecs |
| `data/retail_templates.json`, `bnet/descriptors.pb` | Lobby response templates and Battle.net protobuf descriptors |
| `data/collision_props_174.json` | Per-map prop placement keys and mode filters |

### 3. Private Local Inputs (Keep Confidential)

| Runtime File | Role |
| --- | --- |
| `data/resource_keys_174.json` | Content keys for event resources |
| `data/lobby_state_blob.key` | Exact 64-byte lobby handshake signing key |
| `data/junkenstein/junkenstein-npcs.json` | Explicit NPC definitions required for Junkenstein enemy tests/runs |
| `data/combat_hurt_volumes.json` | Optional local combat regions measured per body, packed and hashed when present. Top-level body regions are standing measurements. A body may supply a `crouched` object with its own `body` and `head` regions. |
| `data/combat_voice_174.json` | Optional local multikill and actor hurt/death voice catalog, packed and hashed when present |
| `data/player_voice_174.json` | Optional actor voice catalog. Its `equipped` table maps playback effects to voice stimuli. |
| `data/voice_effects_174.json` | Optional mapping from equipped voice unlocks to playback effects, preserved by the packer. Generate locally with `tools/extract_voice_effects.py` on the Python reference branch. |
| `data/emote_aliases_174.json`, `data/emote_blend_trees_174.json` | Optional untagged emote mappings from equipped unlocks to animation aliases and blend trees. Generate locally with `tools/extract_emote_aliases.py` on the `python` branch. |
| `data/emote_durations_174.json`, `data/emote_scalar_tracks_174.json`, `data/greeting_durations_174.json` | Optional emote/greeting clip durations and scalar camera/pose tracks preserved when present. |
| `data/map_environment_174.json` | Optional map boundary hazards and per-map/mode respawn delay overrides (Practice Range defaults to 3s). |
| `data/statescript_names_174.json` | Optional human-readable StateScript graph/state labels for the Web 3D Scene Inspector. |

To enable actor hurt and death voice cues, provide locally verified entries in
`hurt.minor`, `hurt.severe`, `hurt.death`, or `hurt.bot_death` and a positive
`hurt_cooldown_ticks`. Each entry specifies a `stimulus` and a nonempty list
of `lines`. Prefer `hurt_by_body` to bind this same table to a specific body
definition GUID. A matched body uses its own table. An unmatched body uses
the optional shared `hurt` table when present. A catalog containing only
`multikills` enables announcer cues.

The combat calibration file has `version: 1` and a `bodies` object keyed
by body-definition ID. Each record contains a `body` capsule and an optional
`head` capsule. Each capsule specifies `lower` and `upper` axis endpoints
in metres above the feet and a positive `radius` in metres. Equal endpoints
make a sphere. This calibration adjusts player hitscan and both player and NPC projectile
contact against the listed bodies. Movement contacts, healing and navigation keep
their own geometry. Bodies without a calibration keep the existing
compatibility proxy. Use local measurements for the intended body and build.
See the [synthetic schema example](../fixtures/behavior/combat-hurt-volumes-v1.json).
