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
  files     on_shutdown: runs after the game's shutdown and after `wrapper`,
            which wrapped shutdown while loading; a failing on_shutdown is
            logged and the next still runs; an error in the game's shutdown
            is raised again after them
  version   a checked build_identifier is named in the log; an unknown one
            logs NOT THIS GAME VERSION and the mods still start; with no
            on_shutdown registered, shutdown is left as the game's own

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
    11: ("mods/test/files", "-- HD2-Addon: mods/test/files\n"
                            "local L = rawget(_G, 'CowboyBingusModLoader')\n"
                            "HAS_ON_SHUTDOWN = L.capabilities.on_shutdown\n"
                            "L.on_shutdown(function() error('swap failed') end)\n"
                            "L.on_shutdown(function() ORDER = ORDER .. 'last,' end)\n", 2),
    12: ("mods/test/wrapper", "-- HD2-Addon: mods/test/wrapper\n"
                              "local prev = rawget(_G, 'shutdown')\n"
                              "rawset(_G, 'shutdown', function(...) prev(...) ORDER = ORDER .. 'wrap,' end)\n", 2),
}
CHECKED = "stingray:46659 e176252a3ac4eb5436b25e7cef665a8c71afa243"
UNKNOWN = "stingray:47000 0000000000000000000000000000000000000000"


def fake_game(root, mods=MODS):
    from build import write_module
    data = os.path.join(root, "game", "data")
    os.makedirs(os.path.join(root, "game", "bin", "lua"))
    for n, (name, source, flag) in mods.items():
        write_module(data, name if name != "PLAIN" else "x", source, flag=flag,
                     archive="9ba626afa44a3aa3.patch_%d" % n)
    open(os.path.join(data, "9ba626afa44a3aa3.patch_8"), "wb").write(b"not an archive at all" * 8)
    os.makedirs(os.path.join(root, "appdata", "Arrowhead", "Helldivers2"))
    os.makedirs(os.path.join(root, "local", "CowboyBingus", "Helldivers2", "Logs"))
    return root


def runtime(root, build_id):
    """A Lua 5.1 state set up like the game's, for a fake game folder."""
    import lupa.lua51 as lua51
    rt = lua51.LuaRuntime()
    game = os.path.join(root, "game")
    rt.execute("package.path = %r" % (".\\?.lua;" + game + "\\bin\\lua\\?.lua;" + game + "\\bin\\lua\\?\\init.lua"))
    rt.execute("local real = os.getenv\n"
               "os.getenv = function(k)\n"
               "  if k == 'APPDATA' then return %r end\n"
               "  if k == 'LOCALAPPDATA' then return %r end\n"
               "  return real(k)\n"
               "end" % (os.path.join(root, "appdata"), os.path.join(root, "local")))
    rt.execute("stingray = { Application = { build_identifier = function() return %r end } }" % build_id)
    rt.execute("init = function() GAME_INITS = (GAME_INITS or 0) + 1 return 'game', 7 end")
    rt.execute("ORDER = ''\n"
               "shutdown = function() ORDER = ORDER .. 'game,' if SHUTDOWN_RAISES then error('boom', 0) end end\n"
               "GAME_SHUTDOWN = shutdown")
    return rt


def check(source):
    """Run the loader source on Lua 5.1 in a fake game; return the failures."""
    failures = []
    root = tempfile.mkdtemp(prefix="bootloader_")
    other = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root)
        rt = runtime(root, CHECKED)
        ok(rt.eval("_VERSION") == "Lua 5.1" and rt.eval("type(jit)") == "nil", "plain Lua 5.1", failures)
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
        ok("11 patch archives read, 7 declared mods" in log, "the count: %r" % [l for l in log.splitlines()
                                                                               if "declared" in l], failures)
        ok("started 5 of 7 mods" in log, "5 of 7 start (alpha once, bingus, late, files, wrapper)", failures)
        ok(("game version: %s (Steam build 26088801" % CHECKED) in log, "a checked build is named", failures)
        ok(rt.eval("CowboyBingusModLoader.game.checked") is True and rt.eval("HAS_ON_SHUTDOWN") is True,
           "game.checked and capabilities.on_shutdown", failures)
        rt.execute("shutdown()")
        ok(rt.eval("ORDER") == "game,wrap,last,",
           "on_shutdown runs after the game and the wrapper: %r" % rt.eval("ORDER"), failures)
        log = open(os.path.join(root, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"),
                   encoding="utf-8").read()
        ok("on_shutdown 1: " in log and "swap failed" in log and "shutdown: 2 on_shutdown function(s) ran" in log,
           "a failing on_shutdown is logged and the next one runs", failures)
        rt.execute("ORDER = '' SHUTDOWN_RAISES = true")
        raised = rt.eval("(function() local a, b = pcall(shutdown) return tostring(a) .. '|' .. tostring(b) end)()")
        ok(raised == "false|boom" and rt.eval("ORDER") == "game,last,",
           "an error in the game's shutdown is raised again after the on_shutdown functions: %r %r"
           % (raised, rt.eval("ORDER")), failures)
        rt.execute("update(0.016) update(0.016)")
        ok(rt.eval("UPDATES") == 2, "a mod's update hook runs", failures)
        ok(log.count("started ") == 1, "init twice starts the mods once", failures)

        fake_game(other, {1: MODS[1]})
        rt2 = runtime(other, UNKNOWN)
        rt2.eval("loadstring")(source, "=boot")()
        rt2.execute("init()")
        log2 = open(os.path.join(other, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"),
                    encoding="utf-8").read()
        ok(("NOT THIS GAME VERSION: %s" % UNKNOWN) in log2 and "started 1 of 1 mods" in log2
           and rt2.eval("CowboyBingusModLoader.game.checked") is False,
           "an unknown build is logged and the mods still start", failures)
        ok(rt2.eval("rawequal(shutdown, GAME_SHUTDOWN)") is True, "no on_shutdown: shutdown left alone", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)
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
