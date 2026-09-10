using System.Runtime.CompilerServices;
using Strata.Core.Encryption;

namespace Strata.Services.Tests;

/// <summary>
/// Derive layer keys with a cheap Argon2 profile, for tests only — the C#
/// equivalent of the Python suite's session-scoped <c>cheap_kdf</c> fixture.
/// </summary>
/// <remarks>
/// Production derives at 256 MiB per hash. A suite that creates dozens of private
/// layers asks for that allocation dozens of times, which is slow and, on a busy
/// machine, starts failing to allocate — failures that move between unrelated tests
/// every run and pass in isolation.
/// <para>
/// Only <em>new</em> layers are affected: the parameters are written into each
/// layer header and read back from it, so unlock still exercises the real code path
/// with whatever the header says. The production constants are untouched, and
/// <see cref="ProductionKdfTests"/> asserts them directly so a real weakening
/// cannot hide behind this.
/// </para>
/// </remarks>
internal static class CheapKdf
{
    [ModuleInitializer]
    internal static void Apply() => KdfParams.Defaults = new KdfParams(
        TimeCost: 1,
        MemoryKib: 8_192, // 8 MiB
        Parallelism: 1);
}

/// <summary>The production profile itself, asserted so the test seam cannot mask a weakening.</summary>
public class ProductionKdfTests
{
    [Fact]
    public void Production_argon2_parameters_are_unchanged()
    {
        Assert.Equal(3, CryptoConstants.Argon2TimeCost);
        Assert.Equal(262_144, CryptoConstants.Argon2MemoryKib); // 256 MiB
        Assert.Equal(4, CryptoConstants.Argon2Parallelism);
        Assert.Equal(1, CryptoConstants.KdfVersion);
    }

    [Fact]
    public void A_default_constructed_KdfParams_still_carries_the_production_profile()
    {
        var production = new KdfParams();

        Assert.Equal(CryptoConstants.Argon2TimeCost, production.TimeCost);
        Assert.Equal(CryptoConstants.Argon2MemoryKib, production.MemoryKib);
        Assert.Equal(CryptoConstants.Argon2Parallelism, production.Parallelism);
    }

    [Fact]
    public void New_always_produces_a_fresh_salt()
    {
        var first = KdfParams.New();
        var second = KdfParams.New();

        Assert.Equal(CryptoConstants.SaltBytes, first.Salt.Length);
        Assert.NotEqual(first.Salt, second.Salt);
    }
}
