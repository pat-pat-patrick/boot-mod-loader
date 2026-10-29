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
  bindings  ModBindingsMenu look-alike: registration checks, the keys list
            under the mod's options title (a Keys page), defaults, press and
            hold, the focus and chat guards, a key set in the window, poll,
            set_binding; nil for an id not registered
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
       "  choices = { 'A', 'B', 'C' }, default = 1, description = 'Which mode', gap = true })\n"
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


KEYS = ("-- HD2-Addon: mods/test/keys\n"
        "local B = ModBindingsMenu\n"
        "KB = {}\n"
        "KB[1] = B.register_binding('keys.toggle', 'Toggle', nil, { category = 'Key Cat', default = 'f6' })\n"
        "KB[2] = B.register_binding('keys.hold', function() return 'Hold' end, nil, { category = 'Key Cat', "
        "default = { { button = 'middle', device = 'mouse', trigger = 'hold' } } })\n"
        "KB[3] = B.register_binding('keys.none', 'None', nil, { category = 'Key Cat' })\n"
        "KB[4] = B.register_binding('keys.toggle', 'Toggle', nil, { category = 'Key Cat', default = 'f6' })\n"
        "KB[5] = B.register_binding('keys.toggle', 'Other', nil, {})\n"
        "KB[6] = B.register_binding('keys.bad', {}, nil, {})\n"
        "ModOptionsMenu.register_option('keys.speed', { type = 'toggle', label = 'Speed', mod = 'Key Test' })\n")

ENGINE = r"""
KB_PRESSED, KB_DOWN, MOUSE_DOWN, FOCUS, LAYER3, LAYER10 = {}, {}, {}, true, 67, 0
local names = { [5] = 'f6', [6] = 'q', [7] = 'middle button **' }
local function button(t) return function(id) return t[id] and 1 or 0 end end
stingray.Keyboard = { button_name = function(id) return names[id] end,
                      pressed = function(id) return KB_PRESSED[id] == true end,
                      released = function(id) return false end, button = button(KB_DOWN) }
stingray.Mouse = { button_name = function(id) return id == 2 and 'middle' or nil end,
                   pressed = function(id) return false end, released = function(id) return false end,
                   button = button(MOUSE_DOWN) }
stingray.Window = { has_focus = function() return FOCUS end }
stingray.Network = { game_session = function() return 'S' end, peer_id = function() return 'P' end }
stingray.GameSession = {
  objects_owned_by = function(s, p) return { 11 } end,
  game_object_exists = function(s, g) return true end,
  game_object_is_type = function(s, g, t) return (g == 11 and t == 'un6y1d') or (g == 22 and t == 'gl7xl2w') end,
  game_object_field = function(s, g, f)
    if g == 11 and f == 'baegche' then return 22 end
    if g == 22 and f == 'state' then return 0 end
    if g == 22 and f == 'position' then return 'here' end
  end }
stingray.Application.worlds = function() return { 'W' } end
stingray.World = { units = function(w) return { 'BODY', 'ROCK' } end }
stingray.Unit = { alive = function(u) return true end,
                  has_node = function(u, n) return u == 'BODY' and n == 'attach_headlight' end,
                  world_position = function(u, i) return 'there' end,
                  has_animation_state_machine = function(u) return true end,
                  animation_get_state = function(u) return 0, 0, LAYER3, 0, 0, 0, 0, 0, 0, LAYER10 end }
stingray.Vector3 = { distance = function(a, b) return 0.4 end }
"""


