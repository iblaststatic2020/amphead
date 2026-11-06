-- Hermes AmpHead bridge, protocol v2.
-- Source of truth lives in the plugin repo (reaper/bridge.lua); the copy under
-- REAPER/Scripts/hermes_bridge is installed by amphead/install_bridge.py.
--
-- Wire format: request.txt holds one line
--     <id>|<CMD>|<key>=<value>|<key>=<value>...
-- Every value is percent-encoded on the Python side, so '|', '=', ',', ';' and ':'
-- can never appear raw. Responses: out/resp_<id>.json.

local PROTO = 2
local VERSION = "2.0.0"

local dir = reaper.GetResourcePath() .. "/Scripts/hermes_bridge"
reaper.RecursiveCreateDirectory(dir .. "/out", 0)
local req = dir .. "/request.txt"
local outdir = dir .. "/out"

local clip_seen = false
local tx_open = false
local tx_label = "Hermes"
local tx_deadline = 0
local rig_project = nil          -- session-scoped pointer to the project we built

-- ---------------------------------------------------------------------------
-- encoding
-- ---------------------------------------------------------------------------

local function esc(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\")                 -- one backslash -> the JSON escape pair
  s = s:gsub('"', '\\"')                     -- quote -> \"
  s = s:gsub("\n", "\\n"):gsub("\r", "")   -- newlines escaped, carriage returns dropped
  return s
end

local function jstr(s) return '"' .. esc(s) .. '"' end

local function urldec(s)
  return (s:gsub("%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end))
end

local function urlenc(s)
  return (tostring(s or ""):gsub("[^%w%._%-/]", function(c)
    return string.format("%%%02X", string.byte(c))
  end))
end

local function bool(b) return b and "true" or "false" end

local function num(n, dp)
  if n == nil then return "null" end
  return string.format("%." .. (dp or 4) .. "f", n)
end

-- ---------------------------------------------------------------------------
-- project helpers
-- ---------------------------------------------------------------------------

local function active_project() return reaper.EnumProjects(-1, "") end

local function project_name(pr)
  local ok, nm = reaper.GetSetProjectInfo_String(pr, "PROJECT_NAME", "", false)
  if ok then return nm or "" end
  return ""
end

-- The project's real path. EnumProjects is the only source that hands back the filename
-- REAPER itself holds; GetProjectPath returns a directory (often the recording path) and
-- gluing the two together invented paths that no file had, which is how a guard silently
-- starts comparing garbage.
local function project_path(pr)
  if not pr then return "", "none" end
  for i = 0, 255 do
    local p, filename = reaper.EnumProjects(i, "")
    if not p then break end
    if p == pr then
      if filename and filename ~= "" then return filename, "enum" end
      return "", "unnamed"
    end
  end
  return "", "not-open"
end

local function normpath(p)
  p = tostring(p or ""):gsub("\\", "/")
  return p:lower()
end

-- Guard used by every mutating command.
--   expect=<path>   -> active project path must match (case and slash insensitive)
--   new=1           -> caller created this unnamed project in the same batch
local function guard(args)
  local pr = active_project()
  if not pr then return false, "no active project" end
  if args.new == "1" then return true end
  local actual = project_path(pr)
  if args.expect == nil or args.expect == "" then
    -- An unnamed project could be anyone's unsaved song, so it needs the caller to say
    -- "I created this tab" (new=1). Without that, refuse.
    if actual == "" then
      return false, "unnamed project: create the rig tab first and pass new=1"
    end
    return false, "guard required: active project is " .. actual
  end
  if normpath(actual) == normpath(args.expect) then return true end
  return false, "wrong project: active is " .. actual .. ", expected " .. args.expect
end

local function err(msg, extra)
  local parts = { jstr("ok") .. ":false", jstr("error") .. ":" .. jstr(msg) }
  if extra then
    for _, pair in ipairs(extra) do
      parts[#parts + 1] = jstr(pair[1]) .. ":" .. jstr(pair[2])
    end
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

local function ok_json(fields)
  local parts = { jstr("ok") .. ":true" }
  for _, pair in ipairs(fields) do
    parts[#parts + 1] = jstr(pair[1]) .. ":" .. pair[2]
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

-- ---------------------------------------------------------------------------
-- undo transactions
-- ---------------------------------------------------------------------------

local function tx_begin(label)
  if tx_open then return false end
  tx_open = true
  tx_label = label or "Hermes"
  tx_deadline = reaper.time_precise() + 10.0
  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)
  return true
end

local function tx_end()
  if not tx_open then return false end
  tx_open = false
  tx_deadline = 0
  reaper.PreventUIRefresh(-1)
  reaper.Undo_EndBlock(tx_label, -1)
  return true
end

-- Every mutating command calls this pair. Inside a transaction the outer block owns
-- the undo point; outside, the command makes one of its own.
local function open_undo(label)
  if tx_open then return false end
  reaper.Undo_BeginBlock()
  return true
end

local function close_undo(label, own)
  if own then reaper.Undo_EndBlock(label, -1) end
end

-- ---------------------------------------------------------------------------
-- track / fx helpers
-- ---------------------------------------------------------------------------

local function track_at(idx)
  return reaper.GetTrack(0, (tonumber(idx) or 1) - 1)
end

local function track_name(tr)
  if not tr then return "" end
  local _, name = reaper.GetTrackName(tr, "")
  return name or ""
end

local function fx_name(tr, fx)
  if not tr then return "" end
  local _, name = reaper.TrackFX_GetFXName(tr, fx, "")
  return name or ""
end

local function db_of(vol)
  if not vol or vol <= 0 then return -150 end
  return 20 * math.log(vol, 10)
end

-- ---------------------------------------------------------------------------
-- commands
-- ---------------------------------------------------------------------------

local COMMANDS = {}

COMMANDS.PING = function(args)
  local names = {}
  for k in pairs(COMMANDS) do names[#names + 1] = k end
  table.sort(names)
  local listed = {}
  for _, n in ipairs(names) do listed[#listed + 1] = jstr(n) end
  return "{" .. jstr("ok") .. ":true," .. jstr("proto") .. ":" .. PROTO ..
    "," .. jstr("bridge") .. ":" .. jstr(VERSION) ..
    "," .. jstr("version") .. ":" .. jstr(reaper.GetAppVersion()) ..
    "," .. jstr("script") .. ":" .. jstr(dir) ..
    "," .. jstr("cmds") .. ":[" .. table.concat(listed, ",") .. "]" ..
    "," .. jstr("removed") .. ":[" .. jstr("TX_BEGIN") .. "," .. jstr("TX_END") .. "]}"
end

COMMANDS.STATUS = function(args)
  local pr = active_project()
  local path, how = project_path(pr)
  return ok_json({
    { "project", jstr(project_name(pr)) },
    { "path", jstr(path) },
    { "path_method", jstr(how) },
    { "tracks", tostring(reaper.CountTracks(0)) },
    { "tempo", num(reaper.Master_GetTempo(), 2) },
    { "tx_open", bool(tx_open) },
    { "rig_marked", bool(rig_project ~= nil and pr == rig_project) },
  })
end

COMMANDS.TABS = function(args)
  local active = active_project()
  local items = {}
  for i = 0, 63 do
    local pr = reaper.EnumProjects(i, "")
    if not pr then break end
    local path = project_path(pr)
    items[#items + 1] = "{" .. jstr("i") .. ":" .. i ..
      "," .. jstr("project") .. ":" .. jstr(project_name(pr)) ..
      "," .. jstr("path") .. ":" .. jstr(path) ..
      "," .. jstr("tracks") .. ":" .. tostring(reaper.CountTracks(pr)) ..
      "," .. jstr("active") .. ":" .. bool(pr == active) .. "}"
  end
  return ok_json({
    { "count", tostring(#items) },
    { "tabs", "[" .. table.concat(items, ",") .. "]" },
  })
end

COMMANDS.ACTION = function(args)
  local id = tonumber(args.id) or 0
  reaper.Main_OnCommand(id, 0)
  return ok_json({ { "action", tostring(id) } })
end

COMMANDS.UNDOSTACK = function(args)
  local n = tonumber(args.n) or 5
  local items = {}
  for i = 0, n - 1 do
    local _, desc = reaper.Undo_CanUndo2(0, i)
    items[#items + 1] = jstr(desc or "")
  end
  return ok_json({ { "stack", "[" .. table.concat(items, ",") .. "]" } })
end

COMMANDS.UNDO = function(args)
  if tx_open then return err("transaction open, close it first") end
  reaper.Undo_DoUndo2(0)
  return ok_json({ { "undone", "true" } })
end

-- Measured on REAPER 7.77: Undo_BeginBlock in one script invocation and Undo_EndBlock in
-- the next produces no undo point at all, so the writes were recoverable only by undoing
-- something earlier (in the worst case, deleting the FX the rig was built on). Grouped
-- writes therefore have exactly one form: BATCH, one round trip, one invocation.
COMMANDS.TX_BEGIN = function(args)
  return err("REAPER cannot hold an undo block between script runs; send the writes as one BATCH")
end

COMMANDS.TX_END = function(args)
  return err("no transaction is open; grouped writes are one BATCH")
end

COMMANDS.TRACKS = function(args)
  local n = reaper.CountTracks(0)
  local items = {}
  for i = 0, n - 1 do
    items[#items + 1] = jstr(track_name(reaper.GetTrack(0, i)))
  end
  return ok_json({ { "count", tostring(n) }, { "names", "[" .. table.concat(items, ",") .. "]" } })
end

COMMANDS.TRACK_ADD = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local own = open_undo("Hermes: add track")
  reaper.InsertTrackAtIndex(reaper.CountTracks(0), true)
  local tr = reaper.GetTrack(0, reaper.CountTracks(0) - 1)
  if args.name and args.name ~= "" then
    reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", args.name, true)
  end
  -- A track Hermes makes is born dead. REAPER's defaults can arrive record-monitoring on,
  -- and a live amp sim on a live track is the exact thing the user has to switch on themselves.
  reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", 0)
  reaper.SetMediaTrackInfo_Value(tr, "I_RECMON", 0)
  close_undo("Hermes: add track", own)
  return ok_json({
    { "index", tostring(reaper.CountTracks(0)) },
    { "name", jstr(track_name(tr)) },
    { "armed", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") ~= 0) },
    { "monitor", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECMON") ~= 0) },
  })
end

COMMANDS.TRACK_DEL = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local idx = tonumber(args.track) or 1
  local tr = track_at(idx)
  if not tr then return err("no track " .. idx) end
  local nm = track_name(tr)
  local own = open_undo("Hermes: delete track")
  reaper.DeleteTrack(tr)
  close_undo("Hermes: delete track", own)
  return ok_json({ { "deleted", jstr(nm) }, { "tracks", tostring(reaper.CountTracks(0)) } })
end

COMMANDS.TRACK_INFO = function(args)
  local idx = tonumber(args.track) or 1
  local tr = track_at(idx)
  if not tr then return err("no track " .. idx) end
  local nfx = reaper.TrackFX_GetCount(tr)
  local chain = {}
  for i = 0, nfx - 1 do
    chain[#chain + 1] = "{" .. jstr("i") .. ":" .. i ..
      "," .. jstr("name") .. ":" .. jstr(fx_name(tr, i)) ..
      "," .. jstr("bypassed") .. ":" .. bool(reaper.TrackFX_GetEnabled(tr, i) ~= true) .. "}"
  end
  local vol = reaper.GetMediaTrackInfo_Value(tr, "D_VOL")
  return ok_json({
    { "index", tostring(idx) },
    { "name", jstr(track_name(tr)) },
    { "mute", bool(reaper.GetMediaTrackInfo_Value(tr, "B_MUTE") ~= 0) },
    { "solo", bool(reaper.GetMediaTrackInfo_Value(tr, "I_SOLO") ~= 0) },
    { "armed", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") ~= 0) },
    { "monitor", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECMON") ~= 0) },
    { "recinput", tostring(reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT")) },
    { "vol", num(vol, 4) },
    { "db", num(db_of(vol), 2) },
    { "pan", num(reaper.GetMediaTrackInfo_Value(tr, "D_PAN"), 3) },
    { "fx_count", tostring(nfx) },
    { "chain", "[" .. table.concat(chain, ",") .. "]" },
  })
end

COMMANDS.TRACK_VOL = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local v
  if args.db ~= nil then v = 10 ^ ((tonumber(args.db) or 0) / 20)
  elseif args.vol ~= nil then v = tonumber(args.vol) or 1
  else return err("need db= or vol=") end
  local own = open_undo("Hermes: track volume")
  reaper.SetMediaTrackInfo_Value(tr, "D_VOL", v)
  close_undo("Hermes: track volume", own)
  local cur = reaper.GetMediaTrackInfo_Value(tr, "D_VOL")
  return ok_json({ { "vol", num(cur, 4) }, { "db", num(db_of(cur), 2) } })
end

-- Arming a track with an amp plugin on it is the moment a rig turns its speakers into a
-- microphone. The master goes down first, the floor is verified before the track is armed,
-- and if it cannot be verified the arming is refused rather than done loudly.
ARM_SAFE_MASTER_DB = -60.0

COMMANDS.ARM = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  -- Arming is the user's decision, never a side effect of building a rig. The pane's
  -- LIVE switch sends confirm=1; an agent that tries it on its own gets refused.
  if args.confirm ~= "1" then
    return err("arming needs confirm=1: the Live switch is the user's, not the builder's")
  end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local mtr = reaper.GetMasterTrack(0)
  if not mtr then return err("no master track") end
  local before = reaper.GetMediaTrackInfo_Value(mtr, "D_VOL")
  local before_db = db_of(before)
  local lowered = false
  if args.allow_loud ~= "1" and before_db > ARM_SAFE_MASTER_DB then
    local own = open_undo("Hermes: master down for arming")
    reaper.SetMediaTrackInfo_Value(mtr, "D_VOL", 10 ^ (ARM_SAFE_MASTER_DB / 20))
    close_undo("Hermes: master down for arming", own)
    local now_db = db_of(reaper.GetMediaTrackInfo_Value(mtr, "D_VOL"))
    if now_db > ARM_SAFE_MASTER_DB + 0.5 then
      return err(string.format(
        "refusing to arm: master is at %.1f dB and would not go down (reads %.1f dB)",
        before_db, now_db), {
        { "master_db_before", num(before_db, 2) }, { "master_db_after", num(now_db, 2) } })
    end
    lowered = true
  end
  local own = open_undo("Hermes: arm track")
  reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", 1)
  if args.monitor ~= "0" then reaper.SetMediaTrackInfo_Value(tr, "I_RECMON", 1) end
  close_undo("Hermes: arm track", own)
  return ok_json({
    { "armed", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") ~= 0) },
    { "monitor", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECMON") ~= 0) },
    { "master_db_before", num(before_db, 2) },
    { "master_db_after", num(db_of(reaper.GetMediaTrackInfo_Value(mtr, "D_VOL")), 2) },
    { "master_safety", jstr("lowered") },
    { "lowered", bool(lowered) },
  })
end

COMMANDS.SET_INPUT = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local want = tonumber(args.input)
  if want == nil then return err("need input=") end
  -- REAPER stores any number you hand I_RECINPUT, including values that select nothing, so
  -- the range check is ours: 0 = none, 1..N = mono channel, 1024.. = stereo pair.
  local inputs = reaper.GetNumAudioInputs()
  local max_pair = 1024 + math.max(0, math.floor(inputs / 2) - 1)
  local valid = (want == 0) or (want >= 1 and want <= inputs) or (want >= 1024 and want <= max_pair)
  if not valid then
    return err(string.format(
      "input %s is not available on this device (%d inputs): 0 = none, 1..%d = mono channel, 1024..%d = stereo pair",
      tostring(want), inputs, inputs, max_pair),
      { { "inputs", tostring(inputs) }, { "requested", tostring(want) } })
  end
  local own = open_undo("Hermes: set input")
  reaper.SetMediaTrackInfo_Value(tr, "I_RECINPUT", want)
  close_undo("Hermes: set input", own)
  return ok_json({
    { "recinput", tostring(reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT")) },
    { "recmode", tostring(reaper.GetMediaTrackInfo_Value(tr, "I_RECMODE")) },
    { "requested", tostring(want) },
    { "applied", bool(tonumber(reaper.GetMediaTrackInfo_Value(tr, "I_RECINPUT")) == want) },
  })
end

COMMANDS.INPUTS = function(args)
  local n = reaper.GetNumAudioInputs()
  local names = {}
  for i = 0, math.min(n, 16) - 1 do
    local nm = "input " .. (i + 1)
    if reaper.GetInputChannelName then
      local ok, got = pcall(reaper.GetInputChannelName, i)
      if ok and type(got) == "string" and got ~= "" then nm = got end
    end
    names[#names + 1] = "{" .. jstr("i") .. ":" .. i .. "," .. jstr("name") .. ":" .. jstr(nm) .. "}"
  end
  return ok_json({
    { "count", tostring(n) },
    { "channels", "[" .. table.concat(names, ",") .. "]" },
    { "has_channel_names", bool(reaper.GetInputChannelName ~= nil) },
  })
end

COMMANDS.FX_ADD = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local want = args.name
  if not want or want == "" then return err("need name=") end
  local own = open_undo("Hermes: add fx")
  local fx = reaper.TrackFX_AddByName(tr, want, false, -1)
  close_undo("Hermes: add fx", own)
  if fx == nil or fx < 0 then return err("fx not found", { { "query", want } }) end
  return ok_json({
    { "fx", tostring(fx) },
    { "name", jstr(fx_name(tr, fx)) },
    { "chain_len", tostring(reaper.TrackFX_GetCount(tr)) },
  })
end

COMMANDS.FX_LIST = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local n = reaper.TrackFX_GetCount(tr)
  local items = {}
  for i = 0, n - 1 do
    items[#items + 1] = "{" .. jstr("i") .. ":" .. i ..
      "," .. jstr("name") .. ":" .. jstr(fx_name(tr, i)) ..
      "," .. jstr("enabled") .. ":" .. bool(reaper.TrackFX_GetEnabled(tr, i) == true) .. "}"
  end
  return ok_json({ { "count", tostring(n) }, { "fx", "[" .. table.concat(items, ",") .. "]" } })
end

COMMANDS.FX_PARAMS = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local fx = tonumber(args.fx) or 0
  local n = reaper.TrackFX_GetNumParams(tr, fx)
  local keep = tonumber(args.keep) or 64
  local items = {}
  for i = 0, math.min(n, keep) - 1 do
    local _, nm = reaper.TrackFX_GetParamName(tr, fx, i, "")
    local _, fmt = reaper.TrackFX_GetFormattedParamValue(tr, fx, i, "")
    -- norm and raw ride along so a snapshot can record every knob in one sweep instead of one
    -- FX_INFO round trip per parameter.
    local norm = reaper.TrackFX_GetParamNormalized(tr, fx, i)
    local raw = reaper.TrackFX_GetParam(tr, fx, i)
    items[#items + 1] = "{" .. jstr("i") .. ":" .. i ..
      "," .. jstr("name") .. ":" .. jstr(nm) ..
      "," .. jstr("fmt") .. ":" .. jstr(fmt) ..
      "," .. jstr("norm") .. ":" .. string.format("%.6f", norm == norm and norm or 0) ..
      "," .. jstr("raw") .. ":" .. string.format("%.6f", raw == raw and raw or 0) .. "}"
  end
  return ok_json({
    { "count", tostring(n) },
    { "fx", tostring(fx) },
    { "fx_name", jstr(fx_name(tr, fx)) },
    { "params", "[" .. table.concat(items, ",") .. "]" },
  })
end

-- num_params=0 is common until a plugin's UI has been opened once. This opens it and re-reads.
COMMANDS.FX_WAKE = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local fx = tonumber(args.fx) or 0
  local before = reaper.TrackFX_GetNumParams(tr, fx)
  if before > 0 then
    return ok_json({ { "before", tostring(before) }, { "after", tostring(before) }, { "woke", "false" } })
  end
  reaper.TrackFX_Show(tr, fx, 3)
  local after = reaper.TrackFX_GetNumParams(tr, fx)
  if args.keep_open ~= "1" then reaper.TrackFX_Show(tr, fx, 0) end
  return ok_json({ { "before", tostring(before) }, { "after", tostring(after) }, { "woke", "true" } })
end

-- Read-only: the raw state of one FX, so the caller can see what a plugin actually stores.
-- Plugin state is the only place a NAM model path can live, because none of these plugins
-- expose the capture as a parameter.
COMMANDS.FX_CHUNK = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local want = tonumber(args.fx) or 0
  local _, chunk = reaper.GetTrackStateChunk(tr, "", false)
  if not chunk then return err("no state chunk") end
  local lines = {}
  for line in (chunk .. "\n"):gmatch("(.-)\n") do lines[#lines + 1] = line end
  local seen, i = -1, 1
  while i <= #lines do
    local line = lines[i]
    if line:match("^<VST ") or line:match("^<JS ") or line:match("^<CLAP ") or line:match("^<AU ") then
      seen = seen + 1
      if seen == want then
        local last = i + 1
        while last <= #lines and not lines[last]:match("^>") do last = last + 1 end
        local block, payload = {}, ""
        for k = i, last do
          block[#block + 1] = lines[k]
          if k > i and not lines[k]:match("^>") then payload = payload .. lines[k] end
        end
        return ok_json({ { "fx", tostring(want) }, { "kind", jstr((line:match("^<(\a+)"))) },
                         { "header", jstr(line) }, { "chunk", jstr(payload) },
                         { "chunk_len", tostring(#payload) },
                         { "lines", tostring(last - i + 1) },
                         { "block", jstr(table.concat(block, "\n")) } })
      end
      -- skip to the end of this block
      i = i + 1
      while i <= #lines and not lines[i]:match("^>") do i = i + 1 end
    end
    i = i + 1
  end
  return err("no fx at index " .. want .. " (found " .. tostring(seen + 1) .. ")")
end

-- Write an FX's raw state back. This is how a NAM capture is loaded: the model path and every
-- parameter live in the plugin's state, and none of it is reachable as a host parameter.
-- The caller supplies base64 that replaces the payload lines; the header line (which carries the
-- VST3 GUID) and the closing ">" are kept exactly as REAPER wrote them.
COMMANDS.FX_SETCHUNK = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local want = tonumber(args.fx) or 0
  local payload_b64 = args.chunk or ""
  if #payload_b64 < 8 then return err("no chunk given") end
  local _, chunk = reaper.GetTrackStateChunk(tr, "", false)
  if not chunk then return err("no state chunk") end
  local lines = {}
  for line in (chunk .. "\n"):gmatch("(.-)\n") do lines[#lines + 1] = line end
  local seen, i = -1, 1
  while i <= #lines do
    local line = lines[i]
    if line:match("^<VST ") or line:match("^<JS ") or line:match("^<CLAP ") or line:match("^<AU ") then
      seen = seen + 1
      if seen == want then
        local last = i + 1
        while last <= #lines and not lines[last]:match("^>") do last = last + 1 end
        if last > #lines then return err("unterminated fx block") end
        local out = {}
        for k = 1, i do out[#out + 1] = lines[k] end
        -- REAPER writes VST state as 128-character base64 lines and its project parser only
        -- accepts that width: shorter lines load the plugin with default state instead.
        local pos = 1
        while pos <= #payload_b64 do
          out[#out + 1] = "        " .. payload_b64:sub(pos, pos + 127)
          pos = pos + 128
        end
        for k = last, #lines do out[#out + 1] = lines[k] end
        local rebuilt = table.concat(out, "\n") .. "\n"
        local ok = reaper.SetTrackStateChunk(tr, rebuilt, false)
        reaper.UpdateArrange()
        -- Read the track back: SetTrackStateChunk can return true while the plugin rejects the
        -- state and REAPER quietly keeps the old bytes. The caller needs to know which happened.
        local _, after = reaper.GetTrackStateChunk(tr, "", false)
        -- Pull the FX block back out of the re-read chunk and report where it differs from what
        -- was sent, so the caller can tell "REAPER ignored me" from "the plugin rejected it".
        local alines = {}
        for line in ((after or "") .. "\n"):gmatch("(.-)\n") do alines[#alines + 1] = line end
        local got, n = "", -1
        for k = 1, #alines do
          if alines[k]:match("^<VST ") or alines[k]:match("^<JS ") or alines[k]:match("^<CLAP ") or alines[k]:match("^<AU ") then
            n = n + 1
            if n == want then
              local m = k + 1
              while m <= #alines and not alines[m]:match("^>") do
                got = got .. alines[m]
                m = m + 1
              end
              break
            end
          end
        end
        local sent_flat, got_flat = payload_b64:gsub("%s", ""), got:gsub("%s", "")
        local diff = 0
        local span = math.min(#sent_flat, #got_flat)
        for k = 1, span do
          if sent_flat:sub(k, k) ~= got_flat:sub(k, k) then diff = k; break end
        end
        if diff == 0 and #sent_flat ~= #got_flat then diff = span + 1 end
        return ok_json({ { "applied", ok and "true" or "false" },
                         { "kept", (diff == 0) and "true" or "false" },
                         { "sent_len", tostring(#sent_flat) },
                         { "got_len", tostring(#got_flat) },
                         { "first_diff", tostring(diff) },
                         { "fx", tostring(want) } })
      end
      i = i + 1
      while i <= #lines and not lines[i]:match("^>") do i = i + 1 end
    end
    i = i + 1
  end
  return err("no fx at index " .. want)
end

-- Force a plugin to re-instantiate from its stored state (offline then back online).
COMMANDS.FX_RELOAD = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local fx = tonumber(args.fx) or 0
  local name = reaper.TrackFX_GetFXName(tr, fx, "")
  reaper.TrackFX_SetOffline(tr, fx, true)
  reaper.TrackFX_SetOffline(tr, fx, false)
  reaper.UpdateArrange()
  return ok_json({ { "fx", tostring(fx) }, { "name", jstr(name) } })
end

COMMANDS.FX_INFO = function(args)
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local fx = tonumber(args.fx) or 0
  local p = tonumber(args.param) or 0
  local n = reaper.TrackFX_GetNumParams(tr, fx)
  if p < 0 or p >= n then
    return err("no param " .. p, { { "count", tostring(n) } })
  end
  local _, nm = reaper.TrackFX_GetParamName(tr, fx, p, "")
  local raw = reaper.TrackFX_GetParam(tr, fx, p)
  local _, mn, mx = reaper.TrackFX_GetParamEx(tr, fx, p)
  local norm = reaper.TrackFX_GetParamNormalized(tr, fx, p)
  local _, fmt = reaper.TrackFX_GetFormattedParamValue(tr, fx, p, "")
  return ok_json({
    { "fx", tostring(fx) },
    { "fx_name", jstr(fx_name(tr, fx)) },
    { "param", tostring(p) },
    { "name", jstr(nm) },
    { "norm", num(norm, 6) },
    { "raw", num(raw, 6) },
    { "min", num(mn, 6) },
    { "max", num(mx, 6) },
    { "fmt", jstr(fmt) },
    { "count", tostring(n) },
  })
end

local function write_param(tr, fx, p, value, normalized)
  local own = open_undo("Hermes: set param")
  if normalized then
    reaper.TrackFX_SetParamNormalized(tr, fx, p, value)
  else
    reaper.TrackFX_SetParam(tr, fx, p, value)
  end
  close_undo("Hermes: set param", own)
  local norm = reaper.TrackFX_GetParamNormalized(tr, fx, p)
  local raw = reaper.TrackFX_GetParam(tr, fx, p)
  local _, fmt = reaper.TrackFX_GetFormattedParamValue(tr, fx, p, "")
  return ok_json({
    { "norm", num(norm, 6) },
    { "raw", num(raw, 6) },
    { "fmt", jstr(fmt) },
    { "mode", jstr(normalized and "norm" or "raw") },
  })
end

COMMANDS.FX_SETV = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local v = tonumber(args.value)
  if v == nil then return err("need value=") end
  return write_param(track_at(args.track), tonumber(args.fx) or 0, tonumber(args.param) or 0, v, args.mode == "norm")
end

COMMANDS.FX_SET = function(args)
  args.mode = "norm"
  return COMMANDS.FX_SETV(args)
end

COMMANDS.FX_BYPASS = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local fx = tonumber(args.fx) or 0
  local own = open_undo("Hermes: bypass fx")
  reaper.TrackFX_SetEnabled(tr, fx, args.on ~= "1")
  close_undo("Hermes: bypass fx", own)
  return ok_json({ { "bypassed", bool(reaper.TrackFX_GetEnabled(tr, fx) ~= true) } })
end

COMMANDS.FX_DEL = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local fx = tonumber(args.fx) or 0
  local nm = fx_name(tr, fx)
  local own = open_undo("Hermes: delete fx")
  reaper.TrackFX_Delete(tr, fx)
  close_undo("Hermes: delete fx", own)
  return ok_json({ { "deleted", jstr(nm) }, { "chain_len", tostring(reaper.TrackFX_GetCount(tr)) } })
end

COMMANDS.FX_MOVE = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local own = open_undo("Hermes: move fx")
  reaper.TrackFX_CopyToTrack(tr, tonumber(args.fx) or 0, tr, tonumber(args.to) or 0, true)
  close_undo("Hermes: move fx", own)
  return ok_json({ { "moved", "true" }, { "chain_len", tostring(reaper.TrackFX_GetCount(tr)) } })
end

COMMANDS.DEVINFO = function(args)
  local function dev(attr)
    local ok, txt = reaper.GetAudioDeviceInfo(attr, "")
    if ok and type(txt) == "string" then return txt end
    return ""
  end
  local total, out = 0, 0
  if reaper.GetInputOutputLatency then
    local a, b = reaper.GetInputOutputLatency()
    total = tonumber(a) or 0
    out = tonumber(b) or 0
  end
  local caps, samp = 0, 0
  local srate_num = tonumber(dev("SRATE")) or 0
  if reaper.GetOutputLatency then
    local ok1, c = pcall(reaper.GetOutputLatency)
    if ok1 then caps = tonumber(c) or 0 end
  end
  -- GetProjectSampleRate is missing in some builds; GetSetProjectInfo covers all of them.
  if reaper.GetProjectSampleRate then
    local ok1, v = pcall(reaper.GetProjectSampleRate)
    if ok1 then samp = tonumber(v) or 0 end
  end
  if samp == 0 and reaper.GetSetProjectInfo then
    local ok2, v = pcall(reaper.GetSetProjectInfo, 0, "PROJECT_SRATE", 0, false)
    if ok2 then samp = tonumber(v) or 0 end
  end
  return ok_json({
    { "mode", jstr(dev("MODE")) },
    { "device_closed", bool(dev("MODE") == "") },
    { "ident_in", jstr(dev("IDENT_IN")) },
    { "bsize", jstr(dev("BSIZE")) },
    { "srate", jstr(dev("SRATE")) },
    { "bps", jstr(dev("BPS")) },
    { "device_latency_ms", num(total, 2) },
    { "output_latency_samples", num(out, 0) },
    { "output_latency_ms", num(srate_num > 0 and out * 1000 / srate_num or 0, 2) },
    { "project_srate", num(samp, 1) },
    { "audio_running", bool(reaper.Audio_IsRunning()) },
    { "inputs", tostring(reaper.GetNumAudioInputs()) },
    { "outputs", tostring(reaper.GetNumAudioOutputs()) },
  })
end

COMMANDS.DEV_REOPEN = function(args)
  -- ASIO is single-client: a killed REAPER can leave the driver wedged, and a fresh
  -- launch then comes up with the device closed. Reopening is the only fix from here.
  local before = reaper.Audio_IsRunning()
  if reaper.Audio_Quit then pcall(reaper.Audio_Quit) end
  if reaper.Audio_Init then pcall(reaper.Audio_Init) end
  local ok, mode = reaper.GetAudioDeviceInfo("MODE", "")
  return ok_json({
    { "was_running", bool(before) },
    { "audio_running", bool(reaper.Audio_IsRunning()) },
    { "mode", jstr(mode or "") },
    { "inputs", tostring(reaper.GetNumAudioInputs()) },
  })
end

COMMANDS.METER = function(args)
  local tr = track_at(args.track)
  local mtr = reaper.GetMasterTrack(0)
  local master = mtr and reaper.Track_GetPeakInfo(mtr, 0) or 0
  if not tr then
    return ok_json({
      { "track", "null" },
      { "master", num(master, 6) },
      { "clip", bool(clip_seen) },
      { "note", jstr("no track") },
    })
  end
  local l = reaper.Track_GetPeakInfo(tr, 0) or 0
  local r = reaper.Track_GetPeakInfo(tr, 1) or 0
  local clip = clip_seen or (l >= 0.999) or (r >= 0.999)
  clip_seen = false
  return ok_json({
    { "track", tostring(tonumber(args.track) or 1) },
    { "name", jstr(track_name(tr)) },
    { "l", num(l, 6) },
    { "r", num(r, 6) },
    { "master", num(master, 6) },
    { "clip", bool(clip) },
    { "armed", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") ~= 0) },
    { "monitor", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECMON") ~= 0) },
  })
end

COMMANDS.DISARM = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local tr = track_at(args.track)
  if not tr then return err("no track " .. tostring(args.track)) end
  local own = open_undo("Hermes: disarm track")
  reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", 0)
  reaper.SetMediaTrackInfo_Value(tr, "I_RECMON", 0)
  close_undo("Hermes: disarm track", own)
  return ok_json({
    { "armed", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") ~= 0) },
    { "monitor", bool(reaper.GetMediaTrackInfo_Value(tr, "I_RECMON") ~= 0) },
  })
end

COMMANDS.MASTER_VOL = function(args)
  local mtr = reaper.GetMasterTrack(0)
  if not mtr then return err("no master") end
  if args.db == nil and args.vol == nil then
    local cur = reaper.GetMediaTrackInfo_Value(mtr, "D_VOL")
    return ok_json({ { "vol", num(cur, 4) }, { "db", num(db_of(cur), 2) } })
  end
  local g, why = guard(args)
  if not g then return err(why) end
  local v
  if args.db ~= nil then v = 10 ^ ((tonumber(args.db) or 0) / 20)
  else v = tonumber(args.vol) or 1 end
  local own = open_undo("Hermes: master volume")
  reaper.SetMediaTrackInfo_Value(mtr, "D_VOL", v)
  close_undo("Hermes: master volume", own)
  local cur = reaper.GetMediaTrackInfo_Value(mtr, "D_VOL")
  return ok_json({ { "vol", num(cur, 4) }, { "db", num(db_of(cur), 2) } })
end

COMMANDS.PROJ_SAVE_AS = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  local path = args.path
  if not path or path == "" then return err("need path=") end
  if reaper.file_exists and reaper.file_exists(path) and args.overwrite ~= "1" then
    return err("file exists", { { "path", path } })
  end
  local pr = active_project()
  if not pr then return err("no active project") end
  local opt = tonumber(args.opt) or 0
  local ok = false
  if reaper.Main_SaveProjectEx then
    local called, res = pcall(reaper.Main_SaveProjectEx, pr, path, opt)
    ok = called and res ~= false
  end
  if not ok then
    return err("Main_SaveProjectEx unavailable or failed", { { "path", path } })
  end
  -- Saving a copy is not the same as giving the tab an identity: if REAPER still reports
  -- an unnamed project, the guard would have nothing to compare against, so refuse.
  local actual = project_path(active_project())
  if normpath(actual) ~= normpath(path) then
    return err("save did not give the project this path", {
      { "requested", path }, { "project_name", actual }, { "opt", tostring(opt) } })
  end
  rig_project = active_project()
  return ok_json({ { "path", jstr(actual) }, { "marked", "true" }, { "opt", tostring(opt) } })
end

-- Creating the rig tab by opening a template file on disk. This exists because
-- Main_SaveProjectEx cannot give an unnamed tab an identity without either silently saving
-- a copy (options 0) or opening the Save Project As dialog (options 1), and a dialog owns
-- REAPER's main thread, which stops the bridge dead.
COMMANDS.PROJ_OPEN = function(args)
  local path = args.path
  if not path or path == "" then return err("need path=") end
  if not reaper.file_exists(path) then return err("no such project file", { { "path", path } }) end
  local before = 0
  for i = 0, 63 do if not reaper.EnumProjects(i, "") then before = i break end end
  -- Main_openProject reuses the active tab when it is unnamed, which is what we want on a
  -- cold start, but it would also reuse the tab of a real song. Give it a fresh unnamed tab
  -- whenever the active project has a path of its own.
  local made_tab = false
  if project_path(active_project()) ~= "" and args.new ~= "1" then
    reaper.Main_OnCommand(40859, 0)   -- File: new project tab (create new project)
    made_tab = true
  end
  local ok = pcall(reaper.Main_openProject, "noprompt:" .. path)
  if not ok then return err("Main_openProject failed", { { "path", path } }) end
  local pr = active_project()
  local actual = project_path(pr)
  if normpath(actual) ~= normpath(path) then
    return err("opened project does not report the expected path", {
      { "requested", path }, { "actual", actual } })
  end
  rig_project = pr
  local after = 0
  for i = 0, 63 do if not reaper.EnumProjects(i, "") then after = i break end end
  return ok_json({ { "path", jstr(actual) }, { "marked", "true" },
                   { "new_tab", bool(made_tab) },
                   { "tabs_before", tostring(before) }, { "tabs_after", tostring(after) } })
end

COMMANDS.PROJ_TAB_CLOSE = function(args)
  local g, why = guard(args)
  if not g then return err(why) end
  if tx_open then return err("transaction open") end
  local pr = active_project()
  local path = project_path(pr)
  local before = 0
  for i = 0, 63 do if not reaper.EnumProjects(i, "") then before = i break end end
  -- A dirty project makes REAPER show "save changes?", and that dialog owns the main thread,
  -- which stops this script dead. Save it to its own path first: the rig is disposable, and
  -- the file is ours.
  local dirty = reaper.IsProjectDirty and reaper.IsProjectDirty(pr)
  local saved = false
  if dirty and path ~= "" then
    reaper.Main_SaveProject(pr, false)
    saved = true
  end
  local mode = "closed"
  local removed_tracks = 0
  if before <= 1 then
    -- The last tab cannot be closed: REAPER either quits or raises a dialog, and a dialog
    -- owns the main thread, which stops this script. So the last tab is emptied instead.
    while reaper.CountTracks(pr) > 0 do
      reaper.DeleteTrack(reaper.GetTrack(pr, 0))
      removed_tracks = removed_tracks + 1
    end
    reaper.Main_SaveProject(pr, false)
    mode = "cleared"
  else
    reaper.Main_OnCommand(40860, 0)   -- File: close current project tab
  end
  local after = 0
  for i = 0, 63 do if not reaper.EnumProjects(i, "") then after = i break end end
  if mode == "closed" and after >= before then
    return err("tab did not close (a dialog may be up)", {
      { "tabs_before", tostring(before) }, { "tabs_after", tostring(after) },
      { "saved_first", bool(saved) } })
  end
  rig_project = nil
  return ok_json({
    { "closed", jstr(path) },
    { "tabs_before", tostring(before) },
    { "tabs_after", tostring(after) },
    { "saved_first", bool(saved) },
    { "mode", jstr(mode) },
    { "tracks_removed", tostring(removed_tracks) },
  })
end

-- Runs a ';'-separated list of CMD:k=v,k=v inside one transaction. Stops at the first
-- failure and reports which operation failed, so a half-built rig is never reported as done.
COMMANDS.BATCH = function(args)
  local ops = args.ops or ""
  local results, failed_at = {}, nil
  tx_begin(args.label or "Hermes: rig")
  local i = 0
  for chunk in ops:gmatch("[^;]+") do
    i = i + 1
    local cname, rest = chunk:match("^([^:]+):?(.*)$")
    local sub = {}
    if rest and rest ~= "" then
      for pair in rest:gmatch("[^,]+") do
        local k, v = pair:match("^([^=]+)=(.*)$")
        if k then sub[urldec(k)] = urldec(v) end
      end
    end
    -- The batch's own guard fields apply to every op inside it: one identity, one owner.
    if args.expect and sub.expect == nil then sub.expect = args.expect end
    if args.new and sub.new == nil then sub.new = args.new end
    local fn = cname and COMMANDS[cname] or nil
    if not fn then
      failed_at = i
      results[#results + 1] = "{" .. jstr("i") .. ":" .. i .. "," .. jstr("cmd") .. ":" .. jstr(tostring(cname)) ..
        "," .. jstr("ok") .. ":false," .. jstr("error") .. ":" .. jstr("unknown command") .. "}"
      break
    end
    local resp = fn(sub)
    local good = resp:find('"ok":true', 1, true) ~= nil
    results[#results + 1] = "{" .. jstr("i") .. ":" .. i ..
      "," .. jstr("cmd") .. ":" .. jstr(cname) ..
      "," .. jstr("ok") .. ":" .. bool(good) ..
      "," .. jstr("resp") .. ":" .. resp .. "}"
    if not good then failed_at = i break end
    if cname == "BATCH" then break end
  end
  tx_end()
  local out = { jstr("ok") .. ":" .. bool(failed_at == nil),
                jstr("proto") .. ":" .. PROTO,
                jstr("count") .. ":" .. #results,
                jstr("failed_at") .. ":" .. (failed_at and tostring(failed_at) or "null"),
                jstr("results") .. ":[" .. table.concat(results, ",") .. "]" }
  return "{" .. table.concat(out, ",") .. "}"
end

-- ---------------------------------------------------------------------------
-- dispatch
-- ---------------------------------------------------------------------------

local function split(line)
  local parts, pos = {}, 1
  while true do
    local i = line:find("|", pos, true)
    if not i then parts[#parts + 1] = line:sub(pos) break end
    parts[#parts + 1] = line:sub(pos, i - 1)
    pos = i + 1
  end
  return parts
end

local function parse_args(parts)
  local args = {}
  for i = 3, #parts do
    local k, v = parts[i]:match("^([^=]+)=(.*)$")
    if k then args[urldec(k)] = urldec(v) end
  end
  return args
end

local function handle(cmd, args)
  local fn = COMMANDS[cmd]
  if not fn then return err("unknown command " .. tostring(cmd)) end
  local ok, res = pcall(fn, args)
  if not ok then return err(tostring(res)) end
  return res
end

-- Echo the request id inside the payload as well as in the filename, and publish the file with a
-- rename. A writer that truncates in place can be read half-finished, which looks exactly like a
-- broken bridge to the client.
local function with_id(json, id)
  local body = json:gsub("%s*}$", "")
  return body .. ',"id":' .. jstr(id) .. "}"
end

local function loop()
  local f = io.open(req, "r")
  if f then
    local line = f:read("*l") or ""
    f:close()
    os.remove(req)
    local p = split(line)
    local id = p[1] or "0"
    local cmd = p[2] or ""
    local json
    if cmd == "" then
      json = err("empty request")
    else
      json = handle(cmd, parse_args(p))
    end
    local final = outdir .. "/resp_" .. id .. ".json"
    local tmp = outdir .. "/.resp_" .. id .. ".tmp"
    local o = io.open(tmp, "w")
    if o then
      o:write(with_id(json, id))
      o:close()
      os.remove(final)
      os.rename(tmp, final)
    end
  end
  -- A client that dies mid-transaction must not leave REAPER with an open undo block.
  if tx_open and tx_deadline > 0 and reaper.time_precise() > tx_deadline then
    tx_end()
  end
  local hb = io.open(dir .. "/alive", "w")
  if hb then hb:write(tostring(os.time())) hb:close() end
  reaper.defer(loop)
end

reaper.defer(loop)
