using System.Text;
using System.Text.RegularExpressions;
using Strata.Core.Errors;

namespace Strata.Infrastructure.Storage;

/// <summary>
/// Path safety — port of <c>app/infrastructure/storage/paths.py</c>.
/// </summary>
/// <remarks>
/// Every path that originates outside the process (a note id, a layer name, an
/// import target) is resolved through here. Path traversal is a security rule, not
/// a nicety: <c>..</c> and absolute paths are rejected, and the resolved path must
/// still be inside the root.
/// </remarks>
public static partial class Paths
{
    public const int MaxNameLength = 120;

    [GeneratedRegex(@"[<>:""/\\|?*\x00-\x1f]")]
    private static partial Regex Unsafe();

    [GeneratedRegex(@"^[-.\s]+|[-.\s]+$")]
    private static partial Regex EdgeJunk();

    private static readonly HashSet<string> ReservedWindows = BuildReserved();

    private static HashSet<string> BuildReserved()
    {
        var reserved = new HashSet<string>(StringComparer.Ordinal) { "CON", "PRN", "AUX", "NUL" };
        for (var i = 1; i < 10; i++)
        {
            reserved.Add($"COM{i}");
            reserved.Add($"LPT{i}");
        }

        return reserved;
    }

    /// <summary>Turn a user-supplied title into a filename that is safe on all targets.</summary>
    public static string SafeFilename(string name)
    {
        name = name.Normalize(NormalizationForm.FormC).Trim();
        name = Unsafe().Replace(name, "-");

        // Collapse dot runs *after* the separators are gone: "../../evil" would
        // otherwise sanitise to "-..-evil", which is harmless as a single component
        // but leaves a traversal token in a name that later code may split again.
        while (name.Contains("..", StringComparison.Ordinal))
        {
            name = name.Replace("..", "-", StringComparison.Ordinal);
        }

        // Strip sanitizer tokens *and* any Unicode whitespace (e.g. a non-breaking
        // space) from both ends, so a name never ends up with an invisible
        // trailing character.
        name = EdgeJunk().Replace(name, string.Empty);

        if (name.Length == 0)
        {
            // A name made only of dots, dashes and spaces is not a name. Refusing is
            // better than inventing one the user did not choose.
            throw new InvalidRequestException("Name is empty after sanitisation.");
        }

        var stem = name.ToUpperInvariant().Split('.')[0];
        if (ReservedWindows.Contains(stem))
        {
            name = $"_{name}";
        }

        return name.Length > MaxNameLength ? name[..MaxNameLength] : name;
    }

    /// <summary>Resolve <paramref name="parts"/> under <paramref name="root"/>, refusing anything that escapes it.</summary>
    public static string ResolveWithin(string root, params string[] parts)
    {
        foreach (var part in parts)
        {
            if (string.IsNullOrEmpty(part))
            {
                continue;
            }

            // Judge every component against *both* path flavours, so hostile input is
            // refused identically on every platform. A POSIX host treats "C:\Windows"
            // or "a\..\b" as an ordinary filename; a Windows host treats them as a
            // drive path and a traversal. Neither is ever a legitimate component here.
            if (IsRootedAnyFlavour(part)
                || part.Contains('\\', StringComparison.Ordinal)
                || HasParentSegment(part)
                // A NUL or other control character is never a legitimate component and
                // makes the OS path resolver throw; refuse it here so the failure is a
                // typed Strata error, not an untyped crash.
                || part.Any(ch => ch < 0x20))
            {
                throw new InvalidRequestException("Path is not permitted.");
            }
        }

        var candidate = Path.Combine([root, .. parts.Where(part => !string.IsNullOrEmpty(part))]);

        string rootResolved;
        string resolved;
        try
        {
            rootResolved = Path.TrimEndingDirectorySeparator(RealPath(root));
            resolved = Path.TrimEndingDirectorySeparator(RealPath(candidate));
        }
        catch (Exception exc) when (exc is ArgumentException or NotSupportedException
            or PathTooLongException or IOException or UnauthorizedAccessException)
        {
            throw new InvalidRequestException("Path could not be resolved.", innerException: exc);
        }

        if (!IsSameOrInside(resolved, rootResolved))
        {
            throw new InvalidRequestException("Path escapes the workspace root.");
        }

        return resolved;
    }

    /// <summary>
    /// Normalise <em>and follow symlinks</em>, like Python's <c>Path.resolve()</c>.
    /// </summary>
    /// <remarks>
    /// <see cref="Path.GetFullPath(string)"/> alone only normalises text. A symlink
    /// planted inside the workspace and pointing outside it would survive that check
    /// and defeat the containment test, so every existing component is resolved to
    /// its final target before the comparison.
    /// </remarks>
    private static string RealPath(string path)
    {
        var full = Path.GetFullPath(path);
        var current = Path.GetPathRoot(full) ?? string.Empty;
        var segments = full[current.Length..].Split(
            [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
            StringSplitOptions.RemoveEmptyEntries);

        foreach (var segment in segments)
        {
            current = Path.Combine(current, segment);
            if (!Path.Exists(current))
            {
                continue; // a tail that does not exist yet cannot be a link
            }

            FileSystemInfo info = Directory.Exists(current)
                ? new DirectoryInfo(current)
                : new FileInfo(current);

            // returnFinalTarget walks the whole chain and throws on a cycle.
            if (info.ResolveLinkTarget(returnFinalTarget: true) is { } target)
            {
                current = Path.GetFullPath(target.FullName);
            }
        }

        return current;
    }

    private static bool IsRootedAnyFlavour(string part)
        => Path.IsPathRooted(part)
            || part.StartsWith('/')
            || (part.Length >= 2 && char.IsAsciiLetter(part[0]) && part[1] == ':');

    private static bool HasParentSegment(string part)
        => part.Split('/', '\\').Any(segment => segment == "..");

    private static bool IsSameOrInside(string resolved, string rootResolved)
    {
        // Path comparison is case-insensitive on Windows and ordinal elsewhere; use
        // the platform's rule rather than guessing.
        var comparison = OperatingSystem.IsWindows()
            ? StringComparison.OrdinalIgnoreCase
            : StringComparison.Ordinal;

        if (string.Equals(resolved, rootResolved, comparison))
        {
            return true;
        }

        // Compare against the root *plus a separator*, so "/rootless" is not treated
        // as living inside "/root".
        var prefix = rootResolved + Path.DirectorySeparatorChar;
        return resolved.StartsWith(prefix, comparison);
    }

    /// <summary>
    /// Write <paramref name="text"/> to <paramref name="path"/> via a sibling temp
    /// file plus <see cref="AtomicFile.Replace"/>.
    /// </summary>
    /// <remarks>
    /// Public Markdown notes are the user's files. A crash mid-write can leave a
    /// truncated document; everything else in Strata already avoids that.
    /// </remarks>
    public static void WriteTextAtomic(string path, string text)
        => AtomicFile.WriteText(path, text);
}
