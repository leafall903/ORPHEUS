# Runtime Data Guide (`rust`)

Public Rust tests use synthetic fixtures. The native server and its prepared-runtime tests use a private build **1.74.0.0.104319** pack generated locally from your own Overwatch 1.74 installation — **no separately downloaded protocol tables, descriptors, templates, or signing keys are required**.

## Build a Practice Range pack (Zero External Inputs)

Run a single command from the repository root on Windows. Supply the directory containing the retail `.build.info` and `data/casc/data`:

```powershell
python scripts/runtime.py build "C:\Games\Overwatch"
python scripts/runtime.py check --complete
```

For extraction, install **Git**, **Python 3.12+**, **Rust (`>= 1.85.0`)**, **PowerShell 7** (used when extracting the collision index; launching works with built-in PowerShell 5.1 too), the **x64 .NET 8 SDK**, and the **x64 .NET 6 runtime** (used by DataTool). The first build fetches pinned open-source tools and their build dependencies (using the tested `cedec602` revision of the `python` branch for repeatable output) and caches every extraction stage locally. A full native check builds the server with Cargo on first use, or uses the binary named by `ORPHEUS_SERVER_BIN`. For a file and manifest check alone, pass `--files-only`. [Extraction tool setup](../scripts/runtime_tools.py)

| Default Windows directory | Purpose |
| --- | --- |
| `%LOCALAPPDATA%\ORPHEUS\inputs-104319` | Optional local overrides or additional maps/voice catalogs (not required for a fresh Practice Range build) |
| `%LOCALAPPDATA%\ORPHEUS\build-cache` | Public extraction tools and reusable per-stage build cache |
| `%LOCALAPPDATA%\ORPHEUS\runtime-104319` | Finished pack, published atomically after verification |

Pass `--inputs <directory>` to reuse existing overrides or additional maps, `--output <new-directory>` to choose a destination, or `--cache <directory>` to relocate the cache. An existing destination is checked in place. The check reports missing stock attachment positions or bone transforms by body ID. A legacy pack can still pass server preflight. To fill its model data, pass that pack as `--inputs` and choose a new `--output`. The build command requires all 32 default models and the entity definitions referenced by persistent projectile volleys. Keep the installation, inputs, cache and destination in separate directories outside the checkout.

### Automatic generation on a fresh machine

When `--inputs` is empty or absent, `scripts/runtime.py build` automatically produces every required runtime file locally:

1. **PE Message Schemas (`data/schemas_174.json`, `data/game_schemas_174.json`)**: extracted offline from `_retail_/Overwatch.exe` (`scripts/runtime_pe_schemas.py`).
2. **CASC Resource Keys (`data/resource_keys_174.json`)**: extracted from local `090` (`STUResourceKey`) assets (`scripts/runtime_resource_keys.py`).
3. **Catalogs, Collision, Graphs, Rulesets & Stock Models**: hero/item/map catalogs, Practice Range movement & ray collision caches (`collision/0688.col`, `collision/0688.rays.col`), StateScript graphs with persistent projectile definitions and `0C5`/`003`/`01B` metadata, Practice Range `0C6` rulesets, and 32 stock hero reference skeletons & hardpoints (`scripts/tools/model_anchors/README.md`).
4. **Clean-Room Protocol Tables & Signing Key**: Battle.net RPC descriptors (`bnet/descriptors.pb`), 104319 StateScript wire codecs (`data/statescript_codecs_174.json`), schema-conformant lobby message templates (`data/retail_templates.json`), and a fresh local 64-byte `data/lobby_state_blob.key`.

Any matching file you place in `--inputs` overrides the corresponding auto-generated output. Keep exports, game files and the finished pack private.

An older pack with only hardpoint positions still works with `python scripts/runtime.py check <pack>`. A bone lookup in that pack returns unknown. To add the skeletons, build into a new directory using the older pack as input.

```powershell
python scripts/runtime.py build "C:\Games\Overwatch" --inputs "D:\private\old-pack" --output "D:\private\pack-with-bones"
```

Verification checks the stock graph loader and reports unresolved graph references. The tested 104319 installation and previous prepared packs both lack `0F9C` (it is absent from retail 104319 CASC). A shared script (`0x01C7`) references it from a message handler; the runtime starts cleanly without this graph.

