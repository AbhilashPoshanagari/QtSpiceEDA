import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice

// Waveform window (W1-W6). Panes are stacked top to bottom and share one X axis
// (WaveformController); each pane autoscales its own Y. The layout of the panes is kept
// per analysis type and saved in the project. Every pane and trace change is undoable.
//
// Mouse in a pane: drag a box to zoom (Esc cancels; X zooms every pane, Y only that
// one), wheel zooms X around the pointer, middle-drag pans, double-click auto-ranges,
// right-click opens the pane menu (add / delete panes, zoom back, axes, settings).
// Zooms are undoable and Zoom back walks the zoom history. The status bar shows the
// mouse position, and while dragging the box size with 1/Δx as a frequency.
FocusScope {
    id: root
    objectName: "waveformView"

    readonly property bool isAc: WaveformController.isAc
    property string statusText: ""

    Component.onCompleted: {
        WaveformController.attach(WaveformModel)
        syncPanes()
    }

    // One delegate per pane. A ListModel that only grows or shrinks at the end keeps
    // the other panes' GraphsViews (and their series) alive when the count changes.
    ListModel { id: paneSlots }
    function syncPanes() {
        const n = WaveformController.paneCount
        while (paneSlots.count < n)
            paneSlots.append({})
        while (paneSlots.count > n)
            paneSlots.remove(paneSlots.count - 1)
    }
    Connections {
        target: WaveformController
        function onLayoutChanged() { root.syncPanes() }
    }
    Component.onDestruction: paneSlots.clear()

    // The pane item at `index` (for tests and the cursor overlay).
    function paneAt(index) {
        return paneRepeater.itemAt(index)
    }

    Keys.onPressed: event => {
        if (event.matches(StandardKey.Undo)) {
            WaveformController.undo()
            event.accepted = true
        } else if (event.matches(StandardKey.Redo)) {
            WaveformController.redo()
            event.accepted = true
        }
    }

    FileDialog {
        id: exportDialog
        title: qsTr("Export waveforms as CSV")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "csv"
        nameFilters: [qsTr("CSV files (*.csv)")]
        onAccepted: WaveformModel.exportCsv(selectedFile)
    }

    PlotConfigDialog {
        id: configDialog
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Traces of the result: ticked = shown in a pane. Ticking adds to the active pane.
        ColumnLayout {
            Layout.preferredWidth: 180
            Layout.maximumWidth: 180
            Layout.fillWidth: false
            Layout.fillHeight: true
            Layout.margins: 4
            spacing: 2

            Label {
                text: WaveformModel.hasAxis
                      ? qsTr("%1 (%2 points)").arg(WaveformModel.analysis.toUpperCase())
                                              .arg(WaveformModel.pointCount)
                      : qsTr("Traces")
                font.bold: true
            }
            ListView {
                id: traceList
                objectName: "traceList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: WaveformController.resultTraces
                delegate: CheckBox {
                    required property var modelData
                    objectName: "trace_" + modelData.name
                    width: ListView.view.width
                    checked: modelData.shown
                    text: modelData.name
                    onToggled: WaveformController.setTraceShown(modelData.name, checked)
                    indicator.implicitWidth: 16
                    Rectangle {
                        anchors.right: parent.right
                        anchors.rightMargin: 4
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14
                        height: 3
                        color: modelData.color
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
            Button {
                objectName: "exportCsvButton"
                Layout.fillWidth: true
                text: qsTr("Export CSV…")
                enabled: WaveformModel.traceNames.length > 0
                onClicked: exportDialog.open()
            }
            Label {
                Layout.fillWidth: true
                visible: WaveformModel.errorString.length > 0
                text: WaveformModel.errorString
                color: "#d73a49"
                wrapMode: Text.WordWrap
            }
        }

        ToolSeparator { Layout.fillHeight: true; orientation: Qt.Vertical }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // ---- toolbar
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 4
                spacing: 2
                enabled: WaveformController.hasData
                ToolButton {
                    objectName: "undoButton"
                    text: qsTr("Undo")
                    enabled: WaveformController.canUndo
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Undo %1 (Ctrl+Z)").arg(WaveformController.undoText)
                    onClicked: WaveformController.undo()
                }
                ToolButton {
                    objectName: "redoButton"
                    text: qsTr("Redo")
                    enabled: WaveformController.canRedo
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Redo %1 (Ctrl+Y)").arg(WaveformController.redoText)
                    onClicked: WaveformController.redo()
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "zoomBackButton"
                    text: qsTr("Zoom back")
                    enabled: WaveformController.canZoomBack
                    onClicked: WaveformController.zoomBack()
                }
                ToolButton {
                    objectName: "autoRangeButton"
                    text: qsTr("Auto-range")
                    onClicked: WaveformController.autoRange()
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "configButton"
                    text: qsTr("Plot settings…")
                    onClicked: configDialog.openFor(WaveformController.activePane)
                }
                Item { Layout.fillWidth: true }
            }

            Item {
                id: plotArea
                Layout.fillWidth: true
                Layout.fillHeight: true

                Label {
                    anchors.centerIn: parent
                    visible: !WaveformController.hasData
                    color: palette.placeholderText
                    text: WaveformModel.analysis === "op"
                          ? qsTr("Operating point: the values are listed in the Console.")
                          : qsTr("Run a .dc, .ac or .tran analysis to see waveforms.")
                }

                Column {
                    id: paneColumn
                    anchors.fill: parent
                    visible: WaveformController.hasData
                    spacing: 2
                    Repeater {
                        id: paneRepeater
                        model: paneSlots
                        delegate: PlotPane {
                            required property int index
                            objectName: "pane" + index
                            paneIndex: index
                            width: paneColumn.width
                            height: Math.max(60, (paneColumn.height - paneColumn.spacing
                                                  * (paneSlots.count - 1)) / Math.max(1, paneSlots.count))
                            onStatus: text => root.statusText = text
                            onConfigure: index => configDialog.openFor(index)
                        }
                    }
                }
            }

            // ---- status bar
            Label {
                objectName: "statusBar"
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.preferredHeight: implicitHeight + 4
                text: root.statusText
                font.pointSize: 8
                color: "#404040"
                elide: Text.ElideRight
            }
        }
    }
}
