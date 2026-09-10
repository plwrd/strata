using Strata.Core.Logging;
using Strata.Infrastructure.Keychain;
using Strata.Services;

namespace Strata.Services.Tests;

/// <summary>
/// A scratch workspace on disk, wired the way the app wires it. Disposing removes
/// the whole tree.
/// </summary>
public sealed class WorkspaceFixture : IDisposable
{
    public const string Password = "correct-horse-battery-staple";

    public WorkspaceFixture()
    {
        Root = Path.Combine(Path.GetTempPath(), "strata-svc-" + Guid.NewGuid().ToString("N"));
        Logger = new RecordingLogger();
        Credentials = new InMemoryCredentialStore();
        Encryption = new EncryptionService(logger: Logger);
        Workspace = new WorkspaceService(
            onOpen: path => Opened.Add(path),
            onClose: () => Closed++,
            encryption: Encryption,
            layerPasswords: Credentials,
            logger: Logger);
    }

    public string Root { get; }

    public RecordingLogger Logger { get; }

    public InMemoryCredentialStore Credentials { get; }

    public EncryptionService Encryption { get; }

    public WorkspaceService Workspace { get; }

    public List<string> Opened { get; } = [];

    public int Closed { get; private set; }

    /// <summary>A workspace with one public "Knowledge" layer, as Create leaves it.</summary>
    public WorkspaceService Created(string name = "Test")
    {
        Workspace.Create(Root, name);
        return Workspace;
    }

    public string PublicLayerId => Workspace.Descriptor.Layers
        .First(layer => layer.Storage == Core.Layers.LayerStorage.Markdown).Id;

    public void Dispose()
    {
        if (Directory.Exists(Root))
        {
            Directory.Delete(Root, recursive: true);
        }
    }
}
