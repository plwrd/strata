using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;
using YamlDotNet.Serialization;

namespace Strata.Infrastructure.Storage;

/// <summary>
/// YAML frontmatter parsing and rendering — the file-format half of
/// <c>app/infrastructure/storage/markdown_store.py</c>.
/// </summary>
/// <remarks>
/// Frontmatter is <b>untrusted input</b>. YamlDotNet's plain deserializer is used
/// with no type resolution — the .NET equivalent of <c>yaml.safe_load</c>, never
/// <c>load</c> — any non-mapping document is discarded, and unknown keys are kept
/// as opaque properties rather than being interpreted.
/// </remarks>
public static class Frontmatter
{
    public const string Fence = "---";

    /// <summary>
    /// Split a Markdown document into (frontmatter, body).
    /// </summary>
    /// <remarks>
    /// Malformed YAML is not an error: the document is treated as having no
    /// frontmatter, which is what every other Markdown tool does and avoids making
    /// a corrupt file unreadable.
    /// </remarks>
    public static (Dictionary<string, object?> Properties, string Body) Parse(string text)
    {
        if (!text.StartsWith(Fence, StringComparison.Ordinal))
        {
            return ([], text);
        }

        var lines = text.Split('\n');
        for (var index = 1; index < lines.Length; index++)
        {
            if (lines[index].Trim() != Fence)
            {
                continue;
            }

            var raw = string.Join('\n', lines[1..index]);
            var body = string.Join('\n', lines[(index + 1)..]);

            YamlNode? document;
            try
            {
                var stream = new YamlStream();
                stream.Load(new StringReader(raw));
                document = stream.Documents.Count > 0 ? stream.Documents[0].RootNode : null;
            }
            catch (YamlException)
            {
                return ([], text);
            }

            if (document is not YamlMappingNode mapping)
            {
                return ([], text);
            }

            var properties = new Dictionary<string, object?>();
            foreach (var (key, value) in mapping)
            {
                properties[Scalar(key)] = Convert(value);
            }

            return (properties, body.TrimStart('\n'));
        }

        return ([], text);
    }

    public static string Render(IReadOnlyDictionary<string, object?> properties)
    {
        if (properties.Count == 0)
        {
            return string.Empty;
        }

        // sort_keys=True, allow_unicode=True: stable output, no \u escaping.
        var ordered = properties.OrderBy(pair => pair.Key, StringComparer.Ordinal)
            .ToDictionary(pair => pair.Key, pair => pair.Value);

        var serializer = new SerializerBuilder()
            .WithQuotingNecessaryStrings()
            .Build();

        return $"{Fence}\n{serializer.Serialize(ordered).TrimEnd('\n', '\r')}\n{Fence}\n\n";
    }

    private static string Scalar(YamlNode node)
        => node is YamlScalarNode scalar ? scalar.Value ?? string.Empty : node.ToString();

    private static object? Convert(YamlNode node) => node switch
    {
        YamlScalarNode scalar => ConvertScalar(scalar),
        YamlSequenceNode sequence => sequence.Select(Convert).ToList(),
        YamlMappingNode mapping => mapping.ToDictionary(pair => Scalar(pair.Key), pair => Convert(pair.Value)),
        _ => null,
    };

    /// <summary>
    /// Resolve an unquoted scalar the way YAML 1.1 core types do, so
    /// <c>count: 3</c> and <c>draft: true</c> survive a round trip as a number and a
    /// bool rather than becoming strings.
    /// </summary>
    private static object? ConvertScalar(YamlScalarNode scalar)
    {
        var value = scalar.Value;
        if (value is null)
        {
            return null;
        }

        // A quoted scalar is a string by construction; only plain ones are resolved.
        if (scalar.Style is not (ScalarStyle.Any or ScalarStyle.Plain))
        {
            return value;
        }

        if (value.Length == 0 || value is "~" or "null" or "Null" or "NULL")
        {
            return null;
        }

        if (value is "true" or "True" or "TRUE")
        {
            return true;
        }

        if (value is "false" or "False" or "FALSE")
        {
            return false;
        }

        if (long.TryParse(value, System.Globalization.NumberStyles.Integer,
                System.Globalization.CultureInfo.InvariantCulture, out var integer))
        {
            return integer;
        }

        if (double.TryParse(value, System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var number))
        {
            return number;
        }

        return value;
    }
}
