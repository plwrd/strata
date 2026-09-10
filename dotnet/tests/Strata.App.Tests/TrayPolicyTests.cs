using Strata.Core.Shell;

namespace Strata.App.Tests;

public class TrayPolicyTests
{
    [Fact]
    public void Hide_when_tray_enabled_and_not_quitting()
    {
        Assert.True(TrayPolicy.ShouldHideToTray(trayEnabled: true, isQuitting: false));
    }

    [Fact]
    public void Quit_when_user_asked_to_quit()
    {
        Assert.False(TrayPolicy.ShouldHideToTray(trayEnabled: true, isQuitting: true));
    }

    [Fact]
    public void Quit_when_no_tray_to_restore_from()
    {
        Assert.False(TrayPolicy.ShouldHideToTray(trayEnabled: false, isQuitting: false));
    }
}

public class ShellSettingsTests
{
    [Fact]
    public void Defaults_match_python_product_defaults()
    {
        var settings = new ShellSettings();
        Assert.True(settings.HideForSharing);
        Assert.False(settings.MinimizeToTray);
        Assert.False(settings.StartInTray);
        Assert.False(settings.HideFromTaskbar);
        Assert.False(settings.BrowserBlurMedia);
        Assert.Equal(12, settings.BrowserBlurAmount);
    }

    [Fact]
    public void Tray_required_when_taskbar_hidden_or_minimize_on()
    {
        Assert.True(new ShellSettings { HideFromTaskbar = true }.TrayRequired);
        Assert.True(new ShellSettings { MinimizeToTray = true }.TrayRequired);
        Assert.False(new ShellSettings { StartInTray = true }.TrayRequired);
    }

    [Fact]
    public void Normalize_clears_start_in_tray_without_minimize()
    {
        var settings = new ShellSettings { StartInTray = true, MinimizeToTray = false };
        settings.NormalizeCouplings();
        Assert.False(settings.StartInTray);
    }

    [Fact]
    public void Normalize_enables_minimize_when_taskbar_hidden()
    {
        var settings = new ShellSettings { HideFromTaskbar = true, MinimizeToTray = false };
        settings.NormalizeCouplings();
        Assert.True(settings.MinimizeToTray);
    }

    [Fact]
    public void Clamp_blur_amount_matches_python_bounds()
    {
        Assert.Equal(1, ShellSettings.ClampBlurAmount(0));
        Assert.Equal(100, ShellSettings.ClampBlurAmount(999));
        Assert.Equal(12, ShellSettings.ClampBlurAmount(12));
    }
}
