namespace Strata.Core.Shell;

/// <summary>
/// Privacy / window-behaviour settings mirrored from the Python AppSettings subset
/// (<c>app/services/settings_service.py</c> — Screen security, System tray, and
/// browser media-blur fields used for shoulder-surfing / screen-share privacy).
/// </summary>
public sealed class ShellSettings
{
    // -- Screen security -----------------------------------------------------

    /// <summary>Signal-style: exclude Strata windows from screenshots / shares. Default on.</summary>
    public bool HideForSharing { get; set; } = true;

    // -- System tray ---------------------------------------------------------

    public bool MinimizeToTray { get; set; }

    public bool StartInTray { get; set; }

    public bool HideFromTaskbar { get; set; }

    // -- Research browser privacy (settings persisted; UI active once a pane exists) --

    /// <summary>Blur images/video/canvas in the research browser (embedded pane).</summary>
    public bool BrowserBlurMedia { get; set; }

    /// <summary>Blur radius in px; clamped 1–100 like Python.</summary>
    public int BrowserBlurAmount { get; set; } = 12;

    /// <summary>
    /// Tray icon must be up when minimize-to-tray or hide-from-taskbar is on
    /// (matches Python <c>_refresh_tray_enabled</c>).
    /// </summary>
    public bool TrayRequired => MinimizeToTray || HideFromTaskbar;

    /// <summary>Clamp blur amount to the Python validator range.</summary>
    public static int ClampBlurAmount(int amount) => Math.Clamp(amount, 1, 100);

    /// <summary>
    /// Normalize couplings: start-in-tray needs minimize-to-tray; hide-from-taskbar
    /// keeps a restore path via tray.
    /// </summary>
    public void NormalizeCouplings()
    {
        BrowserBlurAmount = ClampBlurAmount(BrowserBlurAmount);
        if (StartInTray && !MinimizeToTray)
        {
            StartInTray = false;
        }

        if (HideFromTaskbar && !MinimizeToTray)
        {
            // Tray is still required via TrayRequired; also enable minimize so
            // close/minimize hide instead of stranding a tool-window.
            MinimizeToTray = true;
        }
    }

    public ShellSettings Clone() => new()
    {
        HideForSharing = HideForSharing,
        MinimizeToTray = MinimizeToTray,
        StartInTray = StartInTray,
        HideFromTaskbar = HideFromTaskbar,
        BrowserBlurMedia = BrowserBlurMedia,
        BrowserBlurAmount = BrowserBlurAmount,
    };
}
