namespace Strata.Core.Shell;

/// <summary>
/// Pure policy from <c>app/desktop/tray.py::should_hide_to_tray</c>.
/// </summary>
public static class TrayPolicy
{
    public static bool ShouldHideToTray(bool trayEnabled, bool isQuitting)
        => trayEnabled && !isQuitting;
}
