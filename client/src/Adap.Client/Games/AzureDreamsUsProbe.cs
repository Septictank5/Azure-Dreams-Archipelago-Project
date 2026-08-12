using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal static class AzureDreamsUsProbe
{
    // SLUS_006.14 entry point at 0x80033930. This resident code is outside the
    // gameplay overlays currently targeted for patching.
    private const uint EntryPoint = 0x8003_3930;

    private static ReadOnlySpan<byte> EntrySignature =>
    [
        0x08, 0x80, 0x02, 0x3c,
        0x38, 0x14, 0x42, 0x24,
        0x09, 0x80, 0x03, 0x3c,
        0x60, 0x87, 0x63, 0x24,
    ];

    public static bool TryIdentify(IEmulatorMemory memory, out bool isAzureDreamsUs, out string? error)
    {
        Span<byte> observed = stackalloc byte[EntrySignature.Length];
        if (!memory.TryRead(EntryPoint, observed, out error))
        {
            isAzureDreamsUs = false;
            return false;
        }

        isAzureDreamsUs = observed.SequenceEqual(EntrySignature);
        return true;
    }
}
