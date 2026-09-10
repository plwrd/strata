namespace Strata.Core.Logging;

/// <summary>
/// Structured logging seam — the shape of <c>app/infrastructure/logging/logger.py</c>
/// as used by the services: an event name plus typed key/value fields.
/// </summary>
/// <remarks>
/// The security rule from the Python side carries over unchanged: a log line may
/// name an event and identifiers, never a secret, a decrypted title, a password, a
/// key, or a recovery code. <c>layer.unlocked layer_id=layer_ab12</c> is fine;
/// the password that unlocked it is not.
/// </remarks>
public interface IStrataLogger
{
    void Info(string eventName, params (string Key, object? Value)[] fields);

    void Warning(string eventName, params (string Key, object? Value)[] fields);

    void Exception(Exception exception, string eventName, params (string Key, object? Value)[] fields);
}

/// <summary>Drops everything. The default, so a service is usable without wiring.</summary>
public sealed class NullLogger : IStrataLogger
{
    public static NullLogger Instance { get; } = new();

    public void Info(string eventName, params (string Key, object? Value)[] fields)
    {
    }

    public void Warning(string eventName, params (string Key, object? Value)[] fields)
    {
    }

    public void Exception(Exception exception, string eventName, params (string Key, object? Value)[] fields)
    {
    }
}

/// <summary>Collects entries in memory. For tests and for asserting what was logged.</summary>
public sealed class RecordingLogger : IStrataLogger
{
    public sealed record Entry(string Level, string EventName, IReadOnlyList<(string Key, object? Value)> Fields);

    private readonly List<Entry> _entries = [];

    public IReadOnlyList<Entry> Entries => _entries;

    public void Info(string eventName, params (string Key, object? Value)[] fields)
        => _entries.Add(new Entry("info", eventName, fields));

    public void Warning(string eventName, params (string Key, object? Value)[] fields)
        => _entries.Add(new Entry("warning", eventName, fields));

    public void Exception(Exception exception, string eventName, params (string Key, object? Value)[] fields)
        => _entries.Add(new Entry("exception", eventName, fields));

    public bool Logged(string eventName) => _entries.Any(entry => entry.EventName == eventName);
}
