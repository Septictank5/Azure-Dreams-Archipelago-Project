using System.Buffers.Binary;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

/// <summary>
/// Produces the native 8x16 inventory-font glyph chains consumed by the
/// in-game AP status panel. The disc patch owns the render nodes; the client
/// only refreshes these fixed buffers when mailbox clearance changes.
/// </summary>
internal static class AzureDreamsInventoryHud
{
    public const uint RenderNodeAddress = 0x801f_ed00;
    public const uint RenderTransformAddress = 0x801f_ed60;
    public const uint PanelBorderAddress = 0x801f_eb00;
    public const uint KeycardLabelAddress = 0x801f_eb90;
    public const uint MaxFloorLabelAddress = 0x801f_ec20;
    public const uint CompactRenderNodeAddress = 0x801f_ec50;
    public const uint CompactRenderTransformAddress = 0x801f_ec90;
    public const uint CompactKeycardLabelAddress = 0x801f_ecd0;
    public const uint CompactMaxFloorLabelAddress = 0x801f_ed60;
    public const uint RestoredSeededHudSignatureAddress = 0x801f_e800;
    public const int RenderNodeCount = 6;
    public const int RenderNodeSize = 16;
    public const int PanelBorderSize = 12 * 12;
    public const int LabelBufferSize = 0x90;
    public const int GlyphSize = 12;
    public const int TowerTopFloor = 40;

    private const uint PlayerActionAddress = 0x8008_3552;
    private const byte InventoryPlayerAction = 0x19;
    private const uint HeapScanAddress = 0x8018_0000;
    private const int HeapScanSize = 0x7f808;
    private const uint InventoryControllerCallback = 0x8001_ab88;
    private const uint VanillaInventoryBorder = 0x8007_8cdc;

    private const int ControllerCallbackOffset = 0x10;
    private const int DynamicFillOffset = 0x50;
    private const int RenderContextOffset = 0xb4;
    private const int RenderArrayOffset = 0xd0;

    public static bool TryRefreshFromMailbox(IEmulatorMemory memory, out byte clearance, out string message)
    {
        if (!AzureDreamsMailbox.TryReadElevatorClearance(memory, out clearance, out message))
            return false;

        return TryWrite(memory, clearance, out message);
    }

    public static bool TryWrite(IEmulatorMemory memory, byte clearance, out string message)
    {
        if (!TryGetHudLayout(memory, out bool compactSeededHud, out message))
            return false;

        if (clearance > AzureDreamsMailbox.MaximumElevatorClearance)
        {
            message = $"Elevator clearance must be between 0 and {AzureDreamsMailbox.MaximumElevatorClearance}.";
            return false;
        }

        int maximumFloor = Math.Min(TowerTopFloor, clearance * 5 + 4);
        byte[] keycardLabel = CreatePaddedLabel($"Keycard Lvl: {clearance}");
        byte[] maxFloorLabel = CreatePaddedLabel($"Max Floor: {maximumFloor}");

        if (!compactSeededHud && !TryWritePanelBorder(memory, out message))
            return false;

        uint keycardAddress = compactSeededHud
            ? CompactKeycardLabelAddress
            : KeycardLabelAddress;
        uint maxFloorAddress = compactSeededHud
            ? CompactMaxFloorLabelAddress
            : MaxFloorLabelAddress;
        if (!memory.TryWrite(keycardAddress, keycardLabel, out string? keycardError))
        {
            message = keycardError ?? "Could not write the keycard HUD label.";
            return false;
        }

        if (!memory.TryWrite(maxFloorAddress, maxFloorLabel, out string? maxFloorError))
        {
            message = maxFloorError ?? "Could not write the maximum-floor HUD label.";
            return false;
        }

        message = $"HUD now shows Keycard Lvl: {clearance} and Max Floor: {maximumFloor}.";
        return true;
    }

