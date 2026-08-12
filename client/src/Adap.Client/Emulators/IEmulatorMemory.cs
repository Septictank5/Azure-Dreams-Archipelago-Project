namespace Adap.Client.Emulators;

/// <summary>
/// Emulator-neutral access to the PlayStation's emulated main RAM.
/// Game and Archipelago logic must depend on this interface, not on a
/// particular emulator's process layout or IPC mechanism.
/// </summary>
internal interface IEmulatorMemory : IDisposable
{
    string EmulatorName { get; }

    int ProcessId { get; }

    int RamSize { get; }

    bool TryRead(uint psxAddress, Span<byte> destination, out string? error);

    bool TryWrite(uint psxAddress, ReadOnlySpan<byte> source, out string? error);
}
