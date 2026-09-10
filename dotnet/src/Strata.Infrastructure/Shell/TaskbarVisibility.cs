using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace Strata.Infrastructure.Shell;

/// <summary>
/// Toggle the taskbar / Alt-Tab button via <c>WS_EX_TOOLWINDOW</c> /
/// <c>WS_EX_APPWINDOW</c> — same as <c>app/desktop/taskbar.py</c>.
/// </summary>
[SupportedOSPlatform("windows")]
public static class TaskbarVisibility
{
    private const int GwlExStyle = -20;
    private const nint WsExToolWindow = 0x00000080;
    private const nint WsExAppWindow = 0x00040000;
    private const int SwHide = 0;
    private const int SwShow = 5;

    public static bool SetWindowInTaskbar(nint hwnd, bool shown, bool isVisible)
    {
        if (!OperatingSystem.IsWindows() || hwnd == 0)
        {
            return true;
        }

        var current = GetWindowLongPtr(hwnd, GwlExStyle);
        nint desired = shown
            ? (current & ~WsExToolWindow) | WsExAppWindow
            : (current | WsExToolWindow) & ~WsExAppWindow;

        if (desired == current)
        {
            return true;
        }

        if (isVisible)
        {
            ShowWindow(hwnd, SwHide);
        }

        SetWindowLongPtr(hwnd, GwlExStyle, desired);

        if (isVisible)
        {
            ShowWindow(hwnd, SwShow);
        }

        return true;
    }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern nint GetWindowLongPtr(nint hwnd, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern nint SetWindowLongPtr(nint hwnd, int index, nint value);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool ShowWindow(nint hwnd, int cmdShow);
}
