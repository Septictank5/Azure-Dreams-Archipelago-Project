"""A very small R3000A interpreter, for testing generated MIPS.

The seed page carries hand-assembled routines. Every one of them has to respect
the R3000A's load delay - the destination register of a load is not readable by
the instruction immediately after it - and this project has shipped that bug
more than once (see the elevator gate's `sltu` in
`docs/reverse-engineering-notes.md`). Static review keeps missing it.

So this models the delay rather than ignoring it: a load's result commits after
the following instruction has read its operands. A routine that reads a
register too early sees the stale value here exactly as it would on hardware,
and the test fails on the wrong output instead of on a console.

Only the instructions the generated routines actually use are implemented. An
unknown opcode raises rather than being skipped, so growing a routine cannot
silently fall through this.
"""

from __future__ import annotations

import struct


class MipsError(RuntimeError):
    pass


class Memory:
    """Sparse little-endian memory. Reads of untouched addresses return zero."""

    def __init__(self) -> None:
        self._data: dict[int, int] = {}

    def load_bytes(self, address: int, payload: bytes) -> None:
        for offset, value in enumerate(payload):
            self._data[address + offset] = value

    def read8(self, address: int) -> int:
        return self._data.get(address & 0xFFFF_FFFF, 0)

    def read16(self, address: int) -> int:
        return self.read8(address) | (self.read8(address + 1) << 8)

    def read32(self, address: int) -> int:
        return self.read16(address) | (self.read16(address + 2) << 16)

    def write8(self, address: int, value: int) -> None:
        self._data[address & 0xFFFF_FFFF] = value & 0xFF

    def write32(self, address: int, value: int) -> None:
        for shift in range(4):
            self.write8(address + shift, (value >> (shift * 8)) & 0xFF)

    def read_until_zero(self, address: int) -> bytes:
        out = bytearray()
        while True:
            byte = self.read8(address + len(out))
            out.append(byte)
            if byte == 0:
                return bytes(out)


def _signed(value: int) -> int:
    """Sign-extend a 16-bit immediate."""
    return value - 0x1_0000 if value >= 0x8000 else value


def _as_signed(value: int) -> int:
    """Reinterpret a 32-bit register as signed."""
    value &= 0xFFFF_FFFF
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


