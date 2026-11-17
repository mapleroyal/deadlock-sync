import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "user1.deadlock-ingest"
  manageIpc: false
  readonly property var service: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
  readonly property var status: service ? service.status : Model.empty()
  readonly property var progress: Model.progress(status.progress)
  readonly property var recovery: Model.recovery(status.recovery)
  readonly property var accounts: recovery.accounts
  readonly property bool live: Model.running(status.live)
  readonly property bool backfill: Model.running(status.backfill)
  readonly property bool working: live || backfill
  readonly property bool lastPass: !working && recovery.enabled && progress.mode === "backfill"
  readonly property bool progressKnown: (backfill || lastPass) && Model.progressKnown(progress)
  readonly property bool hasGame: !!service && service.hasGame
  readonly property bool liveFailure: hasGame && status.live === "failed" && recovery.reason !== "upload_rate_limit"
  readonly property bool busy: !!service && service.commandBusy
  readonly property string errorText: service ? service.actionError : ""
  readonly property string stateKey: Model.statusKey(status, hasGame, busy, errorText)
  readonly property bool attention: ["state.failed", "state.error", "state.recovery.attention"].indexOf(stateKey) >= 0
  readonly property bool managing: working || recovery.enabled
  readonly property bool shown: managing || busy
  readonly property bool sharedCooldown: ["upload_rate_limit", "legacy_cooldown"].indexOf(recovery.reason) >= 0
  readonly property bool hasWorkingRow: backfill && accounts.some(a => a.phase === "working")
  readonly property bool hasLiveRow: live && accounts.some(a => a.current && ["caught_up", "retry"].indexOf(a.phase) >= 0)
  readonly property color ink: Commons.Color.popups.text
  readonly property color muted: Util.alpha(ink, 0.76)
  readonly property real openPanelIndicatorWidth: button.opticalSize
  property string expandedAccount: ""
  visible: shown
  implicitWidth: shown ? button.implicitWidth : 0
  implicitHeight: shown ? button.implicitHeight : 0
  onShownChanged: if (!shown) Qt.callLater(function() { if (!root.shown) root.close(); })
  onOpenedChanged: if (!opened) { expandedAccount = ""; scroll.contentY = 0; }

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  function activate() {
    if (service && !busy) service.activate(managing ? "pause" : "enable");
  }

  function timestamp(seconds) {
    return Qt.formatDateTime(new Date(seconds * 1000), Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat);
  }

  function rowDeadline(account) {
    return ["retry", "cooldown"].indexOf(account.phase) >= 0 && account.next_at > Date.now() / 1000;
  }

  // Ordinary activity belongs to the account row. Use a panel-wide message
  // for shared constraints, errors requiring instructions, or a missing row.
  readonly property string detailKey: {
    if (errorText) return "";
    if (liveFailure) return "detail.live_failed";
    if (sharedCooldown || recovery.reason === "account_metadata") return "detail.recovery." + recovery.reason;
    if (working) return "";
    if (recovery.repair_required) return "detail.recovery.corrupt";
    if (recovery.reason === "choose_account" && accounts.some(a => a.phase === "choose_account")) return "";
    if (recovery.reason === "auth" && accounts.some(a => a.phase === "auth")) return "";
    if (recovery.reason === "rate_limit" && accounts.some(a => a.phase === "cooldown")) return "";
    if (recovery.reason) return "detail.recovery." + recovery.reason;
    if (hasGame || accounts.length === 0) return stateKey;
    return "";
  }
  readonly property bool showNextAttempt: Model.scheduledAttempt(recovery, Date.now() / 1000) &&
    (sharedCooldown || !accounts.some(a => root.rowDeadline(a) && a.next_at === recovery.next_at))

  component Label: Text {
    textFormat: Text.PlainText
    font.family: Style.font.family
    font.pixelSize: Style.font.bodySmall
    color: root.ink
    wrapMode: Text.Wrap
  }

  component Stat: Label {
    font.pixelSize: Style.font.caption
    color: root.muted
  }

  component PassDetails: Column {
    id: pass
    required property var dataPoint
    property bool historical: false
    property bool showPhase: true
    readonly property bool known: Model.progressKnown(dataPoint)
    spacing: Style.space(4)

    Label {
      objectName: "deadlock-sync-phase"
      width: parent.width
      visible: pass.showPhase
      text: strings.t(pass.historical ? "progress.lastPass" : "phase." + pass.dataPoint.phase)
      font.bold: pass.historical
    }
    Label {
      objectName: "deadlock-sync-progress-count"
      visible: pass.known
      width: parent.width
      text: strings.tn("progress.processed", pass.dataPoint.total || 0)
        .arg(Commons.I18n.formatNumber(pass.dataPoint.completed || 0)).arg(Commons.I18n.formatNumber(pass.dataPoint.total || 0))
      color: root.muted
    }
    Rectangle {
      objectName: "deadlock-sync-progress-bar"
      visible: pass.known && !pass.historical
      width: parent.width
      height: Style.space(4)
      radius: height / 2
      color: Util.alpha(root.ink, 0.16)
      Rectangle {
        width: parent.width * Model.progressRatio(pass.dataPoint)
        height: parent.height
        radius: parent.radius
        color: Commons.Color.accent
      }
    }
    Flow {
      width: parent.width
      spacing: Style.space(10)
      visible: pass.dataPoint.uploaded !== null || pass.dataPoint.failed > 0 || pass.dataPoint.history !== null

      Stat {
        objectName: "deadlock-sync-uploaded"
        visible: pass.dataPoint.uploaded !== null
        text: strings.t("progress.uploaded").arg(Commons.I18n.formatNumber(pass.dataPoint.uploaded || 0))
      }
      Stat {
        visible: pass.dataPoint.failed > 0
        text: strings.t("progress.failed").arg(Commons.I18n.formatNumber(pass.dataPoint.failed || 0))
        color: Commons.Color.urgent
      }
      Stat {
        visible: pass.dataPoint.history !== null
        text: strings.t("progress.history").arg(Commons.I18n.formatNumber(pass.dataPoint.history || 0))
      }
    }
    Label {
      objectName: "deadlock-sync-last-activity"
      visible: pass.dataPoint.updated_at !== null
      width: parent.width
      text: strings.t("progress.updated").arg(root.timestamp(pass.dataPoint.updated_at || 0))
      font.pixelSize: Style.font.caption
      color: root.muted
    }
  }

  BarIconButton {
    id: button
    objectName: "deadlock-sync-button"
    bar: root.bar
    text: "󰊗"
    foreground: root.attention ? Commons.Color.urgent :
      (root.managing ? Commons.Color.accent : Util.alpha(Commons.Color.foreground, 0.5))
    tooltipText: Model.safeTooltip(strings.t("tooltip").arg(strings.t("name")).arg(strings.t(root.stateKey)))
    Accessible.name: strings.t("name")
    Accessible.description: strings.t(root.stateKey)
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.LeftButton || mouseButton === Qt.RightButton) {
        if (root.service) root.service.refresh();
        root.toggle();
      }
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: popup.fittedContentWidth(Style.space(370))
    contentHeight: popup.fittedContentHeight(content.implicitHeight, Style.space(630))

    Item {
      id: keys
      anchors.fill: parent
      focus: true
      Keys.onEscapePressed: root.close()
      Keys.onDownPressed: scroll.contentY = Math.min(Math.max(0, scroll.contentHeight - scroll.height), scroll.contentY + Style.space(44))
      Keys.onUpPressed: scroll.contentY = Math.max(0, scroll.contentY - Style.space(44))
      Keys.onReturnPressed: root.activate()
      Keys.onEnterPressed: root.activate()
      Keys.onSpacePressed: root.activate()

      Flickable {
        id: scroll
        anchors.fill: parent
        contentHeight: content.implicitHeight
        interactive: contentHeight > height
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        function reveal(item) {
          if (!interactive) return;
          var top = item.mapToItem(content, 0, 0).y;
          contentY = Math.max(0, Math.min(top, Math.max(contentY, top + item.height - height)));
        }

        Column {
          id: content
          width: scroll.width
          spacing: Style.space(12)

          Label {
            width: parent.width
            text: strings.t("name")
            font.pixelSize: Style.font.title
            font.bold: true
          }
          Label {
            objectName: "deadlock-sync-detail"
            visible: text !== ""
            width: parent.width
            text: root.errorText || (root.detailKey ? strings.t(root.detailKey) : "")
            color: root.attention ? Commons.Color.urgent : root.muted
          }
          Label {
            objectName: "deadlock-sync-next-attempt"
            visible: root.showNextAttempt
            width: parent.width
            text: strings.t("recovery.nextAttempt").arg(root.timestamp(root.recovery.next_at || 0))
          }
          Loader {
            width: parent.width
            active: (root.live && !root.hasLiveRow) || (root.backfill && !root.hasWorkingRow) || (root.lastPass && root.accounts.length === 0)
            visible: active
            sourceComponent: PassDetails {
              dataPoint: root.progress.mode ? root.progress : Model.progress({mode: root.live ? "live" : "backfill"})
              historical: root.lastPass
            }
          }
          Column {
            id: accountList
            objectName: "deadlock-sync-account-list"
            visible: root.accounts.length > 0
            width: parent.width
            spacing: Style.space(10)

            Repeater {
              model: root.accounts.length
              delegate: Column {
                id: accountRow
                required property int index
                readonly property var account: root.accounts[index] || ({})
                readonly property bool working: root.backfill && account.phase === "working"
                readonly property bool playing: root.hasLiveRow && !!account.current
                readonly property string statusKey: {
                  if (playing) return "phase." + (root.progress.mode === "live" ? root.progress.phase : "watching");
                  if (working) return "phase." + (root.progress.mode === "backfill" ? root.progress.phase : "preparing");
                  return "account.phase." + account.phase;
                }
                readonly property bool expandable: !!account.id && !!account.progress && !working
                readonly property bool expanded: expandable && root.expandedAccount === account.id
                width: accountList.width
                spacing: Style.space(4)

                Item {
                  width: parent.width
                  implicitHeight: Math.max(line.implicitHeight, Style.space(24))

                  RowLayout {
                    id: line
                    anchors.fill: parent
                    spacing: Style.space(8)
                    Label {
                      objectName: "deadlock-sync-account-name"
                      text: accountRow.account.name || ""
                      font.pixelSize: Style.font.body
                      font.bold: !!accountRow.account.current
                      Layout.maximumWidth: line.width * 0.45
                      maximumLineCount: 1
                      elide: Text.ElideRight
                    }
                    Label {
                      text: strings.t(accountRow.statusKey)
                      Layout.fillWidth: true
                      font.pixelSize: Style.font.caption
                      color: ["attention", "auth"].indexOf(accountRow.account.phase) >= 0 ? Commons.Color.urgent : root.muted
                    }
                    Label {
                      visible: root.accounts.length > 1 && !!accountRow.account.current
                      text: strings.t("account.currentTag")
                      color: Commons.Color.accent
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    Label {
                      visible: accountRow.expandable
                      text: accountRow.expanded ? "▾" : "▸"
                      color: root.muted
                      Accessible.ignored: true
                    }
                  }
                  MouseArea {
                    id: toggle
                    objectName: "deadlock-sync-account-toggle"
                    anchors.fill: parent
                    enabled: accountRow.expandable
                    activeFocusOnTab: enabled
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    Accessible.role: Accessible.Button
                    Accessible.name: accountRow.account.name || ""
                    Accessible.description: strings.t(accountRow.expanded ? "account.hidePass" : "account.showPass")
                    Accessible.onPressAction: toggle.change()
                    function change() {
                      if (enabled) root.expandedAccount = accountRow.expanded ? "" : accountRow.account.id;
                    }
                    Keys.onReturnPressed: change()
                    Keys.onEnterPressed: change()
                    Keys.onSpacePressed: change()
                    onClicked: change()
                    onActiveFocusChanged: if (activeFocus) scroll.reveal(toggle)
                    Rectangle {
                      anchors.fill: parent
                      visible: toggle.activeFocus
                      color: "transparent"
                      border.color: Commons.Color.accent
                      radius: Style.cornerRadius
                    }
                  }
                }
                Label {
                  visible: root.rowDeadline(accountRow.account)
                  width: parent.width
                  text: strings.t("recovery.nextAttempt").arg(root.timestamp(accountRow.account.next_at || 0))
                  color: root.muted
                  font.pixelSize: Style.font.caption
                }
                Label {
                  visible: accountRow.playing && root.progress.updated_at !== null
                  width: parent.width
                  text: strings.t("progress.updated").arg(root.timestamp(root.progress.updated_at || 0))
                  color: root.muted
                  font.pixelSize: Style.font.caption
                }
                Loader {
                  width: parent.width
                  active: accountRow.working || accountRow.expanded
                  visible: active
                  sourceComponent: PassDetails {
                    dataPoint: accountRow.working ? (root.progress.mode === "backfill" ? root.progress : Model.progress({mode: "backfill"}))
                                                  : (accountRow.account.progress || Model.progress({mode: "backfill"}))
                    historical: !accountRow.working
                    showPhase: historical
                  }
                }
              }
            }
          }
          Label {
            objectName: "deadlock-sync-skipped"
            visible: root.recovery.enabled && root.recovery.skipped > 0
            width: parent.width
            text: strings.tn("recovery.skipped", root.recovery.skipped).arg(Commons.I18n.formatNumber(root.recovery.skipped))
            color: Commons.Color.urgent
          }
          Rectangle {
            width: parent.width
            height: Math.max(1, Style.space(1))
            color: Util.alpha(root.ink, 0.16)
          }
          RowLayout {
            width: parent.width
            spacing: Style.space(8)
            Label {
              visible: root.working && root.status.memory > 0
              text: strings.t("memory").arg(Commons.I18n.formatNumber(root.status.memory / 1048576, 1))
              font.pixelSize: Style.font.caption
              color: root.muted
            }
            Button {
              objectName: "deadlock-sync-retry"
              visible: root.liveFailure || (root.recovery.enabled && root.recovery.phase === "attention" &&
                !root.recovery.repair_required && ["auth", "state_store"].indexOf(root.recovery.reason) >= 0)
              text: strings.t("recovery.tryAgain")
              enabled: !root.busy
              focusable: true
              foreground: root.ink
              onActiveFocusChanged: if (activeFocus) scroll.reveal(this)
              onClicked: if (root.service) root.service.activate(root.liveFailure ? "resume" : "enable")
            }
            Item { Layout.fillWidth: true }
            Button {
              objectName: "deadlock-sync-action"
              text: strings.t(root.managing ? "quit" : "resume")
              enabled: !root.busy && (root.managing || root.hasGame)
              focusable: true
              foreground: root.ink
              onActiveFocusChanged: if (activeFocus) scroll.reveal(this)
              onClicked: root.activate()
            }
          }
        }
      }
    }
  }
}
