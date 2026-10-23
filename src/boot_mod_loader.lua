-- Boot Mod Loader (local; after the 2026-10-20 game update).
--
-- Steam build 25988413 runs PUC Lua 5.1 (no LuaJIT, no ffi) and dropped the
-- script Bingus Shared Loader starts from, so no Lua mod runs. Lua VM Probe
-- (in game 2026-10-20) proved that the game still loads its `boot` script
-- from a mod's patch as plain source, that io works, and that the engine
-- calls init/update/render. This takes `boot`: it runs the game's own boot
-- bytecode unchanged, and when the engine calls init it finds every mod that
-- declares itself the Bingus way -- first line "-- HD2-Addon: mods/<a>/<b>"
-- in a plain-text Lua resource -- by reading the deployed
-- data\9ba626afa44a3aa3.patch_<n> archives with io (no ffi, no FindFirstFile),
-- the highest patch number winning for a name, and runs each in turn under
-- pcall, so one that fails does not stop the others. What it found, ran and
-- what failed (with the traceback) goes to
-- %APPDATA%\Arrowhead\Helldivers2\boot_mod_loader.log.
--
-- For mods written against Bingus Shared Loader it sets
-- _G.CowboyBingusModLoader with the parts that need no ffi: api 1, open_log
-- (io, in the loader's usual Logs folder), after_startup, capabilities; its
-- `revision` says it is this loader, not Bingus's. Beside after_startup it has
-- on_shutdown(fn): run when the game quits, after every shutdown handler the
-- mods installed while loading (for file swaps that must come last).
--
-- The game's boot is embedded, so a game update that changed it would leave
-- this running a stale copy. The loader cannot read the game's own boot at
-- run time (it sits in the compressed bundles), so it checks the game's
-- Application.build_identifier against the builds whose boot was checked to
-- be these bytes, and logs "NOT THIS GAME VERSION" on any other. Mods still
-- start then: refusing would stop every mod at every hotfix, and boot has not
-- changed in one so far.
-- Plain Lua 5.1 only (no goto, bit or ffi)
local MARKER = "boot-mod-loader-0.2.0"
local ORIGINAL = "@ORIGINAL@"            -- the game's boot bytecode, put in by the builder
-- Builds whose boot script is byte for byte the one embedded here (extracted
-- from each install and compared).
local CHECKED_BUILDS = {
  ["stingray:46539 987f283054819041dd1b28f29a21b5f339dbd106"] = "Steam build 25988413, 2026-10-20",
  ["stingray:46659 e176252a3ac4eb5436b25e7cef665a8c71afa243"] = "Steam build 26088801, 2026-10-22",
}

if rawget(_G, "__boot_mod_loader") ~= nil then return end
local L = { mods = {}, log_lines = {}, started = false }
rawset(_G, "__boot_mod_loader", L)

local ARCHIVE = "9ba626afa44a3aa3"
local LUA_TYPE = "a14e8dfa2cd117e2"       -- murmur64("lua"), as hex
local MAX_PATCH = 1024
local GAP = 64                           -- stop after this many missing numbers past the last found

local log_path = nil
do
  local ok, dir = pcall(os.getenv, "APPDATA")
  if ok and type(dir) == "string" then log_path = dir .. "\\Arrowhead\\Helldivers2\\boot_mod_loader.log" end
end
local function say(text)
  L.log_lines[#L.log_lines + 1] = os.date("%H:%M:%S ") .. tostring(text)
end
local function flush()
  if #L.log_lines == 0 then return end
  local text = table.concat(L.log_lines, "\n") .. "\n"
  L.log_lines = {}
  if log_path == nil then return end
  local ok, file = pcall(io.open, log_path, "a")
  if ok and file ~= nil then
    pcall(function() file:write(text) file:close() end)
  end
end

say("== " .. MARKER .. " " .. os.date("%Y-%m-%d %H:%M:%S") .. " (" .. tostring(_VERSION) .. ")")

-- The game's own boot, unchanged, first.
do
  local ok, chunk, err = pcall(loadstring, ORIGINAL, "=boot")
  if ok and chunk ~= nil then
    local ran, why = pcall(chunk)
    say("game boot: " .. (ran and "ran" or ("FAILED: " .. tostring(why))))
  else
    say("game boot: could not load: " .. tostring(ok and err or chunk))
  end
end
flush()

-- ------------------------------------------------------------ reading archives

local function u32(s, at)
  local a, b, c, d = string.byte(s, at + 1, at + 4)
  return a + b * 256 + c * 65536 + d * 16777216
end
-- A u64 below 2^53 (offsets) as a number.
local function u64(s, at)
  return u32(s, at) + u32(s, at + 4) * 4294967296
end
-- A u64 as 16 hex digits, most significant first (hashes do not fit a double).
local function hex64(s, at)
  local parts = {}
  for i = 8, 1, -1 do parts[#parts + 1] = string.format("%02x", string.byte(s, at + i)) end
  return table.concat(parts)
end

-- The game folder: package.path names <game>\bin\lua\?.lua (in game
-- 2026-10-20); the data folder is beside bin.
local function data_dir()
  local path = type(package) == "table" and package.path or ""
  for entry in string.gmatch(path, "[^;]+") do
    local game = string.match(entry, "^(.-)[\\/]bin[\\/]lua[\\/]%?%.lua$")
    if game ~= nil and game ~= "" and game ~= "." then return game .. "\\data" end
  end
  return nil
end

-- The declared Lua resources of one archive: { name, at, size } each.
local function declared_in(file)
  local head = file:read(0x48)
  if head == nil or #head < 0x48 or u32(head, 0) ~= 0xF0000011 then return nil, "not an archive" end
  local types, count = u32(head, 4), u32(head, 8)
  if count == 0 or count > 100000 or types > 1000 then return {} end
  file:seek("set", 0x48 + types * 32)
  local rows = file:read(count * 80)
  if rows == nil or #rows < count * 80 then return nil, "short file table" end
  local found = {}
  for i = 0, count - 1 do
    local row = i * 80
    if hex64(rows, row + 8) == LUA_TYPE then
      local at, size = u64(rows, row + 16), u32(rows, row + 56)
      if size > 8 then
        file:seek("set", at)
        local first = file:read(math.min(size, 8 + 300))
        if first ~= nil and #first > 8 and u32(first, 4) == 2 then          -- 2: source, not bytecode
          local name = string.match(string.sub(first, 9), "^%-%- HD2%-Addon: (mods/[A-Za-z0-9_]+/[A-Za-z0-9_/]+)\r?\n")
          if name ~= nil and string.sub(name, -1) ~= "/" then
            found[#found + 1] = { name = name, at = at + 8, size = u32(first, 0) }
          end
        end
      end
    end
  end
  return found
end

local function discover()
  local dir = data_dir()
  if dir == nil then
    say("no game folder in package.path (" .. tostring(type(package) == "table" and package.path) .. ")")
    return {}
  end
  local by_name, last_found, opened = {}, -1, 0
  for n = 0, MAX_PATCH do
    if n - last_found > GAP then break end
    local path = dir .. "\\" .. ARCHIVE .. ".patch_" .. n
    local ok, file = pcall(io.open, path, "rb")
    if ok and file ~= nil then
      last_found, opened = n, opened + 1
      local ok2, found, why = pcall(declared_in, file)
      if ok2 and found ~= nil then
        for _, entry in ipairs(found) do
          entry.patch, entry.path = n, path
          by_name[entry.name] = entry                                      -- a higher patch wins
        end
      else
        say("patch_" .. n .. ": " .. tostring(ok2 and why or found))
      end
      pcall(function() file:close() end)
    end
  end
  local list = {}
  for _, entry in pairs(by_name) do list[#list + 1] = entry end
  table.sort(list, function(a, b)
    if a.patch ~= b.patch then return a.patch < b.patch end
    return a.name < b.name
  end)
  say(string.format("%s: %d patch archives read, %d declared mods", dir, opened, #list))
  return list
end

-- ------------------------------------------------------------ Bingus API, the ffi-free part

local after_startup = {}
local on_shutdown = {}
local game = { build_identifier = nil, checked = false }
local function loader_log_dir()
  local ok, dir = pcall(os.getenv, "LOCALAPPDATA")
  if ok and type(dir) == "string" then return dir .. "\\CowboyBingus\\Helldivers2\\Logs\\" end
  return nil
end
if rawget(_G, "CowboyBingusModLoader") == nil then
  rawset(_G, "CowboyBingusModLoader", {
    version = 17, api = 1, revision = MARKER,
    capabilities = { after_startup = true, on_shutdown = true, open_log = true, ffi = false, jit = false },
    game = game,
    open_log = function(name)
      local dir = loader_log_dir()
      if dir == nil or type(name) ~= "string" then return nil end
      local ok, file = pcall(io.open, dir .. name, "a")
      if ok then return file end
      return nil
    end,
    after_startup = function(fn)
      if type(fn) == "function" then after_startup[#after_startup + 1] = fn end
    end,
    on_shutdown = function(fn)
      if type(fn) ~= "function" then return end
      on_shutdown[#on_shutdown + 1] = fn
      if L.started_done then L.hook_shutdown() end                       -- registered late: hook now
    end,
  })
end

local function pack(...) return { n = select("#", ...), ... } end

-- The game's shutdown, wrapped once and only when a mod asked: whatever the
-- shutdown chain was when the mods had loaded runs first, then each
-- on_shutdown function in turn. An error in the chain is raised again after
-- them, as it would have been.
function L.hook_shutdown()
  if L.shutdown_hooked or #on_shutdown == 0 then return end
  L.shutdown_hooked = true
  local previous = rawget(_G, "shutdown")
  rawset(_G, "shutdown", function(...)
    local results, failed = { n = 0 }, nil
    if type(previous) == "function" then
      local r = pack(pcall(previous, ...))
      if r[1] then results = { n = r.n - 1, unpack(r, 2, r.n) } else failed = r[2] end
    end
    for i, fn in ipairs(on_shutdown) do
      local ran, why = pcall(fn)
      if not ran then say("on_shutdown " .. i .. ": " .. tostring(why)) end
    end
    say(string.format("shutdown: %d on_shutdown function(s) ran", #on_shutdown))
    flush()
    if failed ~= nil then error(failed, 0) end
    return unpack(results, 1, results.n)
  end)
end

local function check_game_version()
  local engine = rawget(_G, "stingray") or rawget(_G, "s3d")
  local ok, id = pcall(function() return engine.Application.build_identifier() end)
  if not ok or type(id) ~= "string" then
    say("game version: unknown (no Application.build_identifier)")
    return
  end
  game.build_identifier = id
  game.checked = CHECKED_BUILDS[id] ~= nil
  if game.checked then
    say("game version: " .. id .. " (" .. CHECKED_BUILDS[id] .. ")")
    return
  end
  local names = {}
  for _, name in pairs(CHECKED_BUILDS) do names[#names + 1] = name end
  table.sort(names)
  say("NOT THIS GAME VERSION: " .. id .. ". The game's boot script embedded in this loader was checked on "
    .. table.concat(names, " and ") .. " only. Mods still start; if the game misbehaves, update Boot Mod Loader.")
end

-- ------------------------------------------------------------ running them

local function run(entry)
  local ok, file = pcall(io.open, entry.path, "rb")
  if not ok or file == nil then
    entry.state = "unreadable"
    say(entry.name .. ": cannot open " .. entry.path)
    return
  end
  file:seek("set", entry.at)
  local source = file:read(entry.size)
  pcall(function() file:close() end)
  if source == nil or #source ~= entry.size then
    entry.state = "unreadable"
    say(entry.name .. ": short read")
    return
  end
  local chunk, err = loadstring(source, "@" .. entry.name)
  if chunk == nil then
    entry.state = "does not compile"
    say(entry.name .. " (patch_" .. entry.patch .. "): does not compile on Lua 5.1: " .. tostring(err))
    return
  end
  local clock0 = os.clock()
  local ran, why = xpcall(chunk, function(e)
    return tostring(e) .. "\n" .. (type(debug) == "table" and debug.traceback("", 2) or "")
  end)
  local took = (os.clock() - clock0) * 1000
  if ran then
    entry.state = "loaded"
    if type(package) == "table" and type(package.loaded) == "table" and package.loaded[entry.name] == nil then
      package.loaded[entry.name] = (why == nil) and true or why
    end
    say(string.format("%s (patch_%d, %d bytes): loaded in %.0f ms", entry.name, entry.patch, entry.size, took))
  else
    entry.state = "failed"
    say(entry.name .. " (patch_" .. entry.patch .. "): FAILED: " .. tostring(why))
  end
end

local function start()
  if L.started then return end
  L.started = true
  local clock0 = os.clock()
  local ok, list = pcall(discover)
  if not ok then
    say("discovery failed: " .. tostring(list))
    flush()
    return
  end
  L.mods = list
  check_game_version()
  flush()
  for _, entry in ipairs(list) do
    run(entry)
    flush()
  end
  for _, fn in ipairs(after_startup) do
    local ran, why = pcall(fn)
    if not ran then say("after_startup: " .. tostring(why)) end
  end
  local loaded = 0
  for _, entry in ipairs(list) do if entry.state == "loaded" then loaded = loaded + 1 end end
  say(string.format("started %d of %d mods in %.0f ms", loaded, #list, (os.clock() - clock0) * 1000))
  L.started_done = true
  L.hook_shutdown()
  flush()
end

-- The mods start when the engine calls init (as under Bingus Shared Loader:
-- update and shutdown exist by then and can be wrapped).
local game_init = rawget(_G, "init")
rawset(_G, "init", function(...)
  local results = { n = 0 }
  if type(game_init) == "function" then results = pack(game_init(...)) end
  local ok, why = pcall(start)
  if not ok then
    say("start failed: " .. tostring(why))
    flush()
  end
  return unpack(results, 1, results.n)
end)
say("waiting for init")
flush()
