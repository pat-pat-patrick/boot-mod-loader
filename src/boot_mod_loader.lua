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
local MARKER = "boot-mod-loader-0.9.0"
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
    capabilities = { after_startup = true, on_shutdown = true, open_log = true, mod_options = true, mod_bindings = true, ffi = false, jit = false },
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
local MOM = { groups = {}, list = {}, names = {}, natives = {}, addon_title = {}, options = {}, values = {}, callbacks = {},
              at = 0, every = 1.0, MAX_OPTIONS = 1000, MAX_MODS = 1000, MAX_CHOICES = 16, NONE = {} }
-- (0.9.0, the user, 10-24) The menu's 32 options a mod and 112 mods were its
-- tab's room; the window has no such limit (ActionCam 3.0 registers 33, and
-- actioncam.legacy was refused). 1000 of each only stops code that registers
-- without end; reaching it is refused as the menu did, and logged once.
do
  local ok, dir = pcall(os.getenv, "APPDATA")
  MOM.dir = (ok and type(dir) == "string") and (dir .. "\\Arrowhead\\Helldivers2\\") or nil
end

-- A guard reached: why, logged once each.
function MOM.full(why)
  MOM.said_full = MOM.said_full or {}
  if not MOM.said_full[why] then
    MOM.said_full[why] = true
    say("ModOptionsMenu: " .. why .. " -- registrations past this guard are refused")
  end
  return why
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
  if o.kind == "key" then return type(v) == "string" end           -- a Mod Bindings Menu binding (below)
  if type(v) ~= "number" or v ~= v then return false end
  if o.kind == "choice" then return v % 1 == 0 and v >= 1 and v <= #o.choices end
  return v >= o.min and v <= o.max
end

function MOM.encode(o, v)
  if o.kind == "toggle" then return v and "true" or "false" end
  if o.kind == "key" then return v end
  if o.kind == "choice" then return tostring(v) end
  return string.format("%.6g", v)
end

function MOM.decode(o, text)
  if type(text) ~= "string" then return nil end
  text = string.match(text, "^%s*(.-)%s*$")
  if o.kind == "key" then return string.lower(text) end                -- the game's key name, "" for none
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
  -- gap = true: the menu's space above the row (0.4.0, the user: "Mod
  -- Options Menu has no headings, but it has gaps"), for the window's gap
  local o = { id = id, kind = spec.type, label = MOM.text(spec.label, 64), profile = spec.profile == true,
              gap = spec.gap == true }
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
  if #MOM.list >= MOM.MAX_MODS then return nil, MOM.full("all " .. MOM.MAX_MODS .. " mod categories are in use") end
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
  -- The same mod's keys (Mod Bindings Menu, below) take this title, so the
  -- window shows both lists as one mod.
  if addon ~= nil then
    MOM.addon_title[addon] = title
    local keys = MOM.groups["mbm:" .. addon]
    if keys ~= nil then keys.dirty = true end
  end
  return g
end

-- The keys of the list a mod writes for the window itself (<addon
-- name>_options.json, as every mod with mod_settings does), read once;
-- {} when it writes none.
function MOM.native(tail)
  local keys = MOM.natives[tail]
  if keys ~= nil then return keys end
  keys = {}
  if string.find(tail, "^mom_") or string.find(tail, "^mbm_") then MOM.natives[tail] = keys return keys end   -- this loader's
  local ok, f = pcall(io.open, MOM.dir .. tail .. "_options.json", "rb")
  if ok and f ~= nil then
    local okr, text = pcall(f.read, f, "*a")
    pcall(f.close, f)
    if okr and type(text) == "string" and not string.find(text, '"stale"%s*:%s*true') then
      for k in string.gmatch(text, '"key"%s*:%s*"([^"]*)"') do keys[k] = true end
      -- the file the window writes the values to (its "settings"), for the
      -- registrations served from the mod's own list (below)
      local settings = string.match(text, '"settings"%s*:%s*"([^"\\/:]+)"')
      MOM.own_settings[tail] = settings or (tail .. "_settings.txt")
    end
  end
  MOM.natives[tail] = keys
  return keys
end

-- The key in the mod's own window list a registration is, or nil. It is
-- that setting when a part of the id after a dot, written as a key (Super
-- Beacon's "patpatpatrick.super_beacon.ammo_mode2" -> "ammo_mode2", Super
-- Laser Sights Plus's "...plus.unit.37cde..." -> "unit_37cde...", ActionCam's
-- "actioncam.dist" -> "dist"), is one of the list's keys.
function MOM.listed(tail, id)
  local keys = MOM.native(tail)
  if next(keys) == nil then return nil end
  local at = 0
  repeat
    local key = string.gsub(string.sub(id, at + 1), "[^%w_]", "_")
    if keys[key] then return key end
    at = string.find(id, ".", at + 1, true)
  until at == nil
  return nil
end

-- ------------------------------------------------------------ mods with their own window list

