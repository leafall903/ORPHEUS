using System.Globalization;
using System.Runtime.Loader;
using System.Text.Json;
using DataTool;
using DataTool.Helper;
using SharpDX;
using TankLib;
using TankLib.Chunks;
using TankLib.STU.Types;
using Tool = DataTool.Program;

internal static class Program
{
    private const int Build = 104319;
    private const string DefaultSkin = "0x0000000000000000";

    private static int Main(string[] args)
    {
        if (args.Length != 4)
        {
            Console.Error.WriteLine("Usage: ModelAnchors <DataTool directory> <install> <hero catalog> <output directory>");
            return 2;
        }
        try
        {
            var assemblies = Path.GetFullPath(args[0]);
            AssemblyLoadContext.Default.Resolving += (context, name) =>
            {
                var file = Path.Combine(assemblies, name.Name + ".dll");
                return File.Exists(file) ? context.LoadFromAssemblyPath(file) : null;
            };
            Export(Path.GetFullPath(args[1]), args[2], Path.GetFullPath(args[3]));
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }

    private static SortedSet<ulong> ReadBodies(string path)
    {
        using var file = File.OpenRead(path);
        using var heroes = JsonDocument.Parse(file);
        var bodies = new SortedSet<ulong>();
        foreach (var hero in heroes.RootElement.EnumerateObject())
        {
            if (!hero.Value.TryGetProperty("body", out var body) || body.ValueKind == JsonValueKind.Null)
                continue;
            var text = body.GetString();
            if (text is null || !text.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
                || !ulong.TryParse(text.AsSpan(2), NumberStyles.AllowHexSpecifier,
                    CultureInfo.InvariantCulture, out var id) || id == 0)
                throw new InvalidDataException($"Invalid body identifier for {hero.Name}.");
            bodies.Add(id);
        }
        if (bodies.Count == 0)
            throw new InvalidDataException("The hero catalog has no body identifiers.");
        return bodies;
    }

    private static void Export(string install, string catalog, string output)
    {
        var ids = ReadBodies(catalog);
        Tool.Flags = new ToolFlags {
            OverwatchDirectory = install, Language = "enUS", SpeechLanguage = "enUS",
            NoLanguageRegistry = true, Online = false, UseCache = false, SkipKeys = true
        };
        Tool.InitStorage(false);
        if (Tool.BuildVersion != Build)
            throw new InvalidDataException($"Expected build {Build}, found {Tool.BuildVersion}.");

        var bodies = new SortedDictionary<string, object>(StringComparer.Ordinal);
        var rigs = new SortedDictionary<string, object>(StringComparer.Ordinal);
        foreach (var body in ids)
        {
            var component = STUHelper.GetInstance<STUModelComponent>(body);
            if (component?.m_model == null)
                throw new InvalidDataException($"Missing default model for body 0x{body:X16}.");
            using var stream = Tool.TankHandler.OpenFile(component.m_model);
            if (stream is null)
                throw new InvalidDataException($"Cannot open the default model for body 0x{body:X16}.");
            using var reader = new BinaryReader(stream);
            var chunks = new teChunkedData(reader);
            var skeleton = chunks.GetChunk<teModelChunk_Skeleton>();
            var hardpoints = chunks.GetChunk<teModelChunk_STU>()?.StructuredData?.m_hardPoints;
            if (hardpoints is null)
                throw new InvalidDataException($"Missing hardpoint data for body 0x{body:X16}.");
            var names = new SortedSet<string>(StringComparer.Ordinal);
            var positions = new SortedDictionary<string, float[]>(StringComparer.Ordinal);
            var bindings = new SortedDictionary<string, object>(StringComparer.Ordinal);
            foreach (var point in hardpoints)
            {
                var key = $"0x{(ulong)point.m_EDF0511C:X16}";
                names.Add(key);
                var parent = Matrix.Identity;
                int? boneIndex = null;
                if (point.m_FF592924 != 0)
                {
                    var bone = skeleton == null ? -1 : Array.FindIndex(skeleton.IDs,
                        id => id == teResourceGUID.Index(point.m_FF592924));
                    if (bone < 0)
                        continue;
                    parent = skeleton!.GetWorldSpace(bone);
                    boneIndex = bone;
                }
                var transform = Matrix.RotationQuaternion(point.m_rotation) * Matrix.Translation(point.m_position) * parent;
                if (!float.IsFinite(transform.M41) || !float.IsFinite(transform.M42) || !float.IsFinite(transform.M43))
                    continue;
                positions.Add(key, new[] { transform.M41, transform.M42, transform.M43 });
                bindings.Add(key, new { bone_index = boneIndex, local_position = new[] { point.m_position.X, point.m_position.Y, point.m_position.Z } });
            }
            var bones = new List<object>();
            if (skeleton != null)
            {
                for (var i = 0; i < skeleton.IDs.Length; i++)
                {
                    var m = skeleton.GetWorldSpace(i);
                    bones.Add(new { id = skeleton.IDs[i], parent_index = skeleton.Hierarchy[i],
                        model_reference_matrix = new[] {
                            m.M11, m.M12, m.M13, m.M14, m.M21, m.M22, m.M23, m.M24,
                            m.M31, m.M32, m.M33, m.M34, m.M41, m.M42, m.M43, m.M44
                        } });
                }
            }
            var name = $"0x{body:X16}";
            bodies.Add(name, new[] { new { skin = DefaultSkin, hardpoints = names, reference_positions = positions } });
            rigs.Add(name, new { model = $"0x{(ulong)component.m_model:X16}", skin = DefaultSkin, bones, hardpoint_bindings = bindings });
        }
        WritePair(output, new { build = Build, bodies }, new {
            build = Build, units = "metres", pose = "model reference",
            matrix_convention = "row vector, row-major; root parent is -1", bodies = rigs
        });
        Console.WriteLine($"Exported {bodies.Count} default models to {output}.");
    }

    private static void WritePair(string output, object anchors, object skeletons)
    {
        Directory.CreateDirectory(output);
        var points = Path.Combine(output, "stock-model-anchors.json");
        var bones = Path.Combine(output, "stock-skeletons.json");
        if (File.Exists(points) || File.Exists(bones))
            throw new IOException("An output file already exists. Choose a fresh output directory.");
        var attempt = Guid.NewGuid().ToString("N");
        var pointsTemp = points + "." + attempt;
        var bonesTemp = bones + "." + attempt;
        try
        {
            using (var f = File.Open(pointsTemp, FileMode.CreateNew, FileAccess.Write))
                JsonSerializer.Serialize(f, anchors);
            using (var f = File.Open(bonesTemp, FileMode.CreateNew, FileAccess.Write))
                JsonSerializer.Serialize(f, skeletons);
            File.Move(pointsTemp, points);
            try { File.Move(bonesTemp, bones); }
            catch { File.Delete(points); throw; }
        }
        finally
        {
            File.Delete(pointsTemp);
            File.Delete(bonesTemp);
        }
    }
}
