from __future__ import annotations

import struct

from . import patch, town_receive, town_shop


# ProGrammar's two trusted US-disc intro-skip seams remain byte-exact. The
# client stages "Welcome back, <saved game name>" proactively when it detects
# a compatible checkpoint, so the angel's native FNO 0x7E remains untouched.
# The trusted wake-up script already calls house FNO 0x79 for the Pita;
# redirect only that house-table entry to publish the first-run checkpoint
# request after preserving the original grant.
ANGEL_SCENE_FILE_OFFSET = 0x6F_680C
ANGEL_DIALOGUE_FILE_OFFSET = 0x6F_681A
WAKE_UP_SCRIPT_FILE_OFFSET = 0x41_2CE0
FNO_TABLE_FILE_OFFSET = 0x5_6534
HOUSE_FNO_TABLE_FILE_OFFSET = FNO_TABLE_FILE_OFFSET + 0x34
HOUSE_PITA_FNO = 0x79
HOUSE_PITA_FNO_POINTER_FILE_OFFSET = (
    HOUSE_FNO_TABLE_FILE_OFFSET + HOUSE_PITA_FNO * 4
)

ANGEL_SCENE_PATCH = b"\x05"
# The live V45 hang capture proves the resource loads whole, including its
# four-byte header, at 0x800176F0: the router prefix that the client watches
# for sits at TOWN offset 0x6F681A / runtime 0x8001770A, and the resource's own
# 0x8001770A dialogue pointer at TOWN offset 0x6F6813 agrees.
ANGEL_RESOURCE_FILE_BASE = 0x6F_6800
ANGEL_RESOURCE_RUNTIME_BASE = 0x8001_76F0
ANGEL_ROUTER_ORIGINAL_PREFIX = bytes.fromhex("57 1D 0F 1D 03")
ANGEL_DIALOGUE_RUNTIME_ADDRESS = (
    ANGEL_RESOURCE_RUNTIME_BASE
    + ANGEL_DIALOGUE_FILE_OFFSET
    - ANGEL_RESOURCE_FILE_BASE
)

# The angel resource keeps short callable subroutines ahead of its dialogue,
# each ending in script RETURN (0x16). The scene-transition subroutine begins
# two bytes before ProGrammar's destination byte: it sets script slot 0 to the
# destination scene and then calls FNO 0x80, which the live town context
# (FNO table 0x800D3CC8) maps to 0x800C24C8. That routine indexes the scene
# descriptor table at 0x800D4758 and calls begin_scene_transition_from
# _descriptor (0x8003BAF8). Calling this subroutine rather than inlining the
# destination keeps the returning path on exactly the trusted patched scene.
ANGEL_SCENE_CALL_FILE_OFFSET = ANGEL_SCENE_FILE_OFFSET - 2
ANGEL_SCENE_CALL_RUNTIME_ADDRESS = (
    ANGEL_RESOURCE_RUNTIME_BASE
    + ANGEL_SCENE_CALL_FILE_OFFSET
    - ANGEL_RESOURCE_FILE_BASE
)
WAKE_UP_TRUSTED_PATCH_SIZE = 14

# Vanilla plays this closing angel pose, counts down thirty-one frames, and
# clears the text area before it hands the scene over.
ANGEL_CLOSING_POSE = bytes.fromhex("0F 1D 19")
ANGEL_TRANSITION_DELAY_FRAMES = 0x1E
SCRIPT_YIELD_FRAME = b"\x30"
SCRIPT_CLEAR_TEXT = b"\x08"
SCRIPT_END = b"\x01"


def _script_set_variable(index: int, value: int) -> bytes:
    if not 0 <= index <= 0xFF:
        raise ValueError("Town script variable index must fit one byte.")
    return b"\x34" + bytes((index,)) + struct.pack("<I", value)


def _script_subtract_variable(index: int, subtrahend: int) -> bytes:
    if not 0 <= index <= 0xFF or not 0 <= subtrahend <= 0xFF:
        raise ValueError("Town script variable indexes must fit one byte.")
    return b"\x3C" + bytes((index, subtrahend))


def _script_loop_while_not_negative(index: int, address: int) -> bytes:
    if not 0 <= index <= 0xFF:
        raise ValueError("Town script variable index must fit one byte.")
    return b"\x42" + bytes((index,)) + struct.pack("<I", address)


def _script_call(address: int) -> bytes:
    return b"\x15" + struct.pack("<I", address)


def _encode_returning_dialogue() -> bytes:
    # Strip the shop encoder's zero terminator, then append the native dynamic
    # player-name token, the exclamation that closes the greeting, and the
    # ordinary acknowledgement command.
    prefix = town_shop._encode_shop_name(
        "Welcome back, ",
        max_characters=None,
    )[:-1]
    suffix = town_shop._encode_shop_name("!", max_characters=None)[:-1]
    return prefix + bytes.fromhex("FE 00") + suffix + b"\x11"


