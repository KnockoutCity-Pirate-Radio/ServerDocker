#!/usr/bin/env python3
"""Apply the three commerce patches a modded Knockout City server needs.

This script is deliberately narrow. Three patches, one binary, no selection
flags and no knowledge of any other patch that exists. A server image that
players connect to has no business being able to enable debug code paths, so
the patches that do that are not in here to be enabled by accident.

Every site is verified against its expected bytes before anything is written,
and a mismatch aborts without touching the file: these are addresses in one
specific shipped build, and applying them blind to a different build would
corrupt it. An already-patched site counts as success, so this is safe to run
on every container boot.

  1. fix_offers_delete
     `vbackend_db_commerce_fix_offers` runs a cleanup query on every import:
         DELETE FROM commerce_offers WHERE NOT EXISTS (...) AND NOT EXISTS (...)
     which deletes any offer whose item is non-consumable and has no currency
     entry -- over 90% of imported offers, including every durable accessory
     offer. Rewriting the first `NOT EXIS` to `FALSE-- ` (same length) turns
     the WHERE clause into `WHERE FALSE -- <rest>`: valid SQL, zero rows
     affected. Without this a custom store does not survive one boot.

  2. skip_clear_initial_inventory
     `backendImport` calls `vdb_clear_initial_inventory`, which issues
     `DELETE FROM commerce_inventory_initial` on every catch-data import.
     NOP the 5-byte call. Sibling clears and the function itself are left
     intact. See patch 3 for why this one is not optional.

  3. honor_initial_inventory
     The accessory-import gate reads the `.accessory` field
     `in_initial_inventory`, but when the flag is false it does not skip --
     it falls through to a type-flag table that only excludes a few
     consumable types. So nearly every accessory lands in
     `commerce_inventory_initial` and is granted to every player. Replacing
     the fall-through with an unconditional jump restores the intended
     meaning of the flag.

     Patches 2 and 3 must be applied together. `commerce_inventory_initial`
     is global -- one `contents uuid` column, no user id -- and player
     ownership is derived from it rather than copied into per-player rows at
     grant time. With 3 on and 2 off, the second boot's import wipes the table
     and re-adds only flagged rows, and every player loses everything the
     table used to grant them.

Usage:
    python3 patch_server.py <path-to-KnockoutCityServer.exe>
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


class Patch:
    """One byte-level edit, with the bytes that prove we are in the right build."""

    def __init__(self, key, description, guard_rva, guard_bytes,
                 patch_rva, patch_original, patch_replacement):
        self.key = key
        self.description = description
        self.guard_rva = guard_rva
        self.guard_bytes = guard_bytes
        self.patch_rva = patch_rva
        self.patch_original = patch_original
        self.patch_replacement = patch_replacement


PATCHES = [
    Patch(
        key="fix_offers_delete",
        description="cleanup DELETE never matches (keeps imported offers)",
        guard_rva=0xF4E1732,
        guard_bytes=b"NOT EXIS",
        patch_rva=0xF4E1732,
        patch_original=b"NOT EXIS",
        patch_replacement=b"FALSE-- ",
    ),
    Patch(
        key="skip_clear_initial_inventory",
        description="skip the initial-inventory wipe on catch-data import",
        guard_rva=0x8CDB12F,
        guard_bytes=bytes([0xE8, 0xFC, 0xF4, 0xFA, 0xFF]),
        patch_rva=0x8CDB12F,
        patch_original=bytes([0xE8, 0xFC, 0xF4, 0xFA, 0xFF]),
        patch_replacement=bytes([0x90, 0x90, 0x90, 0x90, 0x90]),
    ),
    Patch(
        key="honor_initial_inventory",
        description="in_initial_inventory == false actually skips the grant",
        guard_rva=0x8CDB30B,
        guard_bytes=bytes([0x75, 0x16, 0x48, 0x63, 0x47, 0x58]),
        patch_rva=0x8CDB30D,
        patch_original=bytes([0x48, 0x63, 0x47, 0x58]),
        patch_replacement=bytes([0xEB, 0x21, 0x90, 0x90]),
    ),
]


def read_pe_sections(data: bytes):
    """(name, virtual_addr, virtual_size, raw_offset, raw_size) per PE section."""
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        raise RuntimeError("not a valid PE file")
    coff = pe_offset + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    size_opt = struct.unpack_from("<H", data, coff + 16)[0]
    section_table = coff + 20 + size_opt
    sections = []
    for i in range(num_sections):
        off = section_table + i * 40
        name = data[off:off + 8].rstrip(b"\x00").decode("ascii", errors="replace")
        virtual_size = struct.unpack_from("<I", data, off + 8)[0]
        virtual_addr = struct.unpack_from("<I", data, off + 12)[0]
        raw_size = struct.unpack_from("<I", data, off + 16)[0]
        raw_offset = struct.unpack_from("<I", data, off + 20)[0]
        sections.append((name, virtual_addr, virtual_size, raw_offset, raw_size))
    return sections


def rva_to_file_offset(sections, rva: int) -> int:
    for _name, va, vs, ro, rs in sections:
        if va <= rva < va + max(vs, rs):
            return ro + (rva - va)
    raise RuntimeError("RVA 0x{:x} is not in any section".format(rva))


def apply_patch(data: bytearray, sections, patch: Patch) -> bool:
    """True when bytes changed, False when the site was already patched."""
    guard_off = rva_to_file_offset(sections, patch.guard_rva)
    patch_off = rva_to_file_offset(sections, patch.patch_rva)

    already = bytes(data[patch_off:patch_off + len(patch.patch_replacement)])
    if already == patch.patch_replacement:
        print("  {}: already patched".format(patch.key))
        return False

    found = bytes(data[guard_off:guard_off + len(patch.guard_bytes)])
    if found != patch.guard_bytes:
        raise RuntimeError(
            "{}: guard mismatch at RVA 0x{:x}\n"
            "  expected {}\n"
            "  found    {}\n"
            "This is not the game build these patches were made for.".format(
                patch.key, patch.guard_rva,
                patch.guard_bytes.hex(), found.hex(),
            )
        )

    actual = bytes(data[patch_off:patch_off + len(patch.patch_original)])
    if actual != patch.patch_original:
        raise RuntimeError(
            "{}: unexpected bytes at the patch site: {} (expected {})".format(
                patch.key, actual.hex(), patch.patch_original.hex(),
            )
        )

    data[patch_off:patch_off + len(patch.patch_replacement)] = patch.patch_replacement
    print("  {}: patched -- {}".format(patch.key, patch.description))
    return True


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: {} <path-to-KnockoutCityServer.exe>".format(sys.argv[0]))

    exe_path = Path(sys.argv[1])
    if not exe_path.exists():
        sys.exit("not found: {}".format(exe_path))

    original = exe_path.read_bytes()
    sections = read_pe_sections(original)
    data = bytearray(original)

    changed = False
    for patch in PATCHES:
        if apply_patch(data, sections, patch):
            changed = True

    if not changed:
        print("  all patches already applied")
        return

    backup = exe_path.with_suffix(exe_path.suffix + ".bak")
    if backup.exists():
        print("  backup already exists, keeping it: {}".format(backup))
    else:
        backup.write_bytes(original)
        print("  wrote backup: {}".format(backup))

    exe_path.write_bytes(bytes(data))
    print("  wrote patched exe: {}".format(exe_path))


if __name__ == "__main__":
    main()
