import QtQuick
import qs.Commons

// Replace only the Wayland layer-shell container in the offscreen test.
// Actual widget, typography, actions, input, and scroll layout stay unchanged.
Item {
  id: root
  required property Item anchorItem
  required property QtObject bar
  property var owner: null
  property bool open: false
  property int contentWidth: 330
  property int contentHeight: 520
  property Item focusTarget: null
  property int padding: Style.spacing.popupPadding
  default property alias contentItem: holder.children
  objectName: "testPanel"
  parent: anchorItem.parent.parent
  x: 10
  y: 45
  width: contentWidth
  height: contentHeight
  visible: open
  function fittedContentWidth(value) { return value }
  function fittedContentHeight(value, cap) { return Math.min(value + padding * 2, cap) }
  Item { id: holder; anchors.fill: parent; anchors.margins: root.padding }
}
