namespace Strata.Core.Errors;

/// <summary>
/// Base for every failure that is safe to surface to the frontend — port of
/// <c>app/domain/errors.py::StrataError</c>.
/// </summary>
/// <remarks>
/// <see cref="Details"/> must only ever contain non-sensitive, structured data
/// (counts, identifiers, enum values). Never put paths, titles of private objects,
/// or decrypted content in it. Error payloads that leave the process never contain
/// stack traces, filesystem paths, or decrypted private content.
/// </remarks>
public class StrataException : Exception
{
    public StrataException(
        string message,
        IReadOnlyDictionary<string, object?>? details = null,
        bool? retryable = null,
        Exception? innerException = null)
        : base(message, innerException)
    {
        Details = details ?? new Dictionary<string, object?>();
        Retryable = retryable ?? DefaultRetryable;
    }

    /// <summary>The closed-enum code the frontend switches on.</summary>
    public virtual StrataErrorCode Code => StrataErrorCode.Internal;

    protected virtual bool DefaultRetryable => false;

    public IReadOnlyDictionary<string, object?> Details { get; }

    public bool Retryable { get; }
}

public class InvalidRequestException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.InvalidRequest;
}

public class PayloadTooLargeException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.PayloadTooLarge;
}

public class NotFoundException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.NotFound;
}

public class PermissionDeniedException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.PermissionDenied;
}

/// <summary>
/// Raised whenever an operation would need a key the app does not hold.
/// </summary>
/// <remarks>
/// Deliberately generic: it must not reveal whether the requested object exists
/// inside the locked layer.
/// </remarks>
public class LayerLockedException(
    string message = "This layer is locked.",
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.LayerLocked;
}

public class ConflictException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.Conflict;
}

public class UnsupportedException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.Unsupported;
}

public class OperationCancelledException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.Cancelled;
}

public class ProviderException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.ProviderError;

    protected override bool DefaultRetryable => true;
}

public class InternalException(
    string message,
    IReadOnlyDictionary<string, object?>? details = null,
    bool? retryable = null,
    Exception? innerException = null)
    : StrataException(message, details, retryable, innerException)
{
    public override StrataErrorCode Code => StrataErrorCode.Internal;
}

/// <summary>
/// Authentication failed — port of
/// <c>app/infrastructure/encryption/primitives.py::DecryptionError</c>.
/// </summary>
/// <remarks>
/// Deliberately says nothing about <em>why</em>. A wrong password, a corrupted
/// object and a forged object are the same event to a caller: distinguishing them
/// for the user would distinguish them for an attacker too. The default message is
/// the only one that should reach a user; the more specific messages used at call
/// sites exist for developers and logs. Like the Python original it carries the
/// <c>internal</c> code, so a failed unlock is not reported as a distinct
/// permission outcome the caller could probe.
/// </remarks>
public sealed class DecryptionException(
    string message = DecryptionException.DefaultMessage,
    Exception? innerException = null)
    : StrataException(message, details: null, retryable: null, innerException)
{
    public const string DefaultMessage = "The data could not be decrypted.";

    public override StrataErrorCode Code => StrataErrorCode.Internal;
}
