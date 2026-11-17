import QtQuick
import Quickshell
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
  readonly property bool lastPass: !working && recovery.enabled && progress.mode === "backfill"
  readonly property bool hasProgress: (working || lastPass) && progress.mode !== ""
  readonly property bool progressKnown: hasProgress && Model.progressKnown(progress)
  readonly property bool hasGame: !!service && service.hasGame
  readonly property bool liveFailure: hasGame && status.live === "failed" && recovery.reason !== "upload_rate_limit"
  readonly property bool working: Model.running(status.live) || Model.running(status.backfill)
  readonly property bool busy: !!service && service.commandBusy
  readonly property string errorText: service ? service.actionError : ""
  readonly property string stateKey: Model.statusKey(status, hasGame, busy, errorText)
  readonly property bool attention: stateKey === "state.failed" || stateKey === "state.error" ||
    stateKey === "state.recovery.attention"
  readonly property bool managing: working || recovery.enabled
  readonly property bool shown: managing || busy
  readonly property string detailKey: liveFailure ? "detail.live_failed" : (Model.running(status.backfill) ? "detail.backfill" :
    (Model.running(status.live) ? "detail.live" : (recovery.enabled ?
      (recovery.phase === "caught_up" ? "detail.caught_up" : (recovery.phase === "game" ? "detail.game" :
        (recovery.repair_required ? "detail.recovery.corrupt" : "detail.recovery." + recovery.reason))) :
      (status.paused && hasGame ? "detail.paused" : "detail.idle"))))
  readonly property real openPanelIndicatorWidth: button.opticalSize
  visible: shown
  implicitWidth: shown ? button.implicitWidth : 0
  implicitHeight: shown ? button.implicitHeight : 0
  onShownChanged: if (!shown) Qt.callLater(function() { if (!root.shown) root.close(); })

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  function activate() {
    if (!service || busy) return;
    service.activate(managing ? "pause" : "enable");
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
    focusTarget: keyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(310))
    contentHeight: popup.fittedContentHeight(content.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onActivateRequested: root.activate()

      Flickable {
        anchors.fill: parent
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)
        Text {
          width: parent.width
          text: strings.t("name")
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          font.bold: true
          color: Commons.Color.popups.text
        }
        Text {
          width: parent.width
          text: strings.t(root.stateKey)
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          color: root.attention ? Commons.Color.urgent : Commons.Color.popups.text
          wrapMode: Text.WordWrap
        }
        Text {
          objectName: "deadlock-sync-current-account"
          visible: root.recovery.current_account !== ""
          width: parent.width
          text: strings.t("account.current").arg(root.recovery.current_account)
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          color: Commons.Color.popups.text
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          elide: Text.ElideRight
        }
        Text {
          objectName: "deadlock-sync-active-account"
          visible: root.recovery.active_account !== ""
          width: parent.width
          text: strings.t("account.working").arg(root.recovery.active_account)
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          color: Commons.Color.popups.text
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          elide: Text.ElideRight
        }
        Flickable {
          objectName: "deadlock-sync-account-list"
          visible: root.recovery.accounts.length > 1
          width: parent.width
          height: visible ? Math.min(accountRows.implicitHeight, Style.space(100)) : 0
          contentHeight: accountRows.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          Column {
            id: accountRows
            width: parent.width
            spacing: Style.space(4)
            Repeater {
              model: root.recovery.accounts
              delegate: Text {
                required property var modelData
                width: accountRows.width
                text: modelData.name + (modelData.current ? " (" + strings.t("account.currentTag") + ")" : "") +
                  ": " + strings.t("account.phase." + modelData.phase) +
                  ((modelData.next_at || 0) > Math.floor(Date.now() / 1000) && ["cooldown", "retry"].indexOf(modelData.phase) >= 0 ?
                    " — " + Qt.formatDateTime(new Date(modelData.next_at * 1000), Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat) : "")
                textFormat: Text.PlainText
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                color: Util.alpha(Commons.Color.popups.text, 0.75)
                wrapMode: Text.WordWrap
              }
            }
          }
        }
        Text {
          objectName: "deadlock-sync-phase"
          visible: root.hasProgress
          width: parent.width
          text: root.lastPass ? strings.t("progress.lastPass") : strings.t("phase." + root.progress.phase)
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          color: !root.lastPass && (root.progress.phase === "limited" || root.progress.phase === "failed") ? Commons.Color.urgent : Commons.Color.popups.text
          wrapMode: Text.WordWrap
        }
        Column {
          visible: root.hasProgress && root.progress.mode === "backfill"
          width: parent.width
          spacing: Style.space(5)
          Text {
            objectName: "deadlock-sync-progress-count"
            visible: root.progressKnown
            width: parent.width
            text: strings.tn("progress.processed", root.progress.total || 0)
              .arg(Commons.I18n.formatNumber(root.progress.completed || 0))
              .arg(Commons.I18n.formatNumber(root.progress.total || 0))
            textFormat: Text.PlainText
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            color: Commons.Color.popups.text
            wrapMode: Text.WordWrap
          }
          Rectangle {
            objectName: "deadlock-sync-progress-bar"
            visible: root.progressKnown
            width: parent.width
            height: Style.space(5)
            radius: height / 2
            color: Util.alpha(Commons.Color.popups.text, 0.15)
            Rectangle {
              width: parent.width * Model.progressRatio(root.progress)
              height: parent.height
              radius: parent.radius
              color: Commons.Color.accent
            }
          }
          Text {
            objectName: "deadlock-sync-uploaded"
            visible: root.progress.uploaded !== null
            width: parent.width
            text: strings.tn("progress.uploaded", root.progress.uploaded || 0)
              .arg(Commons.I18n.formatNumber(root.progress.uploaded || 0))
            textFormat: Text.PlainText
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            color: Util.alpha(Commons.Color.popups.text, 0.75)
            wrapMode: Text.WordWrap
          }
          Text {
            visible: root.progress.failed !== null && root.progress.failed > 0
            width: parent.width
            text: strings.tn("progress.failed", root.progress.failed || 0)
              .arg(Commons.I18n.formatNumber(root.progress.failed || 0))
            textFormat: Text.PlainText
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            color: Commons.Color.urgent
            wrapMode: Text.WordWrap
          }
          Text {
            visible: root.progress.history !== null
            width: parent.width
            text: strings.tn("progress.history", root.progress.history || 0)
              .arg(Commons.I18n.formatNumber(root.progress.history || 0))
            textFormat: Text.PlainText
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            color: Util.alpha(Commons.Color.popups.text, 0.75)
            wrapMode: Text.WordWrap
          }
        }
        Text {
          objectName: "deadlock-sync-detail"
          width: parent.width
          text: strings.t(root.detailKey)
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          color: Util.alpha(Commons.Color.popups.text, 0.75)
          wrapMode: Text.WordWrap
        }
        Text {
          objectName: "deadlock-sync-next-attempt"
          visible: Model.scheduledAttempt(root.recovery, Math.floor(Date.now() / 1000))
          width: parent.width
          text: strings.t("recovery.nextAttempt").arg(Qt.formatDateTime(new Date((root.recovery.next_at || 0) * 1000),
            Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat))
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          color: Commons.Color.popups.text
          wrapMode: Text.WordWrap
        }
        Text {
          objectName: "deadlock-sync-skipped"
          visible: root.recovery.enabled && root.recovery.skipped > 0
          width: parent.width
          text: strings.tn("recovery.skipped", root.recovery.skipped)
            .arg(Commons.I18n.formatNumber(root.recovery.skipped))
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          color: Commons.Color.urgent
          wrapMode: Text.WordWrap
        }
        Text {
          objectName: "deadlock-sync-last-activity"
          visible: root.hasProgress && root.progress.updated_at !== null
          width: parent.width
          text: strings.t("progress.updated").arg(Qt.formatTime(new Date((root.progress.updated_at || 0) * 1000),
            Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat))
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          color: Util.alpha(Commons.Color.popups.text, 0.75)
          wrapMode: Text.WordWrap
        }
        Text {
          visible: root.working && root.status.memory > 0
          width: parent.width
          text: strings.t("memory").arg(Commons.I18n.formatNumber(root.status.memory / 1048576, 1))
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          color: Util.alpha(Commons.Color.popups.text, 0.75)
        }
        Button {
          objectName: "deadlock-sync-retry"
          visible: root.liveFailure || (root.recovery.enabled && root.recovery.phase === "attention" &&
            !root.recovery.repair_required &&
            (root.recovery.reason === "auth" || root.recovery.reason === "state_store"))
          width: parent.width
          text: strings.t("recovery.tryAgain")
          enabled: !root.busy
          foreground: Commons.Color.popups.text
          leftAlign: true
          onClicked: if (root.service) root.service.activate(root.liveFailure ? "resume" : "enable")
        }
        Button {
          objectName: "deadlock-sync-action"
          width: parent.width
          text: strings.t(root.managing ? "quit" : "resume")
          enabled: !root.busy && (root.managing || root.hasGame)
          foreground: Commons.Color.popups.text
          leftAlign: true
          onClicked: root.activate()
        }
        Text {
          visible: root.errorText !== ""
          width: parent.width
          text: root.errorText
          textFormat: Text.PlainText
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          color: Commons.Color.urgent
          wrapMode: Text.Wrap
          maximumLineCount: 4
          elide: Text.ElideRight
        }
      }
      }
    }
  }
}
