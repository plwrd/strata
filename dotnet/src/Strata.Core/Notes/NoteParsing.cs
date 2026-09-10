using System.Text.RegularExpressions;

namespace Strata.Core.Notes;

/// <summary>
/// Markdown body parsing — port of the <c>extract_*</c> helpers in
/// <c>app/domain/note.py</c>.
/// </summary>
/// <remarks>
/// Untrusted input: these only ever <em>read</em> the note body. They never resolve
/// a path, never follow an instruction, and never execute anything.
/// </remarks>
public static partial class NoteParsing
{
    [GeneratedRegex(@"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|([^\]]+))?\]\]")]
    private static partial Regex WikiLink();

    [GeneratedRegex(@"(?:^|\s)#([A-Za-z0-9][\w/-]*)")]
    private static partial Regex Tag();

    /// <summary><c>[[target]]</c> preceded by a relationship marker, e.g. <c>supports:: [[Note]]</c>.</summary>
    [GeneratedRegex(@"([a-z_]+)::\s*\[\[([^\]|#^]+)")]
    private static partial Regex TypedLink();

    /// <summary>Extract wiki links, including typed ones (<c>supports:: [[Target]]</c>).</summary>
    public static List<NoteLink> ExtractLinks(string content)
    {
        var typed = new Dictionary<string, string>();
        foreach (Match match in TypedLink().Matches(content))
        {
            var relationship = match.Groups[1].Value;
            if (Relationships.Types.Contains(relationship))
            {
                typed[match.Groups[2].Value.Trim()] = relationship;
            }
        }

        var links = new List<NoteLink>();
        var seen = new HashSet<(string Target, string Relationship)>();
        foreach (Match match in WikiLink().Matches(content))
        {
            var target = match.Groups[1].Value.Trim();
            if (target.Length == 0)
            {
                continue;
            }

            var relationship = typed.GetValueOrDefault(target, Relationships.Default);
            if (!seen.Add((target, relationship)))
            {
                continue;
            }

            var alias = match.Groups[2].Success ? match.Groups[2].Value.Trim() : null;
            links.Add(new NoteLink(target, string.IsNullOrEmpty(alias) ? null : alias, relationship));
        }

        return links;
    }

    /// <summary>Union of <c>#inline</c> tags and frontmatter tags, order-stable and unique.</summary>
    public static List<string> ExtractTags(string content, IEnumerable<string>? frontmatterTags = null)
    {
        var tags = new List<string>();
        var candidates = (frontmatterTags ?? []).Concat(
            Tag().Matches(content).Select(match => match.Groups[1].Value));

        foreach (var candidate in candidates)
        {
            var tag = candidate.TrimStart('#').Trim();
            if (tag.Length > 0 && !tags.Contains(tag))
            {
                tags.Add(tag);
            }
        }

        return tags;
    }

    /// <summary>Whitespace-separated word count, matching Python's <c>str.split()</c>.</summary>
    public static int WordCount(string content)
        => content.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).Length;
}
