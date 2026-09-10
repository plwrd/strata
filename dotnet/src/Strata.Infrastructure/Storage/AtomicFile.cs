using System.Text;

namespace Strata.Infrastructure.Storage;

/// <summary>
/// Port of <c>app/infrastructure/storage/paths.py::replace_atomic</c>.
/// </summary>
public static class AtomicFile
{
    /// <summary>
    /// Move <paramref name="source"/> over <paramref name="target"/>, with a bounded
    /// retry for Windows races.
    /// </summary>
    /// <remarks>
    /// An antivirus scanner or the search indexer can hold a freshly written file for
    /// a few milliseconds; the replace then fails even though nothing is actually
    /// wrong. Every atomic write in Strata goes through here so that transient hold is
    /// a short wait, not a crash — while a <em>persistent</em> error still throws after
    /// the final attempt, because masking a real permission problem would be worse.
    /// </remarks>
    public static void Replace(string source, string target, int attempts = 8)
    {
        var delayMs = 10;
        for (var attempt = 0; attempt < attempts; attempt++)
        {
            try
            {
                File.Move(source, target, overwrite: true);
                return;
            }
            catch (Exception exc) when (exc is UnauthorizedAccessException or IOException)
            {
                if (attempt == attempts - 1)
                {
                    throw;
                }

                Thread.Sleep(delayMs);
                delayMs = Math.Min(delayMs * 2, 200);
            }
        }
    }

    /// <summary>Write text to a sibling temporary file, then atomically replace the target.</summary>
    public static void WriteText(string path, string contents, string temporarySuffix = ".tmp")
    {
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var temporary = path + temporarySuffix;
        try
        {
            File.WriteAllText(temporary, contents, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            Replace(temporary, path);
        }
        catch
        {
            TryDelete(temporary);
            throw;
        }
    }

    public static void TryDelete(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException)
        {
            // Best effort: a leftover temp file is not worth failing the operation.
        }
    }
}
