import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Feasibility panel (D8): the FeasibilityReport of the designer's last run
// (GridDiagramController.feasibility, computed in core/grid/analysis.py): Level 1
// (topology and validation) and Level 2 (electrical checks of ONE snapshot), each row
// with a status icon and its worst value. Clicking a row asks the host to select and
// centre that bus or element (elementActivated -> GridDiagramController.focusElement).
// The snapshot note (rule 27) is always visible, with or without a report.
Frame {
    id: panel
    objectName: "gridFeasibilityPanel"
    required property var designer
    signal elementActivated(string elementId)

    readonly property var report: designer.feasibility
    readonly property bool hasReport: report.status !== undefined
    readonly property var colors: ({ pass: "#2da44e", warn: "#d4a72c", fail: "#d73a49",
                                     none: "#6e7781", off: "#b0b0b0" })
    function statusColor(s) { return colors[s] !== undefined ? colors[s] : "#6e7781" }
    function statusMark(s) {
        return ({ pass: "✓", warn: "!", fail: "✕", none: "i", off: "–" })[s] || "?"
    }

    padding: 4
    background: Rectangle { color: palette.base; border.color: palette.mid }

    component StatusIcon: Rectangle {
        property string status: ""
        width: 16; height: 16; radius: 8
        color: panel.statusColor(status)
        Label {
            anchors.centerIn: parent
            text: panel.statusMark(parent.status)
            color: "white"
            font.bold: true
            font.pixelSize: 10
        }
    }

    component SectionHeader: RowLayout {
        property string title
        property string status
        Layout.fillWidth: true
        Layout.topMargin: 4
        StatusIcon { status: parent.status; visible: parent.status !== "" }
        Label { text: parent.title; font.bold: true; Layout.fillWidth: true }
    }

    // One clickable row: icon, title, value, message.
    component ReportRow: ItemDelegate {
        id: row
        property string status
        property string title
        property string value
        property string detail
        property string elementId
        Layout.fillWidth: true
        padding: 2
        implicitHeight: rowLayout.implicitHeight + 4
        enabled: elementId !== ""
        onClicked: panel.elementActivated(elementId)
        hoverEnabled: true
        ToolTip.visible: hovered && elementId !== ""
        ToolTip.delay: 600
        ToolTip.text: qsTr("Select and show %1").arg(elementId)
        contentItem: RowLayout {
            id: rowLayout
            spacing: 6
            StatusIcon { status: row.status; Layout.alignment: Qt.AlignTop }
            Label {
                text: row.title
                font.bold: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 130
                elide: Text.ElideRight
                color: palette.text
            }
            Label {
                text: row.value
                visible: row.value !== ""
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 90
                font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                color: palette.text
            }
            Label {
                text: row.detail
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: palette.text
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 2

        RowLayout {
            Layout.fillWidth: true
            Label { text: qsTr("Feasibility"); font.bold: true; font.pointSize: 11 }
            StatusIcon {
                objectName: "feasibilityStatus"
                visible: panel.hasReport
                status: panel.hasReport ? panel.report.status : ""
            }
            Label {
                visible: panel.hasReport
                text: panel.hasReport ? panel.report.status.toUpperCase() : ""
                color: panel.statusColor(panel.hasReport ? panel.report.status : "")
                font.bold: true
            }
            Label {
                objectName: "feasibilityStale"
                visible: panel.hasReport && panel.designer.resultsStale
                text: qsTr("results out of date - Run again")
                color: "#9a6700"
            }
            Item { Layout.fillWidth: true }
        }

        // rule 27: always visible
        Label {
            objectName: "feasibilitySnapshotNote"
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            font.italic: true
            color: palette.placeholderText
            text: panel.designer.snapshotNote
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            opacity: panel.designer.resultsStale ? 0.6 : 1.0

            ColumnLayout {
                width: parent.width
                spacing: 1

                Label {
                    objectName: "feasibilityEmpty"
                    visible: !panel.hasReport
                    text: panel.designer.hasDesign ? qsTr("Run the design to check its feasibility.")
                                                   : qsTr("No design.")
                    color: palette.placeholderText
                }

                SectionHeader {
                    visible: panel.hasReport
                    title: qsTr("Level 1 - topology and validation")
                    status: panel.hasReport ? panel.report.level1Status : ""
                }
                Label {
                    visible: panel.hasReport && panel.report.level1.length === 0
                    text: qsTr("No topology or validation problems.")
                    color: palette.placeholderText
                    leftPadding: 22
                }
                Repeater {
                    objectName: "feasibilityLevel1"
                    model: panel.hasReport ? panel.report.level1 : []
                    delegate: ReportRow {
                        required property var modelData
                        objectName: "feasibilityL1_" + modelData.element
                        status: modelData.status
                        title: modelData.element
                        value: ""
                        detail: modelData.message
                        elementId: modelData.element
                    }
                }

                SectionHeader {
                    visible: panel.hasReport
                    title: qsTr("Level 2 - electrical, one snapshot")
                    status: panel.hasReport ? panel.report.level2Status : ""
                }
                Label {
                    visible: panel.hasReport && !panel.report.solved
                    text: qsTr("Not solved: there is no snapshot to check.")
                    color: "#d73a49"
                    leftPadding: 22
                }
                Repeater {
                    objectName: "feasibilityLevel2"
                    model: panel.hasReport ? panel.report.level2 : []
                    delegate: ReportRow {
                        required property var modelData
                        objectName: "feasibilityL2_" + modelData.name
                        status: modelData.status
                        title: modelData.label
                        value: modelData.value
                        detail: modelData.element !== ""
                                ? qsTr("at %1, limit %2, %3 checked").arg(modelData.element)
                                      .arg(modelData.limit).arg(modelData.count)
                                : qsTr("nothing to check")
                        elementId: modelData.element
                    }
                }
                Repeater {
                    model: panel.hasReport ? panel.report.info : []
                    delegate: ReportRow {
                        required property var modelData
                        status: modelData.status
                        title: modelData.element
                        value: ""
                        detail: modelData.message
                        elementId: modelData.element
                    }
                }
                Repeater {
                    model: panel.hasReport ? panel.report.summary : []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.leftMargin: 22
                        Label { text: modelData.label; Layout.preferredWidth: 130; color: palette.placeholderText }
                        Label { text: modelData.value; color: palette.text }
                    }
                }
            }
        }
    }
}
