using Strata.Core.Shell;
using Strata.Infrastructure.Shell;

namespace Strata.App.Tests;

public class ShellSettingsStoreTests
{
    [Fact]
    public void Round_trips_settings_through_json_file()
    {
        var path = Path.Combine(Path.GetTempPath(), "strata-shell-settings-test-" + Guid.NewGuid().ToString("N") + ".json");
        try
        {
            var store = new ShellSettingsStore(path);
            var settings = new ShellSettings
            {
                HideForSharing = true,
                MinimizeToTray = true,
                HideFromTaskbar = true,
                StartInTray = false,
            };
            store.Save(settings);

            var loaded = store.Load();
            Assert.True(loaded.HideForSharing);
            Assert.True(loaded.MinimizeToTray);
            Assert.True(loaded.HideFromTaskbar);
            Assert.False(loaded.StartInTray);
        }
        finally
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
    }
}
