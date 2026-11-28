# Local STU XML export

The runtime importer uses the pinned DataTool and DragonML assemblies to read a family of build 104319 assets. It adds a serializer for `System.Guid` so that a `teUUID.Value` contains the canonical 128-bit UUID read from the game. The default DragonML object serializer traverses the static `Guid.Empty` member and loses the instance value.

```powershell
dotnet run --project .\scripts\tools\stu_xml\StuXml.csproj -c Release "-p:DataToolDir=$dataToolDir" -- $dataToolDir $install 01B $outputDir
```

Use an empty local output directory. Each decoded record produces one XML file. If the pinned reader cannot decode a record, its resource identifier is listed in `unreadable.txt`. The runtime importer checks this list against the graph dependencies it requires. Keep the generated files outside the repository.