-- When a mod has both, its own list for the window wins (the user, 10-24:
-- "两种都有时，优先用窗口原生"). ActionCam 3.0 writes actioncam_options.json and
-- reads actioncam_settings.txt itself when it finds no options menu, and asks
-- every frame (type(ModOptionsMenu) == "table" and ModOptionsMenu.api == 1),
-- so hiding the globals while its chunk runs would not do. Instead the two
-- look-alikes answer `api` through a metamethod: nil to a mod that lists its
-- settings for the window itself and names a menu in its source (MOM.hidden,
-- decided as it loads), 1 to everyone else. Reading `api` then looks up the
-- calling mod on the stack (a few debug.getinfo calls); nothing else changes,
-- and no mod's globals go through a metamethod.
-- A hidden mod that registers anyway (it read the table some other way), or
-- one that registers a setting its own list holds, is served from its own
-- list: the registration succeeds, get() is the value in the mod's own
-- settings file (else the default), the file is read once a second like the
-- loader's lists and a changed value goes to on_change, set() writes the
-- value back into that file (its other lines kept), and a key is read from
-- the file for is_down. Nothing of it goes into a mom_ / mbm_ list, so a list
-- of earlier games that only held such settings is set aside (.stale).
MOM.hidden, MOM.owns, MOM.own_settings = {}, {}, {}

-- `api` as the calling mod sees it.
function MOM.api_for()
  if next(MOM.hidden) == nil then return 1 end
  local addon = MOM.caller()
  local tail = addon and string.match(addon, "([%w_]+)$") or nil
  if tail ~= nil and MOM.hidden[tail] then return nil end
  return 1
end

-- A mod that supports both but writes its own list only when it finds no
-- menu (ActionCam 3.0) never gets one while the menus are always there. So
-- (0.8.0, the user, 10-24: "从第二次启动起改用原生"; the first launch must work
-- as before): in the game where it registers with a menu and its source also
-- writes the window's files itself (MOM.features: "<name>_options.json",
-- "<name>_settings.txt", "settings":"<name>_settings.txt" and "#end" all in
-- its source), it is noted, and from the next launch the menus are hidden
-- from it. Its values set through the menus go into its own settings file
-- first (when it has none), by id: the menus' key for an id is the id written
-- as a key, its own key the part after the first dot. If its own list has not
-- appeared after MOM.WAIT seconds of play, the guess was wrong: the menus come
-- back at once (such a mod asks every frame) and are never hidden from it
-- again. The notes are in boot_mod_loader_native.txt: "<name> TAB next | yes
-- | no", and "<name> TAB id TAB <list> TAB <id>" for the values.
MOM.NOTES, MOM.WAIT, MOM.features, MOM.watch = "boot_mod_loader_native.txt", 10, {}, {}

function MOM.notes()
  if MOM.noted ~= nil then return MOM.noted end
  MOM.noted = { state = {}, ids = {} }
  local ok, f = pcall(io.open, MOM.dir .. MOM.NOTES, "rb")
  if ok and f ~= nil then
    local _, text = pcall(f.read, f, "*a")
    pcall(f.close, f)
    for line in string.gmatch(text or "", "[^\r\n]+") do
      local tail, state = string.match(line, "^([%w_]+)\t(%a+)$")
      if tail ~= nil then MOM.noted.state[tail] = state end
      local t2, list, id = string.match(line, "^([%w_]+)\tid\t([%w_]+)\t([^\t]+)$")
      if t2 ~= nil then
        MOM.noted.ids[t2] = MOM.noted.ids[t2] or {}
        table.insert(MOM.noted.ids[t2], { list = list, id = id })
      end
    end
  end
  return MOM.noted
end

