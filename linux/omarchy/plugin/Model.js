function gameSession(toplevels) {
  var ids = [];
  for (var i = 0; i < toplevels.length; i++) {
    var top = toplevels[i];
    var ipc = top.lastIpcObject || {};
    var appId = String(ipc["class"] || (top.wayland && top.wayland.appId) || "");
    if (appId !== "steam_app_1422450") continue;
    var id = String(ipc.pid || top.address || "deadlock");
    if (ids.indexOf(id) < 0) ids.push(id);
  }
  return ids.sort().join(",").slice(0, 256);
}

function empty() {
  return { live: "inactive", backfill: "inactive", paused: false, memory: 0,
    progress: progress({}), recovery: recovery({}) };
}

function count(value, maximum) {
  return typeof value === "number" && isFinite(value) && value >= 0 &&
    Math.floor(value) === value && value <= maximum ? value : null;
}

function progress(value) {
  value = value && typeof value === "object" ? value : {};
  var mode = ["backfill", "live"].indexOf(value.mode) >= 0 ? value.mode : "";
  var phases = ["preparing", "recovering", "watching", "reading", "waiting", "limited", "failed", "complete"];
  var result = {mode: mode, phase: phases.indexOf(value.phase) >= 0 ? value.phase :
    (mode === "live" ? "watching" : "preparing")};
  ["completed", "total", "uploaded", "failed", "history"].forEach(function(key) {
    result[key] = count(value[key], 10000000);
  });
  result.updated_at = count(value.updated_at, 4102444800);
  if (result.total !== null && result.completed > result.total) result.completed = null;
  return result;
}

function progressKnown(value) {
  return value.mode === "backfill" && value.total !== null && value.total > 0 && value.completed !== null;
}

function progressRatio(value) {
  return progressKnown(value) ? Math.min(1, value.completed / value.total) : 0;
}

function recovery(value) {
  value = value && typeof value === "object" ? value : {};
  var phases = ["off", "working", "cooldown", "retry", "game", "caught_up", "attention"];
  var reasons = ["", "rate_limit", "transient", "account_in_use", "auth", "unavailable", "state_store", "settling"];
  var enabled = value.enabled === true;
  return {enabled: enabled,
    phase: enabled ? (phases.indexOf(value.phase) >= 0 ? value.phase : "attention") : "off",
    reason: reasons.indexOf(value.reason) >= 0 ? value.reason : "unavailable",
    next_at: count(value.next_at, 4102444800),
    skipped: count(value.skipped, 10000000) || 0,
    repair_required: value.repair_required === true};
}

function pollInterval(status) {
  return running(status.live) || running(status.backfill) ? 2000 : 60000;
}

function shouldPoll(status) {
  return running(status.live) || running(status.backfill) || recovery(status.recovery).enabled;
}

function scheduledAttempt(value, now) {
  return value.enabled && ["cooldown", "retry", "game"].indexOf(value.phase) >= 0 &&
    value.next_at !== null && value.next_at > now;
}

function parse(text) {
  var result = empty();
  try {
    var value = JSON.parse(String(text).slice(0, 16384));
    var allowed = ["active", "activating", "deactivating", "inactive", "failed", "unavailable"];
    ["live", "backfill"].forEach(function(key) {
      result[key] = allowed.indexOf(value[key]) >= 0 ? value[key] : "unavailable";
    });
    result.paused = value.paused === true;
    result.memory = Math.max(0, Math.min(Number(value.memory) || 0, 1099511627776));
    result.progress = progress(value.progress);
    result.recovery = recovery(value.recovery);
  } catch (_) { result.live = "unavailable"; }
  return result;
}

function running(state) {
  return state === "active" || state === "activating" || state === "deactivating";
}

function statusKey(status, hasGame, busy, error) {
  if (error) return "state.error";
  if (running(status.backfill)) return "state.backfill";
  if (running(status.live)) return "state.syncing";
  if (hasGame && status.live === "failed") return "state.failed";
  var automatic = recovery(status.recovery);
  if (automatic.enabled) return "state.recovery." + automatic.phase;
  if (status.live === "failed" || status.backfill === "failed") return "state.failed";
  if (busy) return "state.changing";
  if (hasGame && status.paused) return "state.paused";
  if (hasGame) return "state.stopped";
  return "state.idle";
}

// Shared shell tooltip components can use AutoText. Never pass markup to them.
function safeTooltip(value) {
  return String(value).slice(0, 240).replace(/&/g, "＆").replace(/</g, "‹").replace(/>/g, "›");
}

if (typeof module !== "undefined") module.exports = {gameSession, empty, parse, progress, progressKnown, progressRatio,
  recovery, pollInterval, shouldPoll, scheduledAttempt, running, statusKey, safeTooltip};
