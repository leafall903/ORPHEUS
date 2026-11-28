using System.Text;
using DataTool.DataModels;
using DataTool.Helper;
using TankLib.STU.Types;

internal sealed class VoiceTiming
{
    private readonly Dictionary<ulong, double?> cache = [];
    private readonly HashSet<string> ambiguous = [];
    public SortedDictionary<string, double> Durations { get; } = [];

    public void Observe(string line, STUVoiceLineInstance row)
    {
        if (ambiguous.Contains(line)) return;
        var seconds = ForLine(row);
        if (seconds == null || (Durations.TryGetValue(line, out var prior) && seconds != prior))
        {
            // The runtime indexes duration by line GUID across all skins and sets.
            Durations.Remove(line);
            ambiguous.Add(line);
            return;
        }
        Durations[line] = seconds.Value;
    }

    private double? ForLine(STUVoiceLineInstance row)
    {
        var media = new VoiceLineInstance(row).VoiceSounds;
        if (media == null) return null;
        var unique = media.Where(id => id != 0).Distinct().Take(2).ToArray();
        if (unique.Length != 1) return null;
        var id = unique[0];
        if (!cache.TryGetValue(id, out var duration))
        {
            using var file = IO.OpenFile(id);
            duration = ReadSeconds(file);
            cache.Add(id, duration);
        }
        return duration;
    }

    // Read the local WEM header and skip its audio payload. Unknown codecs have no duration.
    internal static double? ReadSeconds(Stream? file)
    {
        if (file == null || !file.CanSeek || file.Length < 12) return null;
        using var data = new BinaryReader(file, Encoding.ASCII, true);
        if (new string(data.ReadChars(4)) != "RIFF") return null;
        var end = 8L + data.ReadUInt32();
        if (end > file.Length || new string(data.ReadChars(4)) != "WAVE") return null;
        uint rate = 0, samples = 0;
        while (file.Position + 8 <= end)
        {
            var type = new string(data.ReadChars(4));
            var length = data.ReadUInt32();
            var start = file.Position;
            if (start + length > end) return null;
            if (type == "fmt " && length >= 16)
            {
                var codec = data.ReadUInt16();
                data.ReadUInt16(); // channels
                rate = data.ReadUInt32();
                // The 104319 WWise Vorbis format can embed its sample count in fmt.
                if (codec == 0xffff && length == 0x42)
                {
                    file.Position = start + 0x18;
                    samples = data.ReadUInt32();
                }
            }
            else if ((type == "vorb" || type == "fact") && length >= 4)
                samples = data.ReadUInt32();
            if (rate != 0 && samples != 0) return (double)samples / rate;
            file.Position = start + length + (length & 1);
        }
        return null;
    }
}
