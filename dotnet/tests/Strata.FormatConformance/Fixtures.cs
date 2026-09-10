using System.Text.Json;

namespace Strata.FormatConformance;

/// <summary>
/// Locates and reads the committed format vectors in <c>tests/fixtures/format</c>.
/// Regenerate with <c>python tools/format_fixtures/generate.py</c>; the output must
/// be byte-identical, because these are the contract between the Python app and the
/// .NET port.
/// </summary>
internal static class Fixtures
{
    public static string Root { get; } = Locate();

    public static JsonElement Json(string name)
    {
        // Parse into a detached JsonElement so callers need not manage JsonDocument.
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(Root, name)));
        return document.RootElement.Clone();
    }

    public static byte[] Bytes(string name) => File.ReadAllBytes(Path.Combine(Root, name));

    public static byte[] Hex(this JsonElement element, string property)
        => Convert.FromHexString(element.GetProperty(property).GetString()!);

    private static string Locate()
    {
        var alongside = Path.Combine(AppContext.BaseDirectory, "fixtures", "format");
        if (File.Exists(Path.Combine(alongside, "manifest.json")))
        {
            return alongside;
        }

        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var candidate = Path.Combine(dir.FullName, "tests", "fixtures", "format");
            if (File.Exists(Path.Combine(candidate, "manifest.json")))
            {
                return candidate;
            }

            dir = dir.Parent;
        }

        throw new DirectoryNotFoundException(
            "Could not locate tests/fixtures/format. Run: python tools/format_fixtures/generate.py");
    }
}
