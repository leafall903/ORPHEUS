using System.Runtime.Loader;
using System.Text.Json;
using DataTool.Helper;
using TankLib;
using TankLib.Chunks;
using TankLib.STU.Types;
using Tool = DataTool.Program;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length != 5)
        {
            Console.Error.WriteLine("Usage: VoiceCatalog <DataTool directory> <install> <text locale> <speech locale> <new output directory>");
            return 2;
        }
        try
        {
            var libraries = Path.GetFullPath(args[0]);
            AssemblyLoadContext.Default.Resolving += (context, name) => {
                var candidate = Path.Combine(libraries, name.Name + ".dll");
                return File.Exists(candidate) ? context.LoadFromAssemblyPath(candidate) : null;
            };
            Export(args[1], args[2], args[3], args[4]);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static string Key(ulong value) => $"0x{value:X16}";

    private static void Export(string install, string text, string speech, string output)
    {
        (text, speech) = VoiceLocales.Resolve(install, text, speech);
        Tool.Flags = new DataTool.ToolFlags {
            OverwatchDirectory = install, Language = text, SpeechLanguage = speech, NoLanguageRegistry = true,
            Online = false, UseCache = false, SkipKeys = true
        };
        Tool.InitStorage(false);
        if (Tool.BuildVersion != 104319) throw new InvalidDataException($"Expected 104319, found {Tool.BuildVersion}.");
        if (Tool.TankHandler.m_textContentManifest == null || Tool.TankHandler.m_speechContentManifest == null)
            throw new InvalidDataException($"Missing local text or speech manifest for {text}/{speech}.");

        var criteria = new SortedDictionary<string, SortedSet<string>>();
        foreach (var stimulus in Tool.TrackedFiles[0x78].Order())
        {
            using var data = STUHelper.OpenSTUSafe(stimulus);
            foreach (var trigger in data?.GetInstances<STUCriteria_Statescript>() ?? [])
            {
                ulong identifier = trigger.m_identifier;
                if (identifier == 0) continue;
                if (!criteria.TryGetValue(Key(identifier), out var stimuli))
                    criteria.Add(Key(identifier), stimuli = []);
                stimuli.Add(Key(stimulus));
            }
        }

        var heroes = new SortedDictionary<string, string>();
        foreach (var id in Tool.TrackedFiles[0x75].Order())
        {
            var hero = STUHelper.GetInstance<STUHero>(id);
            ulong set = STUHelper.GetInstance<STUVoiceSetComponent>(hero?.m_gameplayEntity)?.m_voiceDefinition;
            if (set != 0) heroes.Add(Key(id), Key(set));
        }

        var skins = new SortedDictionary<string, SortedDictionary<string, string>>();
        var baseSets = heroes.Values.ToHashSet();
        var resourceGraph = Tool.TankHandler.m_resourceGraph ?? throw new InvalidDataException("Missing skin resource graph.");
        foreach (var (skin, overrides) in resourceGraph.m_skins.OrderBy(x => x.Key))
        {
            var replacements = new SortedDictionary<string, string>();
            foreach (var row in overrides.m_assets)
            {
                if (baseSets.Contains(Key(row.m_srcAsset)) && teResourceGUID.Type(row.m_destAsset) == 0x5f)
                    replacements[Key(row.m_srcAsset)] = Key(row.m_destAsset);
            }
            if (replacements.Count > 0) skins.Add(Key(skin), replacements);
        }

        var effects = new SortedDictionary<string, string>();
        foreach (var unlock in Tool.TrackedFiles[0xa5].Order())
        {
            var voice = STUHelper.GetInstance<STUUnlock_VoiceLine>(unlock);
            ulong effect = voice?.m_F57B051E;
            if (teResourceGUID.Type(effect) == 0x0d) effects.Add(Key(unlock), Key(effect));
        }
        var equipped = new SortedDictionary<string, string>();
        foreach (var effect in effects.Values.Distinct())
        {
            using var stream = IO.OpenFile(Convert.ToUInt64(effect[2..], 16));
            if (stream == null) continue;
            using var chunked = new teChunkedData(stream, true);
            if (chunked.Chunks == null) continue;
            var stimuli = chunked.EnumerateChunks<teEffectComponentVoiceStimulus>()
                .Select(c => c.Header.VoiceStimulus.GUID)
                .Where(id => teResourceGUID.Type(id) == 0x78).Distinct().Take(2).ToArray();
            if (stimuli.Length == 1) equipped.Add(effect, Key(stimuli[0]));
        }

        var sets = new SortedDictionary<string, SortedDictionary<string, List<object>>>();
        var stateConditioned = new SortedDictionary<string, SortedDictionary<string, List<object>>>();
        var timing = new VoiceTiming();
        var unsupported = 0;
        foreach (var id in Tool.TrackedFiles[0x5f].Order())
        {
            var set = STUHelper.GetInstance<STUVoiceSet>(id);
            if (set?.m_voiceLineInstances is not {Length: > 0} rows) continue;
            if (set.m_voiceLineGuids?.Length != rows.Length)
                throw new InvalidDataException($"Mismatched line and identifier counts in voice set {Key(id)}.");
            var groups = new SortedDictionary<string, List<object>>();
            for (var i = 0; i < rows.Length; i++)
            {
                var row = rows[i].m_voiceLineRuntime;
                var key = set.m_voiceLineGuids[i];
                ulong stimulus = row?.m_stimulus;
                if (teResourceGUID.Type(key) != 0x6f || teResourceGUID.Type(stimulus) != 0x78 || row == null) continue;
                if (row.m_active == 0 || !float.IsFinite(row.m_weight) || row.m_weight <= 0) continue;
                timing.Observe(Key(key), rows[i]);
                var entry = VoiceEntry(key, row);
                if (entry == null)
                {
                    if (row.m_criteria is STU_32A19631 { m_criteria: STUCriteria_Statescript predicate } condition
                        && (ulong)predicate.m_identifier != 0)
                    {
                        if (!stateConditioned.TryGetValue(Key(id), out var byStimulus))
                            stateConditioned.Add(Key(id), byStimulus = []);
                        if (!byStimulus.TryGetValue(Key(stimulus), out var restricted))
                            byStimulus.Add(Key(stimulus), restricted = []);
                        // Keep independent graph criteria outside the currently playable set.
                        // Preserve raw fields so a reader must establish their semantics explicitly.
                        restricted.Add(new {
                            guid = Key(key), weight = row.m_weight, criterion = Key(predicate.m_identifier),
                            m_57D96E27 = predicate.m_57D96E27, m_07D0F7AA = predicate.m_07D0F7AA,
                            m_967A138B = (int)predicate.m_967A138B, container_967A138B = (int)condition.m_967A138B
                        });
                    }
                    unsupported++;
                    continue;
                }
                if (!groups.TryGetValue(Key(stimulus), out var lines)) groups.Add(Key(stimulus), lines = []);
                lines.Add(entry);
            }
            if (groups.Count > 0) sets.Add(Key(id), groups);
        }
        if (criteria.Count == 0 || sets.Count == 0 || heroes.Count == 0 || effects.Count == 0 || equipped.Count == 0)
            throw new InvalidDataException($"Incomplete voice metadata for {text}/{speech} (criteria {criteria.Count}, sets {sets.Count}, heroes {heroes.Count}, effects {effects.Count}, equipped {equipped.Count}).");

        if (Path.Exists(output)) throw new IOException($"Choose a new output directory: {output}");
        Directory.CreateDirectory(output);
        var durations = timing.Durations;
        using (var file = File.Open(Path.Combine(output, "player_voice_174.json"), FileMode.CreateNew))
            JsonSerializer.Serialize(file, new { criteria, heroes, skins, sets, equipped, durations, state_conditioned_sets = stateConditioned });
        using (var file = File.Open(Path.Combine(output, "voice_effects_174.json"), FileMode.CreateNew))
            JsonSerializer.Serialize(file, effects);
        Console.WriteLine($"Voice metadata ({text}/{speech}): {criteria.Count} criteria, {sets.Count} sets, {heroes.Count} heroes, {equipped.Count} equipped stimuli and {effects.Count} unlock effects.");
        Console.WriteLine($"Read {durations.Count} localized line durations. Skipped {unsupported} conditioned line rows not expressible by the runtime catalog.");
    }

    private static object? VoiceEntry(ulong id, STUVoiceLine row)
    {
        object? conditions = null;
        if (row.m_criteria is STU_32A19631 wrapper)
        {
            // Only the stock single positive predicate is representable in VoiceConditions.
            if (wrapper.m_criteria != null &&
                ((int)wrapper.m_967A138B != 1 || (int)wrapper.m_criteria.m_967A138B != 1 ||
                 wrapper.m_criteria.m_07D0F7AA != 1)) return null;
            switch (wrapper.m_criteria)
            {
                case null:
                    break;
                case STUCriteria_OnMap rule when (ulong)rule.m_map != 0:
                    conditions = new { map = (ulong)rule.m_map };
                    break;
                default:
                    return null;
            }
        }
        else if (row.m_criteria != null) return null;
        if (conditions != null) return new { guid = Key(id), weight = row.m_weight, conditions };
        return row.m_weight == 1f ? Key(id) : new { guid = Key(id), weight = row.m_weight };
    }
}
