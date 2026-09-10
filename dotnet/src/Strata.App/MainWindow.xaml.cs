using System.ComponentModel;
using System.Windows;
using System.Windows.Forms;
using System.Windows.Interop;
using Strata.Core.Shell;
using Strata.Infrastructure.Shell;
using Application = System.Windows.Application;

namespace Strata.App;

public partial class MainWindow : Window
{
    private readonly ShellSettingsStore _store = new();
    private ShellSettings _settings = new();
    private NotifyIcon? _tray;
    private bool _isQuitting;
    private bool _suppressSettingEvents;
    private bool _trayNotified;
    private bool _syncingTaskbar;
    private bool _captureClassHandlerRegistered;

    public MainWindow()
    {
        InitializeComponent();
        _settings = _store.Load();
        _settings.NormalizeCouplings();
        LoadChecksFromSettings();
        EnsureTray();
        SyncCaptureWindowHook();

        if (_settings.StartInTray && TrayPolicy.ShouldHideToTray(TrayEnabled, isQuitting: false))
        {
            Loaded += (_, _) => HideToTray();
        }
    }

    private bool TrayEnabled =>
        _settings.TrayRequired && _tray is { Visible: true };

    private void LoadChecksFromSettings()
    {
        _suppressSettingEvents = true;
        HideForSharingCheck.IsChecked = _settings.HideForSharing;
        MinimizeToTrayCheck.IsChecked = _settings.MinimizeToTray;
        HideFromTaskbarCheck.IsChecked = _settings.HideFromTaskbar;
        StartInTrayCheck.IsChecked = _settings.StartInTray;
        StartInTrayCheck.IsEnabled = _settings.MinimizeToTray;
        BrowserBlurMediaCheck.IsChecked = _settings.BrowserBlurMedia;
        BrowserBlurAmountSlider.Value = _settings.BrowserBlurAmount;
        BlurAmountLabel.Text = $"Blur strength ({_settings.BrowserBlurAmount}px)";
        _suppressSettingEvents = false;
        StatusText.Text = $"Settings: {_store.FilePath}";
    }

    private void OnSourceInitialized(object? sender, EventArgs e)
    {
        ApplyCaptureExclusion();
        ApplyTaskbarVisibility();
    }

    private void OnStateChanged(object? sender, EventArgs e)
    {
        // Match Python: re-assert affinity on WindowStateChange only (not ActivationChange).
        ApplyCaptureExclusion();

        if (WindowState == WindowState.Minimized
            && TrayPolicy.ShouldHideToTray(TrayEnabled, _isQuitting))
        {
            Dispatcher.BeginInvoke(HideToTray);
        }
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        if (TrayPolicy.ShouldHideToTray(TrayEnabled, _isQuitting))
        {
            e.Cancel = true;
            HideToTray();
            return;
        }

        _tray?.Dispose();
        _tray = null;
    }

    private void OnBlurAmountChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (_suppressSettingEvents || BlurAmountLabel is null)
        {
            return;
        }

        var amount = ShellSettings.ClampBlurAmount((int)Math.Round(e.NewValue));
        BlurAmountLabel.Text = $"Blur strength ({amount}px)";
        if (_settings.BrowserBlurAmount == amount)
        {
            return;
        }

