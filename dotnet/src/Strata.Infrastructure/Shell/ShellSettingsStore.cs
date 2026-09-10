using System.Text.Json;
using Strata.Core.Shell;

namespace Strata.Infrastructure.Shell;

/// <summary>
/// Persist the Phase 0 shell settings subset as JSON under the user app-data folder.
/// </summary>
public sealed class ShellSettingsStore
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private readonly string _path;

    public ShellSettingsStore(string? path = null)
    {
        if (path is not null)
        {
            _path = path;
            return;
        }

        var root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Strata");
        Directory.CreateDirectory(root);
        _path = Path.Combine(root, "shell-settings.json");
    }

    public string FilePath => _path;

    public ShellSettings Load()
    {
        if (!File.Exists(_path))
        {
            return new ShellSettings();
        }

        try
        {
            var json = File.ReadAllText(_path);
            return JsonSerializer.Deserialize<ShellSettings>(json, JsonOptions) ?? new ShellSettings();
        }
        catch (JsonException)
        {
            return new ShellSettings();
        }
    }

    public void Save(ShellSettings settings)
    {
        var directory = System.IO.Path.GetDirectoryName(_path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var json = JsonSerializer.Serialize(settings, JsonOptions);
        var temporary = _path + ".tmp";
        File.WriteAllText(temporary, json);
        File.Move(temporary, _path, overwrite: true);
    }
}
