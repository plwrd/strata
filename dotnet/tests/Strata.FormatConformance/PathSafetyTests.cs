using Strata.Core.Errors;
using Strata.Infrastructure.Storage;

namespace Strata.FormatConformance;

/// <summary>
/// Path traversal is a security rule, not a nicety. These mirror the Python
/// suite's refusals — a component that escapes the root must be refused
/// identically on every platform, not just the one we happen to run on.
/// </summary>
public class PathSafetyTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "strata-paths-" + Guid.NewGuid().ToString("N"));

    public PathSafetyTests() => Directory.CreateDirectory(_root);

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    // -- safe_filename -------------------------------------------------------

    [Theory]
    [InlineData("Plain Title", "Plain Title")]
    [InlineData("with/slash", "with-slash")]
    [InlineData("with\\backslash", "with-backslash")]
    [InlineData("a<b>c:d\"e|f?g*h", "a-b-c-d-e-f-g-h")]
    [InlineData("  padded  ", "padded")]
    public void Safe_filenames_replace_every_unsafe_character(string input, string expected)
        => Assert.Equal(expected, Paths.SafeFilename(input));

    [Fact]
    public void Traversal_tokens_are_collapsed_not_merely_escaped()
    {
        // "../../evil" must not sanitise to something still containing "..".
        var result = Paths.SafeFilename("../../evil");

        Assert.DoesNotContain("..", result, StringComparison.Ordinal);
        Assert.Equal("evil", result);
    }

    [Theory]
    [InlineData("...")]
    [InlineData("   ")]
    [InlineData("- . -")]
    [InlineData("")]
    public void A_name_that_sanitises_to_nothing_is_refused(string input)
        => Assert.Throws<InvalidRequestException>(() => Paths.SafeFilename(input));

    [Theory]
    [InlineData("CON", "_CON")]
    [InlineData("nul", "_nul")]
    [InlineData("COM1.md", "_COM1.md")]
    [InlineData("LPT9", "_LPT9")]
    public void Reserved_windows_device_names_are_prefixed(string input, string expected)
        => Assert.Equal(expected, Paths.SafeFilename(input));

    [Fact]
    public void Names_are_truncated_to_the_maximum_length()
        => Assert.Equal(Paths.MaxNameLength, Paths.SafeFilename(new string('x', 400)).Length);

    [Fact]
    public void A_trailing_non_breaking_space_is_stripped()
        => Assert.Equal("Title", Paths.SafeFilename("Title\u00a0"));

    // -- resolve_within ------------------------------------------------------

    [Fact]
    public void A_plain_component_resolves_inside_the_root()
    {
        var resolved = Paths.ResolveWithin(_root, "Folder", "note.md");

        Assert.StartsWith(_root, resolved, StringComparison.OrdinalIgnoreCase);
        Assert.EndsWith("note.md", resolved, StringComparison.Ordinal);
    }

    [Fact]
    public void Empty_components_are_skipped()
        => Assert.Equal(
            Paths.ResolveWithin(_root, "note.md"),
            Paths.ResolveWithin(_root, string.Empty, "note.md", string.Empty));

    [Theory]
    [InlineData("..")]
    [InlineData("../escape")]
    [InlineData("nested/../..")]
    [InlineData("a\\..\\b")]
    [InlineData("C:\\Windows")]
    [InlineData("/etc/passwd")]
    [InlineData("\\\\server\\share")]
    public void Traversal_and_absolute_components_are_refused(string part)
        => Assert.Throws<InvalidRequestException>(() => Paths.ResolveWithin(_root, part));

    [Fact]
    public void A_nul_byte_is_a_typed_refusal_not_a_crash()
        => Assert.Throws<InvalidRequestException>(() => Paths.ResolveWithin(_root, "note\0.md"));

    [Fact]
    public void A_backslash_is_refused_even_where_the_platform_would_allow_it()
        => Assert.Throws<InvalidRequestException>(() => Paths.ResolveWithin(_root, "a\\b"));

    [Fact]
    public void A_sibling_root_with_a_shared_prefix_is_outside()
    {
        // "/tmp/root" and "/tmp/rootless" share a textual prefix but not a tree.
        var sibling = _root + "less";
        Directory.CreateDirectory(sibling);
        try
        {
            // Reaching it needs a traversal component, which is refused outright —
            // the prefix rule is what stops a bypass if one ever slipped through.
            Assert.Throws<InvalidRequestException>(() => Paths.ResolveWithin(_root, "..", "rootless"));
        }
        finally
        {
            Directory.Delete(sibling, recursive: true);
        }
    }

    [Fact]
    public void A_symlink_pointing_out_of_the_root_is_refused()
    {
        var outside = Path.Combine(Path.GetTempPath(), "strata-outside-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(outside);
        var link = Path.Combine(_root, "escape");

        try
        {
            Directory.CreateSymbolicLink(link, outside);
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException or PlatformNotSupportedException)
        {
            // Creating symlinks needs Developer Mode or admin on Windows. The rule is
            // still enforced; this environment just cannot stage the attack.
            return;
        }
        finally
        {
            if (Directory.Exists(outside) && !Directory.Exists(link))
            {
                Directory.Delete(outside, recursive: true);
            }
        }

        try
        {
            Assert.Throws<InvalidRequestException>(() => Paths.ResolveWithin(_root, "escape", "note.md"));
        }
        finally
        {
            Directory.Delete(link);
            Directory.Delete(outside, recursive: true);
        }
    }
}
