using Strata.Core.Errors;
using Strata.Infrastructure.Storage;

namespace Strata.FormatConformance;

/// <summary>
/// Frontmatter handling and the write/read cycle in a scratch layer.
/// </summary>
public class MarkdownStoreTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "strata-md-" + Guid.NewGuid().ToString("N"));

    public MarkdownStoreTests() => Directory.CreateDirectory(_root);

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    private MarkdownLayerStore Store()
    {
        var store = new MarkdownLayerStore("layer-scratch", _root);
        store.Ensure();
        return store;
    }

    // -- frontmatter ---------------------------------------------------------

    [Fact]
    public void A_document_without_a_fence_is_all_body()
    {
        var (properties, body) = Frontmatter.Parse("# Just a heading\n");

        Assert.Empty(properties);
        Assert.Equal("# Just a heading\n", body);
    }

    [Fact]
    public void A_fenced_mapping_is_split_off_and_the_body_is_left_trimmed()
    {
        var (properties, body) = Frontmatter.Parse("---\nstatus: open\n---\n\nBody here.\n");

        Assert.Equal("open", properties["status"]);
        Assert.Equal("Body here.\n", body);
    }

    [Fact]
    public void Malformed_yaml_is_treated_as_no_frontmatter_rather_than_an_error()
    {
        const string text = "---\n\tthis: [is not\n---\nBody\n";

        var (properties, body) = Frontmatter.Parse(text);

        Assert.Empty(properties);
        Assert.Equal(text, body);
    }

    [Fact]
    public void A_non_mapping_document_is_discarded()
    {
        const string text = "---\n- just\n- a list\n---\nBody\n";

        var (properties, body) = Frontmatter.Parse(text);

        Assert.Empty(properties);
        Assert.Equal(text, body);
    }

    [Fact]
    public void An_unterminated_fence_leaves_the_document_alone()
    {
        const string text = "---\nstatus: open\nno closing fence\n";

        var (properties, body) = Frontmatter.Parse(text);

        Assert.Empty(properties);
        Assert.Equal(text, body);
    }

    [Fact]
    public void Scalars_resolve_to_yaml_core_types()
    {
        var (properties, _) = Frontmatter.Parse(
            "---\ncount: 3\nratio: 1.5\ndraft: false\nempty: null\nquoted: \"7\"\n---\nBody\n");

        Assert.Equal(3L, properties["count"]);
        Assert.Equal(1.5, properties["ratio"]);
        Assert.Equal(false, properties["draft"]);
        Assert.Null(properties["empty"]);
        Assert.Equal("7", properties["quoted"]);
    }

    [Fact]
    public void Rendering_is_empty_for_empty_properties()
        => Assert.Equal(string.Empty, Frontmatter.Render(new Dictionary<string, object?>()));

    [Fact]
    public void Rendered_frontmatter_is_fenced_key_sorted_and_reparses()
    {
        var rendered = Frontmatter.Render(new Dictionary<string, object?>
        {
            ["status"] = "open",
            ["alpha"] = 1L,
        });

        Assert.StartsWith("---\n", rendered, StringComparison.Ordinal);
        Assert.EndsWith("---\n\n", rendered, StringComparison.Ordinal);
        Assert.True(rendered.IndexOf("alpha", StringComparison.Ordinal)
            < rendered.IndexOf("status", StringComparison.Ordinal));

        var (properties, _) = Frontmatter.Parse(rendered);
        Assert.Equal("open", properties["status"]);
        Assert.Equal(1L, properties["alpha"]);
    }

    [Fact]
    public void Unicode_is_written_literally_not_escaped()
    {
        var rendered = Frontmatter.Render(new Dictionary<string, object?> { ["title"] = "Übergrößenträger" });

        Assert.Contains("Übergrößenträger", rendered, StringComparison.Ordinal);
    }

    // -- store ---------------------------------------------------------------

    [Fact]
    public void A_written_note_reads_back_with_its_frontmatter_lifted()
    {
        var store = Store();

        var note = store.WriteNote(
            "Deals",
            "My Note",
            "Body with #tag\n",
            new Dictionary<string, object?> { ["status"] = "open", ["tags"] = new List<object?> { "deals" } });

        Assert.Equal("My Note", note.Metadata.Title);
        Assert.Equal("Deals", note.Metadata.FolderPath);
        Assert.Equal("Body with #tag\n", note.Content);
        Assert.Equal(["deals", "tag"], note.Metadata.Tags);
        Assert.Equal("open", note.Metadata.Properties["status"]!.GetValue<string>());
    }

    [Fact]
    public void A_title_in_frontmatter_wins_over_the_filename()
    {
        var store = Store();
        File.WriteAllText(Path.Combine(_root, "filename.md"), "---\ntitle: Real Title\n---\n\nBody\n");

        Assert.Equal("Real Title", store.ListNotes().Single().Metadata.Title);
    }

    [Fact]
    public void A_bare_string_tag_is_accepted_as_a_one_item_list()
    {
        var store = Store();
        File.WriteAllText(Path.Combine(_root, "n.md"), "---\ntags: solo\naliases: other\n---\n\nBody\n");

        var note = store.ListNotes().Single();
        Assert.Equal(["solo"], note.Metadata.Tags);
        Assert.Equal(["other"], note.Metadata.Aliases);
    }

    [Fact]
    public void Dot_prefixed_directories_are_not_the_users_notes()
    {
        var store = Store();
        store.WriteNote(string.Empty, "Real", "body");
        Directory.CreateDirectory(Path.Combine(_root, ".strata"));
        File.WriteAllText(Path.Combine(_root, ".strata", "internal.md"), "hidden");

        Assert.Single(store.ListNotes());
        Assert.Empty(store.ListFolders());
    }

    [Fact]
    public void The_parse_cache_is_dropped_when_a_file_changes_on_disk()
    {
        var store = Store();
        var note = store.WriteNote(string.Empty, "Note", "first\n");
        Assert.Equal("first\n", note.Content);

        var path = store.PathFor(string.Empty, "Note");
        File.WriteAllText(path, "second\n");
        File.SetLastWriteTimeUtc(path, DateTime.UtcNow.AddSeconds(1));

        Assert.Equal("second\n", store.ListNotes().Single().Content);
    }

    [Fact]
    public void Deleted_files_fall_out_of_the_cache()
    {
        var store = Store();
        store.WriteNote(string.Empty, "Note", "body");
        File.Delete(store.PathFor(string.Empty, "Note"));

        Assert.Empty(store.ListNotes());
    }

    [Fact]
    public void A_title_that_would_escape_the_layer_is_sanitised_into_it()
    {
        var store = Store();

        var path = store.PathFor(string.Empty, "../../evil");

        Assert.StartsWith(_root, path, StringComparison.OrdinalIgnoreCase);
        Assert.Equal("evil.md", Path.GetFileName(path));
    }

    [Fact]
    public void A_folder_component_that_escapes_the_layer_is_refused()
        => Assert.Throws<InvalidRequestException>(() => Store().PathFor("../escape", "Note"));

    [Fact]
    public void Folders_are_listed_with_parents_linked()
    {
        var store = Store();
        store.WriteNote("Outer/Inner", "Note", "body");

        var folders = store.ListFolders();

        Assert.Equal(2, folders.Count);
        var outer = folders.Single(folder => folder.Path == "Outer");
        var inner = folders.Single(folder => folder.Path == "Outer/Inner");
        Assert.Null(outer.ParentId);
        Assert.Equal(outer.Id, inner.ParentId);
    }

    [Fact]
    public void Rewriting_a_note_replaces_it_rather_than_appending()
    {
        var store = Store();
        store.WriteNote(string.Empty, "Note", "first\n");
        var second = store.WriteNote(string.Empty, "Note", "second\n");

        Assert.Equal("second\n", second.Content);
        Assert.Single(store.ListNotes());
    }
}
