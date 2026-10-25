# Boot Mod Loader

Starts Lua mods on Helldivers 2 again after the game update of 2026-10-20
(Steam build 25988413, the switch to Easy Anti-Cheat).

That update changed how the game runs Lua:

- Lua is now **PUC-Rio Lua 5.1.4** inside `helldivers2.exe`. LuaJIT
  (`bin/lua51.dll`) is gone, and with it `ffi`, `bit` and `jit`.
- The game ships only 11 Lua scripts. `core/wwise/lua/wwise_flow_callbacks`,
  the script that Bingus Shared Loader starts from, is no longer one of them, so
  no loader-based mod starts.
- The game still loads its `boot` script from a mod's patch archive, as plain
  source. `io`, `os`, `debug` and the engine's `stingray` API are all there, and
  the engine calls `init`, `update` and `render`.

Boot Mod Loader replaces `boot`. It runs the game's own boot bytecode first,
unchanged. When the engine calls `init`, it starts every mod that declares
itself the Bingus Shared Loader way.

## For players

Install it with your mod manager like any other mod, next to the Lua mods you
use. It needs no settings.

For the settings of mods made for Mod Options Menu and Mod Bindings Menu, use
[HD2 Mod Setting GUI](https://github.com/pat-pat-patrick/hd2-mod-setting-gui)
**1.12.0 or newer**.

- Its log is `%APPDATA%\Arrowhead\Helldivers2\boot_mod_loader.log`. The log
  lists the archives read, every mod found, and whether each mod loaded, did not
  compile, or failed (with a traceback).
- **Only one mod can replace `boot`.** Do not install a second loader that does
  the same.
- After a game update the log says either `game version: ... (Steam build
  ...)` or `NOT THIS GAME VERSION`. The second means the loader's copy of the
  game's `boot` script was not checked on that build. Mods still start, because
  `boot` has not changed in an update so far. If the game misbehaves, update
  the loader.
- **Settings and keys of mods made for Mod Options Menu and Mod Bindings Menu.**
  Those menus' in-game tabs are gone with the update. The loader takes their
  place: those mods' settings and keys show in
  [HD2 Mod Setting GUI](https://github.com/pat-pat-patrick/hd2-mod-setting-gui),
  a small window outside the game, and a change applies in the game within a
  second. Settings you had made in Mod Options Menu carry over.
- A mod written for LuaJIT does not run on this game, loader or not. That
  includes mods that use `ffi` to read game memory, mods that use the `bit`
  library, and mods that use `goto`. The log says which mods failed and why.

## For mod authors

Your mod is found if it meets the same conditions as under Bingus Shared
Loader:

- it is a **plain-text** Lua resource in `9ba626afa44a3aa3.patch_<n>`;
- its first line is `-- HD2-Addon: mods/<author>/<name>`;
- the resource name is `mods/<author>/<name>`.

The loader looks for mods in this way:

- It reads the deployed `data\9ba626afa44a3aa3.patch_0`, `_1`, ... with `io`.
- It stops after 64 numbers in a row are missing.
- When a name appears in several patches, the highest patch number wins.
- Mods run in patch order. Each mod runs under `xpcall`, so a mod that raises
  does not stop the others.

Mods start inside the engine's `init`, just as under Bingus Shared Loader.
`_G.update` and `_G.shutdown` exist by then and can be wrapped.

`_G.CowboyBingusModLoader` is provided with the parts that need no `ffi`:

- `api = 1`;
- `open_log(name)`, which opens a file in
  `%LOCALAPPDATA%\CowboyBingus\Helldivers2\Logs\` with `io`;
- `after_startup(fn)`;
- `on_shutdown(fn)`, which runs `fn` when the game quits, after every
  shutdown handler that mods installed while loading. It is meant for work
  that must come last, such as swapping files. Each function runs under
  `pcall`; an error in the game's own shutdown is raised again after them;
- `capabilities` (`after_startup`, `on_shutdown`, `open_log`, `mod_options`;
  `ffi` and `jit` are false);
- `game`: `build_identifier`, the game's `Application.build_identifier()`,
  and `checked`, true on a build whose `boot` this loader was checked on.

Its `revision` names this loader. It is set only if no other loader has set it.

Each mod's return value (or `true`) is stored in `package.loaded["mods/<author>/<name>"]`,
so mods can check for each other.

### Mod Options Menu

When no other mod defines it, the loader sets `_G.ModOptionsMenu` before any
mod loads, with the API of Mod Options Menu v1.2 (`api = 1`, `version = 3`):
`register_option(id, spec)`, `get(id)`, `set(id, value)`, `on_change(id, fn)`
and `ready()`, which is true. Registrations are checked by the menu's rules
(toggle, choice and slider; texts may be functions). The menu's tab limits
(32 options a mod, 112 mods, Mod Bindings Menu's 36 slots) do not apply: the
window has none. A guard of 1000 options a mod and 1000 mods only stops code
that registers without end, and `max_options`, `max_mods` and `capacity`
report it.

Instead of an in-game tab, each mod's options become a list for
[HD2 Mod Setting GUI](https://github.com/pat-pat-patrick/hd2-mod-setting-gui),
in `%APPDATA%\Arrowhead\Helldivers2\`:

- `mom_<mod>_options.json`: the list, written when it changes. `<mod>` is the
  `mod_id`, else the addon's name.
- `mom_<mod>_settings.txt`: the values. The window writes it whole; the loader
  reads it once a second, and each value it changed goes to the mod's
  `on_change` callbacks, as the menu's apply did.
- `mom_<mod>.log`: a line for each change applied, for the window's status line.

The list names a mod's profile option as `"profile"`, for the window's profile
switcher: an option registered with `profile = true` (an extra field the menu
itself ignores), else a choice whose id ends in `.profile`. A mod that registers
any option with `profiles = true` (another extra field) gets `"profiles": true`,
which lets the window offer its own saved sets of values; this is never inferred. When the window
switches the profile, its file still holds the old profile's other values: the
loader applies only the switch (the mod loads the new profile's values and
`set()`s them) and writes the file back, so the old values are never saved
into the new profile.

A value the window has not set yet comes from Mod Options Menu's own saved
values (`%LOCALAPPDATA%\CowboyBingus\Helldivers2\Logs\ModOptionsMenu.values`).
Nothing is hooked or read until a mod registers an option.

**A mod with its own list for the window.** When a mod has both, its own list
wins. A mod has its own list when it writes `<addon name>_options.json` itself
(not `mom_` or `mbm_`, not marked stale). If such a mod also names
`ModOptionsMenu` or `ModBindingsMenu` in its source:

- both look-alikes answer `api` as `nil` to it, so a mod that checks
  `ModOptionsMenu.api == 1` (ActionCam 3.0, every frame) takes its own path to
  the window; every other mod reads `api = 1`;
- the log names each mod the menus are hidden from.

**From the second launch.** Some mods support both but write their own list
only when they find no menu, so with the loader they would never get one.
When such a mod registers with a menu, and its source also writes the
window's files itself (`<name>_options.json`, `<name>_settings.txt`,
`"settings":"<name>_settings.txt"` and `#end`), the loader notes it in
`boot_mod_loader_native.txt`. The first launch works through the menus as
before. From the next launch:

- the menus are hidden from that mod;
- the values set through the menus go into its own settings file first, when
  it has none (its own key is the part of the id after the first dot);
- if its own list has not appeared after 10 seconds of play, the menus come
  back at once and are never hidden from that mod again.

A setting such a mod registers anyway, or one any mod registers that its own
list holds (a part of the id after a dot, written as a key: `actioncam.dist` ->
`dist`), is served from the mod's own list instead of being refused:

- the registration succeeds; `get()` is the value in the mod's own settings
  file (the list's `"settings"`), else the default;
- that file is read once a second, and a changed value goes to `on_change`;
- `set()` and `set_binding()` write the value back into that file, keeping its
  other lines;
- a key is read from that file for `is_down()`;
- nothing of it goes into a `mom_` or `mbm_` list.

**Lists no mod registers any more.** Once every mod has started, each `mom_` or
`mbm_` list that no mod registered this game is renamed
`<name>_options.json.stale`, so the window shows only current settings. Its
`_settings.txt` stays, so the values come back if the mod registers again. The
candidates are the lists the loader wrote before (its index,
`boot_mod_loader_lists.txt`) and the names of the mods loaded now. Where the
game's Lua has no `os.rename`, the list is emptied and marked
`{"stale": true}` instead, which the window skips from 1.10.1.

**Translation packs.** A translation pack made for Mod Options Menu wraps
`register_option` and `register_binding` and swaps its mod's texts as they
are registered. A mod that uses its own window list never registers, so such
a pack would translate nothing. When a pack has wrapped either function, the
loader does this once a launch for each mod with its own list:

1. It rehearses the mod's settings through the wrappers, as the mod would have
   registered them with the menus: id `<prefix>.<key>`, the English name,
   hint and choices, `mod` = the list's title; key settings through
   `register_binding`.
2. In a rehearsal the loader's own functions only note the texts they get:
   nothing is registered and no list is written.
3. The texts that came back changed are the pack's translations. They are
   written as `<name>_translations.json`, in the window's `translations`
   form, under the tag `"game"`: a pack translates into the language the
   player plays in, and the window reads the game's language setting (the
   loader cannot find that file without listing a folder). The file is
   rewritten only when it changes.
4. When the pack is gone, the file is removed. The loader removes only files
   it wrote (its index, `boot_mod_loader_translations.txt`).

The window uses these translations from 1.12.0, after the mod's own ones.

### Mod Bindings Menu

When no other mod defines it, the loader also sets `_G.ModBindingsMenu` before
any mod loads, with the API of Mod Bindings Menu v2.3 (`api = 1`, `version = 4`):
`register_binding(id, label, slot, options)`, `is_down(id)`, `poll(ids, out)`,
`ready()`, `set_binding`, `binding`, `binding_info`, `revision` and `extended`.

- Each mod's keys become a "Keys" page for the settings window
  (`mbm_<mod>_options.json` / `_settings.txt`), under the title of the same
  mod's Mod Options Menu list when it has one, so the window shows one mod. A
  key's value is the game's key name (`q`, `f6`, `mouse 3`; empty for none):
  the player's choice in the window, else the first keyboard or mouse key of
  `options.default`.
- `is_down(id)` answers from the engine's key state this frame by the
  binding's trigger: `press` (the default: true on the frame the key goes
  down), `release`, or `hold`. It is `nil` for an id that is not registered and
  `false` without a key.
- A key does not count while the game window has no focus
  (`Window.has_focus`), or while the chat box is open, read from the local
  player's body animation as Helmet Headlamp 1.6.0 does (likely, not proven).
  The native menu's keys did not fire while typing in chat, so mods made for it
  have no chat guard of their own. Both are asked only on a frame a key is down.
- Bindings saved in Mod Bindings Menu are not carried over: their keys are in
  the game's input settings under borrowed action names whose order is not
  known here.
- A mod's `gap = true` on a Mod Options Menu option (space above the row) is
  passed on to the window as `"gap": true`.

## Building

The release embeds the game's own `boot` bytecode. That code belongs to the
game, so it is not in this repository. Take it from your own install:

    pip install lz4 lupa
    python tools/extract_boot.py "<...>/steamapps/common/Helldivers 2"
    python tests/test_loader.py
    python build.py

`build.py` writes `dist/Boot Mod Loader v<version>.zip`. Run
`extract_boot.py` again after a game update. If the game's `boot` is
unchanged, add the build's `Application.build_identifier()` to
`CHECKED_BUILDS` in `src/boot_mod_loader.lua`.

`tests/test_loader.py` runs the loader on PUC Lua 5.1, the language the game
runs, against a fake game folder of real patch archives. It covers these cases:

- a duplicate name;
- a mod that raises;
- a mod that does not compile on Lua 5.1;
- a bytecode resource;
- an undeclared script;
- a file that is not an archive;
- a missing patch number;
- the Bingus API;
- `init` called twice;
- `on_shutdown` order, a failing callback, and an error in the game's shutdown;
- a checked and an unknown game version;
- the Mod Bindings Menu look-alike: registration checks, the Keys page and its
  title, defaults, press and hold, the focus and chat guards, a key set in the
  window, `poll` and `set_binding`;
- the Mod Options Menu look-alike: registration checks, carried-over values,
  the window's files, a change from the window, a half-written file, `set()`,
  a late registration, and a mod with its own list;
- lists no mod registered this game, with and without `os.rename`;
- a mod with its own list: it reads `api` as `nil` every frame, other mods
  still use the menus, and the settings it registers anyway are served from
  its own settings file (`get`, `on_change`, `set`, keys).
- a mod that supports both, over three launches: the menus first (noted), then
  its own list with the values carried over; one that only looks like it gets
  the menus back after 10 s and is not hidden again;
- no menu tab limits: 40 options of a mod all listed, the guards reported;
- a translation pack made for the menus and a mod with its own list: the
  pack's texts written under `"game"`, nothing registered, the file removed
  when the pack is gone.

## Credits

The `-- HD2-Addon:` declaration and the loader API are from
[Bingus Shared Loader](https://github.com/CowboyBingus/BingusSharedLoader)
by CowboyBingus (0BSD). This loader is a separate implementation for the
post-update game; it contains no code from it.

## License

MIT, see [LICENSE](LICENSE).
