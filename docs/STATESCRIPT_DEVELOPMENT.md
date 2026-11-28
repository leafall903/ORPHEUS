# StateScript actions: development and measurement

Use the current `rust` module boundaries when extending the VM. This guide integrates useful verification work from the older local foundation experiment without replacing the subsequently merged implementation.

## Shared Rust integration

Use `work/statescript-integration` for all local integration updates.
Phase 2 and Phase 3 keep their existing worktrees for development and
hand off commit IDs, scope, and known risks. The integration maintainer
merges those commits, resolves conflicts, validates each shared update,
and owns actor admission and gameplay diagnosis. Run checks once on the
shared candidate.

| Working branch | Scope |
| --- | --- |
| `work/phase2-generic-vm` | Shared VM primitives, mode rules, voice and message dispatch |
| `work/statescript-local` | Phase 3 targeting, movement and owner projection |
| `work/statescript-integration` | Integration, verification, actor admission, and gameplay fixes |

Run the workspace checks and prepared input and startup regressions on
the combined source before treating it as the next shared baseline.
Keep the previous verified revision available in Git history.

## Execution scope

The VM uses each node's declared execution scope for graph traversal, startup
and message scheduling, and direct dispatch. A client-only node does not run
server actions or start server states. Server-only nodes can execute on the
server while remaining absent from network projection.

Graph IDs within a bundle and node positions within a graph must be unique.
Repeated references in legacy entry, state and synchronization tables
keep their original order. Queued messages check the resolved node's
execution scope before binding parameters or following its output.

## Load runtime state classifications

