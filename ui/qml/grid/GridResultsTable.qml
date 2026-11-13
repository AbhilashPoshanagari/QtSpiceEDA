import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Results of the last grid power flow (G6): node voltages, branches and violations.
// Read-only. Click a column header to sort by it (again to reverse). Rows in
// violation are highlighted; the violations themselves come from GridController
// (core/grid/analysis.py, rule 19). Clicking a node row selects its bus.
ColumnLayout {
    id: root
    objectName: "gridResultsTable"
    spacing: 0

    property alias currentIndex: tabs.currentIndex

    TabBar {
        id: tabs
        objectName: "gridResultTabs"
        Layout.fillWidth: true
        TabButton { text: qsTr("Node voltages") }
        TabButton { text: qsTr("Branches") }
        TabButton {
            text: GridController.hasResult
                  ? qsTr("Violations (%1)").arg(GridController.summary.violationCount)
                  : qsTr("Violations")
        }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: tabs.currentIndex

        ResultTable {
            objectName: "nodeTable"
            tableModel: GridController.nodes
            widths: [90, 60, 90, 90, 90]
            selectsBus: true
        }
        ResultTable {
            objectName: "branchTable"
            tableModel: GridController.branches
            widths: [170, 80, 80, 190, 90, 90, 95, 100, 95]
        }
        ResultTable {
            objectName: "violationTable"
            tableModel: GridController.violations
            widths: [110, 160, 100, 80, 380]
            emptyText: GridController.hasResult ? qsTr("No violations.") : ""
        }
    }

    component ResultTable: Item {
        id: holder
        required property var tableModel
        property var widths: []
        property bool selectsBus: false
        property string emptyText: ""
        readonly property alias table: table

        Label {
            anchors.centerIn: parent
            z: 1
            visible: table.rows === 0
            color: palette.placeholderText
            text: holder.emptyText !== "" ? holder.emptyText
                                          : qsTr("Open a .dss file and press Run to see results.")
        }

        HorizontalHeaderView {
            id: header
            objectName: "header"
            anchors.left: table.left
            anchors.top: parent.top
            anchors.right: parent.right
            syncView: table
            clip: true
            delegate: Rectangle {
                implicitHeight: 26
                implicitWidth: 80
                color: palette.button
                border.color: palette.mid
                Label {
                    anchors.fill: parent
                    anchors.leftMargin: 6
                    anchors.rightMargin: 6
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                    font.bold: true
                    text: display
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: holder.tableModel.sortBy(column)
                }
            }
        }

        TableView {
            id: table
            objectName: "table"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: header.bottom
            anchors.bottom: parent.bottom
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: holder.tableModel
            columnWidthProvider: column => column < holder.widths.length ? holder.widths[column] : 90
            ScrollBar.vertical: ScrollBar {}
            ScrollBar.horizontal: ScrollBar {}
            delegate: Rectangle {
                required property string display
                required property bool highlight
                required property bool numeric
                required property int row
                implicitHeight: 22
                color: highlight ? "#fde2e1" : (row % 2 ? palette.alternateBase : palette.base)
                Label {
                    anchors.fill: parent
                    anchors.leftMargin: 6
                    anchors.rightMargin: 6
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: numeric ? Text.AlignRight : Text.AlignLeft
                    elide: Text.ElideRight
                    color: highlight ? "#b31d28" : palette.text
                    text: display
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: holder.selectsBus
                    onClicked: GridController.selectBus(holder.tableModel.cell(row, 0))
                }
            }
        }
    }
}
