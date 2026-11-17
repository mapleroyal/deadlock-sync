import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import Quickshell.Hyprland
import "Model.js" as Model

Item {
  id: root
  property var shell: null
  readonly property string executable: decodeURIComponent(Qt.resolvedUrl("backend/control.py").toString().replace(/^file:\/\//, ""))
  readonly property string gameSession: Model.gameSession(Hyprland.toplevels.values)
  readonly property bool hasGame: gameSession !== ""
  property var status: Model.empty()
  property string actionError: ""
  property bool ready: false
  property bool syncPending: false
  property bool tickPending: false
  property bool refreshPending: false
  property string pendingAction: ""
  property string output: ""
  property string stderrText: ""
  property string activeAction: ""
  readonly property bool commandBusy: command.running && activeAction !== "status" && activeAction !== "tick"

  function diagnostics(arg) {
    return JSON.stringify({gameSession: gameSession, status: status,
      error: actionError, commandRunning: command.running, action: activeAction});
  }
  function refresh() { refreshPending = true; pump(); }
  function sync() { syncPending = true; pump(); }
  function tick() { tickPending = true; pump(); }
  function activate(action) {
    if (action !== "pause" && action !== "resume" && action !== "enable") return;
    pendingAction = action;
    actionError = "";
    pump();
  }
  function pump() {
    if (!ready || command.running) return;
    var next = pendingAction || (syncPending ? "sync" : (tickPending ? "tick" : (refreshPending ? "status" : "")));
    if (!next) return;
    pendingAction = "";
    if (next === "sync") syncPending = false;
    if (next !== "status") tickPending = false;
    refreshPending = false;
    output = "";
    stderrText = "";
    activeAction = next;
    command.command = ["/usr/bin/python3", executable, next, gameSession];
    command.running = true;
  }

  onGameSessionChanged: if (ready) sync();
  Component.onCompleted: { ready = true; sync(); }

  // The existing shell schedules recovery. Waiting checks are local and slow;
  // account metadata changes are noticed promptly without an extra resident process.
  // The backend starts one eligible account after its persisted retry deadline.
  // Quitting automatic mode disables this timer when both jobs are stopped.
  Timer {
    interval: Model.pollInterval(root.status)
    repeat: true
    running: Model.shouldPoll(root.status)
    onTriggered: root.tick()
  }
  FileView {
    id: unitMarker
    path: Quickshell.env("XDG_RUNTIME_DIR") + "/deadlock-api-ingest-bar.status"
    watchChanges: true
    onFileChanged: { reload(); root.tick(); }
  }
  ShellIpc {
    target: "user1.deadlock-ingest"
    function status(): string { return root.diagnostics(""); }
    function quit(): void { root.activate("pause"); }
  }
  Process {
    id: command
    stdout: StdioCollector { onStreamFinished: root.output = text }
    stderr: StdioCollector { onStreamFinished: root.stderrText = text }
    onExited: function(code) {
      if (code === 0) {
        root.status = Model.parse(root.output);
        if (root.activeAction !== "status") root.actionError = "";
      } else {
        var message = "";
        try { message = JSON.parse(root.output).error || ""; } catch (_) {}
        root.actionError = String(message || root.stderrText || "Could not control the sync service").slice(0, 1024);
      }
      Qt.callLater(root.pump);
    }
  }
}
