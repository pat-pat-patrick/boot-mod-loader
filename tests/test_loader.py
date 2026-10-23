"""Boot Mod Loader on PUC Lua 5.1 against a fake game folder.

The game since Steam build 25988413 runs PUC Lua 5.1 (no LuaJIT), so this runs
on lupa.lua51, not on hd2fake (whose VM is the pre-patch LuaJIT). The fake game
folder holds real patch archives built the way the mods' builders write them:

  alpha     declared, in patch_1 and again, newer, in patch_6: runs once, the
            patch_6 copy
  broken    raises when run: logged with a traceback, the others still run
  nocompile LuaJIT-only syntax (goto): "does not compile on Lua 5.1"
  bytecode  declared but flagged as bytecode (1): not a mod, left alone
  plain     plain Lua with no declaration: left alone
  bingus    uses CowboyBingusModLoader.open_log and after_startup
  junk      patch_8 is not an archive: logged, skipped (9 files read, 5 names)
  late      patch_10, after a missing patch_9: still found
  init      init twice from the engine: the mods start once; the game's own
            init's results are passed back

    pip install lupa
    python tests/test_loader.py

No game files needed: the game's boot bytecode (which only the game can
load) is replaced by a stand-in; the loader must carry on without it.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

FAILURES = []


def ok(cond, what, failures):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


MODS = {
    1: ("mods/test/alpha", "-- HD2-Addon: mods/test/alpha\nALPHA_RAN = (ALPHA_RAN or 0) + 1\nALPHA_V = 1\n", 2),
    2: ("mods/test/broken", "-- HD2-Addon: mods/test/broken\nlocal t = nil\nreturn t.field\n", 2),
    3: ("mods/test/nocompile", "-- HD2-Addon: mods/test/nocompile\ndo goto skip end\n::skip::\n", 2),
    4: ("mods/test/bytecode", "-- HD2-Addon: mods/test/bytecode\nBYTECODE_RAN = true\n", 1),
    5: ("mods/test/plain", "PLAIN_RAN = true\n", 2),
    6: ("mods/test/alpha", "-- HD2-Addon: mods/test/alpha\nALPHA_RAN = (ALPHA_RAN or 0) + 1\nALPHA_V = 2\n"
                           "local prev = rawget(_G, 'update')\n"
                           "rawset(_G, 'update', function(...) UPDATES = (UPDATES or 0) + 1 "
                           "if prev then return prev(...) end end)\n", 2),
    7: ("mods/test/bingus", "-- HD2-Addon: mods/test/bingus\n"
                            "local L = rawget(_G, 'CowboyBingusModLoader')\n"
                            "BINGUS_API = L and L.api\n"
                            "local f = L and L.open_log('BingusTest.log')\n"
                            "if f then f:write('hello\\n') f:close() end\n"
                            "L.after_startup(function() AFTER = true end)\n", 2),
    10: ("mods/test/late", "-- HD2-Addon: mods/test/late\nLATE_RAN = true\n", 2),
}


def fake_game(root):
    from build import write_module
    data = os.path.join(root, "game", "data")
    os.makedirs(os.path.join(root, "game", "bin", "lua"))
    for n, (name, source, flag) in MODS.items():
        write_module(data, name if name != "PLAIN" else "x", source, flag=flag,
                     archive="9ba626afa44a3aa3.patch_%d" % n)
    open(os.path.join(data, "9ba626afa44a3aa3.patch_8"), "wb").write(b"not an archive at all" * 8)
    os.makedirs(os.path.join(root, "appdata", "Arrowhead", "Helldivers2"))
    os.makedirs(os.path.join(root, "local", "CowboyBingus", "Helldivers2", "Logs"))
    return root


def check(source):
    """Run the loader source on Lua 5.1 in a fake game; return the failures."""
    import lupa.lua51 as lua51
    failures = []
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root)
        rt = lua51.LuaRuntime()
        ok(rt.eval("_VERSION") == "Lua 5.1" and rt.eval("type(jit)") == "nil", "plain Lua 5.1", failures)
        game = os.path.join(root, "game")
        rt.execute("package.path = %r" % (".\\?.lua;" + game + "\\bin\\lua\\?.lua;" + game + "\\bin\\lua\\?\\init.lua"))
        rt.execute("local real = os.getenv\n"
                   "os.getenv = function(k)\n"
                   "  if k == 'APPDATA' then return %r end\n"
                   "  if k == 'LOCALAPPDATA' then return %r end\n"
                   "  return real(k)\n"
                   "end" % (os.path.join(root, "appdata"), os.path.join(root, "local")))
        rt.execute("init = function() GAME_INITS = (GAME_INITS or 0) + 1 return 'game', 7 end")
        loaded = rt.eval("loadstring")(source, "=boot")
        ok(loaded is not None, "the loader compiles on Lua 5.1", failures)
        if loaded is None:
            return failures
        loaded()
        ok(rt.eval("type(CowboyBingusModLoader)") == "table" and rt.eval("CowboyBingusModLoader.api") == 1,
           "the Bingus API is there before init", failures)
        ok(rt.eval("ALPHA_RAN") is None, "nothing runs before init", failures)
        first = rt.eval("table.concat({ init() }, ',')")
        rt.execute("init()")
        log = open(os.path.join(root, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"),
                   encoding="utf-8").read()
        ok("== boot-mod-loader-" in log and "game boot: could not load" in log,
           "the log opens with the marker and says the game boot (size_t 4 bytecode) did not load here", failures)
        ok(first == "game,7" and rt.eval("GAME_INITS") == 2, "init passes the game's own init through: %r" % first,
           failures)
        ok(rt.eval("ALPHA_RAN") == 1 and rt.eval("ALPHA_V") == 2, "alpha runs once, the patch_6 copy", failures)
        ok("mods/test/broken (patch_2): FAILED:" in log and "stack traceback" in log,
           "broken: failed, with a traceback", failures)
        ok("mods/test/nocompile (patch_3): does not compile on Lua 5.1" in log, "nocompile: reported", failures)
        ok(rt.eval("BYTECODE_RAN") is None and "mods/test/bytecode" not in log, "a bytecode resource is no mod",
           failures)
        ok(rt.eval("PLAIN_RAN") is None, "undeclared Lua is left alone", failures)
        ok("patch_8: not an archive" in log, "a file that is no archive is skipped: %r" % log[-400:], failures)
        ok(rt.eval("LATE_RAN") is True, "patch_10 found after a missing patch_9", failures)
        ok(rt.eval("BINGUS_API") == 1 and rt.eval("AFTER") is True, "open_log and after_startup work", failures)
        bt = os.path.join(root, "local", "CowboyBingus", "Helldivers2", "Logs", "BingusTest.log")
        ok(os.path.exists(bt) and open(bt).read() == "hello\n", "open_log wrote the mod's log", failures)
        ok("9 patch archives read, 5 declared mods" in log, "the count: %r" % [l for l in log.splitlines()
                                                                               if "declared" in l], failures)
        ok("started 3 of 5 mods" in log, "3 of 5 start (alpha once, bingus, late; not broken or nocompile)", failures)
        rt.execute("update(0.016) update(0.016)")
        ok(rt.eval("UPDATES") == 2, "a mod's update hook runs", failures)
        ok(log.count("started ") == 1, "init twice starts the mods once", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return failures


def main():
    from build import built_source
    source = built_source(boot=b"\x1bLuaQ\x00\x01\x04\x04\x04\x08\x00" + b"\x00" * 32)
    print("boot mod loader on Lua 5.1")
    failures = check(source)
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
