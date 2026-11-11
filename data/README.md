# Runtime data

Prepared manual NPC catalogs may include a complete `mover` object from each
body's authored `STUCharacterMoverComponent`. `tools/extract_pve_npcs.py` exports
it with the existing source fingerprints. The explicit definition supplies NPC
combat capsule dimensions and movement values when the canonical hero table
does not contain that body. A partial or nonfinite mover object is rejected.
Older catalogs without this field retain their existing fallback behavior and
do not establish authored NPC hitbox or movement fidelity.

The server and its tests use the following data sets. The public source checkout uses separately prepared runtime data. See [separate deployment data](../README.md#separate-deployment-data).

| File | Purpose |
| --- | --- |
| `schemas_174.json` | Layouts of 707 lobby messages in 87 protocol groups. |
| `retail_templates.json` | Decoded reference-server templates, including catalogs and prices; display-name markers are anonymized. |
| `extracted_items.json`, `extracted_general_unlocks.json` | Cosmetic names, types, rarity, categories and rewards. |
| `extracted_heroes.json` | Hero identifiers and names. |
| `extracted_maps.json` | Map metadata and seasonal variants. |
| `ai_spawners_174.json` | The training bots: each map's AI spawners (hero, team, place, route) and hint points, and the bot heroes' bodies, health and speed (`tools/extract_ai_spawners.py`). |
| `resource_keys_174.json` | Game content keys used by event resources. These are not account credentials. |
| `lobby_state_blob.key` | Local 64-byte signing input for the target build's lobby handshake. |
| `extracted_events_174.json` | Verified scene mappings used by regression tests. |
| `announced_crcs_174.json` | Protocol identifiers used by the schema-extraction tool. |
| `arcade_cards_174.json` | The Arcade's cards (`tools/extract_arcade_cards.py`). |
| `game_schemas_174.json`, `game_schemas_174.txt` | Layouts of the game link's messages (`tools/dump_schemas.py`). |
| `game_heroes_174.json` | Each playable hero's GUID, name and body (`tools/extract_game_heroes.py`). |
| `game_maps_174.json` | Each map's modes, server-owned placeables and spawn points (`tools/extract_game_maps.py`). |
| `game_queues_174.json` | The queue cards: modes, maps and team sizes (`tools/extract_game_queues.py`). |
| `game_graphs_174.json.gz` | The statescript graphs the game server runs (`tools/extract_game_graphs.py`). |
| `game_stats_174.json` | The stats the Tab board lists for each hero and mode (`tools/extract_game_stats.py`). |
| `game_movers_174.json` | Each hero body's character mover values (speeds, jump, gravity, capsule), from `tools/extract_game_movers.py`. |
| `collision_keys_174.json` | Storage keys of each map's collision chunk in build 104319, so the server can read the collision from your own game (`tools/build_collision.py`, cache in `cache/collision`). |
| `collision_props_174.json` | Storage keys of each map's props (fences, crates) and of their models, and which game modes keep which props; added to the same collision cache. |
| `statescript_codecs_174.json` | What the client reads for each statescript state class in a frame (property block and class reader, read in the client code by `tools/extract_statescript_codecs.py` in IDA); the encoder and the test reader use it. |
| `statescript_names_174.json` | Names for `tools/ssq.py`: class aliases (our research catalog and the VM code), the STU fields that hold a variable (OWLib's STU types), logical buttons, the hero select and HUD graphs, each game mode's scripts (the 1.74 0C5 assets) and the variable names the server code uses. |

The raw reference capture was from client 1.68 and was decoded using 1.74 schemas; templates are not a claim that every original live-service feature has been reproduced. The game client, raw captures and process dumps are not distributed here.

### Optional static ray geometry

In the prepared research checkout, use the local 104319 installation to generate the extra ray data for the selected maps.

```powershell
.\.venv\Scripts\python.exe tools\build_collision.py 0688 0CD3 --ray-geometry
```

The tool reads the installation from `game_path.txt` or an executable path given with `--game`. It writes `0688.rays.col` and `0CD3.rays.col` beside the existing movement caches. Runtime preparation copies each selected map's optional ray cache into the external pack and records its SHA-256.

Explicit static queries can use `collision.RayWorld.load(map_guid, mode_guid)`. It verifies the map and original chunk key, then selects props for the requested mode. A missing or mismatched cache produces a load error. Base coverage includes the core chunk and the supported static shapes in the existing prop index. Sphere and capsule props with positive uniform scale use analytic intersections. Nonuniformly scaled round props retain the existing triangle projection.

For a wider static query, use PowerShell 7 and the built DataTool from OWLib revision `7afe5f52706e7002ed8c2717299cd60846f3fa3a`. List unindexed client-made root definitions, export their local records and build the selected ray caches.

```powershell
$game = 'C:\path\to\Overwatch'
$dataTool = 'C:\path\to\owlib\DataTool\bin\Release\net6.0'
$records = 'C:\path\to\local-storage\ray-props.json'
$roots = @(.\.venv\Scripts\python.exe tools\build_collision.py 0688 0CD3 --list-unindexed-props | ConvertFrom-Json)
& ./tools/export_casc_records.ps1 -GameRoot $game -DataToolDir $dataTool -AssetGuid $roots -IncludeRootModels |
    Set-Content -LiteralPath $records -Encoding utf8
.\.venv\Scripts\python.exe tools\build_collision.py 0688 0CD3 --ray-geometry --prop-records $records
```

The exporter verifies the selected definitions and models against the local content manifest, including both the container and the slice for bundled records. The cache builder reopens those records through CASC and resolves authored root model references in placeable order. It records the placement content key and the catalog's SHA-256 so changed inputs trigger a rebuild. The runtime pack uses the resulting cache without the local catalog. Attached child geometry, server-generated and dynamic objects, and authored ability filtering still need verification. Keep catalogs and generated caches local.

### Local raycast filter and receiver metadata

Use the build 104319 DataTool XML dumps for asset types `097` and `098` and the same pinned OWLib checkout. Keep the dumps and the JSON catalog outside the repository.

```powershell
.\.venv\Scripts\python.exe tools\extract_raycast_metadata.py 'C:\path\to\xml' --owlib 'C:\path\to\owlib' --output 'C:\path\to\local-storage\raycast-metadata.json'
```

The export checks that receiver overrides reference present filters. It retains ordered rules and references, distinguishes unset results from zero flags, and records a SHA-256 for each source XML file. Runtime interpretation of the rules and receiver results still needs separate evidence.

### Local score event metadata

Export the build 104319 `003F.054` settings from a DataTool `054` XML dump with the pinned OWLib checkout. Keep the output outside the repository.

```powershell
.\.venv\Scripts\python.exe tools\extract_game_stats.py 'C:\path\to\xml' --owlib 'C:\path\to\owlib' --score-events 'C:\path\to\score-events.json'
```

The export records the source hash, ordered label rows and `StatEventScoreScaler` rows. Event enums use the numeric values from the pinned types. Label rows retain text resource IDs, duplicate event aliases and anonymous fields for comparison with the original asset. The catalog gives event configuration. Thresholds, score decay, kill credit, and the message that carries an award to the HUD still require separate evidence.

### Additional statescript definitions

`tools/extract_pve_npcs.py` also preserves a body's optional `m_8B9A461F`
linked control definition as `controller`, with its initial script-component
evidence and input fingerprints. `npc.load_catalog` validates the full003 GUID;
manual spawn retains it in metadata. This records authored input, not mounted
AI/controller behavior. Prepare those linked definitions separately with
`tools/extract_graph_shard.py --entity <index>` before implementing a control host.

Use the build 104319 `003` and `01B` XML dumps and the pinned OWLib checkout to include additional entity definitions in a separate local graph bundle. Repeat `--entity` for each hexadecimal `003` asset index.

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\extract_game_graphs.py --xml 'C:\path\to\xml' --owlib 'C:\path\to\owlib' --entity 1403 --entity 143E --output 'C:\path\to\entity-graphs.json.gz'
```

The bundle's `entities` table holds the added definitions by full asset GUID. Their initial graphs retain definition order and use the same graph reference traversal as the hero catalog.

For an immutable existing runtime, `tools/extract_graph_shard.py` accepts
`--base <runtime>/data/game_graphs_174.json.gz`, the same `--xml`/`--owlib`,
repeated `--entity` and optional `--graph` indexes, and an external `--output`.
It writes entity definitions and only otherwise missing graph definitions,
with input SHA256 fingerprints. Set `ORPHEUS_EXTRA_GRAPH_BUNDLES` to the separate
JSON or gzip JSON file; multiple paths use the platform path separator (`;` on
Windows). This opt-in loader refuses differing known graph/entity records and
does not replace hero, map or mode records. Restart to change loaded inputs.

Add `--mode-xml 'C:\path\to\xml\0C5'` to the same extraction command to include the initial mode entity variables from build 104319. The output must be outside the repository. The server resolves these members by mode GUID, independently of the hero and mode schema defaults. Supply the bundle through `--game-graphs` when preparing the runtime.

To serve the bot spawn and death graphs from a prepared pack, include the six Practice Range bot body indexes `1403`, `143E`, `146E`, `14A9`, `14D2` and `14ED`, then pass the generated bundle to the runtime preparation tool. Point `ORPHEUS_RUNTIME_DIR` at the new pack when starting the server.

```powershell
.\.venv\Scripts\python.exe tools\prepare_server_runtime.py --source 'C:\path\to\local-research' --destination 'C:\path\to\orpheus-bot-runtime' --map 0688 --game-graphs 'C:\path\to\entity-graphs.json.gz'
```

For offline Symmetra placement direction verification, use
`tools/compare_placement_axes_native.py --image <authorized-loaded-image> --base <module-base> --output <external-report>`.
Unicorn must be available from a separate research environment. The tool runs
original evaluator/basis/view arithmetic with documented engine and trig ABI
adapters, fingerprints the input and refuses reports inside source or over the
input. It does not launch a client or accept placement collision/actor behavior.

Projection math can be checked with `tools/compare_placement_projection_native.py`
using the same `--image`, `--base` and external `--output` arguments. It compares
original polynomial return words, native late-block retry directions and contact
refinement under declared pose/query/contact/clearance services. The final
candidate validator explicitly rejects in refinement profiles. Actual casts,
candidate validation, geometry-owner
admission and actor deployment remain outside this verification scope.

`tools/compare_placement_samples_native.py` uses the same external image/base/output
arguments to compare C9C760/C9E800 sample queries, ordered status checks and output
metadata, plus CA5550's declared static/no-body-sentinel fallback. Cast, clipping
and final geometry replies remain adapters. Dynamic body/mesh/material handling
and actual placement are outside this report's scope.

`tools/compare_placement_candidates_native.py` compares original CA0E50 count,
sampling-loop and height-selection blocks independently. It uses declared sample,
contextual predicate and coordinate-transform services plus preallocated arrays.
It verifies request order and output metadata, including both success-shortening
flag values. It does not verify complete CA0E50, real geometry or allocation failure.
It also verifies post-contact C90212 minimum-range correction before CA0E50.

`tools/compare_placement_camera_native.py` compares original1A77F80 projection
instructions and their use in the original candidate selection loop. Resolved
camera matrices/mode/viewport and decoded-key lookup are fixtures. Output/depth
float32 words and below-guard no-write behavior are checked. Camera/basis/FOV
construction, real key/display services, actual geometry and placement remain open.
The same tool also compares default perspective construction against1A86910 and
1A86F30, retaining the actual mirrored-x coefficient and separately clamped angle
metadata. View-matrix construction and nondefault shift/scale/reverse-depth remain
outside the accepted builder profile; sinf/cosf ABI values are explicit adapters.
Resolved decoded-pose view construction is also compared with1A77779..1A778F3/
974260, including constructed-camera selection. Input sanitization, owner binding,
encoded key services and physical reciprocal-estimate behavior remain separate.

`symmetra_placement.py` mounts an explicitly reconstructed static preview host
for156B/57 and1571/69. It consumes prepared collision read-only and authored
range/sampling/output fields; it does not write caches or modify graph bundles.
Its line casts, no-clip policy, ground predicate and constructed camera are
server policies, not proof of native swept/footprint/filter/camera parity.
The static/null-parent156B position bridge accepts the resource integer0 and
typed Entity(0); unknown, dynamic-parent and bone transforms remain unsupported.
Actual-input confirmation, Pet deployment and effect acceptance remain open.

Photon Barrier's subsequent server host requires26B0 in the explicit actor shard
and156C in the prepared graphs before owning156B/57 confirmation. It reads4000HP
from26B0/0033,12s from156B/9/567 and the30s watchdog from156C/42. The actor's
ordinary lifetime is measured from server world creation, independent of the VM
clock origin. The wall uses a reconstructed unbounded vertical plane;6880 is a
model resource identifier, not a proved width. Native mesh/filter/Pet prediction
and client presentation are unaccepted. No runtime inputs are modified. Whole
projectile activation absorption leaves same-side explosive splash unaccepted;
the full ability/hero interaction matrix and teleporter deployment remain open.
