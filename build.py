"""Build Boot Mod Loader into a zip for a mod manager (HD2 Arsenal and others).

    python tools/extract_boot.py "<...>/steamapps/common/Helldivers 2"   # once per game update
    python build.py                                                        # -> dist/

The loader replaces the game's `boot` script. Its Lua is plain source with the
game's original boot bytecode (boot.luac, from your own install) embedded, so
the original still runs first, unchanged.
"""
import json
import os
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "src", "boot_mod_loader.lua")
BOOT = os.path.join(HERE, "boot.luac")
ICON = os.path.join(HERE, "icon.png")       # the mod manager's icon, 1024 px
DIST = os.path.join(HERE, "dist")
VERSION = "0.5.1"
NAME = "Boot Mod Loader"
FOLDER = "Boot Mod Loader"
RESOURCE = "boot"
LUA_SOURCE_FLAG = 2       # the payload is Lua source text (1 is bytecode)

# A one-resource patch archive: header, one type row, one file row. The ids,
# offset and sizes in it are filled in by write_module.
SCAFFOLD = bytes.fromhex(
    "110000f0010000000100000000000000ce09f5f4000000006e0b41bab993fecf0040170300000000"
    "00cc5600000000000000000000000000000000000000000000000000000000000000000000000000"
    "e217d12cfa8d4ea101000000000000001000000040000000b1d0e27de00d175ce217d12cfa8d4ea1"
    "b8000000000000000000000000000000000000000000000000b000000000000000c1070000000000"
    "f3af00000000000000000000100000004000000001000000")

MANIFEST = {
    "Version": 1,
    "Guid": "3f7a2c91-6d4e-4b58-9a0c-e15b82d4f6a7",
    "IconPath": "Boot Mod Loader.png",
    "Name": NAME,
    "Description": "Starts Lua mods (-- HD2-Addon:) on the game since its 2026-10-20 update; runs the game's own boot script unchanged.",
    "Options": [{
        "Name": "Loader",
        "Description": "Log: %APPDATA%\\Arrowhead\\Helldivers2\\boot_mod_loader.log",
        "Include": [FOLDER],
    }],
}

README = """Boot Mod Loader v{version}
========================

Starts Lua mods on Helldivers 2 since its 2026-10-20 update (Steam build
25988413; checked again on 26088801, 2026-10-22). Takes the game's `boot` script, runs the original first, then
starts every Lua mod whose first line is "-- HD2-Addon: mods/<author>/<name>",
found in the deployed data\\9ba626afa44a3aa3.patch_<n> archives. Each mod runs
under pcall; failures are logged with a traceback to
%APPDATA%\\Arrowhead\\Helldivers2\\boot_mod_loader.log.

Only one mod can replace `boot`: do not install another loader that does.
Mods written for LuaJIT (ffi, bit, goto, jit) do not work on this game.
On a game build it was not checked on, the log says NOT THIS GAME VERSION;
mods still start.
Mods made for Mod Options Menu or Mod Bindings Menu: their settings and keys
show in HD2 Mod Setting GUI (github.com/pat-pat-patrick/hd2-mod-setting-gui),
outside the game.
Source: https://github.com/pat-pat-patrick/boot-mod-loader (MIT)
""".format(version=VERSION)


def murmur64(data, seed=0):
    """MurmurHash64A, as the engine names resources."""
    m, r, mask = 0xc6a4a7935bd1e995, 47, 0xFFFFFFFFFFFFFFFF
    h = (seed ^ (len(data) * m)) & mask
    whole = len(data) - len(data) % 8
    for at in range(0, whole, 8):
        k = int.from_bytes(data[at:at + 8], "little")
        k = (k * m) & mask
        k ^= k >> r
        k = (k * m) & mask
        h = ((h ^ k) * m) & mask
    tail = data[whole:]
    if tail:
        h ^= int.from_bytes(tail, "little")
        h = (h * m) & mask
    h ^= h >> r
    h = (h * m) & mask
    h ^= h >> r
    return h


LUA_TYPE = murmur64(b"lua")


def write_module(out_dir, resource_name, source, flag=LUA_SOURCE_FLAG, archive="9ba626afa44a3aa3.patch_0"):
    """One Lua resource as a patch archive (plus its empty .gpu_resources and .stream)."""
    encoded = source if isinstance(source, bytes) else source.encode("utf-8")
    payload = struct.pack("<2I", len(encoded), flag) + encoded
    out = bytearray(SCAFFOLD)
    row = 0x48 + 32
    struct.pack_into("<3Q", out, row, murmur64(resource_name.encode("utf-8")), LUA_TYPE, len(SCAFFOLD))
    struct.pack_into("<I", out, row + 56, len(payload))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, archive)
    with open(path, "wb") as f:
        f.write(bytes(out) + payload)
    open(path + ".gpu_resources", "wb").close()
    open(path + ".stream", "wb").close()
    return path


def lua_bytes(data):
    return "".join("\\%d" % b for b in data)


def built_source(boot=None):
    """The loader's Lua with the game's boot bytecode put in its @ORIGINAL@ slot."""
    template = open(SOURCE, encoding="utf-8").read()
    if boot is None:
        if not os.path.exists(BOOT):
            raise SystemExit("no boot.luac: run tools/extract_boot.py first")
        boot = open(BOOT, "rb").read()
    if boot[:5] != b"\x1bLuaQ":
        raise SystemExit("boot.luac is not Lua 5.1 bytecode")
    if template.count('"@ORIGINAL@"') != 1:
        raise SystemExit("the loader Lua has no single @ORIGINAL@ slot")
    if ('local MARKER = "boot-mod-loader-%s"' % VERSION) not in template:
        raise SystemExit("the loader Lua's MARKER is not boot-mod-loader-%s" % VERSION)
    for banned in ("require('ffi')", 'require("ffi")', "ffi.", "bit.", "jit.", "popen", "os.execute", "goto "):
        if banned in template:
            raise SystemExit("the loader names %r" % banned)
    return template.replace('"@ORIGINAL@"', '"' + lua_bytes(boot) + '"')


def build():
    source = built_source()
    out_dir = os.path.join(DIST, "%s v%s" % (NAME, VERSION))
    if os.path.exists(out_dir):
        raise SystemExit("%s exists: bump VERSION or delete it" % out_dir)
    write_module(os.path.join(out_dir, FOLDER), RESOURCE, source)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, indent=2, ensure_ascii=False)
        f.write("\n")
    shutil.copyfile(ICON, os.path.join(out_dir, MANIFEST["IconPath"]))     # the mod manager's icon
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(README)
    zip_path = out_dir + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(out_dir):
            for name in sorted(files):
                full = os.path.join(base, name)
                z.write(full, os.path.relpath(full, out_dir))
    print("wrote %s" % zip_path)


if __name__ == "__main__":
    sys.exit(build())
