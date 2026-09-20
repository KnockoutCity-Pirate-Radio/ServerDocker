#!/usr/bin/env python3
"""Turn a content-patch manifest into the SQL that advertises it.

Writes SQL to stdout; the entrypoint pipes it into psql. Keeping this a pure
text transform means no database driver in the image and nothing to configure:
psql already knows how to reach the database from the connection string the
server itself uses.

The manifest is what the Studio export emits next to the two archives:

    {
      "file_name":         "holoween-2026-09-20-1.vzip",
      "file_hash":         "<base64 RSA signature over the whole archive>",
      "file_size_bytes":   12345678,
      "changelist_number": 269701,
      "platforms":         ["win64-gen2", "win64"],
      "url":               ""
    }

`url` is normally empty in the file -- it is the address *clients* reach this
server on, which the exporting machine cannot know -- and is supplied here by
the host. See the README.

Three things about the generated SQL are load-bearing:

  * It deletes the changelist rows before inserting. The backend's own package
    insert has no ON CONFLICT, so running this twice would otherwise leave two
    rows advertising the same file. Deleting the changelist takes its packages
    with it (both foreign keys cascade).

  * It never inserts into `data_manifest_platforms`. All eight platform rows
    are seeded on a fresh install; inserting would duplicate them.

  * It leaves `mode`, `build`, `build_url`, `release_version` and
    `content_update_version` NULL. That is what a row the backend writes for
    itself looks like -- `vbackend_db_add_data_manifest_package` fills neither
    `mode` nor any derivation for the rest.

Usage:
    python3 register_content_patch.py <manifest.json> [url]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def sql_string(value: str) -> str:
    """A single-quoted SQL literal, with embedded quotes doubled.

    Rejects the characters that would let a value break out of the literal
    across a line. The values here come from a manifest and an environment
    variable, both host-controlled rather than player-controlled, so this is
    a guard against a mangled file rather than against an attacker -- but a
    URL with a stray quote should fail loudly, not produce half a statement.
    """
    if "\x00" in value or "\n" in value or "\r" in value:
        sys.exit("manifest value contains a newline or NUL: {!r}".format(value))
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: {} <manifest.json> [url]".format(sys.argv[0]))

    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    url = sys.argv[2] if len(sys.argv) == 3 and sys.argv[2] else manifest.get("url", "")

    if not url:
        sys.exit(
            "no download URL: the manifest has none and none was given.\n"
            "Set KOC_CONTENT_PATCH_URL to the address clients reach this "
            "server on -- 'localhost' there would point every client at itself."
        )

    for field in ("file_name", "file_hash", "file_size_bytes", "changelist_number"):
        if field not in manifest:
            sys.exit("manifest is missing {!r}".format(field))

    platforms = manifest.get("platforms") or ["win64-gen2", "win64"]
    platform_list = ", ".join(sql_string(p) for p in platforms)
    changelist = int(manifest["changelist_number"])
    size = int(manifest["file_size_bytes"])

    print("BEGIN;")
    print()
    print("DELETE FROM data_manifest_changelists")
    print(" WHERE changelist_number = {}".format(changelist))
    print("   AND platform_id IN (SELECT id FROM data_manifest_platforms")
    print("                        WHERE name IN ({}));".format(platform_list))
    print()
    print("INSERT INTO data_manifest_changelists (changelist_number, platform_id)")
    print("SELECT {}, id FROM data_manifest_platforms".format(changelist))
    print(" WHERE name IN ({});".format(platform_list))
    print()
    print("INSERT INTO data_manifest_packages")
    print("  (changelist_id, url, file_name, file_hash, file_size_bytes,")
    print("   requires_allowlist)")
    print("SELECT c.id, {}, {}, {}, {}, false".format(
        sql_string(url),
        sql_string(str(manifest["file_name"])),
        sql_string(str(manifest["file_hash"])),
        size,
    ))
    print("  FROM data_manifest_changelists c")
    print("  JOIN data_manifest_platforms p ON p.id = c.platform_id")
    print(" WHERE c.changelist_number = {}".format(changelist))
    print("   AND p.name IN ({});".format(platform_list))
    print()
    print("COMMIT;")


if __name__ == "__main__":
    main()