    public static bool TryAttachToOpenInventory(
        IEmulatorMemory memory,
        out uint inventoryRoot,
        out bool newlyAttached,
        out string message)
    {
        inventoryRoot = 0;
        newlyAttached = false;

        if (!TryGetHudLayout(memory, out bool compactSeededHud, out message))
            return false;
        if (compactSeededHud)
        {
            message = "The current seeded build attaches its compact inventory HUD in-game.";
            return true;
        }

        Span<byte> action = stackalloc byte[1];
        if (!memory.TryRead(PlayerActionAddress, action, out string? actionError))
        {
            message = actionError ?? "Could not read the player action state.";
            return false;
        }

        if (action[0] != InventoryPlayerAction)
        {
            message = "Inventory is not open.";
            return true;
        }

        byte[] heap = new byte[HeapScanSize];
        if (!memory.TryRead(HeapScanAddress, heap, out string? heapError))
        {
            message = heapError ?? "Could not scan the game-object heap.";
            return false;
        }

        for (int callbackOffset = ControllerCallbackOffset;
             callbackOffset <= heap.Length - sizeof(uint);
             callbackOffset += sizeof(uint))
        {
            if (BinaryPrimitives.ReadUInt32LittleEndian(heap.AsSpan(callbackOffset)) != InventoryControllerCallback)
                continue;

            int rootOffset = callbackOffset - ControllerCallbackOffset;
            if (rootOffset < 0 || rootOffset + RenderArrayOffset + sizeof(uint) > heap.Length)
                continue;

            uint candidateRoot = HeapScanAddress + (uint)rootOffset;
            uint renderArray = BinaryPrimitives.ReadUInt32LittleEndian(
                heap.AsSpan(rootOffset + RenderArrayOffset));
            if (!IsMainRamPointer(renderArray, 40 * sizeof(uint)))
                continue;

            byte[] entryPointers = new byte[40 * sizeof(uint)];
            if (!memory.TryRead(renderArray, entryPointers, out _))
                continue;

            uint entry0 = BinaryPrimitives.ReadUInt32LittleEndian(entryPointers);
            uint entry2 = BinaryPrimitives.ReadUInt32LittleEndian(entryPointers.AsSpan(2 * sizeof(uint)));
            if (!IsMainRamPointer(entry0, RenderNodeSize) || !IsMainRamPointer(entry2, RenderNodeSize))
                continue;

            Span<byte> validation = stackalloc byte[RenderNodeSize * 2];
            if (!memory.TryRead(entry0, validation[..RenderNodeSize], out _) ||
                !memory.TryRead(entry2, validation[RenderNodeSize..], out _))
            {
                continue;
            }

            uint renderContext = candidateRoot + RenderContextOffset;
            uint dynamicFill = candidateRoot + DynamicFillOffset;
            if (BinaryPrimitives.ReadUInt32LittleEndian(validation) != VanillaInventoryBorder ||
                BinaryPrimitives.ReadUInt32LittleEndian(validation[RenderNodeSize..]) != dynamicFill ||
                BinaryPrimitives.ReadUInt32LittleEndian(validation[(RenderNodeSize + 8)..]) != renderContext)
            {
                continue;
            }

            uint existingTail = BinaryPrimitives.ReadUInt32LittleEndian(validation[(RenderNodeSize + 12)..]);
            uint textRightNode = NodeAddress(5);
            if (existingTail == textRightNode)
            {
                inventoryRoot = candidateRoot;
                message = $"AP panel is attached to inventory root 0x{candidateRoot:x8}.";
                return true;
            }

            if (existingTail != 0)
            {
                message = $"Inventory render tail is already owned by 0x{existingTail:x8}; refusing to overwrite it.";
                return false;
            }

            if (!TryWriteDedicatedNodes(memory, entry2, candidateRoot, out message))
                return false;

            inventoryRoot = candidateRoot;
            newlyAttached = true;
            message = $"Attached the AP panel to inventory root 0x{candidateRoot:x8}.";
            return true;
        }

        message = "Inventory is opening, but its render controller is not ready yet.";
        return true;
    }

    private static bool TryGetHudLayout(
        IEmulatorMemory memory,
        out bool compactSeededHud,
        out string message)
    {
        compactSeededHud = false;
        Span<byte> memoryTopBytes = stackalloc byte[sizeof(uint)];
        if (!memory.TryRead(
                AzureDreamsMailbox.MemoryTopAddress,
                memoryTopBytes,
                out string? readError))
        {
            message = readError ?? "Could not read the patched memory-top marker.";
            return false;
        }

        uint memoryTop = BinaryPrimitives.ReadUInt32LittleEndian(memoryTopBytes);
        if (memoryTop == AzureDreamsMailbox.ExpectedPatchedMemoryTop)
        {
            Span<byte> signature = stackalloc byte[8];
            if (!memory.TryRead(
                    RestoredSeededHudSignatureAddress,
                    signature,
                    out string? signatureError))
            {
                message = signatureError ?? "Could not read the seeded HUD signature.";
                return false;
            }

            compactSeededHud = !signature.SequenceEqual("ADAPHUD1"u8);
            message = string.Empty;
            return true;
        }
        if (memoryTop == AzureDreamsMailbox.LegacyPatchedMemoryTop)
        {
            message = string.Empty;
            return true;
        }

        message =
            "The running game does not expose a supported inventory HUD layout; " +
            "refusing to write HUD memory.";
        return false;
    }

    internal static byte[] CreatePaddedLabel(string text)
    {
        byte[] glyphs = CreateInventoryText(text);
        if (glyphs.Length > LabelBufferSize)
            throw new ArgumentException("Inventory HUD label exceeds its reserved buffer.", nameof(text));

        byte[] padded = new byte[LabelBufferSize];
        glyphs.CopyTo(padded, 0);
        return padded;
    }

