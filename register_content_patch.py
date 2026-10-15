#!/usr/bin/env python3
"""Turn a content-patch manifest into the SQL that advertises it.

Writes SQL to stdout; the entrypoint pipes it into psql. Keeping this a pure
text transform means no database driver in the image and nothing to configure:
psql already knows how to reach the database from the connection string the
server itself uses.

The manifest is what the Studio export emits next to the archives. One client
archive is built per texture resolution, each advertised under the platform
name that client asks with -- `win64` for a low-res client, `win64-gen2` for
a high-res one:

    {
      "changelist_number": 269701,
      "packages": [
        {
          "file_name":       "holoween-gen1.vzip",
          "file_hash":       "<base64 RSA signature over the whole archive>",
          "file_size_bytes": 12345678,
          "platforms":       ["win64"],
          "url":             ""
        },
        {
          "file_name":       "holoween-gen2.vzip",
          ...
          "platforms":       ["win64-gen2"],
          "url":             ""
        }
      ]
    }

The older single-archive form -- `file_name`, `file_hash`, `file_size_bytes`
and `platforms` at the top level beside `changelist_number` -- is still read,
as one package.

`url` is where *clients* download that package from. It is normally empty in
the file -- the exporting machine cannot know the address players reach this
server on. A package's own `url` is used when it is set; otherwise the URL
given here is used as a base and the package's file name is appended. A base
that already ends in the package's file name is used as it is, so a
single-archive manifest still takes the full file URL. See the README.

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
from urllib.parse import quote

DEFAULT_PLATFORMS = ["win64-gen2", "win64"]
PACKAGE_FIELDS = ("file_name", "file_hash", "file_size_bytes")


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


def read_packages(manifest: dict) -> list[dict]:
    """The manifest's packages, each with its fields checked."""
    if "packages" in manifest:
        packages = manifest["packages"]
        if not isinstance(packages, list) or not packages:
            sys.exit("manifest 'packages' must be a non-empty list")
        for package in packages:
            if not package.get("platforms"):
                sys.exit("package {!r} names no platforms".format(
                    package.get("file_name")))
    else:
        packages = [dict(manifest,
                         platforms=manifest.get("platforms") or DEFAULT_PLATFORMS)]

    seen = {}
    for package in packages:
        for field in PACKAGE_FIELDS:
            if field not in package:
                sys.exit("manifest package is missing {!r}".format(field))
        # A client asks under exactly one platform name. Two packages on the
        # same one would hand it both archives.
        for platform in package["platforms"]:
            if platform in seen:
                sys.exit("platform {!r} is claimed by both {!r} and {!r}".format(
                    platform, seen[platform], package["file_name"]))
            seen[platform] = package["file_name"]
    return packages


def package_url(package: dict, base: str) -> str:
    if package.get("url"):
        return package["url"]

    file_name = str(package["file_name"])
    if not base:
        sys.exit(
            "no download URL for {!r}: the manifest has none and none was "
            "given.\nSet KOC_CONTENT_PATCH_URL to the address clients reach "
            "this server on -- 'localhost' there would point every client at "
            "itself.".format(file_name)
        )
    if base.endswith("/" + file_name):
        return base
    if base.endswith(".vzip"):
        sys.exit(
            "KOC_CONTENT_PATCH_URL points at a single archive ({}), but this "
            "manifest has {!r} as well.\nSet it to the folder the archives are "
            "served from instead, e.g. http://your-public-address:8080/".format(
                base, file_name)
        )
    return base.rstrip("/") + "/" + quote(file_name)


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: {} <manifest.json> [url]".format(sys.argv[0]))

    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    base = sys.argv[2] if len(sys.argv) == 3 else ""

    if "changelist_number" not in manifest:
        sys.exit("manifest is missing 'changelist_number'")
    changelist = int(manifest["changelist_number"])

    packages = read_packages(manifest)
    urls = [package_url(package, base) for package in packages]
    all_platforms = ", ".join(
        sql_string(p) for package in packages for p in package["platforms"])

    print("BEGIN;")
    print()
    print("DELETE FROM data_manifest_changelists")
    print(" WHERE changelist_number = {}".format(changelist))
    print("   AND platform_id IN (SELECT id FROM data_manifest_platforms")
    print("                        WHERE name IN ({}));".format(all_platforms))
    print()
    print("INSERT INTO data_manifest_changelists (changelist_number, platform_id)")
    print("SELECT {}, id FROM data_manifest_platforms".format(changelist))
    print(" WHERE name IN ({});".format(all_platforms))

    for package, url in zip(packages, urls):
        platform_list = ", ".join(sql_string(p) for p in package["platforms"])
        print()
        print("INSERT INTO data_manifest_packages")
        print("  (changelist_id, url, file_name, file_hash, file_size_bytes,")
        print("   requires_allowlist)")
        print("SELECT c.id, {}, {}, {}, {}, false".format(
            sql_string(url),
            sql_string(str(package["file_name"])),
            sql_string(str(package["file_hash"])),
            int(package["file_size_bytes"]),
        ))
        print("  FROM data_manifest_changelists c")
        print("  JOIN data_manifest_platforms p ON p.id = c.platform_id")
        print(" WHERE c.changelist_number = {}".format(changelist))
        print("   AND p.name IN ({});".format(platform_list))

    print()
    print("COMMIT;")


if __name__ == "__main__":
    main()
