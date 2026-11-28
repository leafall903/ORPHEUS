using TACTLib.Client;
using TACTLib.Client.HandlerArgs;
using TACTLib.Core.Product.Tank;

internal static class VoiceLocales
{
    public static (string Text, string Speech) Resolve(string install, string text, string speech)
    {
        // build.info can still advertise optional languages after their CASC data is removed.
        var source = new ClientHandler(install, new ClientCreateArgs {
            Online = false, HandlerArgs = new ClientCreateArgs_Tank { LoadManifest = false, CacheAPM = false }
        });
        var tank = source.ProductHandler as ProductHandler_Tank ??
            throw new InvalidDataException("Expected an Overwatch installation.");

        string Readable(string kind, string preferred)
        {
            var candidates = tank.m_rootFiles
                .Select(file => (file, name: Path.GetFileNameWithoutExtension(file.FileName)))
                .Where(x => Path.GetExtension(x.file.FileName) == ".cmf" && x.name != null &&
                    x.name.Split('_').Contains(ProductHandler_Tank.REGION_DEV) && x.name.Split('_').Contains(kind))
                .Select(x => (x.file, locale: ProductHandler_Tank.GetManifestLocale(x.name!)))
                .Where(x => x.locale != null)
                .OrderBy(x => x.locale == preferred ? 0 : x.locale!.StartsWith(preferred[..2], StringComparison.Ordinal) ? 1 : 2)
                .ThenBy(x => x.locale, StringComparer.Ordinal);
            foreach (var candidate in candidates)
            {
                using var manifest = source.OpenCKey(candidate.file.MD5);
                if (manifest is { Length: > 0 }) return candidate.locale!;
            }
            throw new InvalidDataException($"No readable local {kind} manifest in this installation.");
        }

        return (Readable("text", text), Readable("speech", speech));
    }
}