def check_bindings(source, failures):
    """The ModBindingsMenu look-alike: keys for the settings window."""
    import json
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/keys", KEYS, 2)})
        rt = runtime(root, CHECKED)
        rt.execute(ENGINE)
        rt.eval("loadstring")(source, "=boot")()
        ok(rt.eval("ModBindingsMenu.api == 1 and ModBindingsMenu.version == 4 and ModBindingsMenu.ready()") is True,
           "ModBindingsMenu look-alike: api 1, version 4, ready()", failures)
        rt.execute("init()")
        r = rt.eval("table.concat({ tostring(KB[1]), tostring(KB[2]), tostring(KB[3]), tostring(KB[4]), "
                    "tostring(KB[5]), tostring(KB[6]) }, ',')")
        ok(r == "true,true,true,true,false,false",
           "bindings registered; the same again true; a different label and a bad label refused: %r" % r, failures)
        rt.execute("update(0.016)")
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")
        listing = json.load(open(os.path.join(app, "mbm_keys_options.json"), encoding="utf-8"))
        by_key = {o["key"]: o for o in listing["options"]}
        ok(listing["title"] == "Key Test" and listing["pages"] == [{"name": "Keys", "keys": ["keys_toggle",
           "keys_hold", "keys_none"]}] and all(o["type"] == "key" for o in listing["options"])
           and by_key["keys_toggle"]["default"] == "f6" and by_key["keys_hold"]["default"] == "mouse 3"
           and by_key["keys_none"]["default"] == "" and by_key["keys_hold"]["name"] == "Hold",
           "the keys list: the mod's options title, a Keys page, key settings with defaults: %r" % listing, failures)
        mom = json.load(open(os.path.join(app, "mom_keys_options.json"), encoding="utf-8"))
        ok(mom["title"] == listing["title"], "the same title as the mod's options list (one mod in the window)",
           failures)
        text = open(os.path.join(app, "mbm_keys_settings.txt"), encoding="utf-8").read()
        ok(text == "keys_toggle=f6\nkeys_hold=mouse 3\nkeys_none=\n#end\n", "the keys file: %r" % text, failures)

        def down(id):
            return rt.eval("ModBindingsMenu.is_down(%r)" % id)
        ok(down("keys.toggle") is False and down("nope") is None and down("keys.none") is False,
           "not pressed: false; no key: false; not registered: nil", failures)
        rt.execute("KB_PRESSED[5] = true update(0.016)")
        ok(down("keys.toggle") is True, "press: true on the frame f6 goes down", failures)
        rt.execute("FOCUS = false update(0.016)")
        ok(down("keys.toggle") is False, "held back while the game window has no focus", failures)
        rt.execute("FOCUS = true LAYER3, LAYER10 = 72, 306 update(0.016)")
        ok(down("keys.toggle") is False, "held back while the chat box is open (wrist 306, layer 3 72)", failures)
        rt.execute("LAYER3, LAYER10 = 67, 0 update(0.016)")
        ok(down("keys.toggle") is True, "through again once the chat box is closed", failures)
        rt.execute("KB_PRESSED[5] = false MOUSE_DOWN[2] = true update(0.016)")
        ok(down("keys.hold") is True and down("keys.toggle") is False,
           "hold: the middle mouse button held; press: nothing new", failures)
        rt.execute("MOUSE_DOWN[2] = false update(0.016)")
        ok(down("keys.hold") is False, "hold: false once released", failures)

        with open(os.path.join(app, "mbm_keys_settings.txt"), "w", newline="\n") as f:
            f.write("keys_toggle=q\nkeys_hold=mouse 3\nkeys_none=\n#end\n")
        for _ in range(3):
            rt.execute("update(0.5)")
        rt.execute("KB_PRESSED[6] = true update(0.016)")
        ok(down("keys.toggle") is True, "a key set in the window (q) counts", failures)
        polled = rt.eval("(function() local out = {} ModBindingsMenu.poll({ 'keys.toggle', 'keys.none' }, out) "
                         "local a = tostring(out.down[1]) .. tostring(out.pressed[1]) "
                         "KB_PRESSED[6] = false update(0.016) "
                         "ModBindingsMenu.poll({ 'keys.toggle', 'keys.none' }, out) "
                         "return a .. ',' .. tostring(out.down[1]) .. tostring(out.released[1]) .. tostring(out.down[2]) end)()")
        ok(polled == "truetrue,falsetruefalse", "poll: down/pressed, then released: %r" % polled, failures)
        rt.execute("ModBindingsMenu.set_binding('keys.toggle', {})")
        info = rt.eval("ModBindingsMenu.binding_info('keys.toggle').state")
        rt.execute("KB_PRESSED[6] = true update(0.016)")
        ok(info == "cleared" and down("keys.toggle") is False, "set_binding {} clears the key: %r" % info, failures)
        for _ in range(3):
            rt.execute("update(0.5)")
        text = open(os.path.join(app, "mbm_keys_settings.txt"), encoding="utf-8").read()
        ok("keys_toggle=\n" in text, "and writes the file for the window: %r" % text, failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


OLDCAM = "-- HD2-Addon: mods/test/oldcam\nOLDCAM_RAN = true\n"     # loaded, registers nothing any more


def check_stale_lists(source, failures):
    """Lists of earlier games that no mod registered this game are set aside (.stale); their
    settings stay; lists registered now, and files that are not the loader's, are not touched
    (2026-10-24: ActionCam's old mom_ list beside its own list)."""
    import json
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/cam", CAM, 2), 2: ("mods/test/oldcam", OLDCAM, 2)})
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")
        def put(name, text="{}"):
            open(os.path.join(app, name), "w", encoding="utf-8", newline="\n").write(text)
        put("mom_oldcam_options.json", '{"title": "ActionCam", "options": []}')
        put("mom_oldcam_settings.txt", "fov=3\n#end\n")
        put("mbm_oldcam_options.json")
        put("mom_gone_options.json")                     # a mod no longer installed, in the index
        put("mom_gone_settings.txt", "x=1\n#end\n")
        put("boot_mod_loader_lists.txt", "mom_gone\n")
        put("mom_cam_options.json", '{"title": "old"}')  # registered again this game
        put("oldcam_options.json", '{"title": "ActionCam"}')   # the mod's own list: not the loader's
        rt = runtime(root, CHECKED)
        rt.eval("loadstring")(source, "=boot")()
        rt.execute("init()")
        rt.execute("update(0.016)")
        here = set(os.listdir(app))
        ok(rt.eval("OLDCAM_RAN") is True, "the mod that registers nothing still runs", failures)
        ok("mom_oldcam_options.json" not in here and "mom_oldcam_options.json.stale" in here
           and "mbm_oldcam_options.json.stale" in here,
           "a loaded mod's lists that it no longer registers: renamed .stale", failures)
        ok("mom_gone_options.json.stale" in here and "mom_gone_options.json" not in here,
           "a list in the index whose mod is gone: renamed .stale", failures)
        ok({"mom_oldcam_settings.txt", "mom_gone_settings.txt"} <= here, "their settings stay", failures)
        cam = json.load(open(os.path.join(app, "mom_cam_options.json"), encoding="utf-8"))
        ok(cam["title"] == "Cam Test" and "mom_cam_options.json.stale" not in here,
           "a list registered this game stays (written anew)", failures)
        ok("oldcam_options.json" in here, "a mod's own list is not touched", failures)
        index = open(os.path.join(app, "boot_mod_loader_lists.txt"), encoding="utf-8").read().split()
        ok("mom_cam" in index and "mom_gone" in index, "the lists written are remembered in the index", failures)
        log = open(os.path.join(app, "boot_mod_loader.log"), encoding="utf-8").read()
        ok("mom_oldcam_options.json is no mod's this game: renamed .stale" in log, "the log says what was set aside", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    # a game whose Lua has no os.rename (not measured on 25988413): emptied, marked stale
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/oldcam", OLDCAM, 2)})
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")
        open(os.path.join(app, "mom_oldcam_options.json"), "w").write('{"title": "ActionCam", "options": [{"key": "fov"}]}')
        rt = runtime(root, CHECKED)
        rt.execute("os.rename = nil")
        rt.eval("loadstring")(source, "=boot")()
        rt.execute("init()")
        listing = json.load(open(os.path.join(app, "mom_oldcam_options.json"), encoding="utf-8"))
        ok(listing.get("stale") is True and listing.get("options") == [],
           "without os.rename: the list emptied and marked stale: %r" % listing, failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
           and by_key["cam_mode"].get("gap") is True and "gap" not in by_key["cam_on"]
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
        # (0.7.0) a setting the mod's own list holds is served from that list, no longer refused
        ok(rt.eval("NATIVE_R") is True and rt.eval("NATIVE_U") is True and rt.eval("NATIVE_E") is True
           and native_keys == ["native_extra"]
           and json.load(open(native_list, encoding="utf-8")).get("profile") == "native_extra"
           and json.load(open(native_list, encoding="utf-8")).get("profiles") is True
           and "mods/test/native: lists its settings in the settings window itself" in log,
           "a setting in the mod's own window list is served from it, not listed (x, unit.abc); one only in the menu "
           "is listed: %r" % native_keys, failures)

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


OWN = ("-- HD2-Addon: mods/test/own\n"
       "-- reads ModOptionsMenu and ModBindingsMenu like a menu mod, and has its own window list\n"
       "SEEN = {}\n"
       "local prev = rawget(_G, 'update')\n"
       "rawset(_G, 'update', function(...)\n"
       "  SEEN.mom = type(ModOptionsMenu) == 'table' and ModOptionsMenu.api\n"
       "  SEEN.mbm = type(ModBindingsMenu) == 'table' and ModBindingsMenu.api\n"
       "  if prev then return prev(...) end end)\n"
       "local M, B = rawget(_G, 'ModOptionsMenu'), rawget(_G, 'ModBindingsMenu')\n"
       "OWN_CHANGES = {}\n"
       "OWN_R = { M.register_option('own.dist', { type = 'slider', label = 'Dist', min = 0, max = 2, step = 0.5, default = 1 }),\n"
       "          B.register_binding('own.toggle', 'Toggle', nil, { default = 'f6' }) }\n"
       "M.on_change('own.dist', function(v, id) OWN_CHANGES[#OWN_CHANGES + 1] = id .. '=' .. tostring(v) end)\n"
       "OWN_START = M.get('own.dist')\n")


def check_own_list(source, failures):
    """A mod with its own list for the settings window uses it, not the look-alikes: it reads
    api nil; other mods still see the menus; what it registers anyway is served from its own list."""
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/cam", CAM, 2), 2: ("mods/test/own", OWN, 2)})
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")
        def put(name, text):
            open(os.path.join(app, name), "w", encoding="utf-8", newline="\n").write(text)
        put("own_options.json", '{"title": "Own", "settings": "own_settings.txt", "options": '
                                '[{"key": "dist", "type": "slider"}, {"key": "toggle", "type": "key"}, {"key": "other"}]}')
        put("own_settings.txt", "dist=1.5\ntoggle=q\nother=5\n#end\n")
        put("mom_own_options.json", '{"title": "Own", "options": []}')
        rt = runtime(root, CHECKED)
        rt.execute(ENGINE)
        rt.eval("loadstring")(source, "=boot")()
        rt.execute("init()")
        for _ in range(3):
            rt.execute("update(0.4)")
        here = set(os.listdir(app))
        log = open(os.path.join(app, "boot_mod_loader.log"), encoding="utf-8").read()
        ok("mods/test/own: lists its settings in the settings window itself" in log and "mods/test/cam: lists" not in log,
           "the log names the mod the menus are hidden from", failures)
        ok(rt.eval("ModOptionsMenu.api") == 1 and rt.eval("ModBindingsMenu.api") == 1 and rt.eval("R1") is True
           and "mom_cam_options.json" in here, "other mods see the menus (api 1) and use them", failures)
        seen = rt.eval("tostring(SEEN.mom) .. ',' .. tostring(SEEN.mbm)")
        ok(seen == "nil,nil", "the mod with its own list reads api nil every frame: %r" % seen, failures)
        r = rt.eval("tostring(OWN_R[1]) .. ',' .. tostring(OWN_R[2]) .. ',' .. tostring(OWN_START)")
        ok(r == "true,true,1.5", "registering anyway: accepted, the value from its own settings file: %r" % r, failures)
        ok("mom_own_options.json" not in here and "mom_own_options.json.stale" in here and "mbm_own_options.json" not in here
           and "settings window: own: 2 registration(s) served from its own list (own_options.json, own_settings.txt)" in log,
           "nothing of it goes into a mom_/mbm_ list (the old one set aside); logged", failures)
        put("own_settings.txt", "dist=0.5\ntoggle=q\nother=5\n#end\n")
        for _ in range(3):
            rt.execute("update(0.4)")
        ok(rt.eval("table.concat(OWN_CHANGES, ' ')") == "own.dist=0.5" and rt.eval("ModOptionsMenu.get('own.dist')") == 0.5,
           "a change in the window reaches on_change and get() within a second", failures)
        rt.execute("ModOptionsMenu.set('own.dist', 2)")
        for _ in range(3):
            rt.execute("update(0.4)")
        text = open(os.path.join(app, "own_settings.txt"), encoding="utf-8").read()
        ok(text == "dist=2\ntoggle=q\nother=5\n#end\n", "set() writes the value into its own file, the rest kept: %r" % text,
           failures)
        rt.execute("KB_PRESSED[6] = true update(0.016)")
        ok(rt.eval("ModBindingsMenu.is_down('own.toggle')") is True, "a key from its own file (q) counts", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# Supports both: with a menu it registers; without one it writes good_options.json and reads
# good_settings.txt itself ("settings":"good_settings.txt", lines ending #end), as ActionCam 3.0 does.
GOOD = ("-- HD2-Addon: mods/test/good\n"
        "GOOD_SEEN = {}\n"
        "local dir = os.getenv('APPDATA') .. '/Arrowhead/Helldivers2/'\n"
        "local prev = rawget(_G, 'update')\n"
        "rawset(_G, 'update', function(...)\n"
        "  local M = ModOptionsMenu\n"
        "  local api = type(M) == 'table' and M.api\n"
        "  GOOD_SEEN[#GOOD_SEEN + 1] = tostring(api)\n"
        "  if api == 1 then\n"
        "    if not GOOD_REG then GOOD_REG = M.register_option('good.speed', { type = 'slider', label = 'Speed', mod = 'Good',\n"
        "      min = 0, max = 10, default = 1 }) end\n"
        "  elseif not GOOD_WROTE then\n"
        "    local f = io.open(dir .. 'good_settings.txt', 'r')\n"
        "    GOOD_START = f and f:read('*a') or ''\n"
        "    if f then f:close() end\n"
        "    local o = io.open(dir .. 'good_options.json', 'w')\n"
        "    o:write('{\"title\":\"Good\",\"settings\":\"good_settings.txt\",\"options\":[{\"key\":\"speed\",\"type\":\"slider\"}]}' .. '\\n')\n"
        "    o:close()\n"
        "    GOOD_WROTE = true -- its values end with #end\n"
        "  end\n"
        "  if prev then return prev(...) end end)\n")

# Looks like one (the same strings in its source) but never writes a list: the menus must come back.
FAKE = ("-- HD2-Addon: mods/test/fake\n"
        "-- would write fake_options.json and fake_settings.txt ('\"settings\":\"fake_settings.txt\"', '#end')\n"
        "FAKE_SEEN = {}\n"
        "local prev = rawget(_G, 'update')\n"
        "rawset(_G, 'update', function(...)\n"
        "  local M = ModOptionsMenu\n"
        "  FAKE_SEEN[#FAKE_SEEN + 1] = tostring(type(M) == 'table' and M.api)\n"
        "  if type(M) == 'table' and M.api == 1 and not FAKE_DONE then\n"
        "    FAKE_DONE = M.register_option('fake.x', { type = 'toggle', label = 'X', mod = 'Fake' })\n"
        "  end\n"
        "  if prev then return prev(...) end end)\n")


def check_second_launch(source, failures):
    """A mod that supports the window but writes its own list only without a menu: the first launch
    uses the menus and notes it; from the second the menus are hidden from it, its values carried
    over; one that only looks like it gets the menus back after 10 s and is not hidden again."""
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/good", GOOD, 2), 2: ("mods/test/cam", CAM, 2), 3: ("mods/test/fake", FAKE, 2)})
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")

        def launch(seconds, between=None):
            rt = runtime(root, CHECKED)
            rt.execute(ENGINE)
            rt.eval("loadstring")(source, "=boot")()
            rt.execute("init()")
            for i in range(int(seconds / 0.5)):
                rt.execute("update(0.5)")
                if between and i == 2:
                    between()
            log = open(os.path.join(app, "boot_mod_loader.log"), encoding="utf-8").read()
            return rt, log[log.rfind("== boot-mod-loader"):]

        def notes():
            p = os.path.join(app, "boot_mod_loader_native.txt")
            return dict(l.split("\t") for l in open(p, encoding="utf-8").read().splitlines()
                        if l.count("\t") == 1) if os.path.exists(p) else {}

        def player_sets_speed():
            p = os.path.join(app, "mom_good_settings.txt")
            open(p, "w", encoding="utf-8", newline="\n").write("good_speed=7\n#end\n")

        rt, log = launch(4, player_sets_speed)
        ok(rt.eval("GOOD_REG") is True and rt.eval("ModOptionsMenu.get('good.speed')") == 7
           and "good: supports the settings window itself; from the next launch it will use it" in log
           and notes() == {"good": "next", "fake": "next"},
           "launch 1: the menus as before, a value set; noted (not the ordinary menu mod): %r" % notes(), failures)
        rt, log = launch(12)
        here = set(os.listdir(app))
        ok(rt.eval("GOOD_SEEN[1]") == "nil" and "good_options.json" in here and "speed=7" in (rt.eval("GOOD_START") or "")
           and notes().get("good") == "yes" and "mom_good_options.json.stale" in here,
           "launch 2: the menus hidden, its own list written, the value carried over, the menu list set aside", failures)
        seen = rt.eval("table.concat(FAKE_SEEN, ',')").split(",")
        ok(seen[0] == "nil" and seen[-1] == "1" and rt.eval("FAKE_DONE") is True and notes().get("fake") == "no",
           "launch 2: the look-alike writes no list: after 10 s the menus are back: %r .. %r" % (seen[:2], seen[-2:]),
           failures)
        ok(rt.eval("R1") is True, "the ordinary menu mod still uses the menus", failures)
        rt, log = launch(2)
        ok(rt.eval("GOOD_SEEN[1]") == "nil" and rt.eval("FAKE_SEEN[1]") == "1",
           "launch 3: the window again; the look-alike not hidden again", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


MANY = ("-- HD2-Addon: mods/test/many\n"
        "local M = ModOptionsMenu\n"
        "MANY_OK, MANY_MAX = 0, M.max_options\n"
        "for i = 1, 40 do\n"
        "  if M.register_option('many.o' .. i, { type = 'toggle', label = 'O' .. i, mod = 'Many' }) then MANY_OK = MANY_OK + 1 end\n"
        "end\n")


def check_no_menu_limits(source, failures):
    """(0.9.0) The menu's tab limits are gone: 40 options of one mod all go into its list; the
    limits reported are the loader's guards (1000), not 32 / 112 / 36."""
    import json
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/many", MANY, 2)})
        rt = runtime(root, CHECKED)
        rt.eval("loadstring")(source, "=boot")()
        rt.execute("init()")
        rt.execute("update(0.016)")
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")
        listing = json.load(open(os.path.join(app, "mom_many_options.json"), encoding="utf-8"))
        ok(rt.eval("MANY_OK") == 40 and len(listing["options"]) == 40,
           "40 options of one mod all registered and listed: %r" % rt.eval("MANY_OK"), failures)
        ok(rt.eval("MANY_MAX") == 1000 and rt.eval("ModOptionsMenu.max_mods") == 1000
           and rt.eval("ModBindingsMenu.capacity") == 1000,
           "max_options, max_mods and capacity report the guards (1000)", failures)
        # from here (no mod on the stack) the options go to a group of their own: fill it to the guard
        rt.execute("for i = 1, 1000 do ModOptionsMenu.register_option('lots.o' .. i, "
                   "{ type = 'toggle', label = 'O', mod = 'Lots' }) end")
        r = rt.eval("tostring(ModOptionsMenu.register_option('lots.last', { type = 'toggle', label = 'L', mod = 'Lots' }))")
        rt.execute("update(1.5)")                      # the log is written out once a second
        log = open(os.path.join(app, "boot_mod_loader.log"), encoding="utf-8").read()
        ok(r == "false" and log.count("mod already has 1000 options -- registrations past this guard are refused") == 1,
           "past the guard: refused, logged once", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# A mod with its own window list that still names the menus (it checks for them, as ActionCam does).
NATIVE2 = ("-- HD2-Addon: mods/test/native2\n"
           "-- uses ModOptionsMenu only when it is a real menu; writes native2_options.json itself\n"
           "local dir = os.getenv('APPDATA') .. '/Arrowhead/Helldivers2/'\n"
           "local o = io.open(dir .. 'native2_options.json', 'w')\n"
           "o:write('{\"title\":\"Native Two\",\"settings\":\"native2_settings.txt\",\"options\":['\n"
           "  .. '{\"key\":\"speed\",\"type\":\"slider\",\"min\":0,\"max\":10,\"name\":\"Speed\",\"hint\":\"How fast\"},'\n"
           "  .. '{\"key\":\"mode\",\"type\":\"choice\",\"name\":\"Mode\",\"choices\":[\"Slow\",\"Fast\"]},'\n"
           "  .. '{\"key\":\"jump\",\"type\":\"key\",\"name\":\"Jump\"},'\n"
           "  .. '{\"key\":\"other\",\"type\":\"toggle\",\"name\":\"Untranslated\"}]}' .. '\\n')\n"
           "o:close()\n")

# A translation pack made for the menus: wraps both functions and swaps its mod's texts.
PACK = ("-- HD2-Addon: mods/test/native2_zh\n"
        "local words = { ['Native Two'] = '原生二号', Speed = '速度', ['How fast'] = '有多快', Slow = '慢', Fast = '快', Jump = '跳' }\n"
        "local M, B = rawget(_G, 'ModOptionsMenu'), rawget(_G, 'ModBindingsMenu')\n"
        "local option, binding = M.register_option, B.register_binding\n"
        "M.register_option = function(id, spec)\n"
        "  if type(spec) == 'table' and string.find(id, '^native2%.') then\n"
        "    spec.label = words[spec.label] or spec.label\n"
        "    spec.description = words[spec.description] or spec.description\n"
        "    spec.mod = words[spec.mod] or spec.mod\n"
        "    if spec.choices then local c = {} for i, v in ipairs(spec.choices) do c[i] = words[v] or v end spec.choices = c end\n"
        "  end\n"
        "  return option(id, spec)\n"
        "end\n"
        "B.register_binding = function(id, label, ...)\n"
        "  if string.find(id, '^native2%.') then label = words[label] or label end\n"
        "  return binding(id, label, ...)\n"
        "end\n")


def check_translation_pack(source, failures):
    """A translation pack made for the menus reaches a mod with its own window list: the mod's settings
    are rehearsed through the pack's wrappers (nothing registered) and the changed texts are written as
    <name>_translations.json under "game"; removed when the pack is gone."""
    import json
    root = tempfile.mkdtemp(prefix="bootloader_")
    try:
        fake_game(root, {1: ("mods/test/native2", NATIVE2, 2), 2: ("mods/test/native2_zh", PACK, 2),
                         3: ("mods/test/cam", CAM, 2)})
        app = os.path.join(root, "appdata", "Arrowhead", "Helldivers2")

        def launch():
            rt = runtime(root, CHECKED)
            rt.execute(ENGINE)
            rt.eval("loadstring")(source, "=boot")()
            rt.execute("init()")
            for _ in range(3):
                rt.execute("update(0.5)")
            log = open(os.path.join(app, "boot_mod_loader.log"), encoding="utf-8").read()
            return rt, log[log.rfind("== boot-mod-loader"):]

        rt, log = launch()
        path = os.path.join(app, "native2_translations.json")
        tr = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        ok(tr == {"game": {"title": "原生二号", "speed": "速度", "speed.hint": "有多快", "mode.1": "慢", "mode.2": "快",
                           "jump": "跳"}},
           "the pack's texts for the mod's own list, under \"game\": %r" % tr, failures)
        ok("native2: 6 translation(s) (game) from a translation pack" in log and rt.eval("R1") is True
           and not any(n.startswith(("mom_native2", "mbm_native2")) for n in os.listdir(app)),
           "logged; nothing registered for it; the ordinary menu mod untouched", failures)
        os.remove(os.path.join(root, "game", "data", "9ba626afa44a3aa3.patch_2"))
        rt, log = launch()
        ok(not os.path.exists(path) and "native2_translations.json removed" in log,
           "without the pack: the file removed", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


GUEST = ("-- HD2-Guest-Boot: Tracker\n"
         "GUEST_LOADS = (GUEST_LOADS or 0) + 1\n"
         "function init() GUEST_INITS = (GUEST_INITS or 0) + 1 end\n"
         "function update(dt) GUEST_UPDATES = (GUEST_UPDATES or 0) + 1 if GUEST_FAIL then error('guest boom') end end\n"
         "function render() GUEST_RENDERS = (GUEST_RENDERS or 0) + 1 end\n"
         "function shutdown() ORDER = ORDER .. 'guest,' end\n")


def check_guest_boot(source, failures):
    """0.12.0: a boot the mod manager took off the slot ("-- HD2-Guest-Boot:") runs as a guest:
    loaded once after the mods, its init called, its update/render/shutdown after the chain,
    the chain itself kept; errors logged five times, stopped after 50; the highest patch wins;
    an undeclared script is not run; init twice starts it once."""
    root = tempfile.mkdtemp(prefix="bootloader_guest_")
    try:
        fake_game(root, {1: MODS[1],
                         3: ("guest_boot/old", GUEST.replace("GUEST_LOADS = (GUEST_LOADS or 0) + 1", "OLD_GUEST = true"), 2),
                         5: ("guest_boot/Tracker", GUEST, 2),
                         6: ("plain_boot", "PLAIN_BOOT_RAN = true\nfunction update() PLAIN_UPDATE = true end\n", 2)})
        # patch_3 and patch_5 both name the guest "Tracker": the patch_5 one runs
        rt = runtime(root, CHECKED)
        rt.execute("GAME_UPS = 0 update = function() GAME_UPS = GAME_UPS + 1 return 'u' end GAME_UPDATE = update")
        rt.eval("loadstring")(source, "=boot")()
        ok(rt.eval("GUEST_LOADS") is None, "guest: nothing before init", failures)
        first = rt.eval("table.concat({ init() }, ',')")
        rt.execute("init()")
        log = open(os.path.join(root, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"), encoding="utf-8").read()
        ok(first == "game,7", "guest: the game's init results still come back: %r" % first, failures)
        ok(rt.eval("GUEST_LOADS") == 1 and rt.eval("OLD_GUEST") is None, "guest: loaded once, the patch_5 copy", failures)
        ok(rt.eval("GUEST_INITS") == 1, "guest: its init called once, init twice", failures)
        ok(rt.eval("ALPHA_RAN") == 1 and "started 1 of 1 mods" in log, "guest: the mods start as before, not counted with them", failures)
        ok("1 declared mods, 1 guest boot(s)" in log, "guest: counted apart: %r" % [l for l in log.splitlines() if "declared" in l], failures)
        ok("guest boot Tracker (patch_5, " in log and "runs init, update, render, shutdown" in log, "guest: its log line", failures)
        ok(rt.eval("PLAIN_BOOT_RAN") is None and rt.eval("PLAIN_UPDATE") is None, "guest: undeclared script not run", failures)
        ok(rt.eval("rawequal(init, GAME_INIT_SEEN or init)") is True and rt.eval("GUEST_INITS") == 1,
           "guest: init stays the loader's", failures)
        r = rt.eval("update(0.016)")
        ok(r == "u" and rt.eval("GAME_UPS") == 1 and rt.eval("GUEST_UPDATES") == 1,
           "guest: update runs the guest, then the chain (its result kept): %r %r %r" % (r, rt.eval("GAME_UPS"), rt.eval("GUEST_UPDATES")), failures)
        # Mod Lag Watchdog names a link by its source and follows its function upvalues:
        # the guest's link is "guest_boot/<mod>" (its archive row's name) and holds only the chain
        ok(rt.eval("debug.getinfo(update, 'S').source") == "@guest_boot/Tracker",
           "guest: its link is named guest_boot/Tracker: %r" % rt.eval("debug.getinfo(update, 'S').source"), failures)
        held = rt.eval("(function() local n, i = {}, 1 while true do local name, v = debug.getupvalue(update, i) "
                       "if name == nil then break end if type(v) == 'function' then n[#n + 1] = name end i = i + 1 end "
                       "return table.concat(n, ',') end)()")
        ok(held == "previous", "guest: its link holds one function, the chain: %r" % held, failures)
        rt.execute("pcall(render)")
        ok(rt.eval("GUEST_RENDERS") == 1, "guest: render reaches it although the game had none", failures)
        rt.execute("ORDER = '' shutdown()")
        ok(rt.eval("ORDER") == "guest,game,", "guest: shutdown, then the game's: %r" % rt.eval("ORDER"), failures)
        rt.execute("GUEST_FAIL = true for i = 1, 60 do update(0.016) end")
        log = open(os.path.join(root, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"), encoding="utf-8").read()
        ok(log.count("guest boot Tracker: update failed") == 5 and "guest boot Tracker: stopped for this launch after 50 errors" in log,
           "guest: five errors logged, stopped after 50", failures)
        ok(rt.eval("GUEST_UPDATES") == 51 and rt.eval("GAME_UPS") == 61,
           "guest: a stopped guest is not called; the game's update still is: %r %r" % (rt.eval("GUEST_UPDATES"), rt.eval("GAME_UPS")), failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


CHEF_MODS = {
    1: ("mods/test/alpha", MODS[1][1], 2),
    2: ("mods/test/narrow", "-- HD2-Addon: mods/test/narrow\nlocal chef = ...\nif type(chef.own_log) == 'function' then chef.own_log('n') end\nNARROW_RAN = true\n", 2),
    3: ("mods/test/copes", "-- HD2-Addon: mods/test/copes\nlocal chef = ...\nif type(chef) ~= 'table' then COPES_QUIET = true return end\nCOPES_CHEF = true\n", 2),
    4: ("mods/test/declared", "-- HD2-Addon: mods/test/declared\n-- HD2-Requires: chef-shared-loader\nDECLARED_RAN = true\n", 2),
    5: ("mods/test/broken", MODS[2][1], 2),
    # 0.13.3: HUDdle 0.4.9's shape: the script takes chef and uses it untested; its modules test chefs of their
    # own (a local, a parameter), which say nothing of the script's. 0.13.2 ran it here: "attempt to index local 'chef'"
    7: ("mods/test/modular", "-- HD2-Addon: mods/test/modular\nlocal chef = ...\n"
        "local M = (function()\n  local chef, O = nil, {}\n  local function set(c) chef = c end\n  if chef then O.x = 1 end\n  return O\nend)()\n"
        "local N = (function(chef, x) if type(chef) == 'table' then return 1 end end)(nil)\n"
        "local s = 'if chef then' -- if chef then\n"
        "if type(chef.own_log) == 'function' then pcall(chef.own_log, 'modular') end\nMODULAR_RAN = true\n", 2),
}
# Chef Shared Loader's core, as far as the hand-off goes: what it was told, its argument, and what it would start.
# Deployed as an archive (0.13.1: read from there; in game can_get said no at init), and the same as a require
# preload for the case no archive holds it.
CHEF_CORE_SRC = r"""
  local via = ...
  CHEF_VIA = via
  local note = rawget(_G, 'ChefSharedLoaderHandoff')
  CHEF_FROM, CHEF_PHASE = note and note.from, note and note.phase
  local wants = {}
  for _, e in ipairs(__boot_mod_loader.mods) do
    if e.state ~= 'loaded' and package.loaded[e.name] == nil then wants[#wants + 1] = e.name end
  end
  CHEF_STARTS = table.concat(wants, ',')
  CHEF_BROKEN_MARK = tostring(package.loaded['mods/test/broken'])
  -- what Chef 1.2.1 does: keeps the callbacks it finds (to run after its own), then sets its own; with
  -- phase init it calls its init itself
  CHEF_FOUND = (rawget(_G, 'update') ~= nil) or (rawget(_G, 'render') ~= nil) or (rawget(_G, 'shutdown') ~= nil)
  rawset(_G, 'ChefSharedLoader', {})
  function init() CHEF_INITS = (CHEF_INITS or 0) + 1 end
  function update(dt) CHEF_UPS = (CHEF_UPS or 0) + 1 end
  function render() CHEF_RENDERS = (CHEF_RENDERS or 0) + 1 end
  function shutdown() ORDER = ORDER .. 'chef,' end
  if note and note.phase == 'init' then init() end
  return true
"""


def check_chef_handoff(source, failures):
    """0.13.0: a mod made for Chef Shared Loader (HD2-Requires, or local chef = ... used untested) is not run; with
    Chef's core deployed, the loader hands off to it (its documented note, phase init) and Chef would start exactly
    those; one that tests for chef runs here; a failed mod is marked so Chef does not start it again; without the
    core the mods are named and left."""
    root = tempfile.mkdtemp(prefix="bootloader_chef_")
    try:
        from build import write_module
        fake_game(root, CHEF_MODS)
        core = os.path.join(root, "game", "data", "9ba626afa44a3aa3.patch_6")
        for here in (True, False):
            if here:
                write_module(os.path.join(root, "game", "data"), "mods/chef_shared_loader/core_1", CHEF_CORE_SRC, archive="9ba626afa44a3aa3.patch_6")
            else:
                for ext in ("", ".gpu_resources", ".stream"):
                    if os.path.exists(core + ext): os.remove(core + ext)
            rt = runtime(root, CHECKED)
            # as in game: can_get says no, whether the core is there or not
            rt.execute("stingray.Application.can_get = function(kind, name) return false end")
            rt.execute("GAME_UPS = 0 update = function() GAME_UPS = GAME_UPS + 1 end GAME_UPDATE = update")
            rt.eval("loadstring")(source, "=boot")()
            rt.execute("init()")
            log = open(os.path.join(root, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"), encoding="utf-8").read()
            log = log[log.rindex("== boot-mod-loader-"):]
            tag = "chef here" if here else "no chef"
            ok(rt.eval("NARROW_RAN") is None and rt.eval("DECLARED_RAN") is None and rt.eval("MODULAR_RAN") is None,
               "chef (%s): the mods made for it are not run here" % tag, failures)
            ok(rt.eval("ALPHA_RAN") == 1 and rt.eval("COPES_QUIET") is True,
               "chef (%s): the others run, one that tests for chef too" % tag, failures)
            ok("started 2 of 3 mods" in log, "chef (%s): counted without them (alpha and copes; broken fails): %r" % (tag, [l for l in log.splitlines() if "started" in l]), failures)
            if here:
                # 0.13.2: Chef takes over no chain; it is one link, named for Mod Lag Watchdog
                ok(rt.eval("CHEF_FOUND") is False, "chef: it found no callbacks to take over", failures)
                ok(rt.eval("debug.getinfo(update, 'S').source") == "@mods/chef_shared_loader/chef_shared_loader"
                   and rt.eval("debug.getinfo(render, 'S').source") == "@mods/chef_shared_loader/chef_shared_loader",
                   "chef: its link is named chef_shared_loader: %r" % rt.eval("debug.getinfo(update, 'S').source"), failures)
                held = rt.eval("(function() local n, i = {}, 1 while true do local name, v = debug.getupvalue(update, i) "
                               "if name == nil then break end if type(v) == 'function' then n[#n + 1] = name end i = i + 1 end "
                               "return table.concat(n, ',') end)()")
                ok(held == "previous", "chef: its link holds one function, the chain: %r" % held, failures)
                rt.execute("update(0.016) update(0.016) pcall(render)")
                ok(rt.eval("CHEF_UPS") == 2 and rt.eval("GAME_UPS") == 2 and rt.eval("CHEF_RENDERS") == 1 and rt.eval("CHEF_INITS") == 1,
                   "chef: its update and render run, the chain's too, its init once: %r %r %r %r"
                   % (rt.eval("CHEF_UPS"), rt.eval("GAME_UPS"), rt.eval("CHEF_RENDERS"), rt.eval("CHEF_INITS")), failures)
                rt.execute("ORDER = '' shutdown()")
                ok(rt.eval("ORDER") == "chef,game,", "chef: shutdown: Chef, then the game's: %r" % rt.eval("ORDER"), failures)
                rt.execute("init()")
                ok(rt.eval("CHEF_INITS") == 1, "chef: init again (the engine's) does not call Chef's init", failures)
                ok(rt.eval("CHEF_FROM") == "Boot Mod Loader " + __import__("build").VERSION and rt.eval("CHEF_PHASE") == "init" and rt.eval("CHEF_VIA") == "mods/chef_shared_loader/core_1",
                   "chef: the hand-off note: %r %r" % (rt.eval("CHEF_FROM"), rt.eval("CHEF_PHASE")), failures)
                ok(rt.eval("CHEF_STARTS") == "mods/test/narrow,mods/test/declared,mods/test/modular",
                   "chef: it would start exactly those: %r" % rt.eval("CHEF_STARTS"), failures)
                ok(rt.eval("CHEF_BROKEN_MARK") == "false", "chef: a mod that failed here is not started there again", failures)
                ok("handed off to Chef Shared Loader (patch_6) for 3 mod(s) made for it: mods/test/narrow, mods/test/declared, mods/test/modular; it runs as one link (update, render, shutdown)" in log
                   and rt.eval("package.loaded['mods/chef_shared_loader/core_1']") is True,
                   "chef: the log says the hand-off", failures)
            else:
                ok(rt.eval("CHEF_FROM") is None and "made for Chef Shared Loader, which is not deployed: not started (mods/test/narrow, mods/test/declared, mods/test/modular)" in log,
                   "chef: without its core, named and left", failures)
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
        # (0.13.4) build 26251122 of 2026-10-29: its boot is the embedded one, so it is a checked build too
        later = tempfile.mkdtemp(prefix="bootloader_")
        try:
            fake_game(later, {1: MODS[1]})
            rt3 = runtime(later, "stingray:46805 437a3d445f9df7662b60086ac8ae3c65b5c95da6")
            rt3.eval("loadstring")(source, "=boot")()
            rt3.execute("init()")
            log3 = open(os.path.join(later, "appdata", "Arrowhead", "Helldivers2", "boot_mod_loader.log"), encoding="utf-8").read()
            ok("(Steam build 26251122, 2026-10-29)" in log3 and "NOT THIS GAME VERSION" not in log3
               and rt3.eval("CowboyBingusModLoader.game.checked") is True,
               "build 26251122 (2026-10-29) is a checked build", failures)
        finally:
            shutil.rmtree(later, ignore_errors=True)
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
        check_bindings(source, failures)
        check_stale_lists(source, failures)
        check_own_list(source, failures)
        check_second_launch(source, failures)
        check_no_menu_limits(source, failures)
        check_translation_pack(source, failures)
        check_guest_boot(source, failures)
        check_chef_handoff(source, failures)
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