function MOM.save_notes()
  local n, lines = MOM.notes(), {}
  for tail, state in pairs(n.state) do
    lines[#lines + 1] = tail .. "\t" .. state
    for _, x in ipairs(n.ids[tail] or {}) do lines[#lines + 1] = tail .. "\tid\t" .. x.list .. "\t" .. x.id end
  end
  table.sort(lines)
  local ok, w = pcall(io.open, MOM.dir .. MOM.NOTES, "wb")
  if ok and w ~= nil then
    pcall(w.write, w, table.concat(lines, "\n") .. (lines[1] and "\n" or ""))
    pcall(w.close, w)
  end
end

-- A registration with a menu: a mod that supports the window too is noted
-- (with the id and its list, for its values next launch).
function MOM.note(tail, list, id)
  if tail == nil or not MOM.features[tail] or MOM.hidden[tail] then return end
  local n = MOM.notes()
  local state = n.state[tail]
  if state == "no" then return end
  if state == nil then
    n.state[tail] = "next"
    n.ids[tail] = {}
    say(tail .. ": supports the settings window itself; from the next launch it will use it")
  end
  if state == nil or state == "next" then
    for _, x in ipairs(n.ids[tail]) do if x.id == id then return end end
    table.insert(n.ids[tail], { list = list, id = id })
    MOM.notes_dirty = true
  end
end

-- The values a noted mod had through the menus, into its own settings file
-- when it has none yet. How many.
function MOM.carry(tail)
  local own = MOM.dir .. tail .. "_settings.txt"
  local ok, f = pcall(io.open, own, "rb")
  if ok and f ~= nil then pcall(f.close, f) return 0 end
  local files, lines = {}, {}
  for _, x in ipairs(MOM.notes().ids[tail] or {}) do
    files[x.list] = files[x.list] or MOM.read_values(MOM.dir .. x.list .. "_settings.txt")
    local v = files[x.list][(string.gsub(x.id, "[^%w_]", "_"))]
    local after = string.match(x.id, "^[^%.]*%.(.+)$")
    if v ~= nil and after ~= nil then lines[#lines + 1] = string.gsub(after, "[^%w_]", "_") .. "=" .. v end
  end
  if lines[1] == nil then return 0 end
  local okw, w = pcall(io.open, own, "wb")
  if not okw or w == nil then return 0 end
  pcall(w.write, w, table.concat(lines, "\n") .. "\n#end\n")
  pcall(w.close, w)
  return #lines
end

-- Whether a mod about to load is to use its own list: it has one and names
-- a menu (0.7.0), or it was noted in an earlier launch (0.8.0). Then the menus
-- are hidden from it (logged).
function MOM.hide_from(name, source)
  local tail = string.match(name or "", "([%w_]+)$")
  if tail == nil or MOM.dir == nil or type(source) ~= "string" then return false end
  if not string.find(source, "ModOptionsMenu", 1, true) and not string.find(source, "ModBindingsMenu", 1, true) then return false end
  MOM.features[tail] = string.find(source, tail .. "_options.json", 1, true) ~= nil
    and string.find(source, tail .. "_settings.txt", 1, true) ~= nil
    and string.find(source, '"settings"%s*:%s*"' .. tail .. '_settings%.txt"') ~= nil
    and string.find(source, "#end", 1, true) ~= nil
  local state = MOM.notes().state[tail]
  if state == "no" then return false end
  if next(MOM.native(tail)) ~= nil then
    MOM.hidden[tail] = true
    say(name .. ": lists its settings in the settings window itself (" .. tail .. "_options.json): "
      .. "Mod Options Menu and Mod Bindings Menu are hidden from it (api nil), so it uses the window")
    return true
  end
  if state ~= "next" and state ~= "yes" then return false end
  MOM.hidden[tail] = true
  MOM.watch[tail] = MOM.WAIT
  local carried = MOM.carry(tail)
  say(name .. ": supports the settings window itself (noted in an earlier launch): Mod Options Menu and Mod Bindings "
    .. "Menu are hidden from it (api nil), so it uses the window"
    .. (carried > 0 and ("; " .. carried .. " value(s) set through the menus carried over to " .. tail .. "_settings.txt") or "")
    .. "; the menus come back if its list does not appear within " .. MOM.WAIT .. " s")
  return true
end

-- Once a second while a mod is watched: its own list appeared (it uses the
-- window: noted yes), or the time is up (the menus come back: noted no).
function MOM.check_watch(seconds)
  for tail, left in pairs(MOM.watch) do
    MOM.natives[tail] = nil                         -- read the list again
    if next(MOM.native(tail)) ~= nil then
      MOM.watch[tail] = nil
      MOM.notes().state[tail] = "yes"
      MOM.notes_dirty = true
      say(tail .. ": wrote its own list (" .. tail .. "_options.json): it uses the settings window")
    elseif left - seconds <= 0 then
      MOM.watch[tail] = nil
      MOM.hidden[tail] = nil
      MOM.notes().state[tail] = "no"
      MOM.notes_dirty = true
      say(tail .. ": no list of its own after " .. MOM.WAIT .. " s: Mod Options Menu and Mod Bindings Menu are back "
        .. "for it, and will not be hidden from it again")
    else
      MOM.watch[tail] = left - seconds
    end
  end
  if MOM.notes_dirty then
    MOM.notes_dirty = false
    MOM.save_notes()
  end
end

-- The group of the settings served from a mod's own list (one per mod).
function MOM.own_group(tail)
  local g = MOM.owns[tail]
  if g ~= nil then return g end
  local settings = MOM.own_settings[tail] or (tail .. "_settings.txt")
  g = { key = "own:" .. tail, title = tail, name = tail, own = true, settings = settings, options = {}, by_key = {},
        dirty = false, write = false, file_values = MOM.read_values(MOM.dir .. settings), migrated = 0 }
  MOM.owns[tail] = g
  MOM.list[#MOM.list + 1] = g
  return g
end

-- A registration of a setting the mod's own list holds: served from that list.
function MOM.serve_own(tail, key, o)
  local g = MOM.own_group(tail)
  local had = g.by_key[key]
  if had ~= nil then
    if had.id == o.id then return true end
    return false, "another registration already uses " .. key .. " of the mod's own list"
  end
  o.key, o.group = key, g
  g.options[#g.options + 1] = o
  g.by_key[key] = o
  MOM.options[o.id] = o
  local v = MOM.decode(o, g.file_values[key])
  if v == nil then v = o.default end
  MOM.values[o.id] = v
  g.announce = true
  L.hook_update()
  return true
end

function MOM.register(id, spec)
  local o, why = MOM.option(id, spec)
  if o == nil then return false, why end
  local addon = MOM.caller()
  local tail = addon and string.match(addon, "([%w_]+)$") or nil
  local had = MOM.options[id]
  if had ~= nil then
    if MOM.same(had, o) then return true end
    return false, "option already registered differently"
  end
  local own = tail ~= nil and MOM.dir ~= nil and MOM.listed(tail, id) or nil
  if own ~= nil then return MOM.serve_own(tail, own, o) end
  local g, why2 = MOM.group(spec, addon)
  if g == nil then return false, why2 end
  MOM.note(tail, g.name, id)
  if #g.options >= MOM.MAX_OPTIONS then return false, MOM.full(g.title .. ": mod already has " .. MOM.MAX_OPTIONS .. " options") end
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
                name = o.label, hint = o.description, gap = o.gap or nil }
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
  -- A mod's keys (Mod Bindings Menu) are a "Keys" page under the title of
  -- the same mod's options, so the window merges the two lists.
  local title, pages = g.title, MOM.NONE
  if g.keys then
    title = (g.addon and MOM.addon_title[g.addon]) or g.title
    local keys = {}
    for i, o in ipairs(g.options) do keys[i] = o.key end
    pages = { { name = "Keys", keys = keys } }
  end
  local text = MOM.json({ title = title, version = "", settings = g.name .. "_settings.txt", pages = pages,
                          options = opts, menu_owned = MOM.NONE, profile = MOM.profile_key(g),
                          profiles = g.profiles or nil }) .. "\n"
  local path = MOM.dir .. g.name .. "_options.json"
  MOM.remember(g.name)
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
  say(string.format("%s: %s (%s), %d %s -> %s_options.json%s", g.keys and "ModBindingsMenu" or "ModOptionsMenu",
    title, g.addon or "?", #g.options, g.keys and "key(s)" or "option(s)", g.name,
    g.migrated > 0 and (", " .. g.migrated .. " value(s) carried over from Mod Options Menu") or ""))
end

-- ------------------------------------------------------------ lists nobody registered

-- A list this loader wrote in an earlier game stays in the settings folder
-- after its mod stops registering (2026-10-24: ActionCam writes its own
-- actioncam_options.json now; the window merged the old mom_actioncam list by
-- title and the player changed settings that nothing read). So once every mod
-- has started, each mom_ / mbm_ list that no mod registered this game is set
-- aside: renamed <name>_options.json.stale. Its _settings.txt stays, so the
-- values come back if the mod registers again (the list is written anew then).
-- The loader cannot list a folder (io only), so the candidates are the names it
-- wrote before (its index, boot_mod_loader_lists.txt) and the names the mods
-- loaded now would get. Nothing else in the folder is touched.
MOM.INDEX = "boot_mod_loader_lists.txt"

-- The names in the index, read once.
function MOM.index()
  if MOM.indexed ~= nil then return MOM.indexed end
  MOM.indexed = {}
  local ok, f = pcall(io.open, MOM.dir .. MOM.INDEX, "rb")
  if ok and f ~= nil then
    local _, text = pcall(f.read, f, "*a")
    pcall(f.close, f)
    for name in string.gmatch(text or "", "[^\r\n]+") do
      if string.find(name, "^mom_[%w_]+$") or string.find(name, "^mbm_[%w_]+$") then MOM.indexed[name] = true end
    end
  end
  return MOM.indexed
end

-- A list's name into the index (once).
function MOM.remember(name)
  if MOM.dir == nil or MOM.index()[name] then return end
  MOM.indexed[name] = true
  local ok, f = pcall(io.open, MOM.dir .. MOM.INDEX, "ab")
  if ok and f ~= nil then
    pcall(f.write, f, name .. "\n")
    pcall(f.close, f)
  end
end

-- A list set aside: renamed .stale; where the game's Lua has no os.rename
-- (not measured on 25988413), emptied with "stale": true, which the window skips.
function MOM.retire(name)
  local path = MOM.dir .. name .. "_options.json"
  local ok, f = pcall(io.open, path, "rb")
  if not ok or f == nil then return false end
  pcall(f.close, f)
  local stale = path .. ".stale"
  if type(os) == "table" and type(os.rename) == "function" then
    if type(os.remove) == "function" then pcall(os.remove, stale) end
    local okr, done = pcall(os.rename, path, stale)
    if okr and done then
      say("settings window: " .. name .. "_options.json is no mod's this game: renamed .stale (its settings stay)")
      return true
    end
  end
  local okw, w = pcall(io.open, path, "wb")
  if okw and w ~= nil then
    pcall(w.write, w, '{"stale": true, "options": []}\n')
    pcall(w.close, w)
    say("settings window: " .. name .. "_options.json is no mod's this game: emptied, marked stale (no os.rename)")
    return true
  end
  return false
end

-- Once every mod has started: the lists of the index and of the mods loaded
-- now that no mod registered set aside.
function MOM.sweep(mods)
  if MOM.dir == nil then return end
  local names = {}
  for name in pairs(MOM.index()) do names[#names + 1] = name end
  for _, entry in ipairs(mods or {}) do
    local tail = string.match(entry.name or "", "([%w_]+)$")
    if tail ~= nil then
      local base = string.match(string.gsub(string.lower(tail), "[^%w]+", "_"), "^_*(.-)_*$")
      if base ~= "" then
        names[#names + 1] = "mom_" .. base
        names[#names + 1] = "mbm_" .. base
      end
    end
  end
  local done = {}
  for _, name in ipairs(names) do
    if not done[name] and not MOM.names[name] then
      done[name] = true
      MOM.retire(name)
    end
  end
end

-- Every value of a mod, in its options' order, ending #end. A mod's own
-- settings file holds more than what was registered: its other lines are
-- kept as they are (read again now), the registered values replaced.
function MOM.text_of(g)
  local lines = {}
  if g.own then
    local text = g.text
    local ok, f = pcall(io.open, MOM.dir .. g.settings, "rb")
    if ok and f ~= nil then
      local _, now = pcall(f.read, f, "*a")
      pcall(f.close, f)
      if type(now) == "string" and string.find(now, "\n#end%s*$") then text = now end
    end
    local seen = {}
    for line in string.gmatch(text or "", "[^\r\n]+") do
      local k = string.match(line, "^([^=]+)=")
      local o = k and g.by_key[k]
      if o ~= nil then
        lines[#lines + 1] = k .. "=" .. MOM.encode(o, MOM.values[o.id])
        seen[k] = true
      elseif line ~= "#end" then
        lines[#lines + 1] = line
      end
    end
    for _, o in ipairs(g.options) do
      if not seen[o.key] then lines[#lines + 1] = o.key .. "=" .. MOM.encode(o, MOM.values[o.id]) end
    end
    lines[#lines + 1] = "#end"
    return table.concat(lines, "\n") .. "\n"
  end
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
  if g.announce then
    g.announce = false
    say(string.format("settings window: %s: %d registration(s) served from its own list (%s_options.json, %s)",
      g.title, #g.options, g.name, g.settings))
  end
  local path = MOM.dir .. (g.own and g.settings or (g.name .. "_settings.txt"))
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
  if g.own then return end                      -- the mod's own log is its own to write
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
  if next(MOM.watch) ~= nil or MOM.notes_dirty then pcall(MOM.check_watch, MOM.every) end
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
  if L.update_hooked or not L.started_done or (MOM.list[1] == nil and next(MOM.watch) == nil and not MOM.notes_dirty)
     or MOM.dir == nil then return end
  L.update_hooked = true
  local previous = rawget(_G, "update")
  if type(previous) ~= "function" then previous = nil end
  local tick = MOM.tick
  rawset(_G, "update", function(dt, ...)
    MOM.frame = MOM.frame + 1                  -- Mod Bindings Menu's guards are asked once a frame
    pcall(tick, dt)
    if previous ~= nil then return previous(dt, ...) end
  end)
  say("ModOptionsMenu: the settings window's files are read once a second")
end

-- ------------------------------------------------------------ Mod Bindings Menu look-alike

-- Mods written for CowboyBingus' Mod Bindings Menu (its tab needed ffi and
-- is gone) register their keys here as before (v2.3's API: api 1, version 4).
-- Each mod's keys become a "Keys" page for HD2 Mod Setting GUI,
-- mbm_<mod>_options.json / _settings.txt, written and read as the Mod
-- Options Menu lists above; the title is the same mod's options' title, so
-- the window shows them as one mod. A value is the game's key name ("q",
-- "f6", "mouse 3"; "" none), set by the player in the window, else the
-- mod's options.default.
--   is_down(id): what the binding's trigger asks, from the engine's own key
--     state this frame -- press: Keyboard/Mouse.pressed (the frame it goes
--     down; MBM's default), release: .released, hold: .button. nil for an id
--     not registered; false without a key.
--   Guards, asked only when a key is down, at most once a frame: the game
--     window has focus (Window.has_focus, proven: alt-tab moves it), and the
--     chat box is not open, read from the local body's animation as Helmet
--     Headlamp 1.6.0 does (likely, not proven: the wrist raised, layer 10 =
--     306, with layer 3 = 72). The native MBM's keys did not fire while
--     typing in chat (the user, 2026-10-08), so mods made for it have no
--     guard of their own.
-- Old MBM bindings are not carried over: their keys are in the game's input
-- settings under borrowed action names whose order is not known here.
-- Cost: nothing per frame until a mod asks; is_down is one engine call per
-- button the key names (one for a keyboard key), plus the guards on a frame
-- a key is down.
local MBB = { bindings = {}, buttons = {}, guard_frame = -1, guard_ok = true, count = 0,
              MOUSE = { ["mouse 3"] = { "middle button **", "middle", "middle button" },
                        ["mouse 4"] = { "x button 1 **", "extra_1", "x button 1" },
                        ["mouse 5"] = { "x button 2 **", "extra_2", "x button 2" } },
              FROM_MBM = { middle = "mouse 3", extra_1 = "mouse 4", extra_2 = "mouse 5" } }
MOM.frame = 0

-- name -> button id, per device, read once from button_name
function MBB.ids(device)
  local t = MBB.buttons[device]
  if t ~= nil then return t end
  t = {}
  local f = type(device) == "table" and rawget(device, "button_name") or nil
  if type(f) == "function" then
    for id = 0, 511 do
      local ok, n = pcall(f, id)
      if ok and type(n) == "string" and t[string.lower(n)] == nil then t[string.lower(n)] = id end
    end
  end
  MBB.buttons[device] = t
  return t
end

-- A key name -> { {device, id}, ... }: a keyboard name on the keyboard only
-- (the mouse has buttons named like keys), "mouse 3/4/5" on either device.
function MBB.resolve(name)
  if name == nil or name == "" then return nil end
  local sr = rawget(_G, "stingray")
  if type(sr) ~= "table" then return nil end
  local kb, mouse = rawget(sr, "Keyboard"), rawget(sr, "Mouse")
  local devices = MBB.MOUSE[name] and { kb, mouse } or { kb }
  local found = {}
  for _, n in ipairs(MBB.MOUSE[name] or { name }) do
    for _, d in ipairs(devices) do
      local id = type(d) == "table" and MBB.ids(d)[n] or nil
      if id ~= nil then found[#found + 1] = { d, id } end
    end
  end
  return found[1] and found or nil
end

-- One alternative of an MBM binding spec as the window's key name, and its
-- trigger; nil for what the window cannot hold (controller, modifiers,
-- wheel, left/right mouse).
function MBB.alternative(a)
  local button, device, trigger = a, "keyboard", "press"
  if type(a) == "table" then
    if type(a.modifiers) == "table" and a.modifiers[1] ~= nil then return nil end
    button, device, trigger = a.button, a.device or "keyboard", a.trigger or "press"
  end
  if type(button) ~= "string" or (trigger ~= "press" and trigger ~= "release" and trigger ~= "hold") then return nil end
  button = string.lower(button)
  if device == "mouse" then
    button = MBB.FROM_MBM[button]
    if button == nil then return nil end
  elseif device ~= "keyboard" then
    return nil
  end
  return button, trigger
end

function MBB.default(spec)
  if spec == nil then return "", "press" end
  local list = (type(spec) == "table" and spec.button == nil) and spec or { spec }
  for _, a in ipairs(list) do
    local name, trigger = MBB.alternative(a)
    if name ~= nil then return name, trigger end
  end
  return "", "press"
end

-- The local player's body, for the chat guard: the avatar (gl7xl2w, alive,
-- state 0) of the player object (un6y1d) this peer owns, and the rig with
-- attach_headlight within 3 m of it (as Helmet Headlamp binds its lamp).
-- The scan runs on a key press only, and its gap doubles while it finds
-- nothing.
local CHAT = { wrist = 306, layer3 = 72, wait = 0, gap = 60 }
function CHAT.ns(name)
  local sr = rawget(_G, "stingray")
  return type(sr) == "table" and rawget(sr, name) or nil
end
function CHAT.call(ns, name, ...)
  local f = type(ns) == "table" and rawget(ns, name) or nil
  if type(f) ~= "function" then return nil end
  local ok, v = pcall(f, ...)
  if ok then return v end
  return nil
end
function CHAT.avatar(gs, session, peer)
  local owned = CHAT.call(gs, "objects_owned_by", session, peer)
  if type(owned) ~= "table" then return nil end
  for _, goid in pairs(owned) do
    if CHAT.call(gs, "game_object_is_type", session, goid, "un6y1d") == true then
      local avatar = CHAT.call(gs, "game_object_field", session, goid, "baegche")
      if type(avatar) == "number" and avatar ~= 32767
         and CHAT.call(gs, "game_object_exists", session, avatar) == true
         and CHAT.call(gs, "game_object_is_type", session, avatar, "gl7xl2w") == true
         and CHAT.call(gs, "game_object_field", session, avatar, "state") == 0 then
        return avatar
      end
    end
  end
  return nil
end
function CHAT.body(gs, session, avatar)
  local unit = CHAT.ns("Unit")
  if CHAT.avatar_seen == avatar and CHAT.unit ~= nil and CHAT.call(unit, "alive", CHAT.unit) == true then
    return CHAT.unit
  end
  if CHAT.avatar_seen ~= avatar then CHAT.avatar_seen, CHAT.unit, CHAT.wait, CHAT.gap = avatar, nil, 0, 60 end
  if CHAT.wait > MOM.frame then return nil end
  local position = CHAT.call(gs, "game_object_field", session, avatar, "position")
  local worlds = CHAT.call(CHAT.ns("Application"), "worlds")
  local world_ns, vector = CHAT.ns("World"), CHAT.ns("Vector3")
  local best, nearest = nil, 3.0
  if position ~= nil and type(worlds) == "table" then
    for _, world in pairs(worlds) do
      local units = CHAT.call(world_ns, "units", world)
      if type(units) == "table" then
        for _, u in pairs(units) do
          if CHAT.call(unit, "alive", u) == true and CHAT.call(unit, "has_node", u, "attach_headlight") == true then
            local root = CHAT.call(unit, "world_position", u, 1)
            local d = root ~= nil and CHAT.call(vector, "distance", position, root) or nil
            if type(d) == "number" and d == d and d >= 0 and d <= nearest then best, nearest = u, d end
          end
        end
      end
    end
  end
  CHAT.unit = best
  if best == nil then
    CHAT.wait = MOM.frame + CHAT.gap
    CHAT.gap = CHAT.gap < 960 and CHAT.gap * 2 or 960
  end
  return best
end
-- Whether the chat box is open: false whenever it cannot be told (no
-- session, no living body, no animation), so the key goes through.
function CHAT.open()
  local gs, net = CHAT.ns("GameSession"), CHAT.ns("Network")
  local session, peer = CHAT.call(net, "game_session"), CHAT.call(net, "peer_id")
  if session == nil or peer == nil then return false end
  local avatar = CHAT.avatar(gs, session, peer)
  if avatar == nil then return false end
  local body = CHAT.body(gs, session, avatar)
  local unit = CHAT.ns("Unit")
  if body == nil or CHAT.call(unit, "has_animation_state_machine", body) ~= true then return false end
  local f = type(unit) == "table" and rawget(unit, "animation_get_state") or nil
  if type(f) ~= "function" then return false end
  local ok, _, _, layer3, _, _, _, _, _, _, layer10 = pcall(f, body)
  return ok == true and layer10 == CHAT.wrist and layer3 == CHAT.layer3
end

-- Whether a key may count this frame: the window has focus and the chat box
-- is closed; asked at most once a frame.
function MBB.allowed()
  if MBB.guard_frame == MOM.frame then return MBB.guard_ok end
  MBB.guard_frame = MOM.frame
  local ok = true
  local window = CHAT.ns("Window")
  local f = type(window) == "table" and rawget(window, "has_focus") or nil
  if type(f) == "function" then
    local okf, focus = pcall(f)
    if okf and focus == false then ok = false end
  end
  if ok then
    local okc, open = pcall(CHAT.open)
    if okc and open then
      ok = false
      if not MBB.said_chat then
        MBB.said_chat = true
        say("ModBindingsMenu: a key was held back while the chat box was open")
      end
    end
  end
  MBB.guard_ok = ok
  return ok
end

function MBB.is_down(id)
  local b = MBB.bindings[id]
  if b == nil then return nil end
  local name = MOM.values[id]
  if name ~= b.name then b.name, b.resolved = name, MBB.resolve(name) end
  if b.resolved == nil then return false end
  local down = false
  for _, button in ipairs(b.resolved) do
    local f = rawget(button[1], b.how)
    if type(f) == "function" then
      local ok, v = pcall(f, button[2])
      if ok and (v == true or (type(v) == "number" and v > 0.5)) then down = true break end
    end
  end
  return down and MBB.allowed()
end

-- register_binding(id, label, slot, options): the menu's checks; any slot is
-- taken (there are no native actions to share here).
function MBB.register(id, label, slot, options)
  if type(id) ~= "string" or id == "" or #id > 96 or string.find(id, "%c")
     or (slot ~= nil and type(slot) ~= "number") or (options ~= nil and type(options) ~= "table") then
    return false, "invalid binding registration"
  end
  options = options or {}
  local text = type(label) == "number" and id or MOM.text(label, 127)
  if text == nil then return false, "invalid binding registration" end
  local category = nil
  if options.category ~= nil then
    category = MOM.text(options.category, 64)
    if category == nil then return false, "invalid binding category" end
  end
  local had = MBB.bindings[id]
  if had ~= nil then
    if had.label == text and had.slot == slot then return true end
    return false, "binding already registered differently"
  end
  local addon = MOM.caller()
  local tail = addon and string.match(addon, "([%w_]+)$") or nil
  local default, trigger = MBB.default(options.default)
  local function bound()
    MBB.bindings[id] = { label = text, slot = slot, trigger = trigger,
                         how = trigger == "hold" and "button" or (trigger == "release" and "released" or "pressed") }
    MBB.count = MBB.count + 1
    MBB.api.revision = MBB.api.revision + 1
    MBB.api.extended.count = MBB.count
  end
  -- a key the mod's own list holds: read from its own settings file
  local own = tail ~= nil and MOM.dir ~= nil and MOM.listed(tail, id) or nil
  if own ~= nil then
    local done, why = MOM.serve_own(tail, own, { id = id, kind = "key", label = text, default = default })
    if done then bound() end
    return done, why
  end
  local gkey = "mbm:" .. (addon or category or "?")
  local g = MOM.groups[gkey]
  if g == nil then
    if #MOM.list >= MOM.MAX_MODS then return false, MOM.full("all " .. MOM.MAX_MODS .. " mod categories are in use") end
    local title = category or (tail and (string.gsub(tail, "_", " "))) or "Unnamed mod"
    local base = string.match(string.gsub(string.lower(tail or title), "[^%w]+", "_"), "^_*(.-)_*$")
    local name = "mbm_" .. (base ~= "" and base or "mod")
    while MOM.names[name] do name = name .. "_" end
    MOM.names[name] = true
    g = { key = gkey, title = title, name = name, addon = addon, keys = true, options = {}, by_key = {}, dirty = true,
          write = false, file_values = MOM.dir and MOM.read_values(MOM.dir .. name .. "_settings.txt") or {},
          migrated = 0 }
    MOM.groups[gkey] = g
    MOM.list[#MOM.list + 1] = g
  end
  MOM.note(tail, g.name, id)
  local key = string.gsub(id, "[^%w_]", "_")
  while g.by_key[key] ~= nil do key = key .. "_" end
  local o = { id = id, kind = "key", label = text, default = default, key = key, group = g }
  g.options[#g.options + 1] = o
  g.by_key[key] = o
  MOM.options[id] = o
  local v = MOM.decode(o, g.file_values[key])
  if v == nil then v, g.write = default, true end
  MOM.values[id] = v
  bound()
  g.dirty = true
  L.hook_update()
  return true
end

-- set_binding(id, spec): {} clears, nil restores the default.
function MBB.set_binding(id, spec)
  local b = MBB.bindings[id]
  if b == nil then return false, "not an extended binding" end
  local o = MOM.options[id]
  local name
  if spec == nil then name = o.default
  elseif type(spec) == "table" and spec.button == nil and spec[1] == nil then name = ""
  else
    name = MBB.default(spec)
    if name == "" then return false, "invalid binding" end
  end
  if MOM.values[id] ~= name then
    MOM.values[id] = name
    o.group.write = true
  end
  return true
end

MBB.api = {
  version = 4, capacity = 1000, revision = 0,          -- the native menu had 36 slots; none here
  loader = MARKER, window = "HD2 Mod Setting GUI",
  extended = { devices = { "keyboard", "mouse" }, triggers = { "press", "release", "hold" }, count = 0 },
  register_binding = function(id, label, slot, options)
    local ok, done, why = pcall(MBB.register, id, label, slot, options)
    if not ok then return false, tostring(done) end
    return done, why
  end,
  is_down = function(id) return MBB.is_down(id) end,
  ready = function() return true end,
  -- poll(ids, out): is_down for each, with press/release since this table's last poll
  poll = function(ids, out)
    if type(ids) ~= "table" or type(out) ~= "table" then return false, "invalid poll arguments" end
    for _, field in ipairs({ "down", "pressed", "released", "last" }) do
      if out[field] == nil then out[field] = {} elseif type(out[field]) ~= "table" then
        return false, "invalid poll arguments"
      end
    end
    for i = 1, #ids do
      local down = MBB.is_down(ids[i])
      local was = out.last[i] == true
      out.down[i] = down
      out.pressed[i] = down == true and not was
      out.released[i] = was and down ~= true
      out.last[i] = down == true
    end
    return true
  end,
  set_binding = function(id, spec) return MBB.set_binding(id, spec) end,
  binding = function(id)
    local b = MBB.bindings[id]
    if b == nil then return nil end
    local name = MOM.values[id]
    if name == nil or name == "" then return {} end
    local mouse = MBB.MOUSE[name] ~= nil
    local button = name
    if mouse then for k, v in pairs(MBB.FROM_MBM) do if v == name then button = k end end end
    return { { mouse and "mouse" or "keyboard", button, modifiers = {}, trigger = b.trigger } }
  end,
  binding_info = function(id)
    local b = MBB.bindings[id]
    if b == nil then return nil end
    local name, o = MOM.values[id], MOM.options[id]
    return { backend = "extended", state = (name == "" and o.default ~= "") and "cleared"
                                          or (name == o.default and "default" or "set") }
  end,
}

-- `api` is 1, or nil to a mod with its own window list (MOM.api_for, above)
local API = { __index = function(_, k) if k == "api" then return MOM.api_for() end end }
setmetatable(MBB.api, API)
if rawget(_G, "ModOptionsMenu") == nil then
  rawset(_G, "ModOptionsMenu", setmetatable({
    version = 3, max_mods = MOM.MAX_MODS, max_options = MOM.MAX_OPTIONS,
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
  }, API))
end
if rawget(_G, "ModBindingsMenu") == nil then
  rawset(_G, "ModBindingsMenu", MBB.api)
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
  if chunk ~= nil then pcall(MOM.hide_from, entry.name, source) end
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
  local swept, why = pcall(MOM.sweep, list)
  if not swept then say("settings window lists: " .. tostring(why)) end
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
