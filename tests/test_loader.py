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
  options   ModOptionsMenu look-alike: a mod registers a toggle, a choice
            (its label a function) and a slider; the menu's checks refuse a
            bad one; values from the old ModOptionsMenu.values carry over;
            the window's list and settings file appear; a value the window
            changes reaches get() and on_change once; a file without #end
            waits; set() from code writes the file; a late registration
            hooks update then; with none, update is the game's own; a
            setting the mod lists for the window itself is refused, one it
            keeps only in the menu is listed (ActionCam 3.0's split); the
            list names the profile option (profile = true, else a choice
            whose id ends .profile); "profiles": true only when the mod
            declares profiles = true itself, never inferred; a
            profile switch from the window applies only the switch (the
            file's other, old values are not applied) and writes back

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
                           "if prev then return prev(...) end end)\n"
                           "ALPHA_UPDATE = rawget(_G, 'update')\n", 2),
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
               "GAME_SHUTDOWN = shutdown\n"
               "update = function() end GAME_UPDATE = update")
    return rt


CAM = ("-- HD2-Addon: mods/test/cam\n"
       "local M = rawget(_G, 'ModOptionsMenu')\n"
       "CHANGES = {}\n"
       "local function seen(v, id) CHANGES[#CHANGES + 1] = id .. '=' .. tostring(v) end\n"
       "MENU_OK = M.api == 1 and M.version == 3 and M.ready() == true\n"
       "R1 = M.register_option('cam.on', { type = 'toggle', label = 'On', mod = 'Cam Test', default = false })\n"
       "R2 = M.register_option('cam.mode', { type = 'choice', mod = 'Cam Test', label = function() return 'Mode' end,\n"
       "  choices = { 'A', 'B', 'C' }, default = 1, description = 'Which mode' })\n"
       "R3 = M.register_option('cam.fov', { type = 'slider', label = 'FOV', mod = 'Cam Test', min = 0, max = 10, step = 0.5, default = 2 })\n"
       "R4 = M.register_option('cam.on', { type = 'toggle', label = 'On', mod = 'Cam Test', default = false })\n"
       "R5 = M.register_option('cam.on', { type = 'toggle', label = 'On', mod = 'Cam Test', default = true })\n"
       "R6 = M.register_option('cam.bad', { type = 'choice', label = 'Bad', mod = 'Cam Test', choices = { 'only' } })\n"
       "for _, id in ipairs({ 'cam.on', 'cam.mode', 'cam.fov' }) do M.on_change(id, seen) end\n"
       "START = { M.get('cam.on'), M.get('cam.mode'), M.get('cam.fov') }\n")


NATIVE = ("-- HD2-Addon: mods/test/native\n"
          "local M = ModOptionsMenu\n"
          "NATIVE_R = M.register_option('native.x', { type = 'toggle', label = 'X', mod = 'Native' })\n"
          "NATIVE_U = M.register_option('native.unit.abc', { type = 'toggle', label = 'U', mod = 'Native' })\n"
          "NATIVE_E = M.register_option('native.extra', { type = 'toggle', label = 'E', mod = 'Native', "
          "profile = true, profiles = true })\n")


