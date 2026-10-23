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
--
-- Mods written for Mod Options Menu find a ModOptionsMenu look-alike whose
-- settings are edited in HD2 Mod Setting GUI, outside the game (see "Mod
-- Options Menu look-alike" below).
-- Plain Lua 5.1 only (no goto, bit or ffi)
local MARKER = "boot-mod-loader-0.3.6"
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
    capabilities = { after_startup = true, on_shutdown = true, open_log = true, mod_options = true, ffi = false, jit = false },
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

-- ------------------------------------------------------------ Mod Options Menu look-alike

-- Mods written for CowboyBingus' Mod Options Menu (its in-game tab needed ffi
-- and is gone) register their options here as before. Each mod's options
-- become a list for HD2 Mod Setting GUI, the settings window outside the
-- game (github.com/pat-pat-patrick/hd2-mod-setting-gui), in the game's
-- settings folder:
--   mom_<mod>_options.json  the list, written when it changes;
--   mom_<mod>_settings.txt  key=value lines ending "#end", written whole by
--                           the window; opened once and read again each
--                           second; a changed value goes to the mod's
--                           on_change callbacks, as the menu's apply did;
--   mom_<mod>.log           "HH:MM:SS settings window: applied k=v" lines,
--                           for the window's status line.
-- The API is the menu's v1.2 (api 1, version 3: register_option, get, set,
-- on_change, ready) with its checks and limits; ready() is true, the window
-- being the menu. A value the window has not set yet comes from the menu's
-- own saved values (%LOCALAPPDATA%\CowboyBingus\Helldivers2\Logs\
-- ModOptionsMenu.values), so a player's settings from before the update
-- carry over. Nothing is hooked or read unless a mod registers an option.
local MOM = { groups = {}, list = {}, names = {}, natives = {}, options = {}, values = {}, callbacks = {},
              at = 0, every = 1.0, MAX_OPTIONS = 32, MAX_MODS = 112, MAX_CHOICES = 16, NONE = {} }
do
  local ok, dir = pcall(os.getenv, "APPDATA")
  MOM.dir = (ok and type(dir) == "string") and (dir .. "\\Arrowhead\\Helldivers2\\") or nil
end

-- A text: a string, or a function returning one (called now); control
-- characters become spaces; nil when it is empty or over `limit` characters.
function MOM.text(value, limit)
  if type(value) == "function" then
    local ok, result = pcall(value)
    if not ok then return nil end
    value = result
  end
  if type(value) ~= "string" then return nil end
  local text = string.gsub(string.gsub(string.gsub(value, "%c", " "), "^%s+", ""), "%s+$", "")
  local _, chars = string.gsub(text, "[^\128-\191]", "")
  if text == "" or chars > limit then return nil end
  return text
end

function MOM.snap(o, v)
  if type(v) ~= "number" or not (v > -math.huge and v < math.huge) then return nil end
  local steps = math.floor((v - o.min) / o.step + 0.5)
  local s = math.min(o.max, math.max(o.min, o.min + steps * o.step))
  return tonumber(string.format("%." .. o.decimals .. "f", s))
end

function MOM.valid(o, v)
  if o.kind == "toggle" then return type(v) == "boolean" end
  if type(v) ~= "number" or v ~= v then return false end
  if o.kind == "choice" then return v % 1 == 0 and v >= 1 and v <= #o.choices end
  return v >= o.min and v <= o.max
end

function MOM.encode(o, v)
  if o.kind == "toggle" then return v and "true" or "false" end
  if o.kind == "choice" then return tostring(v) end
  return string.format("%.6g", v)
end

function MOM.decode(o, text)
  if type(text) ~= "string" then return nil end
  text = string.match(text, "^%s*(.-)%s*$")
  if o.kind == "toggle" then
    if text == "true" then return true elseif text == "false" then return false end
    return nil
  end
  local v = tonumber(text)
  if v ~= nil and o.kind == "slider" then v = MOM.snap(o, v) end
  if v ~= nil and MOM.valid(o, v) then return v end
  return nil
end