def build_returning_angel_script() -> bytes:
    """Greet a returning player and then hand over exactly like vanilla.

    Script slot 0 is not a persistent variable: it is the first argument of the
    next FNO call. Setting it alone therefore never moved the returning player
    anywhere, which is why V45 and V46 closed the greeting and then sat on the
    angel screen forever. The acknowledgement must be followed by the same
    closing sequence the original name-selection dialogue runs, ending in the
    call that actually starts the scene transition.
    """

    head = b"".join(
        (
            ANGEL_ROUTER_ORIGINAL_PREFIX,
            _encode_returning_dialogue(),
            ANGEL_CLOSING_POSE,
            _script_set_variable(0, ANGEL_TRANSITION_DELAY_FRAMES),
        )
    )
    # The countdown re-enters at its own yield, so the loop target depends on
    # where the client stages this script.
    delay_loop_address = ANGEL_DIALOGUE_RUNTIME_ADDRESS + len(head)
    return b"".join(
        (
            head,
            SCRIPT_YIELD_FRAME,
            _script_set_variable(1, 1),
            _script_subtract_variable(0, 1),
            _script_loop_while_not_negative(0, delay_loop_address),
            SCRIPT_CLEAR_TEXT,
            _script_call(ANGEL_SCENE_CALL_RUNTIME_ADDRESS),
            # End this top-level dialogue instead of returning through the
            # original name-selection continuation.
            SCRIPT_END,
        )
    )


WAKE_UP_SCRIPT_PATCH = bytes.fromhex(
    "0C 14 00 0C 16 00 2E 79 3E 00 1E 22 02 80"
)
if len(WAKE_UP_SCRIPT_PATCH) != WAKE_UP_TRUSTED_PATCH_SIZE:
    raise ValueError("Trusted ProGrammar wake-up patch changed size unexpectedly.")
HOUSE_PITA_FNO_POINTER_PATCH = struct.pack(
    "<I",
    town_receive.INTRO_CAPTURE_WRAPPER_ADDRESS,
)


def iter_intro_skip_file_patches() -> tuple[tuple[int, bytes], ...]:
    return (
        (ANGEL_SCENE_FILE_OFFSET, ANGEL_SCENE_PATCH),
        (WAKE_UP_SCRIPT_FILE_OFFSET, WAKE_UP_SCRIPT_PATCH),
        (
            HOUSE_PITA_FNO_POINTER_FILE_OFFSET,
            HOUSE_PITA_FNO_POINTER_PATCH,
        ),
    )


def iter_intro_skip_raw_patches() -> tuple[tuple[int, bytes], ...]:
    result: list[tuple[int, bytes]] = []
    for file_offset, data in iter_intro_skip_file_patches():
        copied = 0
        while copied < len(data):
            current = file_offset + copied
            within_sector = current % patch.FORM1_USER_SIZE
            length = min(
                len(data) - copied,
                patch.FORM1_USER_SIZE - within_sector,
            )
            raw_offset = town_shop.mode2_file_offset_to_raw_offset(
                town_shop.TOWN_FILE_START_LBA,
                current,
            )
            result.append((raw_offset, data[copied : copied + length]))
            copied += length
    return tuple(result)


def _iter_ppf_ranges(ppf: bytes | bytearray) -> tuple[tuple[int, int], ...]:
    if len(ppf) < patch.PPF_HEADER_SIZE or ppf[:6] != b"PPF10\0":
        raise ValueError("The Azure Dreams player patch is not a PPF1 patch.")

    result: list[tuple[int, int]] = []
    cursor = patch.PPF_HEADER_SIZE
    while cursor < len(ppf):
        if cursor + 5 > len(ppf):
            raise ValueError(f"Truncated PPF record header at 0x{cursor:x}.")
        raw_offset, length = struct.unpack_from("<IB", ppf, cursor)
        cursor += 5
        if not length or cursor + length > len(ppf):
            raise ValueError(f"Invalid PPF record at 0x{cursor - 5:x}.")
        result.append((raw_offset, raw_offset + length))
        cursor += length
    return tuple(result)


def append_intro_skip_ppf_records(ppf: bytearray) -> None:
    """Append the trusted intro skip after proving its ranges are still free."""

    existing_ranges = _iter_ppf_ranges(ppf)
    raw_patches = iter_intro_skip_raw_patches()
    for raw_offset, data in raw_patches:
        end = raw_offset + len(data)
        for existing_start, existing_end in existing_ranges:
            if raw_offset < existing_end and existing_start < end:
                raise ValueError(
                    "Intro-skip patch range "
                    f"0x{raw_offset:x}-0x{end - 1:x} overlaps existing PPF "
                    f"range 0x{existing_start:x}-0x{existing_end - 1:x}."
                )

    for raw_offset, data in raw_patches:
        copied = 0
        while copied < len(data):
            record = data[copied : copied + 255]
            ppf.extend(struct.pack("<IB", raw_offset + copied, len(record)))
            ppf.extend(record)
            copied += len(record)
