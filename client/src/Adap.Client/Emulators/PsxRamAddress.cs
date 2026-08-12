namespace Adap.Client.Emulators;

internal static class PsxRamAddress
{
    private const uint PhysicalAddressMask = 0x1fff_ffff;

    public static bool TryTranslate(uint psxAddress, int byteCount, int ramSize, out long offset)
    {
        offset = psxAddress & PhysicalAddressMask;

        if (byteCount < 0 || offset >= ramSize)
            return false;

        return byteCount <= ramSize - offset;
    }
}