-- The option a registration describes, or nil and why (the menu's rules).
function MOM.option(id, spec)
  if type(id) ~= "string" or id == "" or #id > 96 or string.find(id, "%c") or type(spec) ~= "table" then
    return nil, "invalid option registration"
  end
  local o = { id = id, kind = spec.type, label = MOM.text(spec.label, 64), profile = spec.profile == true }
  if o.label == nil then return nil, "invalid option registration" end
  if spec.description ~= nil then
    o.description = MOM.text(spec.description, 400)
    if o.description == nil then return nil, "invalid description" end
  end
  if o.kind == "toggle" then
    if spec.default ~= nil and type(spec.default) ~= "boolean" then return nil, "invalid default" end
    o.default = spec.default == true
  elseif o.kind == "choice" then
    local list = spec.choices
    if type(list) ~= "table" or #list < 2 or #list > MOM.MAX_CHOICES then
      return nil, "choices must list 2 to " .. MOM.MAX_CHOICES .. " names"
    end
    o.choices = {}
    for i, c in ipairs(list) do
      o.choices[i] = MOM.text(c, 48)
      if o.choices[i] == nil then return nil, "invalid choice name" end
    end
    o.default = spec.default == nil and 1 or spec.default
  elseif o.kind == "slider" then
    local lo, hi, step = spec.min, spec.max, spec.step or 1
    if type(lo) ~= "number" or type(hi) ~= "number" or type(step) ~= "number" or not (lo < hi)
       or not (step > 0) or step > hi - lo or not (lo > -math.huge and hi < math.huge) then
      return nil, "slider needs min < max and 0 < step <= max - min"
    end
    local d = 0
    while d < 3 and math.abs(step * 10 ^ d - math.floor(step * 10 ^ d + 0.5)) > 1e-6 do d = d + 1 end
    if lo % 1 ~= 0 and d < 1 then d = 1 end
    o.min, o.max, o.step, o.decimals = lo, hi, step, d
    o.default = spec.default == nil and lo or spec.default
    if type(o.default) == "number" then o.default = MOM.snap(o, o.default) end
  else
    return nil, "type must be toggle, choice or slider"
  end
  if not MOM.valid(o, o.default) then return nil, "invalid default" end
  return o
end

function MOM.same(a, b)
  if a.kind ~= b.kind or a.default ~= b.default then return false end
  if a.kind == "choice" then return #a.choices == #b.choices end
  if a.kind == "slider" then return a.min == b.min and a.max == b.max and a.step == b.step end
  return true
end

-- key=value lines of a file (nil: none), for the values a window wrote.
function MOM.read_values(path)
  local ok, f = pcall(io.open, path, "rb")
  if not ok or f == nil then return {} end
  local okr, text = pcall(f.read, f, "*a")
  pcall(f.close, f)
  local out = {}
  if okr and type(text) == "string" then
    for k, v in string.gmatch(text, "([^=\r\n]+)=([^\r\n]*)") do out[k] = v end
  end
  return out
end

-- The menu's own saved values from before the update: id TAB value lines.
function MOM.old()
  if MOM.old_values ~= nil then return MOM.old_values end
  MOM.old_values = {}
  local ok, dir = pcall(os.getenv, "LOCALAPPDATA")
  if not ok or type(dir) ~= "string" then return MOM.old_values end
  local base = dir .. "\\CowboyBingus\\Helldivers2\\Logs\\ModOptionsMenu.values"
  for _, path in ipairs({ base, base .. ".bak" }) do
    local oko, f = pcall(io.open, path, "rb")
    if oko and f ~= nil then
      local okr, text = pcall(f.read, f, "*a")
      pcall(f.close, f)
      local found = false
      if okr and type(text) == "string" then
        for id, v in string.gmatch(text, "([^\t\r\n]+)\t([^\r\n]*)") do MOM.old_values[id], found = v, true end
      end
      if found then break end
    end
  end
  return MOM.old_values
end

-- The addon calling the menu (its chunk is named "@mods/<a>/<b>").
function MOM.caller()
  if type(debug) ~= "table" or type(debug.getinfo) ~= "function" then return nil end
  for level = 3, 40 do
    local ok, info = pcall(debug.getinfo, level, "S")
    if not ok or info == nil then break end
    if type(info.source) == "string" and string.find(info.source, "^@mods/") then return string.sub(info.source, 2) end
  end
  return nil
end

-- The mod an option belongs to: by mod_id, else by the addon registering it,
-- else by its mod name; new while there is room.
function MOM.group(spec, addon)
  local title = nil
  if spec.mod ~= nil then
    title = MOM.text(spec.mod, 40)
    if title == nil then return nil, "invalid mod name" end
  end
  local mod_id = spec.mod_id
  if mod_id ~= nil and (type(mod_id) ~= "string" or #mod_id > 64 or not string.find(mod_id, "^[%w_%.%-/:]+$")) then
    return nil, "invalid mod_id"
  end
  local tail = addon and string.match(addon, "([%w_]+)$") or nil
  if title == nil then title = tail and (string.gsub(tail, "_", " ")) or "Unnamed mod" end
  local key = mod_id or addon or title
  local g = MOM.groups[key]
  if g ~= nil then return g end
  if #MOM.list >= MOM.MAX_MODS then return nil, "all " .. MOM.MAX_MODS .. " mod categories are in use" end
  local base = string.gsub(string.lower(mod_id or tail or title), "[^%w]+", "_")
  base = string.match(base, "^_*(.-)_*$")
  if base == "" then base = "mod" end
  local name = "mom_" .. base
  while MOM.names[name] do name = name .. "_" end
  MOM.names[name] = true
  g = { key = key, title = title, name = name, addon = addon, options = {}, by_key = {}, dirty = true, write = false,
        file_values = MOM.dir and MOM.read_values(MOM.dir .. name .. "_settings.txt") or {}, migrated = 0 }
  MOM.groups[key] = g
  MOM.list[#MOM.list + 1] = g
  return g
end

-- The keys of the list a mod writes for the window itself (<addon
-- name>_options.json, as every mod with mod_settings does), read once;
-- {} when it writes none.
function MOM.native(tail)
  local keys = MOM.natives[tail]
  if keys ~= nil then return keys end
  keys = {}
  local ok, f = pcall(io.open, MOM.dir .. tail .. "_options.json", "rb")
  if ok and f ~= nil then
    local okr, text = pcall(f.read, f, "*a")
    pcall(f.close, f)
    if okr and type(text) == "string" then
      for k in string.gmatch(text, '"key"%s*:%s*"([^"]*)"') do keys[k] = true end
    end
  end
  MOM.natives[tail] = keys
  return keys
end

-- A setting the mod already lists for the window itself is refused: listed
-- again here it would show twice, and the mod greys out in its own list what
-- the menu holds. It is that setting when a part of the id after a dot,
-- written as a key (Super Beacon's "patpatpatrick.super_beacon.ammo_mode2" ->
-- "ammo_mode2", Super Laser Sights Plus's "...plus.unit.37cde..." ->
-- "unit_37cde..."), is one of the list's keys. Settings it keeps only in the
-- menu (ActionCam 3.0: options in the menu, keys in its own list) are taken.
function MOM.listed(tail, id)
  local keys = MOM.native(tail)
  if next(keys) == nil then return false end
  local at = 0
  repeat
    if keys[(string.gsub(string.sub(id, at + 1), "[^%w_]", "_"))] then return true end
    at = string.find(id, ".", at + 1, true)
  until at == nil
  return false
end

function MOM.register(id, spec)
  local o, why = MOM.option(id, spec)
  if o == nil then return false, why end
  local addon = MOM.caller()
  local tail = addon and string.match(addon, "([%w_]+)$") or nil
  if tail ~= nil and MOM.dir ~= nil and MOM.listed(tail, id) then
    if not MOM.said_listed then MOM.said_listed = {} end
    if not MOM.said_listed[tail] then
      MOM.said_listed[tail] = true
      say("ModOptionsMenu: " .. tail .. " lists its settings in the settings window itself -- those stay there")
    end
    return false, "this setting is in the mod's own list in the settings window"
  end
  local had = MOM.options[id]
  if had ~= nil then
    if MOM.same(had, o) then return true end
    return false, "option already registered differently"
  end
  local g, why2 = MOM.group(spec, addon)
  if g == nil then return false, why2 end
  if #g.options >= MOM.MAX_OPTIONS then return false, "mod already has " .. MOM.MAX_OPTIONS .. " options" end
  local key = string.gsub(id, "[^%w_]", "_")
  while g.by_key[key] ~= nil do key = key .. "_" end
  o.key, o.group = key, g
  g.options[#g.options + 1] = o
  g.by_key[key] = o
  MOM.options[id] = o
  local v = MOM.decode(o, g.file_values[key])
  if v == nil then
    v = MOM.decode(o, MOM.old()[id])
    if v ~= nil then g.migrated = g.migrated + 1 end
    g.write = true                                     -- the window's file gains this key
  end
  if v == nil then v = o.default end
  MOM.values[id] = v
  if spec.profiles == true then g.profiles = true end   -- the mod asks for the window's own saved sets
  g.dirty = true
  L.hook_update()
  return true
end

function MOM.set(id, value)
  local o = MOM.options[id]
  if o == nil then return false, "unknown option" end
  if o.kind == "slider" and type(value) == "number" then value = MOM.snap(o, value) end
  if not MOM.valid(o, value) then return false, "invalid value" end
  if MOM.values[id] ~= value then
    MOM.values[id] = value
    o.group.write = true
  end
  return true
end

function MOM.json(v)
  if v == MOM.NONE then return "[]" end
  local tv = type(v)
  if tv == "string" then
    return '"' .. string.gsub(v, '[%c"\\]', function(c)
      if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" elseif c == "\n" then return "\\n" end
      return string.format("\\u%04x", string.byte(c))
    end) .. '"'
  elseif tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.10g", v)
  elseif tv == "boolean" then
    return v and "true" or "false"
  elseif tv == "table" then
    local out = {}
    if #v > 0 then
      for _, x in ipairs(v) do out[#out + 1] = MOM.json(x) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = k end
    table.sort(keys)
    for _, k in ipairs(keys) do out[#out + 1] = MOM.json(k) .. ":" .. MOM.json(v[k]) end
    return "{" .. table.concat(out, ",") .. "}"
  end
  return "null"
end

-- The option that switches a mod's profiles, for the window's profile
-- switcher ("profile" in the list): one registered with profile = true (an
-- extension the menu itself ignores), else a choice whose id ends ".profile"
-- (ActionCam 3.0's actioncam.profile).
function MOM.profile_key(g)
  for _, o in ipairs(g.options) do
    if o.profile then return o.key end
  end
  for _, o in ipairs(g.options) do
    if o.kind == "choice" and string.match(o.id, "([^%.]+)$") == "profile" then return o.key end
  end
  return nil
end

-- The window's list for a mod, written only when it differs.
function MOM.export(g)
  local opts = {}
  for _, o in ipairs(g.options) do
    -- texts as plain strings (window 1.8.0); the menu's texts are in one
    -- language, so there are no "translations"
    local e = { key = o.key, type = o.kind, default = o.default, min = o.min, max = o.max, step = o.step,
                name = o.label, hint = o.description }
    if o.choices then
      e.choices = {}
      for i, c in ipairs(o.choices) do e.choices[i] = c end
    end
    opts[#opts + 1] = e
  end
  -- profiles: the window offers its own saved sets of values only to a mod
  -- that asks for them itself, with profiles = true on any of its options
  -- (an extension the menu ignores; the user, 10-24: never inferred, not
  -- even from a profile option); absent otherwise.
  local text = MOM.json({ title = g.title, version = "", settings = g.name .. "_settings.txt", pages = MOM.NONE,
                          options = opts, menu_owned = MOM.NONE, profile = MOM.profile_key(g),
                          profiles = g.profiles or nil }) .. "\n"
  local path = MOM.dir .. g.name .. "_options.json"
  local okr, f = pcall(io.open, path, "rb")
  if okr and f ~= nil then
    local _, old = pcall(f.read, f, "*a")
    pcall(f.close, f)
    if old == text then return end
  end
  local okw, w = pcall(io.open, path, "wb")
  if not okw or w == nil then
    say("ModOptionsMenu: " .. g.title .. ": " .. g.name .. "_options.json not written")
    return
  end
  pcall(w.write, w, text)
  pcall(w.close, w)
  say(string.format("ModOptionsMenu: %s (%s), %d option(s) -> %s_options.json%s", g.title, g.addon or "?",
    #g.options, g.name, g.migrated > 0 and (", " .. g.migrated .. " value(s) carried over from Mod Options Menu") or ""))
end

-- Every value of a mod, in its options' order, ending #end.
function MOM.text_of(g)
  local lines = {}
  for _, o in ipairs(g.options) do lines[#lines + 1] = o.key .. "=" .. MOM.encode(o, MOM.values[o.id]) end
  lines[#lines + 1] = "#end"
  return table.concat(lines, "\n") .. "\n"
end

function MOM.write(g, path)
  local text = MOM.text_of(g)
  local ok, w = pcall(io.open, path, "wb")
  if not ok or w == nil then return false end
  pcall(w.write, w, text)
  pcall(w.close, w)
  g.text = text
  return true
end

-- One mod, once a second: its list when new, its file written when a value
-- changed here, else read again; each value the window changed is applied.
function MOM.tick_group(g)
  if g.dirty then
    g.dirty = false
    MOM.export(g)
  end
  local path = MOM.dir .. g.name .. "_settings.txt"
  if g.write then
    g.write = false
    MOM.write(g, path)
  end
  if g.fh == nil then
    if g.failed then return end
    local ok, f = pcall(io.open, path, "rb")
    if not ok or f == nil then
      MOM.write(g, path)
      ok, f = pcall(io.open, path, "rb")
    end
    if not ok or f == nil then
      g.failed = true
      say("ModOptionsMenu: " .. g.name .. "_settings.txt not opened -- the window's changes apply at the next launch")
      return
    end
    g.fh = f
  end
  local fh = g.fh
  local oks = pcall(fh.seek, fh, "set")
  local okr, text = pcall(fh.read, fh, "*a")
  if not oks or not okr or type(text) ~= "string" or text == g.text then return end
  if not string.find(text, "\n#end%s*$") then return end            -- written half: the next read
  g.text = text
  -- What differs from the values before this read, all found before any
  -- callback runs (a callback may set() other values).
  local found, profile = {}, MOM.profile_key(g)
  for k, v in string.gmatch(text, "([^=\r\n]+)=([^\r\n]*)") do
    local o = g.by_key[k]
    local value = o and MOM.decode(o, v)
    if value ~= nil and MOM.values[o.id] ~= value then
      found[#found + 1] = { o = o, value = value }
      if k == profile then found = { found[#found] } break end
    end
  end
  -- A profile switch from the window comes with the old profile's other
  -- values in the same file: only the switch is applied, the mod loads the
  -- new profile's own values (and set()s them), and the file is written
  -- back with those for the window to show. Applying the old values too
  -- would save them into the new profile.
  if found[1] ~= nil and found[1].o.key == profile then g.write = true end
  local changed = {}
  for _, c in ipairs(found) do
    local o, value = c.o, c.value
    MOM.values[o.id] = value
    changed[#changed + 1] = o.key .. "=" .. MOM.encode(o, value)
    for _, fn in ipairs(MOM.callbacks[o.id] or {}) do
      local okc, err = pcall(fn, value, o.id)
      if not okc then say("ModOptionsMenu: on_change of " .. o.id .. " failed: " .. tostring(err)) end
    end
  end
  if g.write then
    g.write = false
    MOM.write(g, path)
  end
  if #changed == 0 then return end
  local line = os.date("%H:%M:%S ") .. "settings window: applied " .. table.concat(changed, " ")
  say(g.title .. ": " .. line)
  local okl, lf = pcall(io.open, MOM.dir .. g.name .. ".log", "a")
  if okl and lf ~= nil then
    pcall(lf.write, lf, line .. "\n")
    pcall(lf.close, lf)
  end
end

function MOM.tick(dt)
  MOM.at = MOM.at - ((type(dt) == "number" and dt > 0 and dt < 10) and dt or 0.016)
  if MOM.at > 0 then return end
  MOM.at = MOM.every
  for _, g in ipairs(MOM.list) do
    local ok, err = pcall(MOM.tick_group, g)
    if not ok and not g.said then
      g.said = true
      say("ModOptionsMenu: " .. g.name .. ": " .. tostring(err))
    end
  end
  flush()
end

-- The game's update, wrapped once, only when a mod registered an option and
-- the mods have loaded (a mod that registers later gets it then).
function L.hook_update()
  if L.update_hooked or not L.started_done or MOM.list[1] == nil or MOM.dir == nil then return end
  L.update_hooked = true
  local previous = rawget(_G, "update")
  if type(previous) ~= "function" then previous = nil end
  local tick = MOM.tick
  rawset(_G, "update", function(dt, ...)
    pcall(tick, dt)
    if previous ~= nil then return previous(dt, ...) end
  end)
  say("ModOptionsMenu: the settings window's files are read once a second")
end

if rawget(_G, "ModOptionsMenu") == nil then
  rawset(_G, "ModOptionsMenu", {
    api = 1, version = 3, max_mods = MOM.MAX_MODS, max_options = MOM.MAX_OPTIONS,
    revision = MARKER, window = "HD2 Mod Setting GUI",
    register_option = function(id, spec)
      local ok, done, why = pcall(MOM.register, id, spec)
      if not ok then return false, tostring(done) end
      return done, why
    end,
    get = function(id) return MOM.values[id] end,
    set = function(id, value) return MOM.set(id, value) end,
    on_change = function(id, fn)
      if type(id) ~= "string" or type(fn) ~= "function" then return false, "invalid callback" end
      local list = MOM.callbacks[id] or {}
      list[#list + 1] = fn
      MOM.callbacks[id] = list
      return true
    end,
    ready = function() return true end,
  })
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
  L.hook_update()
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
