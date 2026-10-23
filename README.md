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
and limits (toggle, choice and slider; texts may be functions; 32 options a
mod).

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
A mod that already writes its own list for the window (`<addon name>_options.json`)
is refused, so its settings are not listed twice. Nothing is hooked or read
until a mod registers an option.

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
  a late registration, and a mod with its own list.

## Credits

The `-- HD2-Addon:` declaration and the loader API are from
[Bingus Shared Loader](https://github.com/CowboyBingus/BingusSharedLoader)
by CowboyBingus (0BSD). This loader is a separate implementation for the
post-update game; it contains no code from it.

## License

MIT, see [LICENSE](LICENSE).
