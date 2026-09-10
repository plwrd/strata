using System.Text;
using System.Text.Json.Nodes;
using Strata.Core.Notes;
using Strata.Infrastructure.Encryption;

namespace Strata.Infrastructure.Storage;

/// <summary>
/// Markdown-file storage for public layers — port of
/// <c>app/infrastructure/storage/markdown_store.py</c>.
/// </summary>
/// <remarks>
/// Markdown on disk is the source of truth. This store parses files into domain
/// objects and writes them back; it never becomes the authority. If a user edits a
/// file in another editor, the next scan simply reflects it.
/// </remarks>
public sealed class MarkdownLayerStore(string layerId, string root)
{
    public const string MarkdownSuffix = ".md";

    public string LayerId { get; } = layerId;

    public string Root { get; } = root;

    /// <summary>(size, mtime, note) — skip re-parsing unchanged files on list/search.</summary>
    private readonly Dictionary<string, (long Size, DateTime Modified, Note Note)> _noteCache = new(StringComparer.Ordinal);

    /// <summary>
    /// Second-precision UTC with an explicit offset, matching Python's
    /// <c>datetime.now(tz=utc).isoformat(timespec="seconds")</c> — e.g.
    /// <c>2026-09-10T10:20:45+00:00</c>, never a bare <c>Z</c>.
    /// </summary>
    public static string NowIso() => Iso(DateTime.UtcNow);

    private static string Iso(DateTime timestamp)
        => timestamp.ToUniversalTime()
            .ToString("yyyy-MM-ddTHH:mm:sszzz", System.Globalization.CultureInfo.InvariantCulture);

    /// <summary>
    /// A stable, opaque id for a public note.
    /// </summary>
    /// <remarks>
    /// Derived from the layer id and the path so that ids survive a restart without
    /// a sidecar database. Public layers have no filename privacy requirement (that
    /// is what private layers are for), so a derived id is acceptable here and is
    /// explicitly <em>not</em> used for private layers — see ADR-0004.
    /// </remarks>
    public static string NoteIdFor(string layerId, string relativePath)
        => Convert.ToHexString(
                Blake2b.Hash(Encoding.UTF8.GetBytes($"{layerId}\0{relativePath}"), 16))
            .ToLowerInvariant();

    public void Ensure() => Directory.CreateDirectory(Root);

    private string Relative(string path)
        => Path.GetRelativePath(Root, path).Replace(Path.DirectorySeparatorChar, '/');

    public List<string> IterMarkdownFiles()
    {
        if (!Directory.Exists(Root))
        {
            return [];
        }

        return Directory.EnumerateFiles(Root, $"*{MarkdownSuffix}", SearchOption.AllDirectories)
            .Where(path => !IsHidden(path))
            .Order(StringComparer.Ordinal)
            .ToList();
    }

    /// <summary>Dot-prefixed components are tooling, not the user's notes.</summary>
    private bool IsHidden(string path)
        => Relative(path).Split('/').Any(part => part.StartsWith('.'));

    public Note ReadNote(string path)
    {
        var info = new FileInfo(path);
        if (_noteCache.TryGetValue(path, out var cached)
            && cached.Size == info.Length
            && cached.Modified == info.LastWriteTimeUtc)
        {
            return cached.Note;
        }

        // errors="replace": a file that is not valid UTF-8 is damaged content, not a
        // reason to refuse to list the layer.
        var text = new UTF8Encoding(false, throwOnInvalidBytes: false).GetString(File.ReadAllBytes(path));
        var (frontmatter, body) = Frontmatter.Parse(text);
        var relative = Relative(path);

        var rawTags = StringList(frontmatter.GetValueOrDefault("tags"));
        var rawAliases = StringList(frontmatter.GetValueOrDefault("aliases"));

        var properties = new JsonObject();
        foreach (var (key, value) in frontmatter)
        {
            if (key is "tags" or "aliases" or "title")
            {
                continue;
            }

            properties[key] = ToJson(value);
        }

        var title = frontmatter.GetValueOrDefault("title")?.ToString() is { Length: > 0 } fromFrontmatter
            ? fromFrontmatter
            : Path.GetFileNameWithoutExtension(path);

        var folderPath = relative.Contains('/', StringComparison.Ordinal)
            ? relative[..relative.LastIndexOf('/')]
            : string.Empty;

        var note = new Note(
            new NoteMetadata
            {
                Id = NoteIdFor(LayerId, relative),
                LayerId = LayerId,
                Title = title,
                FolderPath = folderPath,
                Aliases = rawAliases,
                Tags = NoteParsing.ExtractTags(body, rawTags),
                Properties = properties,
                Links = NoteParsing.ExtractLinks(body),
                CreatedAt = Iso(info.CreationTimeUtc),
                UpdatedAt = Iso(info.LastWriteTimeUtc),
                SizeBytes = (int)info.Length,
                WordCount = NoteParsing.WordCount(body),
            },
            body);

        _noteCache[path] = (info.Length, info.LastWriteTimeUtc, note);
        return note;
    }

