using System.Buffers.Binary;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

/// <summary>
/// The tower state the progress view draws: which of the two slots on each
/// floor are still uncollected, how deep the current keycard level reaches,
/// and which marked shortcuts are unlocked.
/// </summary>
internal readonly record struct AzureDreamsTowerProgress(
    byte[] CollectedLocationMask,
    int KeycardLevel,
    int CurrentFloor,
    uint ShopMask,
    bool IsInTower = false,
    // Read from the game's own counter rather than counted from the
    // history: the game SPENDS these, so the history only says how many
    // arrived, never how many are left.
    int SendTokens = 0,
    // The three sands, each 0..3. Red raises how far the smith may temper a
    // weapon, Blue a shield, White how many charges the ball charger will hand
    // out per town visit.
    // Durable levels, not inventory - read from the save like the keycard
    // level rather than counted out of the received history.
    int WeaponTemperLevel = 0,
    int ShieldTemperLevel = 0,
    int BallChargeLevel = 0,
    // Whether this ROOM placed the monster-carried third check on each floor
    // (`carrier_system` in the yaml). Not read from memory - the game has no
    // opinion about it - but carried here because everything that draws a
    // floor's slots reads this record.
    bool CarrierChecks = true)
{
    public const int ShopCount = 2;
    public const int SlotsPerShop = 10;

    /// <summary>Shop names in mask order.</summary>
    public static readonly string[] ShopNames = ["Equipment", "Monster"];

    /// <summary>
    /// The save-backed tower floor halfword. The warp helper briefly stages a
    /// marked request here, so anything outside 1-40 means "not in the tower".
    /// </summary>
    public const uint CurrentFloorAddress = 0x8001_0234;

    public const int TopFloor = AzureDreamsInventoryHud.TowerTopFloor;

    /// <summary>The last floor carrying items. Floor 40 is the goal only.</summary>
    public const int ItemFloorCount = AzureDreamsReceiveState.TowerFloorCount;

    /// <summary>
    /// Three per floor since ADSV v4: two on the ground and one carried by
    /// the floor's forced monster spawn, dropped on its death. This is the
    /// journal's width and the save's layout, so it does NOT follow the yaml
    /// option - see <see cref="SlotsWithChecks"/> for what a room placed.
    /// </summary>
    public const int SlotsPerFloor = AzureDreamsReceiveState.SlotsPerFloor;

    /// <summary>The carrier is always the last slot of the floor's three.</summary>
    public const int CarrierSlot = SlotsPerFloor - 1;

    /// <summary>
    /// How many of the floor's slots this room actually filled: three, or two
    /// with the carrier system switched off. The layout keeps its third column
    /// either way - the alternative is the whole tower view changing width the
    /// moment a room connects - it simply has nothing to draw in it.
    /// </summary>
    public int SlotsWithChecks => CarrierChecks ? SlotsPerFloor : CarrierSlot;

    /// <summary>Every sand level shares this ceiling.</summary>
    public const int MaximumSandLevel = AzureDreamsReceiveState.MaximumTemperLevel;

    /// <summary>
    /// What each temper level actually buys at the smith: +0, +10, +20, +40.
    /// Mirrors `blacksmith.TEMPER_CAPS`.
    /// </summary>
    public static readonly int[] TemperCaps = [0, 10, 20, 40];

    /// <summary>
    /// What each ball-charge level buys at the charger: 0, 1, 2 or 3 charges
    /// PER TOWN VISIT, spent wherever the player wants them. Mirrors
    /// `ball_charger.USES_BY_LEVEL`. The ceiling on one ball is ten at every
    /// level (`BallChargeCeiling`), so this is an allowance, not a cap.
    /// </summary>
    public static readonly int[] BallChargeUsesPerVisit = [0, 1, 2, 3];

    /// <summary>The most charges any one ball may hold, at every level.</summary>
    public const int BallChargeCeiling = 10;

    /// <summary>Marked shortcut floors, and the keycard level each needs.</summary>
    public static readonly (int Floor, int RequiredKeycardLevel)[] Shortcuts =
    [
        (10, 2),
        (20, 4),
        (30, 6),
    ];

    /// <summary>
    /// The deepest floor the current clearance reaches. This is the same
    /// formula the in-game HUD prints, so the two can never disagree.
    /// </summary>
    public int MaxReachableFloor =>
        Math.Min(TopFloor, KeycardLevel * 5 + 4);

    public bool IsCollected(int floor, int slot)
    {
        if (floor < 1 || floor > ItemFloorCount ||
            slot < 0 || slot >= SlotsPerFloor)
        {
            return false;
        }

        int index = (floor - 1) * SlotsPerFloor + slot;
        if (CollectedLocationMask is null ||
            !AzureDreamsReceiveState.TryGetTowerMaskPosition(index, out int byteIndex, out byte bit) ||
            byteIndex >= CollectedLocationMask.Length)
        {
            return false;
        }
        return (CollectedLocationMask[byteIndex] & bit) != 0;
    }

    public bool IsShortcutUnlocked(int requiredKeycardLevel) =>
        KeycardLevel >= requiredKeycardLevel;

    /// <summary>
    /// Shops open at keycard level 0, 3 and 6. Since APWorld 0.9.56 the
    /// Monster Shop's threshold is enforced by its building door reading the
    /// same save-backed keycard word this panel reads, so the two views of
    /// "available" cannot diverge. (The unbuilt third shop still has an
    /// interior list gate at level 6.)
    /// </summary>
    public static int RequiredKeycardLevel(int shop) => shop * 3;

    public bool IsShopUnlocked(int shop) =>
        KeycardLevel >= RequiredKeycardLevel(shop);

    public bool IsShopSlotPurchased(int shop, int slot)
    {
        if (shop < 0 || shop >= ShopCount || slot < 0 || slot >= SlotsPerShop)
            return false;
        int index = shop * SlotsPerShop + slot;
        return (ShopMask & (1u << index)) != 0;
    }

    /// <summary>
    /// True when the floor halfword names a real tower floor, whether or not
    /// the player is standing on it right now.
    ///
    /// <para>The halfword is save-backed and is <b>not</b> cleared on the way
    /// back to town, so in town this keeps reading whichever floor the last
    /// trip ended on. Goal recognition wants exactly that - a stale unmarked 40
    /// can only exist if floor 40 was genuinely reached, so a client that
    /// attaches after the fact still sees the goal.</para>
    /// </summary>
    public bool HasCurrentFloor =>
        CurrentFloor >= 1 && CurrentFloor <= TopFloor;

    /// <summary>
    /// True only while the player is actually standing on that floor. This is
    /// what the view draws Koh and the live-floor highlight from: a stale floor
    /// left over from the last trip is a fact about the save, not a position,
    /// and reporting it while the player is in town is just wrong.
    /// </summary>
    public bool IsOnLiveFloor => IsInTower && HasCurrentFloor;

    public bool Equivalent(AzureDreamsTowerProgress other) =>
        KeycardLevel == other.KeycardLevel &&
        // The three sand readouts follow these, and a sand changes nothing
        // else in the record - so leaving them out would freeze the readout
        // at whatever it was when something else last moved.
        WeaponTemperLevel == other.WeaponTemperLevel &&
        ShieldTemperLevel == other.ShieldTemperLevel &&
        BallChargeLevel == other.BallChargeLevel &&
        // Part of the comparison for the same reason the floor is: spending
        // a token changes nothing else, and the readout has to follow it.
        SendTokens == other.SendTokens &&
        CurrentFloor == other.CurrentFloor &&
        // Part of the comparison, not incidental: returning to town changes
        // nothing but this, and the marker has to come off when it does.
        IsInTower == other.IsInTower &&
        // Room configuration rather than game state, but it arrives one poll
        // after the view is first drawn, and the third column has to empty
        // itself when it does.
        CarrierChecks == other.CarrierChecks &&
        ShopMask == other.ShopMask &&
        (CollectedLocationMask ?? []).AsSpan()
            .SequenceEqual(other.CollectedLocationMask ?? []);
}

