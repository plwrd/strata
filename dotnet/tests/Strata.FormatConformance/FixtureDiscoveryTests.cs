namespace Strata.FormatConformance;

/// <summary>
/// The vectors themselves are the contract. This guards the set: a fixture that
/// stops being generated must fail loudly rather than silently drop a test.
/// </summary>
public class FixtureDiscoveryTests
{
    [Fact]
    public void Manifest_lists_all_required_vectors()
    {
        var manifest = Fixtures.Json("manifest.json");
        Assert.Equal(1, manifest.GetProperty("format_fixture_version").GetInt32());

        foreach (var required in manifest.GetProperty("required").EnumerateArray())
        {
            var name = required.GetString()!;
            Assert.True(
                File.Exists(Path.Combine(Fixtures.Root, name)),
                $"Missing required fixture: {name}. Run: python tools/format_fixtures/generate.py");
        }
    }

    [Fact]
    public void Every_file_the_manifest_lists_is_present()
    {
        var manifest = Fixtures.Json("manifest.json");
        foreach (var file in manifest.GetProperty("files").EnumerateArray())
        {
            Assert.True(File.Exists(Path.Combine(Fixtures.Root, file.GetString()!)));
        }
    }
}
