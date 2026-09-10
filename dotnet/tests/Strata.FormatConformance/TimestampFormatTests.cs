using System.Text.RegularExpressions;
using Strata.Infrastructure.Storage;

namespace Strata.FormatConformance;

/// <summary>
/// Timestamps are written into the workspace descriptor and the encrypted
/// manifest, so both apps must agree on the spelling — a bare <c>Z</c> where
/// Python writes <c>+00:00</c> is a diff on every save.
/// </summary>
public partial class TimestampFormatTests
{
    [GeneratedRegex(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")]
    private static partial Regex PythonIsoSeconds();

    [Fact]
    public void NowIso_matches_pythons_isoformat_with_second_precision()
    {
        var now = MarkdownLayerStore.NowIso();

        Assert.Matches(PythonIsoSeconds(), now);
        Assert.DoesNotContain("Z", now, StringComparison.Ordinal);
    }

    [Fact]
    public void Note_timestamps_use_the_same_spelling()
    {
        var root = Path.Combine(Path.GetTempPath(), "strata-ts-" + Guid.NewGuid().ToString("N"));
        try
        {
            var store = new MarkdownLayerStore("layer-ts", root);
            store.Ensure();

            var note = store.WriteNote(string.Empty, "Note", "body");

            Assert.Matches(PythonIsoSeconds(), note.Metadata.CreatedAt);
            Assert.Matches(PythonIsoSeconds(), note.Metadata.UpdatedAt);
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, recursive: true);
            }
        }
    }
}
