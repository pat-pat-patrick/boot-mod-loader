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
- `capabilities`.

Its `revision` names this loader. It is set only if no other loader has set it.

## Building

The release embeds the game's own `boot` bytecode. That code belongs to the
game, so it is not in this repository. Take it from your own install:

    pip install lz4 lupa
    python tools/extract_boot.py "<...>/steamapps/common/Helldivers 2"
    python tests/test_loader.py
    python build.py

`build.py` writes `dist/Boot Mod Loader v<version>.zip`. Run
`extract_boot.py` again after a game update.

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
- `init` called twice.

## Credits

The `-- HD2-Addon:` declaration and the loader API are from
[Bingus Shared Loader](https://github.com/CowboyBingus/BingusSharedLoader)
by CowboyBingus (0BSD). This loader is a separate implementation for the
post-update game; it contains no code from it.

## License

MIT, see [LICENSE](LICENSE).