        _settings.BrowserBlurAmount = amount;
        _store.Save(_settings);
    }

    private void OnSettingChanged(object sender, RoutedEventArgs e)
    {
        if (_suppressSettingEvents)
        {
            return;
        }

        _settings.HideForSharing = HideForSharingCheck.IsChecked == true;
        _settings.MinimizeToTray = MinimizeToTrayCheck.IsChecked == true;
        _settings.HideFromTaskbar = HideFromTaskbarCheck.IsChecked == true;
        _settings.StartInTray = StartInTrayCheck.IsChecked == true;
        _settings.BrowserBlurMedia = BrowserBlurMediaCheck.IsChecked == true;
        _settings.BrowserBlurAmount = ShellSettings.ClampBlurAmount((int)Math.Round(BrowserBlurAmountSlider.Value));
        _settings.NormalizeCouplings();

        _suppressSettingEvents = true;
        MinimizeToTrayCheck.IsChecked = _settings.MinimizeToTray;
        StartInTrayCheck.IsChecked = _settings.StartInTray;
        StartInTrayCheck.IsEnabled = _settings.MinimizeToTray;
        BrowserBlurAmountSlider.Value = _settings.BrowserBlurAmount;
        BlurAmountLabel.Text = $"Blur strength ({_settings.BrowserBlurAmount}px)";
        _suppressSettingEvents = false;

        _store.Save(_settings);
        EnsureTray();
        SyncCaptureWindowHook();
        ApplyTaskbarVisibility();
        ApplyCaptureExclusion();
        StatusText.Text =
            $"Saved · capture={_settings.HideForSharing} tray={_settings.MinimizeToTray} " +
            $"taskbar_hidden={_settings.HideFromTaskbar} blur={_settings.BrowserBlurMedia}/{_settings.BrowserBlurAmount}px";
    }

    private void EnsureTray()
    {
        var wantTray = _settings.TrayRequired;
        if (wantTray)
        {
            if (_tray is null)
            {
                _tray = new NotifyIcon
                {
                    Text = "Strata",
                    Icon = System.Drawing.SystemIcons.Application,
                    Visible = true,
                };
                var menu = new ContextMenuStrip();
                menu.Items.Add("Show Strata", null, (_, _) => ShowFromTray());
                menu.Items.Add(new ToolStripSeparator());
                menu.Items.Add("Quit Strata", null, (_, _) => QuitFromTray());
                _tray.ContextMenuStrip = menu;
                _tray.DoubleClick += (_, _) => ShowFromTray();
            }
            else
            {
                _tray.Visible = true;
            }
        }
        else if (_tray is not null)
        {
            _tray.Visible = false;
            ShowFromTray();
        }
    }

    private void HideToTray()
    {
        Hide();
        if (_tray is { Visible: true } && !_trayNotified)
        {
            _trayNotified = true;
            _tray.ShowBalloonTip(
                4000,
                "Strata is still running",
                "The window is hidden. Click the tray icon to bring it back, or use Quit Strata to close it.",
                ToolTipIcon.Info);
        }
    }

    private void ShowFromTray()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
        ApplyTaskbarVisibility();
        ApplyCaptureExclusion();
    }

    private void QuitFromTray()
    {
        _isQuitting = true;
        _tray?.Dispose();
        _tray = null;
        Application.Current.Shutdown();
    }

    private void SyncCaptureWindowHook()
    {
        // When Hidden for sharing is on, newly shown windows (dialogs) must get affinity too.
        if (_settings.HideForSharing && !_captureClassHandlerRegistered)
        {
            EventManager.RegisterClassHandler(
                typeof(Window),
                LoadedEvent,
                new RoutedEventHandler(OnAnyWindowLoaded));
            _captureClassHandlerRegistered = true;
        }
    }

    private void OnAnyWindowLoaded(object sender, RoutedEventArgs e)
    {
        if (!_settings.HideForSharing || sender is not Window window)
        {
            return;
        }

        var hwnd = new WindowInteropHelper(window).Handle;
        if (hwnd != 0 && OperatingSystem.IsWindows())
        {
            ScreenSecurity.SetWindowExcludedFromCapture(hwnd, enabled: true);
        }
    }

    private nint Hwnd => new WindowInteropHelper(this).Handle;

    private void ApplyCaptureExclusion()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        // Cover every visible top-level HWND in this process (main + owned dialogs).
        ScreenSecurity.SetProcessWindowsExcludedFromCapture(
            Environment.ProcessId,
            _settings.HideForSharing);

        // Also hit this window explicitly in case it is not yet EnumWindows-visible.
        var hwnd = Hwnd;
        if (hwnd != 0)
        {
            ScreenSecurity.SetWindowExcludedFromCapture(hwnd, _settings.HideForSharing);
        }
    }

    private void ApplyTaskbarVisibility()
    {
        if (!OperatingSystem.IsWindows() || _syncingTaskbar)
        {
            return;
        }

        var hwnd = Hwnd;
        if (hwnd == 0)
        {
            return;
        }

        _syncingTaskbar = true;
        try
        {
            TaskbarVisibility.SetWindowInTaskbar(hwnd, shown: !_settings.HideFromTaskbar, isVisible: IsVisible);
        }
        finally
        {
            _syncingTaskbar = false;
        }

        // Taskbar style cycle can drop capture affinity — re-assert (Python _sync_taskbar).
        ApplyCaptureExclusion();
    }
}
