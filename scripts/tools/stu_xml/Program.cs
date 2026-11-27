using System.Globalization;
using System.Runtime.Loader;
using System.Security;
using DataTool;
using DataTool.Helper;
using DataTool.ToolLogic.Render;
using DragonLib.Indent;
using DragonLib.XML;
using TankLib;
using TankLib.STU;
using Tool = DataTool.Program;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length != 4)
        {
            Console.Error.WriteLine("Usage: StuXml <DataTool directory> <install> <family in hex> <output directory>");
            return 2;
        }
        try
        {
            var assemblies = Path.GetFullPath(args[0]);
            AssemblyLoadContext.Default.Resolving += (context, name) =>
            {
                var path = Path.Combine(assemblies, name.Name + ".dll");
                return File.Exists(path) ? context.LoadFromAssemblyPath(path) : null;
            };
            var family = ushort.Parse(args[2], NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture);
            Export(Path.GetFullPath(args[1]), family, Path.GetFullPath(args[3]));
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }

    private static void Export(string install, ushort family, string output)
    {
        if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
            throw new IOException("Choose an empty output directory for this export.");
        Tool.Flags = new ToolFlags {
            OverwatchDirectory = install, Language = "enUS", SpeechLanguage = "enUS",
            NoLanguageRegistry = true, Online = false, UseCache = false, SkipKeys = true
        };
        Tool.InitStorage(false);
        if (Tool.BuildVersion != 104319)
            throw new InvalidDataException($"Expected build 104319, found {Tool.BuildVersion}.");
        if (!Tool.TrackedFiles.TryGetValue(family, out var assets) || assets.Count == 0)
            throw new InvalidDataException($"No assets from family {family:X3} in the installation.");

        var settings = new DragonMLSettings {
            TypeSerializers = new Dictionary<Type, IDragonMLSerializer> {
                [typeof(teStructuredDataAssetRef<>)] = new teResourceGUIDSerializer(),
                [typeof(Guid)] = new GuidSerializer()
            }
        };
        settings.Namespaces["tank"] = settings.Namespaces["hml"] = "https://yretenai.com/dragonml/v1";
        Directory.CreateDirectory(output);
        var unreadable = new List<string>();
        foreach (var asset in assets.Order())
        {
            var name = teResourceGUID.AsString(asset);
            using var data = STUHelper.OpenSTUSafe(asset);
            if (data?.Instances is not { Length: > 0 } || data.Instances[0] is null)
            {
                Console.Error.WriteLine($"DataTool could not decode {name}.");
                unreadable.Add(name);
                continue;
            }
            var xml = DragonML.Print(data.Instances[0], settings);
            using var file = File.Open(Path.Combine(output, name + ".xml"), FileMode.CreateNew, FileAccess.Write);
            using var writer = new StreamWriter(file);
            writer.WriteLine(xml);
        }
        if (unreadable.Count > 0)
        {
            using var file = File.Open(Path.Combine(output, "unreadable.txt"), FileMode.CreateNew, FileAccess.Write);
            using var writer = new StreamWriter(file);
            foreach (var name in unreadable) writer.WriteLine(name);
        }
        Console.WriteLine($"Exported {assets.Count - unreadable.Count} {family:X3} XML records, {unreadable.Count} unreadable.");
    }

    // DragonML's default object inspection sees Guid.Empty, a static field, and misses
    // the private bytes backing the instance. Keep the Guid as a scalar inside teUUID.
    private sealed class GuidSerializer : IDragonMLSerializer
    {
        public DragonMLType OverrideTarget => DragonMLType.Object;

        public object Print(object? value, Dictionary<object, int> visited, IndentHelperBase indent,
            string? fieldName, DragonMLSettings settings)
        {
            if (value is not Guid guid)
                throw new InvalidDataException("Expected a System.Guid value.");
            var name = fieldName == null ? "" : $" dragon:name=\"{SecurityElement.Escape(fieldName)}\"";
            if (!visited.TryGetValue(value, out var id))
            {
                id = visited.Count;
                visited.Add(value, id);
            }
            var identity = settings.UseRefId ? $" dragon:id=\"{id}\"" : "";
            return $"{indent}<Guid{identity}{name}>{guid:D}</Guid>\n";
        }
    }
}