A prepared graph bundle may include `runtime_state_metadata`. The
`plain_classes` list names ordinary runtime state classes. It applies to
nodes with a reciprocal state slot and no explicit behavior override.
VM handlers and their existing guards run before the plain fallback.
For legacy bundles, the loader also records the 104319 inherited base
factory classification for `STU_F08E1453` through the same path.
[Pinned factory research](https://github.com/leafall903/ORPHEUS/blob/c73e5a499ec73d499c97eaae46357af30c9e4b39/docs/ENTITY_CONTROL_STATE.txt#L109-L117)

The `client_states` list identifies instances by graph index, state
slot, RID, and raw class. All four must match. A client classification
skips execution while preserving the raw class, scope flags, and
owner and observer slot tables. A class may have different scopes in
different graphs.

An authored bundle can declare `projected_message_topics` in this metadata.
Each topic is a unique, nonzero literal message ID. The loader requires a
matching static client receiver and rejects a matching server receiver
across all loaded graphs. The declaration reserves the topic in the VM's
message dispatcher. An eligible fixed-self sender with reciprocal state
and remote slots in a networked instance uses the ordinary
state lifecycle and existing `send` codec. Its parameter records stay in
the graph for client use. Server evaluation and delivery are skipped.

```json
{
  "runtime_state_metadata": {
    "projected_message_topics": ["0x02400000000000F1"]
  }
}
```

Verified wire codecs still come from the separate codec table. A plain
classification alone supplies no codec or payload layout. The portable
contract exercises ordinary lifecycle and scope. Native tests verify
owner and observer bytes and missing-codec diagnostics.
See the [synthetic runtime state contract](../fixtures/behavior/script-runtime-state-metadata-v1.json).

Use the offline importer with a local graph bundle and evidence archive
or extracted evidence directory. It writes a separate gzip bundle.
Keep both source data and the generated bundle in your private runtime
workspace.

```text
python scripts/build_runtime_state_metadata.py --graph-bundle path/to/game_graphs_174.json.gz --evidence path/to/classification.zip --output path/to/new/game_graphs_174.json.gz
```

For the Practice Range firing probe, use an evidence directory with one
listing each for placement graph `0B92` and weapon graph `0BA3`. Map
archives can contain identical graph indices in several map folders.
Select the matching listings before importing. Each bot reads its
placement roots from the map spawner. The server privately mounts roots
with a supported weapon input state when the body supplies a usable
slot binding. The body's ordinary logical-button dispatcher receives the
input. The headless probes check press, release, and projectile flight admission.
Flight uses the room's existing hostile-target and collision queries.
A controlled room fixture verifies damage to a live bot and a player
while a same-team target remains unharmed. A prepared-room replay also
finds an opposed firing lane from the actual map spawns and places a
player 1.5 metres behind its front bot. With the front bot alive, that bot
takes the hit. If the front bot dies after the shot launches, the player
behind it takes the hit. The replay checks observer projectile completion
on the same simulation tick as each impact.
The earlier spawn-reset and button lifecycle are documented in the
[Python bot host](https://github.com/leafall903/ORPHEUS/blob/299b44111fc3e3dddcc5a691859b7a6c3ac661a6/src/ow174/game/script/bot.py).

Replacement stock bots receive their authored reset on the new body
before observer publication. The prepared probe checks that the
spawn animation is present in the replacement's first encoded
observer snapshot. For weapon effects, an instant volley can already
be off when the server captures the frame. Its activation identity
remains available for the observer's projectile launch record.

For authored predicted projectiles, observer delivery retains the exact
volley activation while the launch can be retried. It creates the
visible projectile entities after the source baseline is acknowledged,
then sends the channel 2 launch after both those creates and the
matching volley projection are acknowledged. Interrupted delivery
retries the same record and entity IDs. The room's flight completion
drives retirement. Acknowledged volleys retire projectiles individually
at the launch send frame plus each server flight duration. While the
launch acknowledgement is outstanding, the observer keeps the referenced
entities available for retries until every flight in the volley is due
for retirement. At that point it queues their destroys and discards
further launch retries. The source, flight definition, and shot timing
come from the authored volley and shared simulation.

A single-shot activation can start and end within one VM tick. The
shot report captures active remote data-flow inputs sourced from the
volley's own child states before their links are removed. Observer
delivery retains those values with the corresponding volley until the
launch is acknowledged or expires. The retained snapshot sends the captured
values as literals and drops competing live bindings for those variables.
The ordinary projection sends the live bindings again after retirement.
The VM still clears its own stack
claims at the authored time. The prepared firing probe verifies the
stock firing input on both teams. Rendered attacks remain an
original-client acceptance check.

## Supply model hardpoint membership

An external graph bundle can include `model_hardpoints` in a body entry.
Use one record per selected skin with `skin` and `hardpoints` fields.
The zero skin GUID denotes the stock selection. Each list contains the
complete hardpoint keys for that selected model. An empty list declares
that the model has no hardpoints. An omitted record keeps the query
unavailable for that skin, even if the stock model has a record. Use
this data only when the model seen by the spawning body is known.
The server reads membership for the current owner body and rebuilds the
binding when the body is replaced. A synthetic example lives in the
[owner model contract](../fixtures/behavior/owner-model-hardpoints-v1.json).
Supply the enriched bundle through `prepare_server_runtime --game-graphs`.

## Supply authored asset categories

The optional graph bundle field `asset_categories` maps each asset GUID
to a list of category records with `type` and `id` GUIDs. The evaluator
matches the complete pair from the node operand. An absent catalog leaves
the category query unavailable. For a supplied catalog, an asset without
a matching pair returns false. Keep the extracted catalog in the private
runtime bundle. The [wheel input contract](../fixtures/behavior/wheel-selection-ingress-v1.json)
uses synthetic input and generation changes. The build-specific wire layout
and category fields are documented in the [Python reference](https://github.com/leafall903/ORPHEUS/blob/499b9871b1ff9bf3d86715b7648626b18b154f0d/docs/COMMUNICATION_WHEEL.txt).
The live owner accepts context-free wheel selections from a living,
acknowledged body when the selected asset has authored category data.
It passes the selection and confirmation flag straight to the body's
authored handlers. The contextual target marker still requires native
validation. The `TrackTargets` view shape
passes its origin, offset, radius, aperture, limit, filter masks, ranking and
sight profile to the room. A frozen room with no other living player bodies
returns an empty result. Bots may obstruct sight when the supplied profile
includes them as blockers.

For local validation, the optional graph bundle field `view_sight_profiles`
maps sight-profile GUIDs to records with `static_mask`, `block_actor_flags`,
and `samples`. The static mask selects collision categories. Actor flags
select blockers from the room snapshot. Each sample is a fraction of a
target's body height in the range 0 to 1, with up to eight samples per
profile. The supplied actor flags describe the room's actor filter.
The raw dynamic collision mask from a sight asset requires a separate
receiver mapping before it can be used here. Missing profiles or required
room facts leave occupied queries unavailable. The public
[view target fixture](../fixtures/behavior/script-view-targets-v1.json)
exercises this interface with synthetic settings and geometry.

The prepared `prepared_wheel_packet_bindings_reach_both_authored_handlers`
probe compares the same selection with a missing room snapshot and with a
frozen single-player snapshot. It uses a local 104319 graph bundle with
synthetic room geometry. With the asset categories supplied, the solo
case passes the view query and evaluates the selected action's localized
parameter row. `STU_7757D403` evaluates each pair's value and key in authored
order. Expression opcode 19 collects its stack pairs into an ordered
integer-keyed parameter map. A networked `STU_7106C356` uses the authored
`m_BF5B22B7` scope rows to capture its effective variables. The owner
and observer streams send the captured values with the short action.
An unacknowledged action keeps its initial stamp and values when a later
selection arrives. A networked `STUStatescriptActionEffect` with a literal
effect and an authored owner selector uses the same short-action stream.
The client graph supplies the effect asset and evaluates its authored
DataFlow bindings using the synchronized variables. The prepared 104319
shared combat graph declares remote slots and synchronized parameters for
its hit effects. Original-client playback of these actions remains to be
verified. Full baselines omit prior callouts. The client retains
its existing text rendering and audience checks. `STU_1ECB66A0` looks up
the complete communication action GUID in the optional `asset_textures`
catalog. A missing key in a supplied catalog returns a zero texture
reference. If the catalog is absent, the VM reports the lookup as
unavailable. The 104319 PC registration list has 1,735 entries.
`STU_631D36C3` (typeinfo `2DF7B50`), `STU_71FE9852` (`2DE36F8`)
and `STU_94771561` (`2DF3C18`) first match the same parent Action
`2DD5430` at record 165, with constructor `1B72650`. The shared path
follows the authored output plug without reading cosmetic fields.
Sight profiles for occupied rooms and original-client presentation remain open.
The pair and opcode behavior follows the [pinned Python reference for pairs](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/src/ow174/game/script/play.py#L81-L93)
and [expression maps](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/src/ow174/game/script/expr.py#L832-L835).
The synchronized variable capture follows the [pinned Python frame-action reference](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/src/ow174/game/script/driver.py#L781-L811).

Use `scripts/build_runtime_state_metadata.py --asset-categories` with a
local JSON catalog containing `version: 1`, `build: 104319`, and the asset
map under `assets`. Supply the resulting bundle through
`prepare_server_runtime --game-graphs`.

Use `--asset-textures` on the same script to add a local action-to-texture
catalog. It takes the same version and build and maps full action GUIDs
to full texture GUIDs under `assets`. The public fixture uses synthetic
keys. The native resource shape and offline checks are described in the
[pinned wheel research](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/docs/COMMUNICATION_WHEEL.txt#L537-L543).

### Emote preload lifetime

The prepared wheel replay observes a separate cosmetic preload graph.
Interrupting the emote releases its aim lock and retires the emote, while
the preload remains active. The next selection replaces it through the
authored PlayScript policy and the replay verifies that only one preload
remains.

The earlier [wheel research](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/docs/COMMUNICATION_WHEEL.txt#L261-L265)
associated the preload with the exit action by comparing two numeric
values. The build 104319 schema declares the exit operand of
`STU_3D419B63` as a reference to `STUVoiceCategory`. Handler `CB9C70`
sends it and each resolved entity to the voice service. The other value,
`m_7E0BC920`, belongs to the nested editor `STUGraph` and tracks above
the numeric editor item IDs. Treating it as a runtime voice category
would match unrelated graphs. The native preload release condition
after interruption is still open.

## Supply actor voice criteria

Place the optional catalog at `data/player_voice_174.json` in the private
runtime source before preparing the server runtime. Its `criteria` map
contains each criterion's ordered stimulus IDs. The `heroes` map identifies
the base set, `skins` maps a skin's base sets to replacements, and `sets`
maps each voice set to its stimulus and line lists. A criterion plays the
first available stimulus in the selected set. A mapped skin replacement
controls playback for that set. Missing entries leave the action silent.
[Reference catalog extractor](https://github.com/leafall903/ORPHEUS/blob/d1676cecd1162983987a48024d7c787544fd3593/tools/extract_player_voice.cs)

The supported cue path reads `m_identifier` and `m_targets` from
`STUStatescriptActionCriteria`. When the target is the acting body, its
living allies and the speaker can receive the cue from the existing room
queue. The catalog contains identifiers only and stays private. The
prepared wheel path still requires target tracking. Original-client playback
remains pending. [Synthetic selection and audience contract](../fixtures/behavior/player-voice-v1.json)

## Build the private evidence reports

Use the graph index, RID, state slot, and raw class as the verified
identifiers for local evidence. Record the input digest and importer
version so two runs can be compared. Keep extracted records private.
Shared tests use original graphs and values.

Use these reports to clear independent actor startup failures first.
Repeat the startup matrix on the same inputs and keep room admission
separate from independent factory results. Then finish the shared
Phase 2 paths and run their regressions before starting Phase 3
ability support.

1. **Field contracts**. Group nodes by capability and class. Record
   field paths, operand types, presence counts, and incompatible shapes.
   Validate candidate contracts against the Rust loader and evaluator.
2. **GameMessage catalog**. Index message IDs, send and receive
   placements, and parameter-to-variable bindings. Record dynamic or
   unresolved routes explicitly.
3. **Timing contracts**. Separate literal values, literal ranges,
   and unknown expressions. Compare observable timer order and frame
   boundaries with a headless trace.
4. **Variable access**. Track read, write, and initialization edges by
   scope. Report missing initializers separately from queries whose
   runtime inputs could supply the value.
5. **Coverage baseline**. Join the graph inventory to a pinned trace.
   Report reached, skipped by scope, unsupported, and unreached nodes
   with enough trace provenance to reproduce the Phase 3 ordering.
6. **Synthetic test scaffolds**. Generate only the verified operand
   shape and lifecycle outline. Use invented values, graphs, IDs and
   expected results derived from the server contract.

### Phase 2 shared VM paths

- The native pitched direction operand reads a bound entity through the
  host pose provider. The existing roll adapter still supplies its
  compatibility vector. A host's explicit no-target reply completes the
  operand before selector evaluation.
- The scene placement collection action uses a literal required tag and
  a scene placement inventory from its host. An empty inventory and an
  unavailable inventory produce distinct results.
- Timed graph states with no completion operands or alternate links use
  parent-owned lifecycle at any placement. The prior configured-duration
  path remains a compatibility case. Configured predicates and alternate
  subgraphs need separate contracts.
- Owner graphs can read `BodyActive` as an activity gate with no hero or
  graph-specific bindings. The prepared scene provides the initial level.
  The room updates the current body by generation.
- Native eye-position operands use the player's simulation eye anchor.
  Native eye and direction expressions with potential foreign selectors
  request a frozen room pose.
- A native local combat accumulator enters as a plain state when its host
  confirms the local service is absent. It leaves spatial operands dormant.
  Hosts that report the service present or return an unknown result still
  produce an unsupported-state diagnostic.

- Player, NPC, mode and gate scripts read native engine settings through
  the mode's authored table or its captured compatibility table.

- Auxiliary view bindings and outline states keep their ordinary
  lifecycle when the host confirms the local presentation context is
  absent. Graph placement is independent of that host fact.
- Dynamic elapsed waits with a direct numeric variable sample the duration
  again on each tick. Reentry with restart enabled cancels the prior
  registration and resets the start time. Ending the state unregisters
  its tick.
- Cinematic boundary projections with a literal integer delay use the
  current component frame at entry and release. The owner and observer
  slots must both refer to the state. Room simulation still uses its
  existing clock.

- A scoped headless player body can report that its native voice context
  is absent. A voice state keeps its lifecycle and uses its verified
  empty codec. Authored room cues use their existing request and
  delivery path.

- Tagged target selection with literal include and exclude operands
  uses the same VM path at any placement. It preserves the host's
  ordered result and reports unavailable scene context.

- The hardpoint membership expression queries a bound entity and a
  literal hardpoint asset. A known model can report presence or absence.
  Unavailable model facts produce a runtime diagnostic.

- The native `STU_9028E035` linked state uses its reciprocal state
  slot and known authored types to enter and leave the shared registry.
  Links cross instances in entry order and skip server-only destinations.
  The self-targeted `STU_43994F1B` and `STU_93B8758C` forms with no
  resource operand use the same registry and keep their own class lists.
  Authored subgraph links still follow the ordinary state lifecycle.
  The linked codec remains the source of wire layout.
- Static model protection requires a reciprocal state and remote slot,
  a single protected-entity rule, and a host that owns the body's model
  mask. Overlapping states retain protection until the last one retires.
  The current encounter host supplies the verified boss-body capability.
- A server-owned `ModifyHealth` state can declare periodic self shield
  recovery through its validated operands. The encounter host restores
  attached shield pools from the live rate on the VM clock. A host without
  this service keeps the admission diagnostic.
- An owner graph can check the active mode script entity through the
  shared query. An available mode table supplies the entity. The verified
  encounter scope can report it absent. The existing voice recipient
  audience and team selectors still need their own routing contracts.
- The native horizontal clamp action accepts one unprioritized selector-1
  row with a zero lower bound. Integer and floating bounds use the same
  schema. A supported host queues the cap for its next movement frame.
  This action preserves vertical velocity and uses the existing mover
  and NPC motion primitives.

- Native `PhysicsExplosion` uses the ordinary state lifecycle when its
  authored operands, visibility and compiled slots match and the room
  confirms that it has no dynamic map rigid bodies. The VM keeps the
  authored transitions, including timers, and uses the existing empty
  state payload. The static path leaves the impulse operands dormant.
  A dynamic-body implementation and original-client acceptance remain open.

- `TrackStat` actions use their typed stat fields and target selectors.
  A confirmed absent owner stats record supports self-target actions.
  A variable target requires confirmation that this VM exposes no stat
  service. The VM leaves the operands dormant and follows the normal
  continuation. Live stat recording still needs a host implementation.

Native target tracking still needs ordered samples, optional typed
output bindings, and the lifecycle of its embedded state slots. The
authored `track_entities` node owns separate child graph instances.
Start with a validated query-only shape and an empty embedded plug.
Preserve the difference between unavailable queries and empty results,
then verify the output lifetime and owner and observer projections.
The other pending paths include migration of the legacy charge bindings,
health effects, spatial queries, control effects, and voice recipients.

Phase 2 still tracks three groups. The common charge route needs prepared
coverage for the stock damage, healing, passive and bonus messages and
migration of the legacy admission bridge. The core graph states still
need the remaining `TrackTargets` and `ModifyHealth` shapes,
dynamic map impulse behavior, and the other linked and timed states. The
handoff's `SpatialQuery` ID, `STU_B5C87F3D`, uses the native local
combat accumulator lifecycle. Its spatial operands stay dormant with
explicitly absent context. Present-context behavior still needs native
evidence. The shared `EntitiesInRadius` expression already queries the
room. Mode settings use the shared mode table. Flashbang control
priority and voice audience routing still have compatibility selectors.
Independent hero startup is a regression for these changes. The
per-hero input and client results have separate acceptance.

The prepared Python ordinary sender trace takes its compatibility branch.
That branch leaves the persistent recipient wrapper unresolved, so it
supplies no evidence of delivery or registration. Keep the existing
persistent registration diagnostic until host policy is modeled or the
topic has a verified client-only receiver set. Use projected message
topic declarations for the latter case.

### Generate the static reports

```text
python scripts/summarize_statescript_evidence.py --evidence path/to/classification.zip --graph-bundle path/to/game_graphs_174.json.gz --families path/to/capability-families.json --output-dir path/to/new-private-report
```

The output directory holds the four static reports and
`evidence-manifest.json`. The manifest identifies the normalized source
and report version. The command accepts an extracted evidence directory
as well as a ZIP. It requires a new output directory.

In `field-contracts.json`, field shapes from confidently attributable
listing constructors appear under `listing`. Missing rendering stays
distinct from field absence.
The optional prepared bundle adds exact matches by graph, RID, slot,
class, and scope. Its field counts use the matched state population.
Prepared shapes include array-element paths and one example per kind.
Use an optional JSON object under `--families` to group raw classes by
capability. Values are arrays of class names.

The `gamemessage-catalog.json` report groups literal IDs and lists sender and receiver
locations, observed parameter GUIDs, low-16-bit slots, and direct
variable bindings. Dynamic and unresolved endpoints retain their own
records. Treat endpoint pairs as candidate routes. Runtime traces
establish delivery. Reports retain message IDs and binding slots.
Other field values are reduced to structural kinds.

The `timing-contracts.json` report identifies direct wait-state operands
by graph, RID, state slot, and scope. It records finite literals and
ordered literal random bounds. Dynamic and unsupported operands keep
their location and classification. A null unit indicates that the
listing lacks an explicit unit. Use VM traces to verify elapsed time
and event order.
The v3 report generator requires exactly one wait operand before inferring
timing. A malformed multi-argument wait stays unresolved.

The `variable-access.json` report records definite reads and write sites,
schema default declarations, direct message bindings, and uncertain
contexts for each graph. It keeps shared slots separate from instance
slots. For a matching prepared graph, the report also lists instance
slots declared by a recognized public schema. A source candidate means
the listing shows a read and lacks a visible value source. Review the
prepared schema, cross-graph input, and runtime ordering before treating
it as a missing initializer. The report omits source expressions and
default values.

### Collect a headless coverage baseline

Run the existing 900-frame owner test with the `script-coverage` feature
and a local prepared pack that includes target tags. The capture tracks
up to 4,096 distinct positions per component starting at VM initialization.
The live Inspector response remains capped at 128 positions per component.
Set a fresh output path and record the full revision used for the run.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\private\runtime'
$env:ORPHEUS_SCRIPT_COVERAGE = 'C:\private\owner-coverage.json'
$env:ORPHEUS_SCRIPT_COVERAGE_REVISION = git rev-parse HEAD
cargo test --locked -p orpheus --features script-coverage --lib stock_soldier_owner_session_idles_fires_reloads_and_sprints -- --ignored
```

Join observations with the exact graph bundle and a local hero catalog
to identify graph position, RID, state slot, and class.

```text
python scripts/summarize_statescript_coverage.py --graph-bundle path/to/game_graphs_174.json.gz --hero-catalog path/to/game_heroes_174.json --observation path/to/owner-coverage.json --output path/to/new-coverage.json
```

The input catalog supplies direct body roots. The report follows declared
`track_entities` child graph links from those roots and any observed
graphs. `static_dependencies` lists those references and their source
nodes. `missing_dependency_graphs` identifies absent targets. A graph
with origin `referenced_child` has a static reference and no observed
site in that scenario. Dynamic graph choices need a capture. Use repeated
`--observation` options for separate runs. With no observation, the report
marks inventory nodes as having no sampled scenario. The report lists each
node's declared
`scope` and effective `server_scope`. Its `runtime_classification` identifies
an exact client state or a candidate for plain lifecycle fallback. Review
scope skips, registration rejections, and unsupported sites separately.
An observed target passed the VM scope check. Missing rows in a capped
observation have unknown coverage. The scripted inputs exercise one hero
and one path. Use scenario-specific captures to prioritize other paths.

### Inspect actor startup readiness

Use the same local prepared pack to check startup across the actor catalog.
The report records available prerequisites, ordinary room admission, and
an independent attempt through the production body factory. Each row
describes setup and initial diagnostics. Ability inputs, subsequent ticks,
and client presentation require their own scenarios.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\private\runtime'
$env:ORPHEUS_ACTOR_STARTUP_REPORT = 'C:\private\actor-startup.json'
$env:ORPHEUS_ACTOR_STARTUP_REVISION = git rev-parse HEAD
cargo test --locked -p orpheus --features script-coverage --lib prepared_practice_actor_startup_matrix -- --ignored
```

Use a new private output path for each run. The report records input
digests and the source revision, and leaves existing output files intact.
In schema version 2, a factory rejection also records its failure stage.
This distinguishes scene prerequisites, root layout, VM startup steps,
snapshot projection, and full snapshot encoding. An incomplete projection
lists a bounded set of typed diagnostics with graph and node positions.
An unavailable entity member read reports `entity_variable_unavailable`.
The row carries the script site and omits the entity ID and variable key.
For an unsupported config diagnostic, `operand_class` records the
bounded identifier from the evaluator when available. The containing
node class remains a separate field. A class identifier shows where
the evaluator attributed the error, including shape and context failures.
The independent factory probe retains these details when the ordinary
room admission reports a generic unsupported error.

An owned-entity count with a valid definition and an explicitly unbound
owner leaves the caller's initial value intact. A bound owner requires a
count supplied by its host. A known empty registry yields integer zero.
An unavailable registry still requires a diagnostic.

A state-managed recipient set reports `unsupported_message_registration`
when its persistent registration path is unavailable. The probe keeps
the failure blocking and records the sender node. The ordinary one-shot
send path remains separate from registration of a persistent sender.

The 49-site prepared review found eight fixed-self senders across two
message topics, including the sole startup blocker for Orisa and Sigma.
The prepared bundle and listing audit found ten matching receivers
marked client-only. The actor graph closures still contain unresolved
references, and the wider bundle contains unresolved server-side message
selectors. The declared projection contract has synthetic coverage for
the fixed-self sender shape and preserves parameter bindings. The existing
prepared pack still reports the eight registration diagnostics.

The report's `probe_roster` declares the synthetic admitted player slots
used by both attempts. In a live room, the player host reads the admitted
roster at startup and refreshes it on departure. The count includes members
whose body is unspawned or dead and excludes NPCs. With a known roster, an
empty team yields zero. Asset-only factory preparation has no room roster.
The roster rule follows the existing Python ready-switch adapter at
`c73e5a49` in `src/ow174/game/match.py`.

### Compare a prepared startup matrix with Python

Run the comparison with a clean Python checkout and its full commit SHA.
Use the same runtime and the Rust report generated above.

```powershell
$pythonReference = 'C:\work\orpheus-python'
$pythonRevision = git -C $pythonReference rev-parse HEAD
python -I -S -B scripts/compare_actor_startup.py --python-reference $pythonReference --python-revision $pythonRevision --runtime $env:ORPHEUS_RUNTIME_DIR --rust-report 'C:\private\actor-startup.json' --private-output 'C:\private\actor-startup-comparison.json'
```

The comparison verifies the SHA-256 of all eight inputs from the Rust
matrix and matches the exact hero and body IDs. Python uses the ordinary
room and body switch with the existing in-memory client. It decodes the
initial full tree and the first owner frame after acknowledging the tree.
The report records the Python strict flag and counts distinct compatibility
notices observed per actor. Silent compatibility paths require separate
observation. Rust retains its recorded independent factory result and
its separate room factory result. This comparison measures startup
milestones. Frame parity, gameplay, and original-client acceptance have
separate tests.

### Inspect actor input and owner delivery

The prepared Practice Range input matrix selects each actor in a fresh room
and sends commands through the native input and owner delivery paths. Its
schedule includes movement, primary and secondary fire, both ability inputs,
quick melee, reload, ultimate input, and release phases.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\private\runtime'
$env:ORPHEUS_ACTOR_INPUT_REPORT = 'C:\private\actor-input.json'
$env:ORPHEUS_ACTOR_INPUT_REVISION = git rev-parse HEAD # Optional
$env:ORPHEUS_ACTOR_INPUT_NODE_SHAPES = '1' # Optional
cargo test --locked -p orpheus --lib game_replication::tests::practice_observation::prepared_practice_actor_input_matrix -- --ignored --exact
```

Use a new private output file for each run. The JSON records the schedule,
completed input phases, owner acknowledgements, combat requests, and the
first failed stage and frame for each actor. When a script fault is captured,
its record includes the delivery step and available graph diagnostics.
With the optional node-shape setting, diagnostics also include the authored
action or state markers, scope, slot presence, registration rejection and
a bounded operand structure. Constructor and field labels identify the
schema. Literal values and wire references are summarized by type. Missing
sites have a null shape. Direct variable bindings report entity or instance
scope. Runtime operand values require separate evidence. The complete report
still includes the existing textual diagnostics and belongs in private storage.
The matrix measures headless input processing and owner delivery. Weapon and
ability mechanics, ultimate charge, and original-client presentation require
separate verification.

### Generate a synthetic test scaffold

```text
python scripts/generate_statescript_scaffold.py --field-contracts path/to/field-contracts.json --output path/to/new-scaffold.json
```

The scaffold contains an original server-only plain state and example
fields for allowlisted operand kinds. It uses invented identifiers,
values and expected behavior. Use `--family` or `--class` to select
operand kinds for an exact family or class in the private report. The
selected names remain private. Operand examples are inert. Review the
draft, add the behavior to verify, and register it with a Rust contract
consumer before counting it as a regression test.

## Declare an ordinary server state

An authored node can opt into the plain state lifecycle with
`"state_behavior": "plain"`. Set `server_only` to true and `client_only`
to false. Its `state` must index a slot in the graph's `states` table that
points back to the node exactly once. The loader checks the link shape
of the optional begin, end, abort and finished output plugs. Omit a plug
or set it to null when it is unused. The loader assigns the common plain
dispatch class, so graphs can use the ordinary lifecycle without class or
position registration. These nodes remain outside the network projection.

## Declare a forwarding action

Set `"action_behavior": "forward"` on an authored server-only action whose
job is to follow its `m_outPlug`. Supply a plug with a link list and leave the
action out of the state table. Links run in their declared order with the
regular VM budgets. An empty plug does not take a transition. The action
requires no registration by class or graph position.

## Route an authored action by a condition

Set `"action_behavior": "branch"` on a server-only action, or declare
`"branch_action"` for an authored class. Supply `m_condition` and valid
`m_truePlug` and `m_falsePlug` outputs. The VM evaluates the condition
once at dispatch and uses the ordinary expression truthiness rules. It
follows the selected output in link order. An empty selected plug takes
no transition. Evaluation errors are reported at the node and take no
output. Use a client-only operand with a server fallback when an
authored condition needs a different value on the server.

## Lock a logical button for a state

Set `"state_behavior": "button_lock"` on a server-only state, or declare
the `button_lock_state` behavior for an authored class. Set `fields.button`
to a logical button from 1 through 255. A positive integer
`fields.duration_frames` finishes the state after that many VM frames.
Omit the duration to let another graph action end or abort the state.

Locks on the same button stack. The VM releases each claim when its state
finishes, aborts, or is destroyed. Acquisition discards buffered ability
presses for that button. Logical listeners resynchronize against the
held input as counts change, and their existing bypass applies.
This operates on VM input. Room movement and the native whole-body input
gate use their own controls.

A directed room-message entry can begin the lock in the recipient's VM.
The lock takes effect when its begin runs. Commands and state transitions
continue to follow the component's event order.

Logical button states use the same death admission rule on a press,
on entry while the physical button is held, and when a lock changes.
The first VM update after owner death releases any held scope whose
listener requires a living owner, even on a timer-only frame.
A listener that explicitly accepts input while the owner is dead keeps
its normal input and lock behavior.

See the [synthetic button lock contract](../fixtures/behavior/script-button-lock-v1.json).

## Start and stop states with held input

Set `"state_behavior": "held_input"` on a server-only state, or declare
`held_input_state` for an authored class. Use a logical button from 1 through
255. List the graph's raw state slots in the order they should begin.

```json
{
  "button": 29,
  "held_states": [1, 2],
  "idle_states": [3],
  "on_press": {"$": "Plug", "links": []},
  "on_release": {"$": "Plug", "links": []}
}
```

The loader registers the listener automatically and uses the existing
logical-button lifecycle. Entering a branch starts its declared states.
Switching branches, ending the listener, or retiring its instance releases
the outgoing branch's states. Omit `idle_states` for an empty idle branch.
Use distinct child slots in listeners that can be active at the same time.
The `on_press` and `on_release` plugs are optional edge callbacks.
A listener that starts while the button is already held enters its held
branch using the current admission state.
If several transitions happen before a branch starts, only the latest
transition can start a queued branch.

Set optional `allow_dead` to true to admit input while the owner is dead.
Otherwise owner death releases the held branch before the next observation
update, and the existing logical-button lock also controls admission.

For logical-button nodes that read a binding expression, a change to the
resolved button discards a buffered press from the old button and checks the
new button against current held input, death admission, and button locks. If
both bindings are held and admitted, the active branch keeps its state.

See the [synthetic held input contract](../fixtures/behavior/script-logical-input-owner-lifecycle-v1.json).

## Scale movement speed for a state

Set `"state_behavior": "movement_scale"` on a server-only state, or declare
`movement_scale_state` for an authored class. Set `fields.factor` to a
finite number from zero through one. An optional positive integer
`fields.duration_frames` finishes the state on the VM timeline. With
no duration, the state stays active until it ends, aborts, or its instance
is destroyed.

The state adds one multiplier in the existing speed slot. Overlapping
scales multiply under the normal modifier order. Each claim belongs to
its own instance and state. Movement consumers see additions and
removals at their next frame boundary. Player movement and scripted AI
gate approaches read this slot. A factor of zero scales the speed limit
to zero. Acceleration, momentum and collision still follow their mover
rules.

See the [synthetic movement scale contract](../fixtures/behavior/script-movement-scale-v1.json).

## Control a body's facing for a state

Set `state_behavior` to `"facing_claim"` on a server-only state or
declare `facing_claim_state` for an authored class. Provide a
`direction` expression and a `priority` with an integer `level` in
the range -128 to 127 and a boolean `above_equals`. The state's own
body is the target. The VM evaluates and normalizes the finite nonzero
direction on entry and refresh.

The highest priority claim supplies the body's facing. A later claim
at the same level wins when it sets `above_equals`. Refreshing a claim
preserves its place in that order. Area queries can supply inputs through
the usual StateScript expressions and variables.

Finishing, aborting or tearing down the state releases its claim.
An invalid direction or a refused host refresh aborts the state and
releases its previous claim. Players apply the accepted direction at
the next processed pose. An NPC's own direction queries sample the claim
that was active when its script update began. The room resolves facing
again for the published pose, so a claim that ends during the update can
return control to navigation in that frame. Server-facing claims support
vertical directions. An active cinematic aim has precedence.

See the [synthetic facing claim contract](../fixtures/behavior/statescript-facing-claim-v1.json).

## Schedule collision-checked displacement

Set `action_behavior` to `"swept_displacement"` on a server-only action or
declare `swept_displacement_action` for an authored class. Supply a
`delta` expression that returns a finite, nonzero world-space vector
with length at most 32 world units. The action requests movement for
the script's own living body on the next VM frame. Its output plug runs
when the host accepts the request.

The player mover consumes the request once on the first actual posed
frame at or after its due frame. Script-only timer updates can queue the
request. NPCs consume it once when due on the room tick before their
steering update. Rooms with physical collision geometry freeze living
body capsules before the first movement of each room tick. Regular
player poses, recovered inputs, NPC patrol and objective movement, and
scripted sweeps use this snapshot with the same capsule solver. Teammates
pass through one another in team modes. In free-for-all every other
living actor can block movement. Each mover checks the other actors'
sampled positions for the duration of that tick. The body's character
collision setting and its active movement modifiers control contact
participation. Static map collision still applies when a body passes
through actors. Death and runtime retirement clear pending displacement
and velocity requests, including work already passed to the native mover.
If a player script fails while recovering input frames, later poses use
the settled motion and current input.

An actual player displacement opens a bounded owner correction window at
the moved pose. Delivery uses the existing create acknowledgement,
packet budget, and input replay rules, including the input acknowledged
before this motion. A correction for an already moved
pose stays eligible if its body script stops. Body retirement discards
the old generation's correction. Shared regression and original-client
movement acceptance remain open.

See the [script dispatch contract](../fixtures/behavior/script-swept-displacement-v1.json)
and the [native capsule contract](../fixtures/behavior/native-swept-displacement-v1.json).
The [regular movement contract](../fixtures/behavior/native-regular-actor-contact-v1.json)
covers player input recovery and NPC patrol and objective movement.
The [contact participation contract](../fixtures/behavior/movement-contact-participation-v1.json)
covers body settings and modifier start and retirement.
The [pending motion contract](../fixtures/behavior/owner-script-motion-cancellation-v1.json)
covers a script fault during recovered player input.

### Relocate a body to a checked destination

Set `action_behavior` to `"relocation"` on a server-only action or declare
`relocation_action` for an authored class. The `destination` expression
returns absolute world-space feet coordinates. The next eligible pose
checks a nonzero distance of at most 32 world units from the current
feet, then tests the destination capsule against static geometry and
eligible actors. The current capsule posture and movement settings
determine clearance. The action checks the destination only and can
place the body in the air.

Relocation uses the same pending slot, lifecycle and owner correction
as swept displacement. The output plug indicates queue acceptance.
An occupied destination leaves the body in place and consumes the
request. The owner correction starts only when a pose actually moves.
See the [portable action contract](../fixtures/behavior/script-relocation-v1.json)
and [physical destination contract](../fixtures/behavior/native-capsule-relocation-v1.json).

The [owner motion contract](../fixtures/behavior/owner-swept-motion-v1.json)
covers correction scheduling and a same-frame script failure.

## Declare map lethal volumes

A prepared runtime can supply `data/map_environment_174.json`. Version 1
maps hexadecimal map GUIDs to a list of world-space boxes. This example
uses an original test map and coordinates.

```json
{
  "version": 1,
  "maps": {
    "0x42": {
      "lethal_volumes": [
        {"min": [-2.0, -18.0, -2.0], "max": [2.0, -8.0, 2.0]}
      ],
      "player_respawns": [{"mode": "0x43", "delay_ms": 1200}]
    }
  }
}
```

The server allows up to 64 volumes per map. Each axis requires two
finite, strictly ordered endpoints. The room checks the final sampled
feet position, including the box faces, and settles lethal damage with
no killer credit. Training bots use their existing replacement timer.

The common body-death message (`003E`) supplies the terminal hit's ordered
identifier tags in parameter `003D`. Original death scripts bind this list
before choosing a reaction. A map volume defined only by its bounds provides
no damage tags. A falling-death reaction needs an independently identified tag
source for the particular hazard.
Player death notices use the ordinary health path.
Missing files and unlisted maps have no configured lethal volumes.

`player_respawns` is optional. Each entry binds a mode GUID to a positive
delay in milliseconds. The server includes a three-second Practice Range
rule. Local entries override individual map and mode pairs. Other map and
mode pairs require an explicit rule. An enabled room starts the delay on its simulation
clock when it settles a player death. The deadline belongs to that body
generation and retains the hero and skin. An already queued hero or
skin selection gets processed first. Automatic replacement gives the
old death notice up to the connection silence window to enter the reliable
queue. It uses the ordinary body creation and possession path. Practice Range
lethal regions and original-client respawn acceptance remain open. The shipped
delay comes from the [pinned Practice Range timing reference](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/src/ow174/game/combat.py#L106).

## Use priorities for control effects

A server-only state can declare `"state_behavior": "stack_claim"`, or use
an authored class with `stack_claim_state`. Set `fields.variable` to an
object with `scope` set to `"entity"` or `"instance"` and a nonzero integer
`id`. Entity scope is shared by graph instances on the same body.
Instance scope is local to the running graph.

Set `fields.priority` to an object containing an integer `level` from
-128 through 127 and a boolean `above_equals`. A higher level wins. An
incoming claim at the same level wins only if it declares
`above_equals: true`. Set `fields.value` to the value expression that the
claim contributes. A positive `fields.duration_frames` schedules the
claim's end. Claims also release on abort or instance destruction.
Maps have no supported stack snapshot representation. A map result
produces a diagnostic and leaves the current binding untouched.

Use `{"$":"StackAllows","variable":...,"priority":...}` with the same
variable and priority objects in an authored branch. It checks whether
the new claim would lead the current stack at the point of evaluation.
For a directed room message, run the check in the recipient's graph.
The predicate reads the stack and leaves it untouched. An unavailable
context produces a diagnostic.

Use the existing `m_topSubgraphPlug` and `m_underSubgraphPlug` outputs to
start effects at the corresponding stack position. Each output's
`m_FF3DAF1E` array lists the state slots it owns, so the VM can end those
states when the claim changes position or ends.

See the [synthetic stack claim contract](../fixtures/behavior/script-stack-claim-v1.json).

The existing timed projectile bridge shares one native compatibility binding
for the shot, wrapper, duration override, and NPC guards. Recipient scripts
queue the existing wrapper and hit notification in order. The room can
suppress its native control timer while the graph still handles the
notification.

## Limit instance variables to the owner

A graph can declare `"owner_only_instance_vars": [12]` to restrict variable
12 to owner StateScript snapshots. Declare unique nonzero variable IDs
on the graph object. A declared ID can use one existing instance sync
slot or the ordinary extra-variable encoding. A sync slot for shared
entity scope or multiple sync slots for that ID is invalid.

The owner receives the resolved value through the existing codec.
Observer full snapshots and deltas omit the declared instance value.
The declaration applies independently to every instance of the graph,
including child instances. Entity variables, actions, and explicit
copies to public variables keep their usual visibility. Client-side
interpretation of the value requires a verified receiver.

## Share class behavior across a graph bundle

Add `authored_classes` to a version 1 bundle and use those names in the
`class` field of any node in the bundle.

```json
{
  "authored_classes": [
    {"class": "authored.latch", "behavior": "plain_state"},
    {"class": "authored.continue", "behavior": "forward_action"},
    {"class": "authored.indicator", "behavior": "client_only"}
  ]
}
```

Each name starts with `authored.` and has a nonempty suffix of ASCII
letters, digits, underscores or hyphens. Duplicate names are rejected.
The scope flags are still required. Set both to false to inherit a
declared class's scope, or set only the matching flag to true. Authored
states and actions inherit server-only scope. Client-only
nodes skip server execution and wire projection. Conflicting flags or
per-node behavior fail at load time with the graph and node positions.

The loader applies these declarations before checking state slots and
output plugs and building the wire tables. Nodes outside the catalog
follow the ordinary rules. The
[synthetic class contract](../fixtures/behavior/script-authored-classes-v1.json)
exercises the same declarations across two graphs.

## Send a message within a component

Set `action_behavior` to `"local_message"` on a server-only action, or
declare `"local_message_action"` for an authored class. Supply a nonzero
canonical message ID in `m_gameMessage.m_gameMessage`, an output plug,
and optionally `m_params` entries with `m_B5051BCE` and `m_value`.
Parameter IDs must resolve to distinct nonzero low-16-bit slots.
Leave `m_target` and `m_targets` unset.

The action uses the component's existing message queue and delivery rules.
It captures parameter values when the action runs and follows its output
plug. Message entries are indexed at graph load in their original order.
State listeners still need to be active when the message is queued and
delivered. Client-only listeners remain outside the server route.
See the [synthetic local message contract](../fixtures/behavior/script-local-message-v1.json).

## Send an authored message to another room body

Set `action_behavior` to `"room_message"` on a server-only action, or
declare `"room_message_action"` for an authored class. Supply a nonzero
message ID in `m_gameMessage.m_gameMessage`, an `m_target` expression that
yields a different live body, and a valid `m_outPlug`. Use up to 16 distinct
nonzero parameter slots in `m_params`. Parameters can hold scalar values or
finite vectors.

A receiving game-message entry declares `"room_message_entry": true` and
server-only scope. That declaration puts the entry on the authored room route.
The sender captures parameter values when the request is accepted and can
then follow its output. The room uses the same per-body FIFO and identity
checks as health requests, with a combined limit of 64 pending requests per
body. It schedules delivery for the receiving body's next VM frame.
An accepted message can expire if the body is replaced or retires, or if
the recipient has no matching listener. A receiver with too little queue
space rejects the whole batch of matching listeners.

See the [synthetic room message contract](../fixtures/behavior/script-room-message-v1.json).

A private prepared bundle can declare a reviewed native numeric topic
under `runtime_state_metadata.directed_numeric_messages`. Each record
has a full `topic` GUID and full `parameter` GUID. The loader requires a
matching native action with one instance-scoped target variable and a
matching standalone server receiver. The receiver also continues to
accept ordinary local messages. A foreign send requires a bound entity
and exactly one finite numeric payload item. The room checks both body
generations at settlement and delivers on the recipient's next VM
frame.

Use the preparation tool to append metadata to a fresh output file.
The following example opts in the reviewed `00EB` route. The targeted
probe isolates the two original sender actions and the original bonus
receiver. Natural target binding and native timing in a running game
require separate verification.

```powershell
python scripts/build_runtime_state_metadata.py --graph-bundle private/input.json.gz --directed-numeric-message 0x02400000000000EB:0x0A4000000000002A --output private/directed.json.gz
$env:ORPHEUS_DIRECTED_GRAPH_BUNDLE = 'private/directed.json.gz'
cargo test --locked -p orpheus-statescript --test stock_charge -- --ignored --exact prepared_bonus_actions_preserve_local_and_directed_delivery
```

## Send a cue to selected players

A server-only action can declare `"action_behavior": "room_cue"` or use
an authored class with `room_cue_action`. Supply literal, nonzero
`fields.stimulus` and `fields.line` identifiers in canonical 16-digit hex
form, an `audience` expression that returns an array of entity references,
and a valid `m_outPlug`. A graph can build the audience with
`EntitiesInRadius` and its team, liveness, and tag filters.

The VM keeps the first occurrence of each entity and accepts at most 64
distinct entries. An empty audience continues immediately. For other
audiences the room keeps current living player bodies and ignores other
entities. The batch uses the sender's existing request budget. The action
continues once the batch is accepted.

Omit `fields.speaker` or set it to `"actor"` to use the triggering actor
as the voice source. Set it to `"mode"` to use the room's mode entity
for a match announcement. The transport checks the triggering actor and
listener generations and the listener's liveness. It waits for the
listener and selected voice source to be acknowledged. Each player
has at most one unacknowledged authored cue in reliable delivery.
When the pending combat queue is full, a new cue is dropped.
Cue identifiers describe resources supplied by the author.

See the [synthetic room cue contract](../fixtures/behavior/script-room-cue-v1.json).

The Python branch models match announcements as timeline transitions and
delivers them through a per-listener reliable queue. Its mode speaker
is independent of an actor anchor. The Python implementation covers
round announcements. Timed elimination counts
and their eligibility still need a separate contract.
[Python match audio](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/src/ow174/game/match_audio.py)

## Request a health change

Set `action_behavior` to `"health_change"` on a server-only action, or
declare `"health_change_action"` for an authored class. Supply a `target`
expression yielding a bound entity reference, a `delta` expression yielding
a finite nonzero number, and a valid `m_outPlug`. Positive deltas heal and
negative deltas deal damage through the room's existing health rules.

Run this action from events after the body receives its first room frame.
During component initialization, the host can lack a room frame and reject
the request. Admission requires a living target with health pools in that
frame and free space in the source body's queue. The output runs when the host accepts it.
The room settles accepted requests before retirement and checks both body
generations again. A replacement can neither receive nor inherit a queued
health effect. Already accepted effects keep their attribution if the same
source body dies while the room is settling the batch. Effects follow queued
order within each source body. Death and health updates use the existing room
lifecycle. These authored actions also enable the room body snapshot in
bundles that use no spatial expressions.

See the [synthetic health contract](../fixtures/behavior/script-health-change-v1.json).

## Set mode and actor rules

The optional `mode_rulesets` section of a version 1 graph bundle supplies
immutable rules for the running mode. This example uses synthetic IDs.
To add profiles while preparing a runtime, save the `mode_rulesets` array
itself as a local JSON file and pass it with `--mode-rulesets`. See the
[runtime packing guide](../data/README.md).

```json
{
  "mode_rulesets": [
    {
      "mode": "0x00000000000000A1",
      "rules": [
        {"id": 7, "value": false},
        {"id": 7, "body": "0x00000000000000B1", "value": true},
        {"id": 8, "value": 1.0}
      ],
      "engine_settings": [
        {"id": 7, "value": 4}
      ]
    }
  ]
}
```

Use a nonzero canonical GUID for the mode and any `body` override. The
body key is the actor's profile GUID, shared by the player and AI host
lookups. A rule ID is a 32-bit unsigned integer. Values accept Boolean,
signed 64-bit integer, finite floating-point, or explicit `null`. For a
ruleset enum, encode its selected identifier as a canonical nonzero asset GUID
string. The VM returns the identifier as a type 4 asset. A mode
must appear once, and each rule ID must be unique within its scope.

The VM checks its existing entity variable first when it has a value.
The native host then looks for a body override, a mode default, and an
existing compatibility value in that order. An explicit `null` is an
authored result and prevents compatibility fallback. A fully missing
rule retains the VM's existing integer-zero fallback. Graph bundles
without the optional section retain the existing host rules.

The Windows runtime preparer reads the ordered `m_gameRulesetSchemas`
references on the mode (`0C5`) and each playable hero (`075`). It resolves
their entries from local `0C6` XML. The first schema declaring a key wins,
with mode schemas taking precedence over hero schemas. The exported body
rows therefore contain only keys outside the mode defaults.

A mode row can also declare `engine_settings`. This is the separate table
used by native engine-setting expressions and watchers. Keys are unsigned
32-bit hashes. Values accept Boolean, integer, float or explicit `null`. The table is
mode-wide, with unique keys. Player, NPC, mode and
scene-object scripts read the table for their current mode.

An omitted table keeps the existing mode-specific compatibility lookup.
An authored table, including an empty list, supplies the whole mode table.
A missing key in that table keeps the expression's initial value, while
an explicit `null` supplies a null result. A row may declare engine settings
without declaring body rules.

See the [synthetic mode ruleset contract](../fixtures/behavior/script-mode-rulesets-v1.json).

## Contact combat profiles

A linked hostile contact volume uses its authored collision profile to select the static ray category. A local graph bundle can provide the mapping in its top-level `static_ray_profiles` table. Each key is the canonical profile GUID and each value is a nonzero unsigned 16-bit category. For example, `"static_ray_profiles": {"0x06900000000000AA": 24}` uses invented test data. Supply the composed graph bundle with `prepare_server_runtime --game-graphs`.

The server samples the living room bodies at the damage state's first posed activation. It matches the two authored variable bindings and the tracker-to-state link before settling damage and knockback. A missing profile leaves the contact unavailable. Static walls and body affiliation are supported by this path. Authored dynamic barrier filters, client rendering, and original-client movement require separate evidence.

## Receive a settled health event

A graph can subscribe to room-authoritative health changes with an
`EntryHostEvent` node declared as `"host_event_entry": "health_applied"`
and `"server_only": true`. Bind the known parameter slots in `m_params`
and continue from `m_outPlug`.

| Slot | Value |
| --- | --- |
| 1 | Source entity, or null if unavailable |
| 2 | Target entity at settlement |
| 3 | Actual signed pool delta, positive for healing and negative for damage |
| 4 | Whether damage killed the target |
| 5 | Ordered hit tag assets, or null at a declared binding when unavailable |
| 6 | Ordered records of named shield loss, or null at a declared binding when unavailable |
| 7 | Confirmed critical hit flag for settled weapon damage, or null when no impact classification is available |
| 8 | Authoritative room frame of damage settlement, or null when unavailable |

The hit tag slot uses the list carried by the applied impact. A known
empty list stays empty. Other settled effects that lack hit attribution
omit the slot, so delivery clears any previous value in a declared
binding. Tags preserve authored order and duplicates.

Damage with pool accounting can bind slot 6 to an array of named shield
loss records. Each record has an asset identifier at key `16056` and a
positive amount at key `499`, rounded to native float precision. An
empty array means a known result with no named loss. Healing and damage
without pool accounting omit the slot. The compatibility charge message
uses the same record encoder. A same-body GameMessage can carry the
array as an explicit parameter.

A confirmed normal impact binds slot 7 to `false`. A confirmed critical
impact binds it to `true` only after nonzero damage settles. Slot 7 is
an ORPHEUS server host binding. A graph can route a cue to the source
from slot 1 for a confirmed critical hit, or to the target from slot 2
for victim feedback after negative damage in slot 3. The health event
reaches living receivers and shares their normal body lifetime. Scripts
can combine the kill flag with slot 8 and authored eligibility and
timing rules for multi-elimination cues. Slot 8 uses the room frame
that applied damage, rather than a fire command, VM delivery or
acknowledgment frame. Room-settled weapon and scripted damage supply
it. Healing and VM-local changes without a sampled room frame leave
it unavailable. Cue resources are supplied by authored content.
[Synthetic timed elimination contract](../fixtures/behavior/script-timed-elimination-cues-v1.json)

The 104319 map-script archive's shared graph `0215` sets its
multi-elimination interval to two seconds. An eligible message increments
the count, selects the cue, and starts a restartable wait. The wait clears
the count when it finishes. On the expiry frame, the VM processes the
message actions before the wait finishes and discards the pending reentry.
The room announcer mirrors that order: eliminations on the deadline can
announce from the old count, then the next frame starts a fresh streak.
At runtime load, the combat voice catalog finds the authored counter switch
by its configured count and stimulus pairs. It follows the target filter's
action links to that switch and reads the excluded target tags. With a
bound exclusion list, the target must have a known absence of every
excluded tag before it can advance the streak. The ordinary elimination
feedback path can still run for excluded bodies. Packs without a matching
graph retain the catalog's affiliation check. The other authored filter
fields and announcer-suppression conditions still need room bindings.

The cue gate in `0215` follows the count increment and wait rearm. One
input is set while the `069C` message provider is present. The 104319 archive
lists providers for Junkenstein's Revenge, King's Row Archives, and
Rialto Archives. The Practice Range lists the shared receiver and has no
matching publisher in its map index. The other input checks membership
in selection `21925`; its room interpretation remains unresolved.

The 104319 hit-marker message updates the shooter's HUD. The damage
feedback path uses the struck body's combat event for victim effects
and audio. Keep speaker and recipient selection tied to the settled
source or victim for each cue. A severe victim cue does not establish
the shooter's native headshot sound.

Set `ORPHEUS_PLAYTEST_HIT_VARIANT` to a finite, nonnegative number when
running an Inspector build to exercise authored hit effects. The room
routes confirmed source impacts to the active graph whose damage entry
declares the five native presentation parameters. The graph receives the
settled amount, target, hit tags, critical flag and the configured value
of parameter 0724. Use 1 to exercise the ordinary and critical default
variants. The retail source of 0724 and original-client playback and
audience acceptance remain open.

Set `ORPHEUS_PLAYTEST_ELIMINATION=1` to exercise the shared final-blow
effect after the room settles an enemy elimination. The active receiver
selects its authored effect using the defeated body's facts. The test
captures hitscan and projectile final blows through owner egress. Assist
credit and the additional elimination audio layers still need verification.

For a received-damage client comparison, set
`ORPHEUS_PLAYTEST_RECEIVED_DAMAGE=1` on an Inspector server using the prepared
104319 runtime. Only positive, nonlethal loss from a distinct room actor queues
the local damage event. The victim's active body and the source generation must
still match when delivered. The client copies the position at offset
`0x9c` to its combat event and subtracts its view origin before projecting
the result onto the view axes. The server supplies the settled weapon
source point for this field and falls back to the attacking body's position
for settled damage without weapon geometry. The native contact effect consumes the
negated surface normal from offset `0x84`, falling back to the
direction from source to impact when the normal is absent. Direct weapon
contacts supply the outward hurt-volume normal. The hit-position field
and flags retain neutral values. The prepared schema replay checks both
geometry fields on the struck player's connection. The directional
indicator and pose reaction remain separate validation tasks.

The existing playtest settlement trace includes the geometry available at the
point of damage. Direct hits record the start of the ray or contacting
projectile segment, the target surface contact point and normal, and
the incoming direction. An expanded projectile sweep keeps the
projectile centre separately for its own completion. Splash records the
blast origin and leaves the surface normal unset. Damage without weapon
geometry leaves the surface normal and contact position unset. The prepared Practice Range firing
lane confirms that a live front bot prevents player damage feedback.
When the bot dies while the shot is in flight, the shot reaches the
player and their notice carries the NPC origin and outward normal.

The hit-position decoder uses three basis vectors and a translation
from the local entity's transform path when that transform is available.
It passes the position through when the transform cannot be resolved.
The headless room has the world contact point. The exact local transform
and its sampling frame need to be established before filling that field.

The 104319 victim event also has a compact contact-set operand (`@0xca`). The
client expands it to a `.049` asset and carries it into its contact effect
request. The shared contact-set graph (`67`) declares `0x0D800000000002A3`
schema entries per body (`STUConfigVarContactSet.m_contactSet`, e.g. `0x01200000000005EE`
or `0x01200000000000D5`), which the server now propagates to `25000` `@0xca`.
Shared presenter graph `64` also reads `0x0240000000000012` (`0x724 = 1.0` default ->
`0x030000000000183B` crit / `0x0300000000000206` normal) and `0x0240000000000093`
(`0x03000000000000EE` final blow). This is a contact-set selector. The local `STUContactSet` schema
maps contact type and material to authored responses, and actor and
projectile components use contact sets for different purposes. A body's
default set alone does not identify the material or the contact response
for an incoming hit. The local contact sets refer to both `.04A` and
`.00D` responses. Sampled `.04A` rows use different audio events for
different material IDs, so a material-specific sound does not establish
the particle used for the hit. Actor `STUModelLook` records also have
material-effect selectors and variants. Their connection to weapon-hit
particles remains unverified. The map-script archive lists
`m_hitReactions` on `STU_F3104738` with AI damage ranges, tags and
priorities. Its listings classify those nodes as plain State on the
examined path, so their authored rows alone do not establish a runtime
binding for the player's flinch.

For the authored `STU_23A00A53` state, the VM reads the literal
`m_contactSet`, verifies a networked self source with a synchronized
instance target variable, and republishes the contact state when that
variable changes. The owner and NPC hosts validate their own source and
available target entities. The original graph retains the resource and
its presentation operands. This covers the confirmed literal/default
mode shape; expression-driven selection, other modes, and generic
weapon-hit material selection remain open. The [Python contact projection](https://github.com/leafall903/ORPHEUS/blob/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/src/ow174/game/junkenstein_scene.py)
provided the earlier NPC synchronization reference.

Current hit feedback is scoped to settled weapon impacts with a hit
classification. A generic scripted health change has no hit classification,
so it can still send victim feedback on a positive nonlethal loss without
queuing a shooter hit effect. The victim path can queue once per eligible
health settlement. Authored concentric pellet volleys now use the same
pattern parser for player hitscan and NPC weapons. Each ray goes through the
shared combat query and settlement, while the VM still owns the single shot
event and its ammunition. Uniform-cone hitscan uses the authored pellet
count and a per-pellet seed offset. An explicit launch vector
(`m_E2FD6F04`) still requires its own direction semantics. The 104319 Tracer
startup probe stops before firing with four initialization diagnostics
(`0024:63`, `0024:9`, `0034:51`, `001A:5`), so its two-ray volley is
covered by a separate synthetic weapon graph. Feedback cadence for pellet
impacts and recurring damage remains open.

Weapon notifications carry the target's captured filter flags, target
tags, and script variables. A room-settled health change from a distinct
sampled source can carry the same facts after applying the signed pool
delta. A change without a weapon impact leaves hit tags unavailable.
An immediate foreign query about that target reads the captured values. A direct `EntityMatches` tag
filter uses the captured tags and, when available, the target's
room affiliation and post-settlement liveness. This permits credit for
a retired target while an explicit alive filter rejects a lethal
outcome. Relationship sources and spatial searches use the current
room frame. Own-body queries, watchers and other entities use their
usual host.
Unavailable captured facts stay unavailable. The room checks body
generation when dispatching the event, and the VM clears the snapshot
at the end of each delivery.
For NPC-local changes, publication samples the committed target after
the local VM step. Each queued delta records the actual applied amount.

An immediate directed message or direct health change to either
participant uses their room-recorded generation. The VM verifies it
against the room frame used for admission. An expired body is skipped.
The original source binding and captured target facts still describe
the completed event. Radial effects select present candidates through
their spatial query.

An immediate same-body GameMessage carries each participant's context
when a top-level typed entity parameter names that participant.
Target facts follow the target parameter. Numeric parameters, incoming
room messages, externally scheduled messages and later graph work
use their own context.

The compatibility damage message runs at the current VM clock on its
selected receiver. It carries the same settlement snapshot and must
supply a matching typed target parameter. The host's compatibility
filter and hostility queries apply only during that original delivery.
A relay can carry matching target facts through its own typed parameter.

The enabled room records successful weapon damage, room-settled script
and AI requests, field healing, and VM-local AI damage and healing.
Pool mitigation and caps are already reflected in the delta. Changes
that apply zero produce no event. For overlapping pulses from one owner,
the record carries the actual total healed per target. Pool capacity,
respawn, and shield recharge have independent lifecycles.
The room checks each recipient's body identity after settlement and
schedules delivery at the start of that component's next VM frame.
Living source and target actors can subscribe. A self-effect is delivered
once. A killed target can still be reported to the living source.

Host entries use the bounded VM event queue and parameter binding. Their
subscription is distinct from local and directed room messages. Queue
rejection discards the notification and leaves the settled pool result
in place. The same room queue delivers VM-local outcomes after the
corresponding simulation phase.

See the [synthetic host health contract](../fixtures/behavior/script-host-health-events-v1.json).

## Attach a server-only graph to a body

Use a body's optional `server_graphs` list to install an authored
graph as an isolated root. Each entry uses the same graph index and
sync bindings as a `graphs` entry. Sync bindings seed instance variables
before the root's startup entries run.

The VM installs numbered roots first. Private roots and the scripts they
start within the same component allocate IDs from the top of the
instance range. The public sequence grows from the low end. The VM
reports exhaustion when the ranges run out of free IDs and keeps retired
IDs reserved for the component lifetime. Isolated instances stay outside owner and
observer projection.

Use instance-scoped variables for private balances. A graph that writes
shared entity variables or requests room effects still participates in
those shared interfaces. Compare both wire views when adding a binding
to a client-facing body.

See the [synthetic root allocation contract](../fixtures/behavior/script-server-root-ids-v1.json).

## Compose a graph-owned resource meter

Bind the same meter graph to the actors that use it. Each body's
graph-binding sync list can seed its instance variables for the starting
balance, limit and gain rates. Instance variables keep the configured
balance local to that graph instance.

A `health_applied` entry can award credit after checking that the actor is
the source. Use the signed delta and `EntityMatches` with the room's
affiliation data to route damage and healing. An interval state can update
the same balance independently. Ordinary assignment and numeric clamp
operations apply the limit.

Use `{"$":"BodyActive"}` in the gain branches when the meter depends on
room and body activity. A live owner in a regular room is active. An
encounter supplies its existing pre-result activity level after its
runtime is installed. Death closes the gate through authoritative vitals.
A detached body with no supplied room
activity reports an unavailable host read. A deferred health event reads
the level when its graph handler executes. Gating interval gain keeps
the interval schedule anchored to startup. The debug scheduler can
freeze the VM clock independently.

The [synthetic meter contract](../fixtures/behavior/script-authored-meter-v1.json)
runs this graph for two differently configured body definitions and checks
an actor without the binding. Its rates, limits and test actors are
original examples. The fixture verifies execution and scoped balances.
The [activity contract](../fixtures/behavior/script-meter-body-activity-v1.json)
checks when gains stop, resume, or belong to a replacement actor.
Adding a root to a client-facing body also requires an owner and observer
projection regression, since body graph bindings participate in instance
ordering.

A player body can select one private root as its charge graph. Use the
normal graph binding for settings and a canonical graph asset GUID for
`authored_charge_graph`. This example uses a synthetic graph ID.

```json
{
  "server_graphs": [{"graph": 612, "m_75408640": 0}],
  "authored_charge_graph": "0x0580000000000264"
}
```

The loader requires exactly one private mount of the selected graph and
a valid server `health_applied` entry. The factory checks the same mount
constraint after adding room roots. A selected root uses the ordinary
settled events on its next VM frame. The host skips its compatibility
charge admission and synchronous damage credit for that body.
Graph bindings remain the source for authored starting values and costs.
A configured private root can inspect hit tags after weapon settlement.
The stock charge graph still reads its synchronous native damage message
and target snapshot. Its timing, target flags, and mitigation records
need separate validation before switching routes.

See the [synthetic charge route contract](../fixtures/behavior/script-authored-charge-route-v1.json).

Use `charge_compatibility` to bind the synchronous bridge to an existing
receiver on a player body. Declare the supported mode GUIDs in `modes`,
the receiving graph GUID in `receiver_graph`, its entity-scoped
`admission_variable` key, and the ordinary `damage_message` GUID.
The loader checks for one server receiver with distinct writable bindings
for all four damage parameters.

The receiving graph can be a public root mounted once on the body. For
an existing native child, give the mounted public root and its native
subscript's raw state slot in `parent: {graph, state}`. The subscript must
refer to the declared receiver. Optional `inhibit: {graph, state}`
identifies an ability state that suspends damage credit while active.
Both references use full graph GUIDs and raw state slots.

A body can declare either `charge_compatibility` or
`authored_charge_graph`. A compatibility declaration applies only in its
listed modes. Bodies with neither declaration keep the existing legacy
bridge for its supported profile. The synthetic example includes both
mount layouts. Original stock admission and rollout still require
validation. [Synthetic compatibility fixture](../fixtures/behavior/script-charge-compatibility-v1.json)

To check the stock timing and bonus receiver, point
`ORPHEUS_RUNTIME_DIR` at the private 104319 graph bundle and run:

```text
cargo test --locked -p orpheus-statescript --test stock_charge -- --ignored --exact prepared_stock_charge_uses_authored_passive_clock_and_bonus_entry
```

The probe starts the stock passive subtree with a detached typed host
and uses the original bonus receiver. It verifies the one-second
deadline, cancellation and re-arm, and the configured cap. Room
admission, stock hit and healing attribution, and original-client
presentation have separate verification.

The [settled bonus contract](../fixtures/behavior/script-settled-bonus-v1.json)
binds the actual healed delta and target from `health_applied` to a
declared numeric message in a synthetic graph. The room delivers the
message in the recipient's next VM frame after routing. The recipient
graph owns admission and capping. A body replacement between request
admission and room settlement discards the pending grant.

The prepared local probe exercises the original admission claim, passive
wait, damage and alternate-credit receivers, and local bonus sender on one
meter. It checks damage-pool deductions, cap and re-arm, and cancellation on
admission withdrawal.

```text
cargo test --locked -p orpheus-statescript --test stock_charge -- --ignored --exact prepared_stock_admission_controls_local_bonus_credit
```

This probe supplies the admission trigger, damage and alternate-credit
messages, and bonus amount. The `0044` receiver's native producer and the
connection to an actual heal are still unverified. Room-origin native
`0D9A` emission and the previously unbound foreign operands also need
independent evidence.

The prepared Mercy probe injects `0D9A` into the original weapon graph
and follows its local `00EB` sender. It supplies a filter policy and
typed target facts inside the test because the production player host
does not yet provide that policy. The sibling `0042` action still
reports an unsupported diagnostic. This exercises the receiver and
amount gates with controlled inputs.

```text
cargo test --locked -p orpheus-statescript --test stock_charge -- --ignored --exact mercy_receiver::prepared_mercy_admission_delivers_local_bonus_with_explicit_policy
```

To exercise incoming stock damage credit with explicit pool records, use
the same private runtime:

```text
cargo test --locked -p orpheus-statescript --test stock_charge -- --ignored --exact prepared_stock_damage_credit_consumes_typed_pool_records
```

The probe keeps its target facts fixed and compares empty, unrelated
and matching records in slot `0624`. The authored fold reads a typed
pool identifier and a numeric amount, then deducts matching records
before capping the award. Current player hits use the actual pool loss,
following the pinned Python settlement path. Room hits carry actual
named shield drains from the same outcome into the compatibility
bridge. The slot uses full identifiers and rounded amounts.
The original producer's exact output and timing need independent evidence.
[Pinned damage settlement](https://github.com/leafall903/ORPHEUS/blob/c73e5a499ec73d499c97eaae46357af30c9e4b39/src/ow174/game/combat.py#L1870-L1896)

Named temporary shields use typed owner-scoped pool identifiers.
They absorb damage before permanent pools and group contributions into
a single component 51 row by name. A depleted auto-retiring grant
finishes its owning state. An ordinary grant is removed when its state
ends. Generic healing and shield recovery affect permanent pools.
The named owner query reads current shield points, and aggregate
vitality includes them. [Pinned temporary shield lifecycle](https://github.com/leafall903/ORPHEUS/blob/c73e5a499ec73d499c97eaae46357af30c9e4b39/src/ow174/game/script/nodes.py#L3255-L3310)

A mode-2 owner effect can decay the named pool using a typed selector
and a finite positive rate. The VM evaluates the current rate and uses
the elapsed time from the shared scheduler. The state finishes at zero.
Decay changes the shield row directly. Only a settled hit produces
the pool-loss rows used by the charge consumer. The compatible
prepared states share this field layout. The optional named-state
outputs and original-client timing still need evidence. [Pinned named shield decay](https://github.com/leafall903/ORPHEUS/blob/c73e5a499ec73d499c97eaae46357af30c9e4b39/src/ow174/game/script/driver.py#L986-L1000)

The prepared `0044` probe passes a numeric amount, a subject entity and
an array of asset identifiers to the stock receiver. It checks
multipliers, exclusions, admission and capping under a fixed host.

```text
cargo test --locked -p orpheus-statescript --test stock_charge -- --ignored --exact prepared_stock_0044_charge_applies_receiver_gates_and_scales
```

## Apply a radial health change

Set `action_behavior` to `"radial_health"` on a server-only action, or
declare `"radial_health_action"` for an authored class. Supply an `area`
object using the ordinary `EntitiesInRadius` expression, a finite nonzero
numeric `delta` expression, and a valid `m_outPlug`. Team and tag filters,
cones and static line of sight follow the same query rules. Distances
come from the room's position sample used for membership.

An optional numeric `edge_scale` expression ranges from zero to one
and defaults to one. The action scales `delta` linearly from its full
value at the center to the specified fraction at the edge. A zero-radius
query applies the full value at the center. A target contributes at most
one change, and zero changes use no request slots.

The room admits the whole ordered batch against the body's shared request
limit. The output continues after acceptance or an empty selection.
Each target's binding is checked again when the room settles its request.
A target that retires before settlement can expire independently of the
other requests in that batch.

See the [synthetic radial health contract](../fixtures/behavior/script-radial-health-v1.json).

## Run an output at a frame interval

Set `state_behavior` to `"interval"` on a server-only state, or
declare `"interval_state"` for an authored class. Provide a positive
integer `interval_frames` within the `u32` range and a valid
`on_interval` output plug. Connect this output to a health change action
to apply a periodic effect. The action reads its target and amount at
each pulse.

The first pulse is due one period after activation. The ordinary VM timer
queue runs the output once when a frame catches up to a due pulse. Missed
pulses are dropped and the next deadline keeps the activation's cadence.
Ending the state cancels its pending timer. Reactivation starts a new
cadence. See the [synthetic interval and health contract](../fixtures/behavior/script-interval-health-v1.json).

## Track entities with child graphs

Set `state_behavior` to `"track_entities"` on a server-only state, or
declare `"track_entities_state"` for an authored class. Supply a
`targets` expression yielding a flat array of entity references, a
`child_graph` index in the same bundle, and a positive `target_variable`
ID. Each selected entity receives one child instance. Its local target
variable is available before the child runs its startup entries.

The tracker samples once per VM frame after activation and accepts up
to 64 unique targets. It deduplicates each valid sample and opens new
children in ascending entity ID order. Membership is keyed by VM entity ID.
A retained reference keeps its child. A later sample can create a new child
after removing that reference. Unavailable or invalid samples leave the
previous set in place.
A successful empty result removes all children. Removing an entity or
ending the tracker tears down the corresponding child, scoped descendants
and timers. The tracker and its child instances execute on the server.

Set `"identity": "room_actor"` to bind each child to the actor in the room's
frozen sample. A target with no live identity waits for a successful
sample with a known identity. An unchanged identity keeps its child while
the target query is unavailable. A changed or missing identity retires
the child and its descendants before their queued work can run, even if
the target query is unavailable. A subsequent successful sample can start
a fresh child for the replacement body at the same entity ID.

See the [synthetic target child contract](../fixtures/behavior/script-target-children-v1.json).

## Declare a client-only operand

Use `{"$": "ClientOnly", "value": ...}` for an authored operand that the
server must skip. Its `value` is opaque to the server. An optional
`server_fallback` is evaluated in the same scalar or ordinary context as
the enclosing operand. Without a fallback, the evaluator retains the
caller's initial value. Wrappers still consume work and nesting budget.
Ordinary unknown operands continue to produce diagnostics.

In an assignment or subscript binding, a fallback can supply a config
group. The selector flattens the selected group's values in authored
order. An entry with no fallback contributes no value inside a group.

## Read an entity's position or direction

Use `EntityPosition` or `EntityDirection` as the `$` tag of an authored
operand with one `entity` field. The field must evaluate to a bound
entity reference. On each evaluation, the VM reads the position or
forward vector from the host and returns its finite components verbatim.
A missing or nonfinite response is an unavailable-entity query error.
The origin is a valid position.

In native room scripts, an actor reads its own live pose, including
before the room has sampled it. Other living actors use the room's
existing frozen spatial frame. Changes to those actors appear in the
next sample. The frame keeps position and packed aim together, and
the host derives forward vectors on demand. A server operand requests
this frame without requiring tag data. Static anchors remain separate
from living body observations.

Player startup reads the room's frozen snapshot when available.
Replacement views exclude the previous body by its owner at capture time.
An owner can replace the body repeatedly in one room frame while other
actors keep their frozen poses and facts. The new body joins the room
sample after admission. The factory probe captures the prepared scene
before starting its temporary runtime.

The [synthetic spatial contract](../fixtures/behavior/statescript-entity-spatial-v1.json)
shows both operands applied to the same entity at two points in time.
The [room pose contract](../fixtures/behavior/script-room-actor-pose-v1.json)
checks how the native room captures actor movement, aim, and player eye
anchors.

The native eye-position shape uses `STU_21A13145` with one `m_entity`
selector. A player anchor combines feet position, the existing stance
height, and the mover's spring offset in movement units. It is seeded
with the spawn pose and refreshed on each owner advance. Other bodies
read the same frozen sample as their pose. NPC bodies and static
placements have no player eye anchor. The VM reports an unavailable
anchor when the host returns none.

The loader requests a spatial frame for native eye and direction
expressions with selectors that may refer to other entities. Direct
self reads and client-only operands without a server fallback do not
request the frame. See the [pose demand contract](../fixtures/behavior/script-native-pose-demand-v1.json).

## Find room entities within a radius

Use `EntitiesInRadius` as the `$` tag, with a `center` expression
that returns a finite vector and a `radius` expression that returns a
finite nonnegative number. The result is an array of unique entity
references ordered by ID. The boundary is inclusive in three dimensions.
A query spends expression budget on each examined candidate and yields an
error if it runs out of budget or the room sample is unavailable. An
available sample can return an empty array.

Prepared rooms enable the candidate service when a server graph contains
this expression. Player and AI scripts share the living body positions
captured at the first input or simulation work of the room frame. Position
and membership updates appear in the following frame's sample. The sample
contains player and bot bodies and is supplied during room runtime events.
A component initialized before it joins the room has no candidate sample.

To filter by team, add a `filter` object with a `relation` of `ally` or
`opponent` and a `source` expression that yields a living entity reference.
The query finds that entity's affiliation in the same snapshot, even when
the source lies beyond the radius. Both relations exclude the source.
Allies share an assigned team. Independents, including free-for-all
participants, are opponents of every other entity. A missing source or
conflicting snapshots for its affiliation yield an unavailable query.
Omit `filter` to include every entity in range.

Add a `cone` object to restrict the same query by direction. Its `direction`
expression must yield a finite nonzero vector, and `half_angle_degrees`
must yield a number from 0 to 180. A cone includes its angular boundary and
excludes bodies exactly at its center because they have no bearing.
Use `EntityDirection` to read a direction supplied by the host. A cone
can be combined with a team filter and uses the same candidate budget.

Set `"line_of_sight": true` to require a clear segment from the query
center to each remaining body position. The query runs through the prepared
room's static collision scene after applying radius, cone, and team
conditions. Bodies use the same snapshot and geometry for both player and
AI scripts. Each surviving entity spends one extra work unit for visibility.
An unavailable segment or conflicting candidate positions returns a
visibility error and leaves the observation's previous value in place.

See the [synthetic radius contract](../fixtures/behavior/statescript-entities-in-radius-v1.json).

## Check whether a segment is clear

Use `SegmentClear` as the `$` tag with `origin` and `target` expressions.
Each expression must return a finite three-component vector. The VM
evaluates the origin first, evaluates the target second, and spends one
work unit on the geometry request. The result is a Boolean for the
prepared room's static collision scene. The target endpoint can touch
the scene. An obstruction strictly before it returns false.

Player and AI scripts use the same geometry as
`EntitiesInRadius.line_of_sight`. A room with no available collision
sample produces an unavailable visibility error. An `observe` state
can retry on the next frame and keep its previous successful value.
The loader enables spatial queries for this expression in server
fields and in a `ClientOnly` server fallback. Client-only values do
not require the service.

See the [synthetic segment contract](../fixtures/behavior/statescript-segment-clear-v1.json).

## Read the nearest static ray hit

Use `RayCast` as the `$` tag with `origin`, `direction`, and
`max_distance` expressions. Supply a finite origin vector, a finite
nonzero direction vector, and a positive finite distance. The VM evaluates
them in that order, normalizes the direction, and spends one work unit on
the geometry query.

The result is `[distance, point, normal]` when the static scene has a hit
at or before the distance limit, and `[]` when the bounded ray misses.
Player and NPC queries use the same room collision scene and movement
category. A missing room frame or an invalid or untraversable ray reports
unavailable geometry. An `observe` state retains the last successful
result until the service recovers. Static ray results exclude dynamic
bodies and gameplay effects.

See the [synthetic ray contract](../fixtures/behavior/statescript-ray-cast-v1.json).

## Find the first actor along a ray

Use `EntityRayCast` with the same `origin`, `direction`, and
`max_distance` expressions. An optional `filter` uses the existing room
team, liveness, and tag rules. The team filter resolves its source from
the full room snapshot and excludes that source from the eligible IDs.
Without a filter the caller's captured capsule is eligible. A team
filter whose source is absent from the sampled frame is unavailable,
including when a replacement body has not joined the frame yet.
The VM spends work for each scanned candidate and each eligible capsule
before calling the room. Actor geometry uses the room's frozen player
capsules and existing NPC collision proxies.

A hit produces `[entity, distance, point]`. A ray starting inside an
eligible capsule can return distance zero. At equal distances, the
lowest entity ID wins. A static surface strictly ahead of the actor
occludes it. An available ray with no visible eligible hit returns
`[]`. Unavailable ray data preserves an observation's last successful
value until the query recovers.

The result identifies an actor in the sampled frame. If a script queues
a health or message operation in that frame, the room captures the
target's identity and checks its generation at settlement. See the
[synthetic actor ray contract](../fixtures/behavior/statescript-entity-ray-cast-v1.json).

## Inspect StateScript ray samples

In an Inspector build, enable **Rays & probes** to see script queries
from both player and NPC bodies. Open **Script ray samples** to compare the
room capture tick with the requesting VM frame and locate the graph,
instance, and node. The overlay distinguishes actor hits, walls, misses,
and unavailable host samples. A wall can hide an eligible actor while the
script receives the normal empty actor result.

The room retains its newest 64 ray samples across script and weapon
queries. A request that fails before reaching a captured room frame still
has its normal VM error path. The overlay records results from the same
queries the VM uses.

## Match a room entity against authored conditions

Use `EntityMatches` as the `$` tag and supply an `entity` expression
that yields a bound entity reference. An optional `filter` can specify
`alive`, a team `relation` with its `source` expression, and `tags`.
A known entity that satisfies the configured conditions returns true.
An entity absent from an available room snapshot returns false once the
filter source, if supplied, is resolved. An `alive` condition can select
either living bodies or registered dead bodies in that snapshot.

Tag conditions are `all`, `any`, and `none`. Each takes a list of
nonzero hexadecimal tag IDs. Empty lists impose no condition. A known
mismatch rejects the entity. An unresolved condition returns an unavailable
query instead of assuming a tag is missing. Use the same conditions in
`EntitiesInRadius.filter` to narrow its living candidates before visibility
checks. A relative radius query still needs a living source. Both
expressions use the same affiliation rule and room frame.

The room collects body metadata when a server graph uses these conditions.
It copies tag data only for graphs that request tag checks. Player and AI
scripts read the same snapshot, including tag grants and suppressions
that were active when that frame began. An observation can
retry on the next frame and keep its previous result while facts are
unavailable.

See the [synthetic entity filter contract](../fixtures/behavior/statescript-room-entity-filter-v1.json).

## Observe an expression across frames

Declare `"state_behavior": "observe"` on a server-only state or use
`"observe_state"` in the bundle class catalog. Give it a `value`
expression and a writable `output` variable. An optional `on_change`
plug follows the first successful sample and each changed sample after it.
An optional `on_sample` plug follows every successful sample, including
an unchanged result or a known empty result. The state writes the current
value before either plug. On a changed sample, `on_change` runs first.
If the observation has stopped or restarted by the time the sample
callback runs, the old activation's callback is dropped. If another
state or an earlier plug overwrote the output, the VM restores this
activation's sample at callback entry. The normal variable binding
priority still applies. If a binding hides the sample, the VM reports
the output as unavailable and suppresses that callback.

State starts queued directly by `on_sample` or `on_change`, or through
their synchronous actions, carry the callback's view of the output into
initialization. Another observer can write the shared variable before
the queued state starts. The queued state's own read still uses its
captured input, subject to ordinary binding priority. An explicit write
in the callback affects starts queued after that write. A start is
discarded when its source activation ends or the sample frame advances.
The capture lasts for initialization and its synchronous actions.

When a sampled callback starts or changes a logical-button listener's
held or idle branch, the branch and its starting states use the sample
while it is current and visible. If the observation ends or a higher
binding hides the sample, the committed branch continues with current
values. The listener's own cancellation still stops the branch.

Sampling starts in the VM frame after activation and runs at most once
per VM frame. That lets a body initialize before the room publishes its
candidate snapshot. Unavailable scene data records an observation diagnostic
and preserves the last successful result without failing the runtime.
The state retries on the next frame. An optional `on_unavailable` plug
runs after a transient host failure on that frame. It follows the same
activation and frame guards as `on_sample`. Use it to clear derived
target or presentation state while retaining the last successful raw
sample. Queued starts also check those guards and read the ordinary
variables when this callback has no fresh value. Invalid expressions
still report regular evaluation errors. Finish and abort stop polling
and leave the last written value
available. The ordinary lifecycle plugs can clear it when needed.

Use `on_sample` for effects that depend on a current ray result. When
the geometry or room snapshot is unavailable, this plug stays inactive.
A known empty ray result still reaches the plug so the graph can handle
a miss. Keep target effects behind the graph's hit check. A target
indicator can use the hit check to select its ready or empty value and
use `on_unavailable` to clear it when the host cannot sample the scene.

Use `EntitiesInRadius` as the value to follow membership of an area.
The [synthetic observation contract](../fixtures/behavior/script-observe-radius-v1.json)
exercises entry, departure, recovery, and state lifetime.

## Follow an action

1. [`script_graph.rs`](../rust/crates/statescript/src/script_graph.rs) loads authored graph metadata and preserves wire slots and link order.
2. [`script_init.rs`](../rust/crates/statescript/src/script_init.rs) exposes the component API; [`machine.rs`](../rust/crates/statescript/src/script_init/machine.rs) owns execution state and queues.
3. [`vm/nodes.rs`](../rust/crates/statescript/src/script_init/vm/nodes.rs) dispatches actions and writes variables. Reuse its shared write and routing mechanisms, including the newer nested-destination support.
4. [`script_expr.rs`](../rust/crates/statescript/src/script_expr.rs) evaluates live operands, while [`world.rs`](../rust/crates/statescript/src/script_init/world.rs) supplies host queries. Values, World reads and random draws must retain their event-time semantics.
5. [`script_snapshot_wire.rs`](../rust/src/script_snapshot_wire.rs) encodes the resulting state. Fixture agreement and original-client presentation are separate acceptance categories.

## Check graph links

With the Inspector enabled, startup logs show up to 16 decoded links whose
target position is absent from both the node and container tables. Each warning
identifies the graph, source node, plug path, and zero-based link index. You can
also inspect links through `GraphStore::dangling_links`. Explicit null targets
and declared containers are valid. The report is read-only and continues to
show structural problems even if the VM never reaches the source node.

Startup also counts nodes declared client-only and nodes explicitly
declared as authored plain states. Those counts include unreached nodes.
The live Inspector reports active plain states under `active_states` and
separates known registration rejections from other unsupported nodes. The first 16
rejected graph and node locations appear in `registration_rejection_sites`.
The registration category covers explicit placement checks in action and
state dispatch and the shared guarded state registration path. An accepted
fallback produces no rejection. When dispatch is unresolved, a rejection
means all evaluated registration checks denied the node. If an explicit
placement passes but a separate field check leaves the node unhandled,
the fallback still reports it as unsupported.
A node's absence from a snapshot gives no evidence that execution reached it.
The Inspector VM summary displays counts by category and the bounded list
of rejected graph and node locations.

## Compare pseudocode with VM diagnostics

In the 104319 pseudocode listings, use the numeric suffix of a State label
to look up the decoded node's `rid`. The number after `state` in the header is
the state slot, and Inspector registration rejections report `pos`. Join on
graph ID and `rid`, confirm the class and state slot, then read `pos` to locate
the registration check. If the prepared graph has no matching node, record
the missing match before interpreting the listing.

Pseudocode shows the authored fields and links. An exporter annotation such as
`plain State` describes its interpretation of the node. Check Rust dispatch
and runtime evidence before drawing conclusions about the node's effects.

## Inspect observed node targets

Inspector builds collect up to 128 distinct graph and node positions per
script component. The VM panel shows the count and flags any observations
omitted after the list fills. The existing `/api/debug/v1/state` response includes each component's
`node_observations` with the positions and the `client_scope_skipped` flag.
A false flag means the target passed the server scope check. Other
diagnostics can still describe why its dispatch failed.

This records resolved link targets, action dispatch, state starts and
delivery of queued messages. It does not record every callback or
candidates filtered before those routes. An absent position was not
observed at the instrumented points, and a capped list can also omit later
positions. The observations are local to the component and copied with a
cloned VM snapshot. Default builds do not collect them.

## Pinned action contract

The [synthetic fixture](../fixtures/behavior/script-actions-v1.json) records 15 cases against Rust `f763beb9d7017b9364a3c8fc79ae6192df590043`. Its expected values were checked against that old executor before integration. The [consumer](../rust/crates/statescript/src/script_init/tests/actions_contract.rs) uses the current production loader and VM, checking scoped writes, dynamic values, error sites, RNG consumption, index behavior, work budgets and ordered links.

```powershell
cargo test --locked -p orpheus-statescript --lib actions_preserve_the_pinned_synthetic_reference_contract
```

This is a Rust regression contract with a historical reference, not a Python or original-server equivalence claim. It runs in both public workspace test lanes; the existing cross-language contracts remain separate. Do not update expected output merely to accommodate an implementation change: explain the evidence and version any intentional semantic change.

## Synthetic action benchmark

```powershell
cargo bench --locked --bench script_actions
```

The [Criterion harness](../rust/benches/script_actions.rs) uses production `GraphStore` and `Component` APIs. One workload parses, compiles and drops an 11-node synthetic graph; another processes 32 messages with changing assignment, conditional and Switch inputs. Component setup is outside the execution timing. Expected state is checked before sampling, including every conditional and Switch path. `cargo test --all-targets` runs both workloads in test mode without performance sampling.

For comparisons, keep the benchmark, dependency lock, compiler, feature set, profile and machine conditions identical. Save a baseline using `--save-baseline <name>`, then compare using `--baseline <name>` and the same `CRITERION_HOME`. Record absolute time and confidence intervals for loading and execution separately. Criterion's time estimate and change estimate use different statistics; do not derive its reported percentage from displayed slope estimates.

This measures synthetic loading and action execution only. Full tick latency, allocation cost, prepared encounters, collision solving and client performance require separate workloads. Raw benchmark results and runtime data stay local.

## Integration decisions

- Retained the 15-case contract, production-API benchmark and contributor guidance from the local experiment.
- Kept the current `script_init/`, `game_combat/` and `scene_assets/` splits. The superseded local split and older README/architecture snapshots are not applied.
- Deferred the experimental typed action IR. It predates current VM behavior, including nested destinations. Its earlier synthetic comparison measured roughly doubled load cost and about 9% lower execution time; those results do not describe the current branch. Any later IR proposal must preserve current contracts and justify loading, memory and execution tradeoffs against a fresh baseline.
- Follow [current architecture](NATIVE_SERVER_ARCHITECTURE.md), [verification state](STATE.md) and [roadmap](ROADMAP.md); the removed historical tracking documents are not restored.
