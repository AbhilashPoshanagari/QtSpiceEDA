import QtQuick
import QtQuick.Controls
import QtSpice

// A coloured trace name above a pane (W4), as in LTspice:
// - drag it to another pane to move the trace there, Ctrl+drop to copy it;
//   drop it outside every pane to remove it;
// - double-click shows only this trace in its pane;
// - in Delete mode (WaveformController.deleteMode) a click removes it;
// - otherwise a click attaches cursor 1 and right-click opens a menu for cursor 2,
//   both cursors or removing the trace (W6), or editing its expression (W8);
// - Ctrl+click shows the average and RMS over the zoomed X range (W10).
// Spike D: Drag.Internal + Drag.keys + one DropArea per pane; while dragging the chip
// is re-parented to `dragParent` so it can leave its pane. Ctrl is read on release
// (a DragEvent has no modifiers) and passed as Drag.proposedAction.
Item {
    id: label
    required property int paneIndex
    required property string name
    property string caption: name  // M7: e.g. "inertia.A.w  J=0.002" for a sweep run
    required property color traceColor
    property bool present: true
    property string error: ""  // why an expression trace cannot be drawn (W7)
    property Item dragParent: null

    signal cursorRequested(int which)  // 1, 2 or 3 (both)
    signal editRequested()  // "Edit expression…" (W8)
    signal statsRequested()  // Ctrl+click: average and RMS (W10)

    objectName: "label_" + paneIndex + "_" + name
    width: chip.implicitWidth
    height: chip.implicitHeight

    Rectangle {
        id: chip
        objectName: "chip"
        // Read by the pane's DropArea.
        property int paneIndex: label.paneIndex
        property string traceName: label.name

        implicitWidth: text.implicitWidth + 8
        implicitHeight: text.implicitHeight + 2
        width: label.width
        height: label.height
        radius: 2
        color: mouse.drag.active ? "#ffe9a8"
             : mouse.containsMouse && WaveformController.deleteMode ? "#ffd6d6"
             : "transparent"
        border.color: mouse.drag.active ? "#c8a040" : "transparent"
        Drag.active: mouse.drag.active
        Drag.dragType: Drag.Internal
        Drag.keys: ["qtspice/trace"]
        Drag.supportedActions: Qt.MoveAction | Qt.CopyAction
        Drag.hotSpot: Qt.point(width / 2, height / 2)

        Label {
            id: text
            anchors.centerIn: parent
            text: label.caption
            color: label.traceColor
            font.pointSize: 9
            font.strikeout: !label.present
        }

        states: State {
            when: mouse.drag.active && label.dragParent !== null
            ParentChange { target: chip; parent: label.dragParent }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        drag.target: chip
        drag.threshold: 6
        cursorShape: WaveformController.deleteMode ? Qt.PointingHandCursor : Qt.OpenHandCursor
        ToolTip.visible: containsMouse && label.error !== "" && !drag.active
        ToolTip.text: label.error

        onPressed: event => {
            chip.Drag.proposedAction = Qt.MoveAction
            if (event.button === Qt.RightButton && !WaveformController.deleteMode)
                labelMenu.popup()
        }
        onReleased: event => {
            if (!drag.active)
                return
            chip.Drag.proposedAction = (event.modifiers & Qt.ControlModifier)
                                       ? Qt.CopyAction : Qt.MoveAction
            const action = chip.Drag.drop()
            chip.x = 0
            chip.y = 0
            // Dropped outside every pane: remove. Later, because the removal rebuilds
            // the labels, this one included.
            if (action === Qt.IgnoreAction) {
                const pane = label.paneIndex, name = label.name
                Qt.callLater(() => WaveformController.removeTrace(pane, name))
            }
        }
        onClicked: event => {
            if (event.button !== Qt.LeftButton)
                return
            if (WaveformController.deleteMode) {
                const pane = label.paneIndex, name = label.name
                Qt.callLater(() => WaveformController.removeTrace(pane, name))
            } else if (event.modifiers & Qt.ControlModifier) {
                label.statsRequested()
            } else {
                label.cursorRequested(1)
            }
        }
        onDoubleClicked: event => {
            if (event.button === Qt.LeftButton && !WaveformController.deleteMode) {
                const pane = label.paneIndex, name = label.name
                Qt.callLater(() => WaveformController.showOnly(pane, name))
            }
        }
    }

    Menu {
        id: labelMenu
        MenuItem {
            objectName: "cursor1Item"
            text: qsTr("Attach cursor 1")
            enabled: label.present
            onTriggered: label.cursorRequested(1)
        }
        MenuItem {
            objectName: "cursor2Item"
            text: qsTr("Attach cursor 2")
            enabled: label.present
            onTriggered: label.cursorRequested(2)
        }
        MenuItem {
            objectName: "cursorBothItem"
            text: qsTr("Attach both cursors")
            enabled: label.present
            onTriggered: label.cursorRequested(3)
        }
        MenuSeparator {}
        MenuItem {
            objectName: "editExpressionItem"
            text: qsTr("Edit expression…")
            onTriggered: label.editRequested()
        }
        MenuItem {
            text: qsTr("Show only this trace")
            onTriggered: WaveformController.showOnly(label.paneIndex, label.name)
        }
        MenuItem {
            text: qsTr("Remove trace")
            onTriggered: WaveformController.removeTrace(label.paneIndex, label.name)
        }
    }
}