    public List<Note> ListNotes()
    {
        var files = IterMarkdownFiles();
        var live = files.ToHashSet(StringComparer.Ordinal);
        foreach (var stale in _noteCache.Keys.Where(path => !live.Contains(path)).ToList())
        {
            _noteCache.Remove(stale);
        }

        return files.Select(ReadNote).ToList();
    }

    /// <summary>
    /// Find one note by id without parsing every body.
    /// </summary>
    /// <remarks>
    /// Ids are derived from path, so a directory walk is enough to pick the file;
    /// the body is read (and cached) only for the match.
    /// </remarks>
    public (Note Note, string Path)? Locate(string noteId)
    {
        foreach (var path in IterMarkdownFiles())
        {
            if (NoteIdFor(LayerId, Relative(path)) == noteId)
            {
                return (ReadNote(path), path);
            }
        }

        return null;
    }

    public List<FolderNode> ListFolders()
    {
        if (!Directory.Exists(Root))
        {
            return [];
        }

        return Directory.EnumerateDirectories(Root, "*", SearchOption.AllDirectories)
            .Where(path => !IsHidden(path))
            .Order(StringComparer.Ordinal)
            .Select(path =>
            {
                var relative = Relative(path);
                var parent = relative.Contains('/', StringComparison.Ordinal)
                    ? relative[..relative.LastIndexOf('/')]
                    : string.Empty;

                return new FolderNode(
                    Id: NoteIdFor(LayerId, relative + "/"),
                    LayerId: LayerId,
                    Name: Path.GetFileName(path),
                    Path: relative,
                    ParentId: parent.Length == 0 ? null : NoteIdFor(LayerId, parent + "/"));
            })
            .ToList();
    }

    public string PathFor(string folderPath, string title)
    {
        var filename = $"{Paths.SafeFilename(title)}{MarkdownSuffix}";
        var parts = folderPath.Split('/')
            .Where(part => part.Length > 0 && part != ".")
            .Append(filename)
            .ToArray();

        return Paths.ResolveWithin(Root, parts);
    }

    public Note WriteNote(
        string folderPath,
        string title,
        string content,
        IReadOnlyDictionary<string, object?>? properties = null)
    {
        var path = PathFor(folderPath, title);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);

        var document = Frontmatter.Render(properties ?? new Dictionary<string, object?>()) + content;
        Paths.WriteTextAtomic(path, document);

        Invalidate(path);
        return ReadNote(path);
    }

    /// <summary>Drop cached parses. Call after any write so the next read is fresh.</summary>
    public void Invalidate(string? path = null)
    {
        if (path is null)
        {
            _noteCache.Clear();
            return;
        }

        _noteCache.Remove(path);
    }

    // -- frontmatter coercion ------------------------------------------------

    /// <summary>A bare string is a one-item list; anything else non-list is dropped.</summary>
    private static List<string> StringList(object? value) => value switch
    {
        string single => [single],
        List<object?> items => items.Select(item => item?.ToString() ?? string.Empty).ToList(),
        _ => [],
    };

    private static JsonNode? ToJson(object? value) => value switch
    {
        null => null,
        string text => JsonValue.Create(text),
        bool flag => JsonValue.Create(flag),
        long integer => JsonValue.Create(integer),
        double number => JsonValue.Create(number),
        List<object?> items => new JsonArray(items.Select(ToJson).ToArray()),
        Dictionary<string, object?> mapping => new JsonObject(
            mapping.Select(pair => new KeyValuePair<string, JsonNode?>(pair.Key, ToJson(pair.Value)))),
        _ => JsonValue.Create(value.ToString()),
    };
}
