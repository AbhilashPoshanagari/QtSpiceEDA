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
    signal violationsRequested()
    contentWidth: availableWidth

    component SummaryCard: Rectangle {
        id: card
        property string title
        property string value
        property string detail
        property string tip: ""
        property bool alert: false
        signal clicked()
        Layout.fillWidth: true
        Layout.preferredWidth: 1  // equal columns
        implicitHeight: cardColumn.implicitHeight + 12
        radius: 4
        color: alert ? "#fde2e1" : palette.alternateBase
        border.color: cardHover.hovered && tip !== "" ? palette.highlight : palette.mid
        ColumnLayout {
            id: cardColumn
            anchors.fill: parent
            anchors.margins: 6
            spacing: 1
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: card.title
                color: card.alert ? "#b31d28" : palette.placeholderText
                font.pointSize: 8
            }
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: card.value
                font.pointSize: 12
                font.bold: true
                color: card.alert ? "#b31d28" : palette.text
            }
            Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: card.detail
                visible: text !== ""
                font.pointSize: 8
                color: card.alert ? "#b31d28" : palette.placeholderText
            }
        }
        HoverHandler { id: cardHover; cursorShape: card.tip !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor }
        TapHandler { onTapped: card.clicked() }
        ToolTip.visible: cardHover.hovered && tip !== ""
        ToolTip.delay: 600
        ToolTip.text: tip
    }

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

        // Totals of the last power flow as cards; Violations and the voltage range are
        // clickable (the violation list, the bus with the lowest voltage).
        GridLayout {
            id: totals
            objectName: "gridTotals"
            Layout.fillWidth: true
            Layout.leftMargin: 8
            Layout.rightMargin: 8
            visible: GridController.hasResult
            columns: root.availableWidth > 430 ? 4 : 2
            columnSpacing: 6
            rowSpacing: 6
            readonly property var s: GridController.summary

            SummaryCard {
                objectName: "cardSource"
                title: qsTr("Source power")
                value: qsTr("%1 kW").arg(totals.s.totalKw)
                detail: qsTr("%1 kvar").arg(totals.s.totalKvar)
            }
            SummaryCard {
                objectName: "gridLosses"
                title: qsTr("Losses")
                value: qsTr("%1 kW").arg(totals.s.lossesKw)
                detail: totals.s.lossesPct !== undefined
                        ? qsTr("%1 % · %2 kvar").arg(totals.s.lossesPct).arg(totals.s.lossesKvar)
                        : qsTr("%1 kvar").arg(totals.s.lossesKvar)
            }
            SummaryCard {
                objectName: "cardVoltage"
                title: qsTr("Min / max V (pu)")
                value: totals.s.minPu !== undefined ? totals.s.minPu + " / " + totals.s.maxPu : "—"
                detail: totals.s.minPu !== undefined
                        ? qsTr("low %1 · high %2").arg(totals.s.minPuBus).arg(totals.s.maxPuBus) : ""
                tip: qsTr("Select the bus with the lowest voltage")
                onClicked: GridController.selectBus(totals.s.minPuBus ?? "")
            }
            SummaryCard {
                objectName: "cardViolations"
                readonly property int n: totals.s.violationCount ?? 0
                title: qsTr("Violations")
                value: String(n)
                detail: n > 0 ? qsTr("show the list") : qsTr("none")
                alert: n > 0
                tip: qsTr("Show the violations in the results table")
                onClicked: root.violationsRequested()
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            visible: GridController.hasResult
            color: palette.placeholderText
            text: qsTr("Converged in %1 iterations (%2 control)")
                  .arg(GridController.summary.iterations)
                  .arg(GridController.summary.controlIterations)
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
