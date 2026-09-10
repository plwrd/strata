using Strata.Core.Notes;

namespace Strata.FormatConformance;

/// <summary>
/// Wiki-link and tag parsing must agree with <c>app/domain/note.py</c>, or the same
/// note produces a different graph in the two apps.
/// </summary>
public class NoteParsingTests
{
    [Fact]
    public void Plain_wiki_links_default_to_references()
    {
        var links = NoteParsing.ExtractLinks("See [[Target]].");

        var link = Assert.Single(links);
        Assert.Equal("Target", link.TargetTitle);
        Assert.Null(link.Alias);
        Assert.Equal("references", link.Relationship);
    }

    [Fact]
    public void Aliases_and_heading_anchors_are_stripped_from_the_target()
    {
        var links = NoteParsing.ExtractLinks("[[Target#Heading|Shown]]");

        var link = Assert.Single(links);
        Assert.Equal("Target", link.TargetTitle);
        Assert.Equal("Shown", link.Alias);
    }

    [Fact]
    public void A_typed_marker_sets_the_relationship_for_that_target()
    {
        var links = NoteParsing.ExtractLinks("supports:: [[Evidence]] and [[Other]]");

        Assert.Equal("supports", links.Single(l => l.TargetTitle == "Evidence").Relationship);
        Assert.Equal("references", links.Single(l => l.TargetTitle == "Other").Relationship);
    }

    [Fact]
    public void An_unknown_marker_is_not_treated_as_a_relationship()
        => Assert.Equal(
            "references",
            Assert.Single(NoteParsing.ExtractLinks("frobnicates:: [[Evidence]]")).Relationship);

    [Fact]
    public void Repeated_links_to_the_same_target_collapse()
        => Assert.Single(NoteParsing.ExtractLinks("[[A]] then [[A]] again"));

    [Fact]
    public void An_empty_target_is_ignored()
        => Assert.Empty(NoteParsing.ExtractLinks("[[   ]]"));

    [Theory]
    [InlineData("#research at the start", new[] { "research" })]
    [InlineData("mid sentence #deals/q1 here", new[] { "deals/q1" })]
    [InlineData("#a #b #a", new[] { "a", "b" })]
    [InlineData("no#tag when glued to a word", new string[0])]
    [InlineData("#1nvalid cannot start with a digit-only run", new[] { "1nvalid" })]
    public void Inline_tags_are_extracted_in_order_and_deduplicated(string content, string[] expected)
        => Assert.Equal(expected, NoteParsing.ExtractTags(content));

    [Fact]
    public void Frontmatter_tags_come_first_and_merge_with_inline_tags()
        => Assert.Equal(
            ["alpha", "beta", "gamma"],
            NoteParsing.ExtractTags("body with #gamma and #alpha", ["alpha", "beta"]));

    [Fact]
    public void A_leading_hash_on_a_frontmatter_tag_is_stripped()
        => Assert.Equal(["alpha"], NoteParsing.ExtractTags(string.Empty, ["#alpha"]));

    [Theory]
    [InlineData("", 0)]
    [InlineData("one", 1)]
    [InlineData("  one   two\nthree\t four  ", 4)]
    public void Word_count_splits_on_any_whitespace_run(string content, int expected)
        => Assert.Equal(expected, NoteParsing.WordCount(content));

    [Fact]
    public void Display_path_includes_the_folder_when_there_is_one()
    {
        var bare = new NoteMetadata { Id = "1", LayerId = "l", Title = "Note" };
        var nested = bare with { FolderPath = "Deals" };

        Assert.Equal("Note.md", bare.DisplayPath);
        Assert.Equal("Deals/Note.md", nested.DisplayPath);
    }
}
