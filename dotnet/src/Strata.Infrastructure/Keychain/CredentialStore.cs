using System.Runtime.InteropServices;
using System.Text;
using Strata.Core.Logging;

namespace Strata.Infrastructure.Keychain;

/// <summary>
/// Secrets, in the OS keychain — port of
/// <c>app/infrastructure/keychain/credentials.py</c>.
/// </summary>
/// <remarks>
/// Never in a Markdown file, a workspace file, a database, a log, the frontend
/// store, or an export package.
/// <para>
/// If the platform has no usable keychain, the store fails <b>closed</b>: it
/// reports that it cannot save, and Strata treats the secret as unconfigured. It
/// does not quietly fall back to a file — a silent downgrade from "encrypted by the
/// OS" to "plaintext on disk" is exactly the kind of helpfulness that gets people's
/// keys stolen.
/// </para>
/// </remarks>
public interface ICredentialStore
{
    /// <summary>Whether this machine has a keychain we can actually use.</summary>
    bool IsAvailable();

    string? Get(string id);

    /// <summary>Store a secret. Returns false rather than falling back to a file.</summary>
    bool Set(string id, string secret);

    bool Delete(string id);

    bool Has(string id);
}

/// <summary>Windows Credential Manager, the platform's keychain.</summary>
public sealed class WindowsCredentialStore(
    string service = WindowsCredentialStore.ProviderService,
    IStrataLogger? logger = null) : ICredentialStore
{
    public const string ProviderService = "strata.ai-provider";
    public const string LayerUnlockService = "strata.layer-unlock";

    private readonly IStrataLogger _logger = logger ?? NullLogger.Instance;
    private bool? _available;

    private string TargetFor(string id) => $"{service}:{id}";

    public bool IsAvailable()
    {
        if (_available is not null)
        {
            return _available.Value;
        }

        _available = OperatingSystem.IsWindows();
        if (!_available.Value)
        {
            _logger.Warning("keychain.unavailable");
        }

        return _available.Value;
    }

    public string? Get(string id)
    {
        if (!IsAvailable())
        {
            return null;
        }

        var handle = IntPtr.Zero;
        try
        {
            if (!CredRead(TargetFor(id), CredTypeGeneric, 0, out handle))
            {
                return null;
            }

            var credential = Marshal.PtrToStructure<Credential>(handle);
            if (credential.CredentialBlob == IntPtr.Zero || credential.CredentialBlobSize == 0)
            {
                return null;
            }

            var bytes = new byte[credential.CredentialBlobSize];
            Marshal.Copy(credential.CredentialBlob, bytes, 0, bytes.Length);
            return Encoding.UTF8.GetString(bytes);
        }
        catch (Exception exc) when (exc is EntryPointNotFoundException or DllNotFoundException)
        {
            _logger.Warning("keychain.read_failed", ("id", id));
            return null;
        }
        finally
        {
            if (handle != IntPtr.Zero)
            {
                CredFree(handle);
            }
        }
    }

    public bool Set(string id, string secret)
    {
        if (!IsAvailable())
        {
            return false;
        }

        var bytes = Encoding.UTF8.GetBytes(secret);
        var blob = Marshal.AllocHGlobal(bytes.Length);
        try
        {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            var credential = new Credential
            {
                Type = CredTypeGeneric,
                TargetName = TargetFor(id),
                CredentialBlob = blob,
                CredentialBlobSize = (uint)bytes.Length,
                Persist = CredPersistLocalMachine,
                UserName = id,
            };

            if (!CredWrite(ref credential, 0))
            {
                _logger.Warning("keychain.write_failed", ("id", id));
                return false;
            }
        }
        catch (Exception exc) when (exc is EntryPointNotFoundException or DllNotFoundException)
        {
            _logger.Warning("keychain.write_failed", ("id", id));
            return false;
        }
        finally
        {
            // The secret is copied into the credential store; do not leave a plain
            // copy in unmanaged memory afterwards.
            for (var i = 0; i < bytes.Length; i++)
            {
                Marshal.WriteByte(blob, i, 0);
            }

            Marshal.FreeHGlobal(blob);
            Array.Clear(bytes);
        }

        // The secret itself is never logged, only the fact that one was stored.
        _logger.Info("keychain.stored", ("id", id));
        return true;
    }

    public bool Delete(string id)
    {
        if (!IsAvailable())
        {
            return false;
        }

        try
        {
            if (!CredDelete(TargetFor(id), CredTypeGeneric, 0))
            {
                return false;
            }
        }
        catch (Exception exc) when (exc is EntryPointNotFoundException or DllNotFoundException)
        {
            return false;
        }

        _logger.Info("keychain.deleted", ("id", id));
        return true;
    }

    public bool Has(string id) => !string.IsNullOrEmpty(Get(id));

    // -- advapi32 ------------------------------------------------------------

    private const uint CredTypeGeneric = 1;
    private const uint CredPersistLocalMachine = 2;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct Credential
    {
        public uint Flags;
        public uint Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetName;
        [MarshalAs(UnmanagedType.LPWStr)] public string? Comment;
        public long LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        [MarshalAs(UnmanagedType.LPWStr)] public string? TargetAlias;
        [MarshalAs(UnmanagedType.LPWStr)] public string? UserName;
    }

    // DllImport rather than LibraryImport: the source generator needs
    // AllowUnsafeBlocks and cannot marshal CREDENTIAL, and turning unsafe code on
    // across the project that holds the crypto is not worth a keychain binding.
    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);

    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredWrite(ref Credential credential, uint flags);

    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredDelete(string target, uint type, uint flags);

    [DllImport("advapi32.dll", EntryPoint = "CredFree")]
    private static extern void CredFree(IntPtr buffer);
}

/// <summary>
/// In-memory store for tests and for hosts that deliberately hold nothing.
/// </summary>
/// <remarks>
/// Reports itself as available so tests exercise the remember/forget paths; it is
/// never a production fallback, because it persists nothing.
/// </remarks>
public sealed class InMemoryCredentialStore : ICredentialStore
{
    private readonly Dictionary<string, string> _secrets = new(StringComparer.Ordinal);

    public bool IsAvailable() => true;

    public string? Get(string id) => _secrets.GetValueOrDefault(id);

    public bool Set(string id, string secret)
    {
        _secrets[id] = secret;
        return true;
    }

    public bool Delete(string id) => _secrets.Remove(id);

    public bool Has(string id) => !string.IsNullOrEmpty(Get(id));
}

/// <summary>A store on a machine with no usable keychain: fails closed, always.</summary>
public sealed class UnavailableCredentialStore : ICredentialStore
{
    public bool IsAvailable() => false;

    public string? Get(string id) => null;

    public bool Set(string id, string secret) => false;

    public bool Delete(string id) => false;

    public bool Has(string id) => false;
}
