using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace Strata.Infrastructure.Shell;

/// <summary>
/// Win32 <c>SetWindowDisplayAffinity</c> — same behaviour as
/// <c>app/desktop/screen_security.py</c>.
/// </summary>
[SupportedOSPlatform("windows")]
public static class ScreenSecurity
{
    public const uint WdaNone = 0x00000000;
    public const uint WdaMonitor = 0x00000001;
    public const uint WdaExcludeFromCapture = 0x00000011;
    private const uint GaRoot = 2;

    public static bool SetWindowExcludedFromCapture(nint hwnd, bool enabled)
    {
        if (!OperatingSystem.IsWindows() || hwnd == 0)
        {
            return true;
        }

        var root = GetAncestor(hwnd, GaRoot);
        if (root != 0)
        {
            hwnd = root;
        }

        if (!enabled)
        {
            return SetWindowDisplayAffinity(hwnd, WdaNone);
        }

        if (SetWindowDisplayAffinity(hwnd, WdaExcludeFromCapture))
        {
            return true;
        }

        // Fallback: black out in captures on older builds.
        return SetWindowDisplayAffinity(hwnd, WdaMonitor);
    }

    /// <summary>
    /// Exclude every visible top-level window owned by <paramref name="pid"/> —
    /// covers owned dialogs and (later) a Chrome research backend process.
    /// </summary>
    public static int SetProcessWindowsExcludedFromCapture(int pid, bool enabled)
    {
        if (!OperatingSystem.IsWindows() || pid <= 0)
        {
            return 0;
        }

        var affinity = enabled ? WdaExcludeFromCapture : WdaNone;
        var touched = 0;

        EnumWindows((hwnd, _) =>
        {
            if (!IsWindowVisible(hwnd))
            {
                return true;
            }

            GetWindowThreadProcessId(hwnd, out var owner);
            if (owner != (uint)pid)
            {
                return true;
            }

            if (SetWindowDisplayAffinity(hwnd, affinity)
                || (enabled && SetWindowDisplayAffinity(hwnd, WdaMonitor)))
            {
                touched++;
            }

            return true;
        }, 0);

        return touched;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowDisplayAffinity(nint hwnd, uint affinity);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint GetAncestor(nint hwnd, uint flags);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(nint hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint GetWindowThreadProcessId(nint hwnd, out uint processId);

    private delegate bool EnumWindowsProc(nint hwnd, nint lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, nint lParam);
}
