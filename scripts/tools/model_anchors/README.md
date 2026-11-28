# Default model anchors for build 104319

This optional preparation tool reads the default model of each unique body in a local `game_heroes_174.json` catalog. It writes two private inputs for runtime preparation.

| Output | Contents |
| --- | --- |
| `stock-model-anchors.json` | Default-skin hardpoint identifiers and resolved positions in model reference space |
| `stock-skeletons.json` | Default model identifier, bone hierarchy, model-space reference matrices and hardpoint-to-bone bindings |

Install the .NET 8 SDK. Supply a local build of OWLib DataTool compatible with retail 104319, including its companion assemblies and native dependencies. Use an authorized local game installation and a hero catalog from the same build. Keep the output outside the repository.

```powershell
./scripts/tools/model_anchors/export.ps1 `
    -DataToolDir 'D:\tools\DataTool' `
    -Install 'D:\games\Overwatch' `
    -Heroes 'D:\private-runtime\data\game_heroes_174.json' `
    -OutputDir 'D:\private-output\model-anchors'
```

Pass `-DotNet <path>` to select a local SDK. The helper opens game storage offline and verifies build 104319. It preserves existing output files. A hardpoint with an unresolved parent remains in the membership list and has no reference position. All transforms use the model reference pose. Animated poses and skin replacements require separate evidence.

The same CLI runs directly through the SDK, including on Windows hosts that restrict PowerShell scripts. The first path supplies build references and the first argument after `--` supplies runtime dependencies.

```text
dotnet run --project scripts/tools/model_anchors/ModelAnchors.csproj -c Release "-p:DataToolDir=D:\tools\DataTool" -- "D:\tools\DataTool" "D:\games\Overwatch" "D:\private-runtime\data\game_heroes_174.json" "D:\private-output\model-anchors"
```