internal static class AzureDreamsTowerProgressReader
{
    /// <summary>
    /// The floor Koh is standing on RIGHT NOW, or 0 for anything else - town,
    /// the title screen, a warp helper's staged value, a floor number outside
    /// the tower's range.
    ///
    /// <para>Two small reads rather than the whole record: this is asked on
    /// the poll cadence by the forced-trap pump, which only needs to know
    /// whether the player is still standing where the trap was picked up.</para>
    /// </summary>
    public static int ReadLiveTowerFloor(IEmulatorMemory memory)
    {
        Span<byte> loadedMode = stackalloc byte[1];
        if (!memory.TryRead(AzureDreamsTownCheckpoint.LoadedModeAddress, loadedMode, out _) ||
            loadedMode[0] != AzureDreamsTownCheckpoint.TowerMode)
        {
            return 0;
        }

        Span<byte> floorBytes = stackalloc byte[2];
        if (!memory.TryRead(AzureDreamsTowerProgress.CurrentFloorAddress, floorBytes, out _))
            return 0;

        int floor = BinaryPrimitives.ReadUInt16LittleEndian(floorBytes);
        return floor >= 1 && floor <= AzureDreamsTowerProgress.TopFloor ? floor : 0;
    }

    /// <summary>
    /// Reads the whole persistent block in one pass. Returns false only when
    /// the read itself fails; an absent extension is reported as no progress
    /// so the view can still draw an empty tower.
    /// </summary>
    public static bool TryRead(
        IEmulatorMemory memory,
        out AzureDreamsTowerProgress progress,
        out string message,
        bool carrierChecks = true)
    {
        progress = new AzureDreamsTowerProgress(
            new byte[AzureDreamsReceiveState.LocationMaskSize],
            0,
            0,
            0);

        Span<byte> state = stackalloc byte[AzureDreamsReceiveState.PersistentStateSize];
        if (!memory.TryRead(
                AzureDreamsReceiveState.PersistentStateAddress,
                state,
                out string? readError))
        {
            message = readError ?? "Could not inspect the persistent Azure Dreams state.";
            return false;
        }
        if (BinaryPrimitives.ReadUInt32LittleEndian(state) !=
            AzureDreamsReceiveState.PersistentStateMagic)
        {
            message = string.Empty;
            return true;
        }

        byte[] mask = state
            .Slice(
                AzureDreamsReceiveState.PersistentLocationMaskOffset,
                AzureDreamsReceiveState.LocationMaskSize)
            .ToArray();
        uint keycard = BinaryPrimitives.ReadUInt32LittleEndian(
            state[AzureDreamsReceiveState.KeycardLevelOffset..]);
        if (keycard > AzureDreamsReceiveState.MaximumKeycardLevel)
            keycard = AzureDreamsReceiveState.MaximumKeycardLevel;

        // The floor lives outside the extension block, in the save-backed area
        // the warp helper also writes, so it needs its own read.
        int currentFloor = 0;
        Span<byte> floorBytes = stackalloc byte[2];
        if (memory.TryRead(
                AzureDreamsTowerProgress.CurrentFloorAddress,
                floorBytes,
                out _))
        {
            currentFloor = BinaryPrimitives.ReadUInt16LittleEndian(floorBytes);
        }

        // The floor halfword survives the walk back to town, so it cannot say
        // on its own whether the player is in the tower. The loaded-mode byte
        // can, and it is the same one the checkpoint lifecycle already trusts.
        bool isInTower = false;
        Span<byte> loadedMode = stackalloc byte[1];
        if (memory.TryRead(
                AzureDreamsTownCheckpoint.LoadedModeAddress,
                loadedMode,
                out _))
        {
            isInTower = loadedMode[0] == AzureDreamsTownCheckpoint.TowerMode;
        }

        uint shopMask = BinaryPrimitives.ReadUInt32LittleEndian(
            state[AzureDreamsReceiveState.PersistentShopMaskOffset..]);

        // Its own read: the pair sits past the ADSV record, outside the
        // block above, precisely so it needed no journal version bump.
        int sendTokens = 0;
        if (AzureDreamsReceiveState.TryReadSendTokens(memory, out uint tokens, out _))
            sendTokens = (int)Math.Min(tokens, int.MaxValue);

        // The two temper levels came down with the block above; the ball
        // charger's byte sits just BELOW the record (the record is full) and
        // needs its own read.
        int weaponTemper = Math.Min(
            (int)state[AzureDreamsReceiveState.WeaponTemperLevelOffset],
            AzureDreamsTowerProgress.MaximumSandLevel);
        int shieldTemper = Math.Min(
            (int)state[AzureDreamsReceiveState.ShieldTemperLevelOffset],
            AzureDreamsTowerProgress.MaximumSandLevel);
        int ballCharge = 0;
        Span<byte> charge = stackalloc byte[1];
        if (memory.TryRead(AzureDreamsReceiveState.BallChargeLevelAddress, charge, out _))
        {
            ballCharge = Math.Min(
                (int)charge[0], AzureDreamsTowerProgress.MaximumSandLevel);
        }

        progress = new AzureDreamsTowerProgress(
            mask,
            (int)keycard,
            currentFloor,
            shopMask,
            isInTower,
            sendTokens,
            weaponTemper,
            shieldTemper,
            ballCharge,
            carrierChecks);
        message = string.Empty;
        return true;
    }
}