def check_options(source, failures):
    """The ModOptionsMenu look-alike and the settings window's files."""
    import json
    root = tempfile.mkdtemp(prefix="bootloader_")
    late = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/cam", CAM, 2), 2: ("mods/test/native", NATIVE, 2)})
        open(os.path.join(root, "appdata", "Arrowhead", "Helldivers2", "native_options.json"), "w").write(
            '{"options":[{"key":"x","type":"toggle"},{"key": "unit_abc","type":"toggle"},{"key":"profile_1"}]}')
        logs = os.path.join(root, "local", "CowboyBingus", "Helldivers2", "Logs")
        open(os.path.join(logs, "ModOptionsMenu.values"), "w", newline="\n").write("cam.fov\t4\ncam.on\ttrue\nother.x\t1\n")
        rt = runtime(root, CHECKED)
        rt.eval("loadstring")(source, "=boot")()
        ok(rt.eval("ModOptionsMenu.revision") is not None, "the look-alike is there before the mods load", failures)
        rt.execute("init()")
        ok(rt.eval("MENU_OK") is True, "api 1, version 3, ready() true", failures)
        ok(rt.eval("R1 and R2 and R3 and R4") is True and rt.eval("R5") is False and rt.eval("R6") is False,
           "valid options registered; the same one again is true, a different one and a bad one are refused",
           failures)
        start = rt.eval("table.concat({ tostring(START[1]), tostring(START[2]), tostring(START[3]) }, ',')")
        ok(start == "true,1,4", "old ModOptionsMenu.values carried over at registration: %r" % start, failures)
        ok(rt.eval("rawequal(update, GAME_UPDATE)") is False, "update hooked once an option is registered", failures)
        rt.execute("update(0.016)")
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")
        listing = json.load(open(os.path.join(app, "mom_cam_options.json"), encoding="utf-8"))
        by_key = {o["key"]: o for o in listing["options"]}
        ok(listing["title"] == "Cam Test" and listing["settings"] == "mom_cam_settings.txt"
           and sorted(by_key) == ["cam_fov", "cam_mode", "cam_on"]
           and by_key["cam_mode"]["name"] == "Mode" and by_key["cam_mode"]["hint"] == "Which mode"
           and by_key["cam_mode"]["choices"] == ["A", "B", "C"]
           and by_key["cam_on"]["name"] == "On" and "hint" not in by_key["cam_on"]
           and not any("en" in o or "zh" in o for o in listing["options"]) and "translations" not in listing
           and by_key["cam_fov"]["type"] == "slider" and by_key["cam_fov"]["step"] == 0.5
           and listing["pages"] == [] and listing["menu_owned"] == [] and "profile" not in listing
           and "profiles" not in listing,
           "the window's list: %r" % listing, failures)
        settings = os.path.join(app, "mom_cam_settings.txt")
        text = open(settings, encoding="utf-8").read()
        ok(text == "cam_on=true\ncam_mode=1\ncam_fov=4\n#end\n", "the settings file: %r" % text, failures)
        log = open(os.path.join(app, "boot_mod_loader.log"), encoding="utf-8").read()
        ok("2 value(s) carried over from Mod Options Menu" in log, "the carry-over is logged", failures)
        native_list = os.path.join(app, "mom_native_options.json")
        native_keys = [o["key"] for o in json.load(open(native_list, encoding="utf-8"))["options"]] \
            if os.path.exists(native_list) else None
        ok(rt.eval("NATIVE_R") is False and rt.eval("NATIVE_U") is False and rt.eval("NATIVE_E") is True
           and native_keys == ["native_extra"]
           and json.load(open(native_list, encoding="utf-8")).get("profile") == "native_extra"
           and json.load(open(native_list, encoding="utf-8")).get("profiles") is True
           and "native lists its settings in the settings window itself" in log,
           "a setting in the mod's own window list is refused (x, unit.abc); one only in the menu is listed: %r"
           % native_keys, failures)

        with open(settings, "w", newline="\n") as f:
            f.write("cam_on=false\ncam_mode=3\ncam_fov=7.3\n#end\n")
        for _ in range(3):
            rt.execute("update(0.5)")
        now = rt.eval("table.concat({ tostring(ModOptionsMenu.get('cam.on')), ModOptionsMenu.get('cam.mode'), "
                      "ModOptionsMenu.get('cam.fov') }, ',')")
        changes = rt.eval("table.concat(CHANGES, ' ')")
        ok(now == "false,3,7.5", "the window's values reach get() (7.3 snapped to 7.5): %r" % now, failures)
        ok(changes == "cam.on=false cam.mode=3 cam.fov=7.5", "on_change once each: %r" % changes, failures)
        mod_log = open(os.path.join(app, "mom_cam.log"), encoding="utf-8").read()
        ok("settings window: applied cam_on=false cam_mode=3 cam_fov=7.5" in mod_log and mod_log[2] == ":",
           "the window's status line finds the applied line: %r" % mod_log, failures)

        with open(settings, "w", newline="\n") as f:
            f.write("cam_on=true\n")
        for _ in range(3):
            rt.execute("update(0.5)")
        ok(rt.eval("ModOptionsMenu.get('cam.on')") is False, "a file without #end is not read", failures)

        rt.execute("ModOptionsMenu.set('cam.mode', 2)")
        for _ in range(3):
            rt.execute("update(0.5)")
        text = open(settings, encoding="utf-8").read()
        ok("cam_mode=2" in text and text.endswith("#end\n") and rt.eval("#CHANGES") == 3,
           "set() writes the file and calls no on_change: %r" % text, failures)

        fake_game(late, {1: MODS[1]})
        rt2 = runtime(late, CHECKED)
        rt2.eval("loadstring")(source, "=boot")()
        rt2.execute("init()")
        ok(rt2.eval("rawequal(update, GAME_UPDATE)") is True, "no option at start: update is the game's own",
           failures)
        rt2.execute("ModOptionsMenu.register_option('late.x', { type = 'toggle', label = 'X', mod = 'Late' })")
        rt2.execute("local M = ModOptionsMenu\n"
                    "M.register_option('late.profile', { type = 'choice', label = 'P', mod = 'Late', "
                    "choices = { 'One', 'Two' } })\n"
                    "M.register_option('late.dist', { type = 'slider', label = 'D', mod = 'Late', min = 0, max = 10, "
                    "default = 0 })\n"
                    "LATE = {}\n"
                    "M.on_change('late.profile', function(v, id) LATE[#LATE + 1] = id .. '=' .. v "
                    "M.set('late.dist', v * 3) end)\n"
                    "M.on_change('late.dist', function(v, id) LATE[#LATE + 1] = id .. '=' .. v end)")
        ok(rt2.eval("rawequal(update, GAME_UPDATE)") is False, "a late registration hooks update then", failures)
        rt2.execute("update(0.016)")
        app2 = os.path.join(late, "appdata", "Arrowhead", "Helldivers2")
        late_list = json.load(open(os.path.join(app2, "mom_late_options.json"), encoding="utf-8"))
        ok(late_list.get("profile") == "late_profile" and "profiles" not in late_list,
           "and its list is written; a choice whose id ends .profile is the profile switcher: %r"
           % late_list.get("profile"), failures)
        late_file = os.path.join(app2, "mom_late_settings.txt")
        with open(late_file, "w", newline="\n") as f:          # the window switches: new profile, old values
            f.write("late_x=false\nlate_profile=2\nlate_dist=0\n#end\n")
        for _ in range(3):
            rt2.execute("update(0.5)")
        text = open(late_file, encoding="utf-8").read()
        ok(rt2.eval("table.concat(LATE, ' ')") == "late.profile=2" and rt2.eval("ModOptionsMenu.get('late.dist')") == 6
           and "late_dist=6" in text and "late_profile=2" in text,
           "a profile switch applies only the switch; the old value of another setting in the same file is not "
           "applied, and the new profile's value is written back: %r %r" % (rt2.eval("table.concat(LATE, ' ')"), text),
           failures)
        with open(late_file, "w", newline="\n") as f:          # then an ordinary edit
            f.write("late_x=false\nlate_profile=2\nlate_dist=4\n#end\n")
        for _ in range(3):
            rt2.execute("update(0.5)")
        ok(rt2.eval("table.concat(LATE, ' ')") == "late.profile=2 late.dist=4",
           "an ordinary edit after it applies: %r" % rt2.eval("table.concat(LATE, ' ')"), failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(late, ignore_errors=True)


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
        ok(rt2.eval("rawequal(update, GAME_UPDATE)") is True, "no option registered: update left alone", failures)
        check_options(source, failures)
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