On a supported server platform, use `python scripts/runtime.py check` for the default pack or `python scripts/runtime.py check <pack>` for a custom output with Python and the native Rust server or its build toolchain. This verifies the manifest hashes and the Practice Range graph data, then runs native preflight (`--check-runtime-graphs`). Add `--complete` to require the same stock models, projectile definitions, localized voice catalogs, status scripts, and collision caches as a fresh build, or `--files-only` to skip the native build and preflight. A missing item returns a nonzero exit code with its name. It does not start the game. Set `ORPHEUS_RUNTIME_DIR` to the verified pack for prepared-runtime tests. On Windows you can then launch with the [setup script](../scripts/setup_and_play.ps1) or [launcher](../scripts/PLAY_ORPHEUS.cmd).

---

## Native preflight and manual packing

To run the native preflight stage alone:

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\authorized-base-runtime'
cargo run --locked --bin orpheus_server -- --check-runtime
```

> [!IMPORTANT]
> `--check-runtime` only verifies the 22 required filenames, map header metadata, and the 64-byte `data/lobby_state_blob.key`. It does **not** fail if `data/game_graphs_174.json.gz` was exported without mode scripts/variables (`0C5`), hero `target_tags` (`003`), or `mode_rulesets`.
>
> A bare graph bundle (e.g. ~388 graphs, `0/32` bodies with `target_tags`, no `mode_scripts`/`mode_variables`) will pass `--check-runtime` **but will reject hero spawns with StateScript initialization faults and stick clients on the "Assemble Your Team" / hero-select screen.**

### Manual packer for prepared source data

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
| `data/collision_props_174.json` | Local map collision-prop placements and mode filters from the gameplay extraction stage |
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

### 3. Private Local Inputs (Keep Confidential)

| Runtime File | Role |
| --- | --- |
| `data/resource_keys_174.json` | Content keys for event resources |
| `data/lobby_state_blob.key` | Exact 64-byte lobby handshake signing key |
| `data/junkenstein/junkenstein-npcs.json` | Explicit NPC definitions required for Junkenstein enemy tests/runs |
| `data/combat_hurt_volumes.json` | Optional local combat regions measured per body, packed and hashed when present. Top-level body regions are standing measurements. A body may supply a `crouched` object with its own `body` and `head` regions. |
| `data/combat_voice_174.json` | Optional local multikill and actor hurt/death voice catalog, packed and hashed when present |
| `data/player_voice_174.json` | The build command generates hero and skin voice sets, StateScript criteria, equipped stimuli and locally readable clip durations. It reads the installation's text and speech manifests. Lines with an independent StateScript condition are recorded separately in `state_conditioned_sets` until the server supports that condition. |
| `data/voice_effects_174.json` | The build command generates the voice-unlock to playback-effect mapping alongside the actor catalog. Existing packs retain their verified local files. |
| `data/status_scripts_174.json` | Optional status-to-StateScript index derived from the on-apply field of each local `.053` definition. Contains script indices only. |
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

### Model attachment and bone coordinates

The graph bundle can carry model-space hardpoint positions for each body and skin.
For a local 104319 model export, resolve each hardpoint's local translation through its
parent bone's world matrix in the model's reference pose. Include the complete hardpoint
inventory for that model. Omit a coordinate if its parent bone cannot be resolved.
Use the stock skin key only for the stock model. A model-space reference position
describes a static pose. Animated attachments require a bone pose for the sampled frame.

The exporter writes two local files. `stock-model-anchors.json` contains hardpoint membership and positions by body and skin. `stock-skeletons.json` contains the bone indices and model-space reference transforms. The importer records each bone once under its exact body and skin. The runtime uses the 32-bit skeleton index of a typed bone GUID and applies the row-vector transform to a local offset in metres. A missing skin, bone or transform returns unknown. [Synthetic skin record](../fixtures/behavior/owner-model-hardpoints-v1.json)

The unified installer imports both files. To enrich a graph bundle manually, use the exports from the same 104319 installation.

```powershell
python -B scripts/build_runtime_state_metadata.py --graph-bundle .\game_graphs_174.json.gz --model-hardpoints .\stock-model-anchors.json --model-skeletons .\stock-skeletons.json --output .\game_graphs_with_models.json.gz
```

Pass the new bundle to `prepare_server_runtime --game-graphs`. Keep measured model data in the private runtime.

For the prepared regression, set `ORPHEUS_RUNTIME_DIR` to the new pack and `ORPHEUS_MODEL_SKELETONS` to the matching local `stock-skeletons.json`. The test compares imported bone origins and transformed local attachment offsets with the exporter's model-space positions.

```powershell
cargo test --locked -p orpheus-statescript --test model_reference_bones -- --ignored
```
