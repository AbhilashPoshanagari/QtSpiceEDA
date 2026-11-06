import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Cursor readout (W6): the trace, X and value of cursor 1 and cursor 2 and, with both
// attached, their differences. For a .tran result Δx is also shown as a frequency
// (1/Δx); for .ac the values are magnitude (dB) and phase.
Rectangle {
    id: root
    objectName: "cursorReadout"
    readonly property var cursors: WaveformController.cursors
    readonly property var diff: WaveformController.cursorDiff
    readonly property bool isAc: WaveformController.isAc
    visible: cursors.length > 0
    implicitHeight: visible ? grid.implicitHeight + 6 : 0
    color: "#f7f7f7"
    border.color: "#dddddd"

    GridLayout {
        id: grid
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: 3
        anchors.leftMargin: 8
        columns: root.isAc ? 5 : 4
        columnSpacing: 16
        rowSpacing: 1

        component Cell: Label { font.pointSize: 8 }

        Cell { text: qsTr("cursor"); font.bold: true }
        Cell { text: qsTr("trace"); font.bold: true }
        Cell { text: WaveformController.xLabel; font.bold: true }
        Cell { text: root.isAc ? qsTr("magnitude") : qsTr("value"); font.bold: true }
        Cell { text: qsTr("phase"); font.bold: true; visible: root.isAc }

        Repeater {
            model: root.cursors
            delegate: Cell {
                required property var modelData
                objectName: "cursor" + modelData.which + "Name"
                Layout.row: modelData.which
                Layout.column: 0
                text: qsTr("Cursor %1").arg(modelData.which)
            }
        }
        Repeater {
            model: root.cursors
            delegate: Cell {
                required property var modelData
                Layout.row: modelData.which
                Layout.column: 1
                text: modelData.name
                color: modelData.color
            }
        }
        Repeater {
            model: root.cursors
            delegate: Cell {
                required property var modelData
                objectName: "cursor" + modelData.which + "X"
                Layout.row: modelData.which
                Layout.column: 2
                text: modelData.xText
            }
        }
        Repeater {
            model: root.cursors
            delegate: Cell {
                required property var modelData
                objectName: "cursor" + modelData.which + "Y"
                Layout.row: modelData.which
                Layout.column: 3
                text: modelData.yText
            }
        }
        Repeater {
            model: root.isAc ? root.cursors : []
            delegate: Cell {
                required property var modelData
                objectName: "cursor" + modelData.which + "Phase"
                Layout.row: modelData.which
                Layout.column: 4
                text: modelData.phaseText
            }
        }

        // Differences (both cursors attached).
        Cell {
            visible: root.diff.dxText !== undefined
            Layout.row: 3
            Layout.column: 0
            text: qsTr("2 − 1")
            font.bold: true
        }
        Cell {
            objectName: "cursorFreq"
            visible: root.diff.dxText !== undefined
            Layout.row: 3
            Layout.column: 1
            text: root.diff.freqText ? qsTr("1/Δx = %1").arg(root.diff.freqText) : ""
        }
        Cell {
            objectName: "cursorDx"
            visible: root.diff.dxText !== undefined
            Layout.row: 3
            Layout.column: 2
            text: root.diff.dxText || ""
        }
        Cell {
            objectName: "cursorDy"
            visible: root.diff.dxText !== undefined
            Layout.row: 3
            Layout.column: 3
            text: root.diff.dyText || ""
        }
        Cell {
            objectName: "cursorDPhase"
            visible: root.isAc && root.diff.dxText !== undefined
            Layout.row: 3
            Layout.column: 4
            text: root.diff.dPhaseText || ""
        }
    }

    ToolButton {
        objectName: "cursorsOffButton"
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: qsTr("Remove cursors")
        font.pointSize: 8
        onClicked: WaveformController.detachCursors()
    }
}