class Cpu:
    """Runs one routine to its `jr ra`, with Python stubs for called addresses."""

    def __init__(self, memory: Memory, stubs: dict[int, object] | None = None) -> None:
        self.memory = memory
        self.registers = [0] * 32
        self.stubs = stubs or {}

    def run(self, start: int, stack_pointer: int = 0x801F_C000, limit: int = 100_000) -> int:
        self.registers[29] = stack_pointer
        self.registers[31] = 0xDEAD_0000  # sentinel return address
        pc = start
        branch_target: int | None = None
        pending_load: tuple[int, int] | None = None

        for _ in range(limit):
            instruction = self.memory.read32(pc)
            next_pc = pc + 4
            take = branch_target
            branch_target = None

            new_pending, jumped = self._execute(instruction, pc, pending_load)
            pending_load = new_pending
            if jumped is not None:
                branch_target = jumped

            if take is None:
                pc = next_pc
                continue
            if take == 0xDEAD_0000:
                return self.registers[2]
            stub = self.stubs.get(take)
            if stub is not None:
                # The delay slot has just run, which is the whole reason stubs
                # are dispatched here rather than at the jal itself.
                stub(self)
                pc = self.registers[31]
                if pc == 0xDEAD_0000:
                    # A tail-jump into a stub: the routine ended by handing
                    # control to the stubbed callee, whose return goes
                    # straight back to the harness.
                    return self.registers[2]
                continue
            pc = take
        raise MipsError("instruction limit reached; the routine never returned")

    def _commit(self, pending_load: tuple[int, int] | None) -> None:
        if pending_load is not None:
            register, value = pending_load
            if register:
                self.registers[register] = value & 0xFFFF_FFFF

    def _execute(self, instruction, pc, pending_load):
        """Returns (new pending load, branch target or None)."""
        opcode = instruction >> 26
        rs = (instruction >> 21) & 0x1F
        rt = (instruction >> 16) & 0x1F
        rd = (instruction >> 11) & 0x1F
        shift = (instruction >> 6) & 0x1F
        immediate = instruction & 0xFFFF
        registers = self.registers

        # Operands are read before the previous load commits: that is the delay.
        source = registers[rs]
        target = registers[rt]
        self._commit(pending_load)

        def write(register: int, value: int) -> None:
            if register:
                registers[register] = value & 0xFFFF_FFFF

        if opcode == 0x00:
            function = instruction & 0x3F
            if function == 0x00:  # sll (also nop)
                write(rd, target << shift)
            elif function == 0x02:  # srl
                write(rd, (target & 0xFFFF_FFFF) >> shift)
            elif function == 0x03:  # sra
                write(rd, _as_signed(target) >> shift)
            elif function == 0x04:  # sllv
                write(rd, target << (source & 0x1F))
            elif function == 0x06:  # srlv
                write(rd, (target & 0xFFFF_FFFF) >> (source & 0x1F))
            elif function == 0x07:  # srav
                write(rd, _as_signed(target) >> (source & 0x1F))
            elif function == 0x21:  # addu
                write(rd, source + target)
            elif function == 0x23:  # subu
                write(rd, source - target)
            elif function == 0x24:  # and
                write(rd, source & target)
            elif function == 0x25:  # or
                write(rd, source | target)
            elif function == 0x26:  # xor
                write(rd, source ^ target)
            elif function == 0x2A:  # slt
                write(rd, 1 if _as_signed(source) < _as_signed(target) else 0)
            elif function == 0x2B:  # sltu
                write(rd, 1 if source < target else 0)
            elif function == 0x08:  # jr
                return None, source
            else:
                raise MipsError(f"unimplemented SPECIAL 0x{function:02x} at 0x{pc:08x}")
            return None, None

        if opcode == 0x01:  # REGIMM: bltz / bgez
            taken = _as_signed(source) < 0 if rt == 0x00 else _as_signed(source) >= 0
            if rt not in (0x00, 0x01):
                raise MipsError(f"unimplemented REGIMM 0x{rt:02x} at 0x{pc:08x}")
            return None, (pc + 4 + _signed(immediate) * 4) if taken else None
        if opcode == 0x02:  # j
            return None, (pc & 0xF000_0000) | ((instruction & 0x3FF_FFFF) << 2)
        if opcode == 0x03:  # jal
            registers[31] = pc + 8
            return None, (pc & 0xF000_0000) | ((instruction & 0x3FF_FFFF) << 2)
        if opcode == 0x04:  # beq
            return None, (pc + 4 + _signed(immediate) * 4) if source == target else None
        if opcode == 0x05:  # bne
            return None, (pc + 4 + _signed(immediate) * 4) if source != target else None
        if opcode == 0x06:  # blez
            return None, (pc + 4 + _signed(immediate) * 4) if _as_signed(source) <= 0 else None
        if opcode == 0x07:  # bgtz
            return None, (pc + 4 + _signed(immediate) * 4) if _as_signed(source) > 0 else None
        if opcode == 0x09:  # addiu
            write(rt, source + _signed(immediate))
            return None, None
        if opcode == 0x0B:  # sltiu
            write(rt, 1 if (source & 0xFFFF_FFFF) < (_signed(immediate) & 0xFFFF_FFFF) else 0)
            return None, None
        if opcode == 0x0A:  # slti
            write(rt, 1 if _as_signed(source) < _signed(immediate) else 0)
            return None, None
        if opcode == 0x0C:  # andi
            write(rt, source & immediate)
            return None, None
        if opcode == 0x0D:  # ori
            write(rt, source | immediate)
            return None, None
        if opcode == 0x0E:  # xori
            write(rt, source ^ immediate)
            return None, None
        if opcode == 0x0F:  # lui
            write(rt, immediate << 16)
            return None, None
        if opcode == 0x20:  # lb
            byte = self.memory.read8(source + _signed(immediate))
            return (rt, byte - 0x100 if byte >= 0x80 else byte), None
        if opcode == 0x21:  # lh
            half = self.memory.read16(source + _signed(immediate))
            return (rt, half - 0x1_0000 if half >= 0x8000 else half), None
        if opcode == 0x23:  # lw
            return (rt, self.memory.read32(source + _signed(immediate))), None
        if opcode == 0x24:  # lbu
            return (rt, self.memory.read8(source + _signed(immediate))), None
        if opcode == 0x25:  # lhu
            return (rt, self.memory.read16(source + _signed(immediate))), None
        if opcode == 0x28:  # sb
            self.memory.write8(source + _signed(immediate), target & 0xFF)
            return None, None
        if opcode == 0x29:  # sh
            address = source + _signed(immediate)
            self.memory.write8(address, target & 0xFF)
            self.memory.write8(address + 1, (target >> 8) & 0xFF)
            return None, None
        if opcode == 0x2B:  # sw
            self.memory.write32(source + _signed(immediate), target)
            return None, None

        raise MipsError(f"unimplemented opcode 0x{opcode:02x} at 0x{pc:08x}")
