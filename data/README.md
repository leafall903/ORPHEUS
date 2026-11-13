# Runtime data

The Rust developer preview needs a separate, authorized **base 104319 Practice Range** runtime. Public source tests use synthetic data. For native startup, run the same preflight used by the server before it opens listeners.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\authorized-base-runtime'
cargo run --locked --bin orpheus_server -- --check-runtime
```

The check inventories 22 required paths, including `collision/0688.col`. It reports file presence and the signing input length. The server parses the prepared data at startup. A complete original-client connection requires separately authorized local prerequisites. See [native startup](../README.md#separate-deployment-data).

## Tool outputs from authorized local inputs

| Prepared pack paths | Prerequisites |
| --- | --- |
| `data/extracted_items.json`, `data/extracted_general_unlocks.json`, `data/extracted_heroes.json`, `data/extracted_maps.json` | Catalogs built with local build 104319 exports. |
| `data/arcade_cards_174.json`, `data/game_queues_174.json` | Local game exports and prepared retail templates for queues. |
| `data/ai_spawners_174.json`, `data/game_heroes_174.json`, `data/game_maps_174.json`, `data/game_movers_174.json`, `data/game_stats_174.json`, `data/game_graphs_174.json.gz` | Local gameplay exports. Some tools also need pinned types or prepared map indexes. Use the base graph bundle for native Practice Range. |
| `data/collision_keys_174.json`, `collision/0688.col` | Local map storage indexes and a movement cache built for Practice Range with the prepared placement index. |

The Rust `prepare_server_runtime` command assembles existing authorized inputs. Its output is a separate directory with a SHA-256 manifest. The generation tools have their own local prerequisites.

```powershell
cargo run --locked -p orpheus-runtime --bin prepare_server_runtime -- --source 'C:\path\to\prepared-source' --destination 'C:\path\to\orpheus-runtime' --map 0688
```

## Bring prepared research data

Provide the remaining protocol and scene research files in the pack.

| Prepared pack paths | Purpose |
| --- | --- |
| `data/schemas_174.json`, `data/game_schemas_174.json`, `data/statescript_codecs_174.json` | Prepared lobby, match and StateScript layouts. |
| `data/retail_templates.json` | Prepared retail lobby response templates. |
| `data/collision_props_174.json` | Prepared placement and mode filters. |
| `bnet/descriptors.pb` | Prepared protobuf descriptors for the local service. |

## Blocked without restricted local inputs

| Prepared pack path | Format |
| --- | --- |
| `data/resource_keys_174.json` | JSON content-key table. |
| `data/lobby_state_blob.key` | Exactly 64 raw bytes. |

Keep both files and their source locations private. The packer records per-file SHA-256 checksums in the local manifest. Public issues, releases and community channels may record format and validation requirements. Keep raw data, access instructions and locations out of public materials.

## Reference inventory and research notes

The following records document the research tools and Python reference behavior. The named tools live on [`main`](https://github.com/leafall903/ORPHEUS/tree/main/tools); run the Python tool commands below from a `main` checkout.

| File | Purpose |
| --- | --- |
| `schemas_174.json` | Layouts of 707 lobby messages in 87 protocol groups. |
| `retail_templates.json` | Decoded reference-server templates, including catalogs and prices; display-name markers are anonymized. |
| `extracted_items.json`, `extracted_general_unlocks.json` | Cosmetic names, types, rarity, categories and rewards. |
| `extracted_heroes.json` | Hero identifiers and names. |
| `extracted_maps.json` | Map metadata and seasonal variants. |
| `ai_spawners_174.json` | The training bots: each map's AI spawners (hero, team, place, route) and hint points, and the bot heroes' bodies, health and speed (`tools/extract_ai_spawners.py`). |
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

In a `main` checkout, use the local 104319 installation to generate the extra ray data for the selected maps.

```powershell
.\.venv\Scripts\python.exe tools\build_collision.py 0688 0CD3 --ray-geometry
```

The tool reads the installation from `game_path.txt` or an executable path given with `--game`. It writes `0688.rays.col` and `0CD3.rays.col` beside the existing movement caches. Runtime preparation copies each selected map's optional ray cache into the external pack and records its SHA-256.

In the Python reference on `main`, explicit static queries can use `collision.RayWorld.load(map_guid, mode_guid)`. It verifies the map and original chunk key, then selects props for the requested mode. A missing or mismatched cache produces a load error. Base coverage includes the core chunk and the supported static shapes in the existing prop index. Sphere and capsule props with positive uniform scale use analytic intersections. Nonuniformly scaled round props retain the existing triangle projection.

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

Use the build 104319 `003` and `01B` XML dumps and the pinned OWLib checkout to include additional entity definitions in a separate local graph bundle. Repeat `--entity` for each hexadecimal `003` asset index.

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\extract_game_graphs.py --xml 'C:\path\to\xml' --owlib 'C:\path\to\owlib' --entity 1403 --entity 143E --output 'C:\path\to\entity-graphs.json.gz'
```

The bundle's `entities` table holds the added definitions by full asset GUID. Their initial graphs retain definition order and use the same graph reference traversal as the hero catalog.

Add `--mode-xml 'C:\path\to\xml\0C5'` to the same extraction command to include the initial mode entity variables from build 104319. The output must be outside the repository. The server resolves these members by mode GUID, independently of the hero and mode schema defaults. Supply the bundle through `--game-graphs` when preparing the runtime.

To serve the bot spawn and death graphs from a prepared pack, include the six Practice Range bot body indexes `1403`, `143E`, `146E`, `14A9`, `14D2` and `14ED`, then pass the generated bundle to the runtime preparation tool. Point `ORPHEUS_RUNTIME_DIR` at the new pack when starting the server.

```powershell
cargo run --locked -p orpheus-runtime --bin prepare_server_runtime -- --source 'C:\path\to\local-research' --destination 'C:\path\to\orpheus-bot-runtime' --map 0688 --game-graphs 'C:\path\to\entity-graphs.json.gz'
```

### Junkenstein NPC mover inputs

For a Junkenstein runtime, include the explicit external
`data/junkenstein/junkenstein-npcs.json` catalog and the authorized build
104319 body `003` XML directory. Runtime preparation with
`--target-tags-xml 'C:\path\to\xml\003'` enriches both entity metadata
and the selected NPC character movers. It reads only catalog-selected bodies,
preserves other mover records, validates required numeric fields and capsule
sizes, and hashes the generated mover table in the output manifest. Missing
or malformed selected XML fails before creating the output directory.
Existing packs can be supplied as `--source`; always use a new destination.
The source pack and XML remain unchanged. Junkenstein scene preparation
requires authored common-NPC movers rather than the hero fallback.
