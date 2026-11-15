import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice

// Results of the last grid power flow (G6): node voltages (one row per node, or one
// row per bus), branches and violations. Read-only. Click a column header to sort by
// it (again to reverse). The filter keeps the rows whose bus / element / location
// contains the text, in every tab; "Only violations" keeps the highlighted rows.
// Rows in violation are highlighted; the violations themselves come from
// GridController (core/grid/analysis.py, rule 19). Clicking a node or bus row
// selects its bus. Export CSV saves the rows shown in the current tab.
ColumnLayout {
    id: root
    objectName: "gridResultsTable"
    spacing: 0

    property alias currentIndex: tabs.currentIndex
    property alias perBus: perBusBox.checked
    readonly property var models: [GridController.nodes, GridController.buses,
                                   GridController.branches, GridController.violations]
    // The model of the table shown now.
    readonly property var currentModel: tabs.currentIndex === 0
                                        ? (perBus ? GridController.buses : GridController.nodes)
                                        : tabs.currentIndex === 1 ? GridController.branches
                                                                  : GridController.violations

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

    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: 4
        Layout.rightMargin: 4
        spacing: 6
        TextField {
            id: filterField
            objectName: "gridFilter"
            Layout.preferredWidth: 160
            placeholderText: qsTr("Filter bus or element")
            selectByMouse: true
            onTextChanged: { for (const m of root.models) m.setFilter(text) }
        }
        CheckBox {
            id: onlyBox
            objectName: "gridOnlyViolations"
            text: qsTr("Only violations")
            onToggled: { for (const m of root.models) m.setOnlyHighlighted(checked) }
        }
        CheckBox {
            id: perBusBox
            objectName: "gridPerBus"
            text: qsTr("One row per bus")
            visible: tabs.currentIndex === 0
        }
        Item { Layout.fillWidth: true }
        Label {
            objectName: "gridRowCount"
            color: palette.placeholderText
            visible: root.currentModel.totalCount > 0
            text: root.currentModel.count === root.currentModel.totalCount
                  ? qsTr("%1 rows").arg(root.currentModel.count)
                  : qsTr("%1 of %2 rows").arg(root.currentModel.count).arg(root.currentModel.totalCount)
        }
        ToolButton {
            objectName: "gridExportCsv"
            text: qsTr("Export CSV…")
            enabled: root.currentModel.count > 0
            onClicked: csvDialog.open()
        }
    }

    FileDialog {
        id: csvDialog
        title: qsTr("Export the table")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "csv"
        nameFilters: [qsTr("CSV files (*.csv)")]
        onAccepted: root.currentModel.saveCsv(selectedFile.toString())
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: tabs.currentIndex

        StackLayout {
            currentIndex: root.perBus ? 1 : 0
            ResultTable {
                objectName: "nodeTable"
                tableModel: GridController.nodes
                widths: [90, 60, 90, 90, 90]
                selectsBus: true
            }
            ResultTable {
                objectName: "busTable"
                tableModel: GridController.buses
                widths: [100, 80, 80, 80, 80, 80]
                selectsBus: true
            }
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
        // Columns grow in proportion to fill the width (never shrink below `widths`).
        readonly property real stretch: {
            let sum = 0
            for (let c = 0; c < tableModel.columnCount(); c++)
                sum += c < widths.length ? widths[c] : 90
            return sum > 0 ? Math.max(1, (table.width - 2) / sum) : 1
        }
        onStretchChanged: table.forceLayout()

        Label {
            anchors.centerIn: parent
            z: 1
            visible: table.rows === 0
            color: palette.placeholderText
            text: holder.tableModel.totalCount > 0
                  ? qsTr("No rows match the filter.")
                  : holder.emptyText !== "" ? holder.emptyText
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
            columnWidthProvider: column => (column < holder.widths.length ? holder.widths[column] : 90)
                                           * holder.stretch
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