    internal static byte[] CreateInventoryText(string text)
    {
        ArgumentNullException.ThrowIfNull(text);

        int glyphCount = text.Count(character => character != ' ');
        if (glyphCount == 0)
            return [];

        byte[] result = new byte[glyphCount * GlyphSize];
        int x = 0;
        int glyphIndex = 0;

        foreach (char character in text)
        {
            if (character == ' ')
            {
                // The game's large-font builder advances half a glyph for a
                // literal space in an otherwise two-byte encoded string.
                x += 4;
                continue;
            }

            if (character is < '!' or > '~')
                throw new ArgumentException($"Unsupported inventory HUD character U+{(int)character:x4}.", nameof(text));

            Span<byte> glyph = result.AsSpan(glyphIndex * GlyphSize, GlyphSize);
            glyph[0] = 0x00;
            glyph[1] = 0x2c;
            glyph[2] = unchecked((byte)(x + 0x80));
            glyph[3] = 0x80;
            BinaryPrimitives.WriteUInt16LittleEndian(glyph[4..], 0x001f);
            BinaryPrimitives.WriteUInt16LittleEndian(glyph[6..], 0x7c84);
            glyph[8] = (byte)((character & 0x0f) << 3);
            glyph[9] = unchecked((byte)((character & 0xf0) - 0x20));
            glyph[10] = 8;
            glyph[11] = 16;

            x += 8;
            glyphIndex++;
        }

        result[^GlyphSize] |= 0x80;
        return result;
    }

    private static bool TryWritePanelBorder(IEmulatorMemory memory, out string message)
    {
        byte[] border = new byte[PanelBorderSize];
        if (!memory.TryRead(VanillaInventoryBorder, border, out string? readError))
        {
            message = readError ?? "Could not read the vanilla inventory border asset.";
            return false;
        }

        // Preserve the native-size 8x8 screw caps while shortening the three
        // vertical rail segments to the validated 22-unit AP panel height.
        border[3] = 22;
        border[15] = 22;
        border[59] = 0;
        border[71] = 18;
        border[83] = 0;
        border[95] = 18;
        border[99] = 22;
        border[131] = 0;
        border[143] = 0;

        if (!memory.TryWrite(PanelBorderAddress, border, out string? writeError))
        {
            message = writeError ?? "Could not write the AP panel border asset.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static bool TryWriteDedicatedNodes(
        IEmulatorMemory memory,
        uint vanillaFillLeftNode,
        uint inventoryRoot,
        out string message)
    {
        uint renderContext = inventoryRoot + RenderContextOffset;
        uint dynamicFill = inventoryRoot + DynamicFillOffset;

        uint[] kinds =
        [
            dynamicFill,
            dynamicFill,
            PanelBorderAddress,
            PanelBorderAddress,
            KeycardLabelAddress,
            MaxFloorLabelAddress,
        ];
        uint[] scales =
        [
            0x0400_1000,
            0x0400_1000,
            0x1000_1000,
            0x1000_1000,
            0x1000_1000,
            0x1000_1000,
        ];
        uint[] coordinates =
        [
            0xff2a_0000,
            0xff2a_0080,
            0xffea_0000,
            0xffea_0080,
            0x0070_0048,
            0x0070_00c8,
        ];
        uint[] links =
        [
            0,
            NodeAddress(0),
            NodeAddress(1),
            NodeAddress(2),
            NodeAddress(3),
            NodeAddress(4),
        ];

        byte[] transformBytes = new byte[RenderNodeSize];
        byte[] nodeBytes = new byte[RenderNodeSize];
        for (int index = 0; index < RenderNodeCount; index++)
        {
            Span<byte> transform = transformBytes;
            transform.Clear();
            BinaryPrimitives.WriteUInt32LittleEndian(transform, 0x0080_8080);
            BinaryPrimitives.WriteUInt32LittleEndian(transform[4..], scales[index]);
            BinaryPrimitives.WriteUInt32LittleEndian(transform[8..], coordinates[index]);
            if (!memory.TryWrite(TransformAddress(index), transform, out string? transformError))
            {
                message = transformError ?? $"Could not write AP render transform {index}.";
                return false;
            }

            Span<byte> node = nodeBytes;
            node.Clear();
            BinaryPrimitives.WriteUInt32LittleEndian(node, kinds[index]);
            BinaryPrimitives.WriteUInt32LittleEndian(node[4..], TransformAddress(index));
            BinaryPrimitives.WriteUInt32LittleEndian(node[8..], renderContext);
            BinaryPrimitives.WriteUInt32LittleEndian(node[12..], links[index]);
            if (!memory.TryWrite(NodeAddress(index), node, out string? nodeError))
            {
                message = nodeError ?? $"Could not write AP render node {index}.";
                return false;
            }
        }

        Span<byte> tailLink = stackalloc byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(tailLink, NodeAddress(5));
        if (!memory.TryWrite(vanillaFillLeftNode + 12, tailLink, out string? linkError))
        {
            message = linkError ?? "Could not attach the AP nodes to the inventory render chain.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static uint NodeAddress(int index) => RenderNodeAddress + (uint)(index * RenderNodeSize);

    private static uint TransformAddress(int index) => RenderTransformAddress + (uint)(index * RenderNodeSize);

    private static bool IsMainRamPointer(uint address, int length)
    {
        uint offset = address & 0x1fff_ffff;
        return offset < 2 * 1024 * 1024 && length >= 0 && offset <= 2 * 1024 * 1024 - (uint)length;
    }
}
