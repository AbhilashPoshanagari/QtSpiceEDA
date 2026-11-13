import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// The Grid tab (G6): the open .dss file, the totals of the last power flow, losses
// per element class and the phase voltages of the selected bus. Read-only.
ScrollView {
    id: root
    objectName: "gridPanel"
    signal openRequested()
    contentWidth: availableWidth

    ColumnLayout {
        width: root.availableWidth
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 8
            Button {
                objectName: "gridOpenButton"
                text: qsTr("Open .dss…")
                enabled: !GridController.running
                onClicked: root.openRequested()
            }
            Label {
                objectName: "gridFileName"
                Layout.fillWidth: true
                elide: Text.ElideMiddle
                text: GridController.fileName !== "" ? GridController.fileName : qsTr("No feeder open")
                font.bold: GridController.fileName !== ""
                ToolTip.visible: hovered && GridController.filePath !== ""
                ToolTip.text: GridController.filePath
                HoverHandler { id: hovered_ }
                readonly property bool hovered: hovered_.hovered
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            Layout.rightMargin: 8
            wrapMode: Text.WordWrap
            visible: GridController.summary.elements !== undefined
            text: qsTr("%1 (%2 Hz): %3").arg(GridController.summary.circuit)
                  .arg(GridController.summary.baseFrequency).arg(GridController.summary.elements)
            color: palette.placeholderText
        }

        GroupBox {
            objectName: "gridTotals"
            Layout.fillWidth: true
            Layout.leftMargin: 8
            Layout.rightMargin: 8
            title: qsTr("Power flow")
            visible: GridController.hasResult
            GridLayout {
                columns: 2
                columnSpacing: 12
                Label { text: qsTr("Iterations") }
                Label {
                    text: qsTr("%1 (%2 control)").arg(GridController.summary.iterations)
                          .arg(GridController.summary.controlIterations)
                }
                Label { text: qsTr("Source power") }
                Label {
                    text: qsTr("%1 kW, %2 kvar").arg(GridController.summary.totalKw)
                          .arg(GridController.summary.totalKvar)
                }
                Label { text: qsTr("Losses") }
                Label {
                    objectName: "gridLosses"
                    text: qsTr("%1 kW, %2 kvar").arg(GridController.summary.lossesKw)
                          .arg(GridController.summary.lossesKvar)
                }
                Label { text: qsTr("Violations") }
                Label {
                    text: GridController.summary.violationCount ?? ""
                    color: GridController.summary.violationCount > 0 ? "#d73a49" : palette.text
                }
            }
        }

        GroupBox {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            Layout.rightMargin: 8
            title: qsTr("Losses by element class")
            visible: GridController.hasResult
            GridLayout {
                columns: 4
                columnSpacing: 12
                Label { text: qsTr("Class"); font.bold: true }
                Label { text: qsTr("Count"); font.bold: true }
                Label { text: qsTr("kW"); font.bold: true }
                Label { text: qsTr("kvar"); font.bold: true }
                Repeater {
                    model: GridController.hasResult ? GridController.summary.losses : []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        text: modelData.kind
                        Layout.row: index + 1
                        Layout.column: 0
                    }
                }
                Repeater {
                    model: GridController.hasResult ? GridController.summary.losses : []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        text: modelData.count
                        Layout.row: index + 1
                        Layout.column: 1
                        Layout.alignment: Qt.AlignRight
                    }
                }
                Repeater {
                    model: GridController.hasResult ? GridController.summary.losses : []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        text: modelData.kw
                        Layout.row: index + 1
                        Layout.column: 2
                        Layout.alignment: Qt.AlignRight
                    }
                }
                Repeater {
                    model: GridController.hasResult ? GridController.summary.losses : []
                    delegate: Label {
                        required property var modelData
                        required property int index
                        text: modelData.kvar
                        Layout.row: index + 1
                        Layout.column: 3
                        Layout.alignment: Qt.AlignRight
                    }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.margins: 8
            wrapMode: Text.WordWrap
            color: palette.placeholderText
            text: qsTr("Click a bus in the single-line view, or a row of the node table, "
                       + "to see its phase voltages. The voltage profile is in the "
                       + "Waveforms tab; messages are in the Console.")
        }
    }
}
