import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import qs.Commons
import qs.Commons as Commons
import "Plugin" as Plugin

ShellRoot {
  QtObject {
    id: service
    property var status: ({live:"active", backfill:"inactive", paused:false, memory:22000000,
      progress:{mode:"live",phase:"watching"}})
    property bool hasGame: true
    property bool commandBusy: false
    property string actionError: ""
    property string lastCommand: ""
    function refresh() {}
    function activate(command) { lastCommand = command }
  }
  QtObject { id: fakeShell; function serviceFor(id) { return service } }
  QtObject {
    id: fakeBar
    property var shell: fakeShell
    property bool vertical: false
    property int barSize: 26
    property color foreground: Commons.Color.foreground
    property color barForeground: Commons.Color.foreground
    property color urgent: Commons.Color.urgent
    property string fontFamily: Style.font.family
    property bool foregroundAnimationEnabled: false
    property string position: "top"
    property var activePopout: null
    function registerClickTarget(item) {}
    function unregisterClickTarget(item) {}
    function showTooltip(item, text) {}
    function hideTooltip(item) {}
    function requestPopout(item) { activePopout = item }
    function releasePopout(item) { activePopout = null }
  }
  Window {
    id: surface
    visible: true
    width: 380
    height: 550
    color: Commons.Color.popups.background
    Plugin.BarWidget { id: widget; bar: fakeBar }
    TestEvent { id: events }
    property int stage: 0
    property int failures: 0
    function check(value, message) { if (!value) { failures++; console.error("FAIL " + message); } }
    function named(item, name) {
      if (item.objectName === name) return item;
      if (item.children) for (var child of item.children) { var found = named(child, name); if (found) return found; }
      return null;
    }
    function hasText(item, text) {
      if (item.text === text) return true;
      for (var child of item.children || []) if (hasText(child, text)) return true;
      return false;
    }
    Timer {
      interval: 250; running: true; repeat: true
      onTriggered: {
        if (surface.stage === 0) {
          surface.check(widget.visible && widget.implicitWidth > 0, "active icon is visible");
          var icon = surface.named(widget, "deadlock-sync-button");
          events.mousePress(icon, icon.width / 2, icon.height / 2, Qt.RightButton, Qt.NoModifier, 1);
          events.mouseRelease(icon, icon.width / 2, icon.height / 2, Qt.RightButton, Qt.NoModifier, 1);
        } else if (surface.stage === 1) {
          surface.check(widget.opened, "right-click opens controls");
          var phase = surface.named(surface.contentItem, "deadlock-sync-phase");
          surface.check(phase.text === "Watching for new matches" && phase.textFormat === Text.PlainText, "live phase is plain text");
          var action = surface.named(surface.contentItem, "deadlock-sync-action");
          surface.check(action.enabled, "quit action is enabled");
          events.mouseClick(action, action.width / 2, action.height / 2, Qt.LeftButton, Qt.NoModifier, 1);
          surface.check(service.lastCommand === "pause", "quit only requests uploader pause");
          service.status = {live:"inactive",backfill:"inactive",paused:true,memory:0};
        } else if (surface.stage === 2) {
          surface.check(widget.stateKey === "state.paused", "paused indicator reflects stop");
          surface.check(!widget.visible, "manual quit hides the icon");
          surface.check(!widget.opened, "manual quit dismisses the details popup");
          service.hasGame = false;
          service.status = {live:"inactive",backfill:"inactive",paused:false,memory:0};
        } else if (surface.stage === 3) {
          surface.check(!widget.visible && widget.implicitWidth === 0, "idle icon hides without space");
          service.status = {live:"inactive",backfill:"activating",paused:false,memory:10000000,
            progress:{mode:"backfill",phase:"preparing"}};
        } else if (surface.stage === 4) {
          surface.check(widget.visible && widget.stateKey === "state.backfill", "backfill indicator appears without game");
          surface.check(!widget.progressKnown, "unknown startup total has no percentage");
          if (!widget.opened) widget.toggle();
          service.status = {live:"inactive",backfill:"activating",paused:false,memory:10000000,
            progress:{mode:"backfill",phase:"recovering",completed:12,total:40,uploaded:10,
              failed:2,history:600,updated_at:1794768000}};
        } else if (surface.stage === 5) {
          surface.check(widget.progressKnown, "known backfill total enables progress");
          var count = surface.named(surface.contentItem, "deadlock-sync-progress-count");
          surface.check(count.visible && count.text === "12 of 40 missing matches processed", "processed numerator is labeled accurately");
          var uploads = surface.named(surface.contentItem, "deadlock-sync-uploaded");
          surface.check(uploads.visible && uploads.text === "10 matches uploaded this pass", "confirmed uploads are separate");
          var track = surface.named(surface.contentItem, "deadlock-sync-progress-bar");
          surface.check(track.visible && Math.abs(track.children[0].width / track.width - 0.3) < 0.001, "bar uses actual processed ratio");
          var updated = surface.named(surface.contentItem, "deadlock-sync-last-activity");
          surface.check(updated.visible && updated.text.indexOf("Last activity:") === 0, "last activity timestamp formats successfully");
          service.status = {live:"active",backfill:"inactive",paused:false,memory:10000000,
            progress:{mode:"live",phase:"reading"}};
        } else if (surface.stage === 6) {
          surface.check(!widget.progressKnown, "live scanning does not show a fake percentage");
          surface.check(surface.named(surface.contentItem, "deadlock-sync-phase").text === "Reading the current game", "live memory phase appears");
          service.status = {live:"inactive",backfill:"activating",paused:false,memory:10000000,
            progress:{mode:"backfill",phase:"<img src=https://invalid>",completed:"12",total:-10}};
        } else if (surface.stage === 7) {
          surface.check(!widget.progressKnown, "invalid progress remains indeterminate");
          surface.check(surface.named(surface.contentItem, "deadlock-sync-phase").text === "Checking Steam match history…", "untrusted phase is never displayed");
          service.status = {live:"inactive",backfill:"failed",paused:false,memory:0,
            recovery:{enabled:true,phase:"cooldown",reason:"rate_limit",next_at:Math.floor(Date.now()/1000)+86400,skipped:0},
            progress:{mode:"backfill",phase:"limited",completed:35,total:320,uploaded:35,failed:0,history:1025,updated_at:1794768486}};
        } else if (surface.stage === 8) {
          surface.check(widget.visible && widget.stateKey === "state.recovery.cooldown", "cooldown keeps the icon visible after worker stops");
          surface.check(widget.lastPass && widget.progressKnown, "waiting preserves last-pass progress");
          surface.check(surface.named(surface.contentItem, "deadlock-sync-phase").text === "Last recovery pass", "old progress is clearly labeled");
          var nextAttempt = surface.named(surface.contentItem, "deadlock-sync-next-attempt");
          surface.check(nextAttempt.visible && nextAttempt.text.indexOf("Earliest next attempt:") === 0, "retry date and time are visible");
          var detail = surface.named(surface.contentItem, "deadlock-sync-detail");
          surface.check(detail.text.indexOf("Steam did not provide a reset time") >= 0, "cooldown is not presented as a Steam reset");
          var quitWaiting = surface.named(surface.contentItem, "deadlock-sync-action");
          surface.check(quitWaiting.enabled && quitWaiting.text === "Quit syncing", "waiting mode can be quit");
          service.lastCommand = "";
          events.mouseClick(quitWaiting, quitWaiting.width / 2, quitWaiting.height / 2, Qt.LeftButton, Qt.NoModifier, 1);
          surface.check(service.lastCommand === "pause", "waiting quit disables automatic mode");
          service.status = {live:"inactive",backfill:"inactive",paused:false,memory:0,recovery:{enabled:false,phase:"off"}};
        } else if (surface.stage === 9) {
          surface.check(!widget.visible && !widget.opened, "quitting cooldown removes icon and popup");
          service.status = {live:"inactive",backfill:"inactive",paused:false,memory:0,
            recovery:{enabled:true,phase:"caught_up",reason:"",next_at:null,skipped:0}};
        } else if (surface.stage === 10) {
          surface.check(widget.visible && widget.stateKey === "state.recovery.caught_up", "caught-up automatic mode stays visible");
          widget.open();
          surface.check(!surface.named(surface.contentItem, "deadlock-sync-next-attempt").visible, "caught-up mode does not invent another deadline");
          service.status = {live:"inactive",backfill:"failed",paused:false,memory:0,
            recovery:{enabled:true,phase:"attention",reason:"auth",next_at:null,skipped:0}};
        } else if (surface.stage === 11) {
          surface.check(widget.attention, "authentication issue is visible as attention");
          var retry = surface.named(surface.contentItem, "deadlock-sync-retry");
          surface.check(retry.visible && retry.enabled, "fixed authentication can be retried explicitly");
          service.lastCommand = "";
          events.mouseClick(retry, retry.width / 2, retry.height / 2, Qt.LeftButton, Qt.NoModifier, 1);
          surface.check(service.lastCommand === "enable", "retry requests enable without bypassing backend cooldown");
          service.status = {live:"active",backfill:"inactive",paused:false,memory:10000000,
            recovery:{enabled:true,phase:"game",reason:"rate_limit",next_at:Math.floor(Date.now()/1000)+86400,skipped:0},
            progress:{mode:"live",phase:"watching"}};
        } else if (surface.stage === 12) {
          surface.check(widget.stateKey === "state.syncing" && !widget.lastPass, "live work takes priority over waiting history");
          surface.check(surface.named(surface.contentItem, "deadlock-sync-detail").text.indexOf("History recovery waits while you play") >= 0, "live priority is explained");
          service.status = {live:"inactive",backfill:"inactive",paused:false,memory:0,
            recovery:{enabled:true,phase:"attention",reason:"unavailable",next_at:null,skipped:3}};
        } else if (surface.stage === 13) {
          surface.check(!surface.named(surface.contentItem, "deadlock-sync-retry").visible, "unavailable old matches have no bypass retry button");
          surface.check(surface.named(surface.contentItem, "deadlock-sync-skipped").visible, "skipped count is disclosed");
          service.status = {live:"inactive",backfill:"inactive",paused:false,memory:0,
            recovery:{enabled:true,phase:"attention",reason:"state_store",next_at:null,skipped:0,repair_required:true}};
        } else if (surface.stage === 14) {
          surface.check(!surface.named(surface.contentItem, "deadlock-sync-retry").visible, "damaged recovery state has no ineffective retry button");
          surface.check(surface.named(surface.contentItem, "deadlock-sync-detail").text.indexOf("needs repair") >= 0, "damaged tracking state explains required repair");
          service.hasGame = true;
          service.status = {live:"failed",backfill:"inactive",paused:false,memory:0,
            recovery:{enabled:true,phase:"game",reason:"rate_limit",next_at:Math.floor(Date.now()/1000)+86400,skipped:0}};
        } else if (surface.stage === 15) {
          surface.check(widget.attention && widget.stateKey === "state.failed", "failed live sync is not hidden by recovery mode");
          var liveRetry = surface.named(surface.contentItem, "deadlock-sync-retry");
          surface.check(liveRetry.visible && liveRetry.enabled, "failed live sync offers explicit restart");
          service.lastCommand = "";
          events.mouseClick(liveRetry, liveRetry.width / 2, liveRetry.height / 2, Qt.LeftButton, Qt.NoModifier, 1);
          surface.check(service.lastCommand === "resume", "live retry uses resume action");
          service.hasGame = false;
          service.status = {live:"inactive",backfill:"active",paused:false,memory:0,
            recovery:{enabled:true,phase:"working",reason:"",current_account:"<img src=https://invalid>",active_account:"Second",
              account_count:2,accounts:[{name:"<b>First</b>",current:true,phase:"cooldown",next_at:Math.floor(Date.now()/1000)+86400},
                {name:"Second",current:false,phase:"working",next_at:null}]}};
        } else if (surface.stage === 16) {
          var current = surface.named(surface.contentItem,"deadlock-sync-current-account");
          var active = surface.named(surface.contentItem,"deadlock-sync-active-account");
          surface.check(current.visible && current.textFormat === Text.PlainText && current.text.indexOf("<img") >= 0,
            "current Steam name is rendered only as plain text");
          surface.check(active.visible && active.text === "Recovering: Second", "working account is distinct from current account");
          surface.check(surface.named(surface.contentItem,"deadlock-sync-account-list").visible, "per-account waiting list is visible");
          service.status = {live:"inactive",backfill:"failed",paused:false,memory:0,
            recovery:{enabled:true,phase:"waiting_for_account",reason:"",current_account:"First",
              account_count:2,accounts:[{name:"First",current:true,phase:"caught_up"},
                {name:"Second",current:false,phase:"choose_account"}]}};
        } else {
          surface.check(widget.visible && !widget.attention && widget.stateKey === "state.recovery.waiting_for_account",
            "a saved account waiting for Steam selection gives a neutral current-account status");
          surface.check(!surface.named(surface.contentItem,"deadlock-sync-retry").visible, "retry is hidden until actionable");
          surface.check(surface.hasText(surface.contentItem, "Second: choose this account in Steam to retry automatically"),
            "the row names the account that Steam needs to authorize");
          surface.check(surface.named(surface.contentItem,"deadlock-sync-detail").text.indexOf("when Deadlock is closed") >= 0,
            "automatic retry timing is explained");
          console.log("DEADLOCK_UI_RESULTS " + JSON.stringify({failures:surface.failures}));
          Qt.quit();
        }
        surface.stage++;
      }
    }
  }
}
