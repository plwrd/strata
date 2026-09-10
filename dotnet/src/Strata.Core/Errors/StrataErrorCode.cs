namespace Strata.Core.Errors;

/// <summary>
/// Closed set of error codes carried across the bridge, mirrored from
/// <c>app/domain/errors.py::ErrorCode</c>.
/// </summary>
/// <remarks>
/// The frontend switches on the wire value, so adding a code is a protocol change.
/// Use <see cref="StrataErrorCodes.ToWire"/> for the exact string — the enum names
/// are C# spelling, the wire values are the contract.
/// </remarks>
public enum StrataErrorCode
{
    InvalidRequest,
    PayloadTooLarge,
    NotFound,
    PermissionDenied,
    LayerLocked,
    Conflict,
    Unsupported,
    Cancelled,
    ProviderError,
    Internal,
}

public static class StrataErrorCodes
{
    /// <summary>The snake_case value the frontend receives.</summary>
    public static string ToWire(this StrataErrorCode code) => code switch
    {
        StrataErrorCode.InvalidRequest => "invalid_request",
        StrataErrorCode.PayloadTooLarge => "payload_too_large",
        StrataErrorCode.NotFound => "not_found",
        StrataErrorCode.PermissionDenied => "permission_denied",
        StrataErrorCode.LayerLocked => "layer_locked",
        StrataErrorCode.Conflict => "conflict",
        StrataErrorCode.Unsupported => "unsupported",
        StrataErrorCode.Cancelled => "cancelled",
        StrataErrorCode.ProviderError => "provider_error",
        StrataErrorCode.Internal => "internal",
        _ => throw new ArgumentOutOfRangeException(nameof(code), code, "Unknown error code."),
    };

    public static StrataErrorCode FromWire(string wire) => wire switch
    {
        "invalid_request" => StrataErrorCode.InvalidRequest,
        "payload_too_large" => StrataErrorCode.PayloadTooLarge,
        "not_found" => StrataErrorCode.NotFound,
        "permission_denied" => StrataErrorCode.PermissionDenied,
        "layer_locked" => StrataErrorCode.LayerLocked,
        "conflict" => StrataErrorCode.Conflict,
        "unsupported" => StrataErrorCode.Unsupported,
        "cancelled" => StrataErrorCode.Cancelled,
        "provider_error" => StrataErrorCode.ProviderError,
        "internal" => StrataErrorCode.Internal,
        _ => throw new ArgumentOutOfRangeException(nameof(wire), wire, "Unknown error code."),
    };
}
