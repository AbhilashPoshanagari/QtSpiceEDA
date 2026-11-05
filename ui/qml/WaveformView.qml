import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtGraphs
import QtSpice

// Waveform viewer (F4).
// Mouse: wheel zooms x around the pointer, drag pans, double-click fits. Drag the A / B
// cursor lines to move them. AC results show magnitude in dB and phase on a log
// frequency axis (log10 f plotted on a linear axis, labelled in Hz).
// Each redraw asks WaveformModel for at most two points per pixel and trace.
Item {
    id: root
    objectName: "waveformView"

    readonly property var colors: ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
                                   "#8c564b", "#e377c2", "#17becf", "#7f7f7f", "#bcbd22"]
    readonly property bool isAc: WaveformModel.isAc
    property real x0: 0
    property real x1: 1
    property real cursorA: 0
    property real cursorB: 1
    property bool cursorsVisible: true

    function colorFor(index) {
        return colors[Math.max(0, index) % colors.length]  // -1: not a trace (indexOf)
    }

    function fit() {
        const lo = WaveformModel.xMin
        const hi = WaveformModel.xMax > lo ? WaveformModel.xMax : lo + 1
        x0 = lo
        x1 = hi
        cursorA = lo + (hi - lo) * 0.25
        cursorB = lo + (hi - lo) * 0.75
        scheduleRefresh()
    }

    // Zoom the x range by `factor` (<1 zooms in) keeping `anchor` in place.
    function zoom(factor, anchor) {
        const span = (x1 - x0) * factor
        const full = Math.max(WaveformModel.xMax - WaveformModel.xMin, 1e-300)
        if (span < full * 1e-9)
            return
        let lo = anchor - (anchor - x0) * factor
        let hi = lo + span
        if (span >= full) {
            lo = WaveformModel.xMin
            hi = WaveformModel.xMax
        }
        x0 = lo
        x1 = hi
        scheduleRefresh()
    }

    function pan(dx) {
        x0 += dx
        x1 += dx
        scheduleRefresh()
    }

    function scheduleRefresh() {
        refreshTimer.restart()
    }

    function refresh() {
        magnitudePlot.refresh()
        if (isAc)
            phasePlot.refresh()
    }

    Timer {
        id: refreshTimer
        interval: 16
        onTriggered: root.refresh()
    }

    Connections {
        target: WaveformModel
        function onResultChanged() { root.fit() }
        function onVisibilityChanged() { root.scheduleRefresh() }
    }

    FileDialog {
        id: exportDialog
        title: qsTr("Export waveforms as CSV")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "csv"
        nameFilters: [qsTr("CSV files (*.csv)")]
        onAccepted: WaveformModel.exportCsv(selectedFile)
    }

    // One GraphsView. `phase` selects the AC phase plot. GraphsView 6.8 has no
    // plot-area property; with labels shown its axes take a fixed 60 px on the left
    // and 40 px at the bottom (checked by tests/ui/test_waveform_view.py).
    component Plot: Item {
        id: plot
        property bool phase: false
        property string yTitle: ""
        readonly property real plotLeft: graph.marginLeft + 60
        readonly property real plotTop: graph.marginTop
        readonly property real plotWidth: Math.max(1, graph.width - graph.marginRight - plotLeft)
        readonly property real plotHeight: Math.max(1, graph.height - graph.marginBottom - 40
                                                       - plotTop)
        // [{name, visible, count}] after the last refresh (for tests and debugging).
        property var seriesState: []

        function toPixel(x) {
            return plotLeft + (x - root.x0) / (root.x1 - root.x0) * plotWidth
        }
        function toX(px) {
            return root.x0 + (px - plotLeft) / plotWidth * (root.x1 - root.x0)
        }
        function refresh() {
            const names = WaveformModel.traceNames
            const state = []
            growPool(names.length)
            for (let i = 0; i < pool.length; ++i) {
                const s = pool[i]
                const used = i < names.length
                const shown = used && WaveformModel.isVisible(names[i])
                s.name = used ? names[i] : ""
                s.visible = shown
                if (shown)
                    WaveformModel.fillSeries(s, names[i], root.x0, root.x1,
                                             Math.round(plotWidth), phase)
                else
                    s.clear()
                if (used)
                    state.push({ name: names[i], visible: shown, count: s.count })
            }
            seriesState = state
            const range = WaveformModel.yRange(root.x0, root.x1, phase)
            yAxis.min = range[0]
            yAxis.max = range[1]
            setLine(cursorASeries, root.cursorA)
            setLine(cursorBSeries, root.cursorB)
        }
        function setLine(s, x) {
            s.clear()
            s.append(x, yAxis.min)
            s.append(x, yAxis.max)
        }

        onPlotWidthChanged: root.scheduleRefresh()

        Connections {
            target: root
            function onCursorAChanged() { plot.setLine(cursorASeries, root.cursorA) }
            function onCursorBChanged() { plot.setLine(cursorBSeries, root.cursorB) }
        }

        GraphsView {
            id: graph
            anchors.fill: parent
            marginLeft: 4
            marginRight: 12
            marginTop: 8
            marginBottom: 2
            theme: GraphsTheme {
                colorScheme: GraphsTheme.ColorScheme.Light
                gridVisible: true
            }
            axisX: ValueAxis {
                min: root.x0
                max: root.x1
                tickAnchor: 0
                // One label per decade on the AC log axis; automatic otherwise.
                tickInterval: root.isAc && root.x1 - root.x0 >= 2 ? 1 : 0
                subTickCount: root.isAc ? 8 : 4
                labelFormat: "%.9g"
                labelDelegate: Item {
                    property string text
                    implicitWidth: xLabel.implicitWidth
                    implicitHeight: xLabel.implicitHeight
                    Label {
                        id: xLabel
                        anchors.centerIn: parent
                        font.pointSize: 8
                        // formatX depends on the result (unit, log axis): re-evaluate on change.
                        text: WaveformModel.isAc !== undefined && WaveformModel.xLabel
                              && parent.text.length > 0
                              ? WaveformModel.formatX(Number(parent.text)) : ""
                    }
                }
            }
            axisY: ValueAxis {
                id: yAxis
                min: -1
                max: 1
                labelFormat: "%.4g"
            }

            LineSeries {
                id: cursorASeries
                objectName: "cursorA"
                visible: root.cursorsVisible
                color: "#505050"
                width: 1
            }
            LineSeries {
                id: cursorBSeries
                objectName: "cursorB"
                visible: root.cursorsVisible
                color: "#a0a0a0"
                width: 1
            }
        }

        // Trace series: a pool that only grows and lives as long as the graph, like the
        // cursor series. refresh() names, fills or hides them. Series are never removed
        // or destroyed while the view is up: in QtGraphs 6.8 a LineSeries destroyed while
        // still in a GraphsView aborts the process (pure virtual call in removeSeries),
        // which happened when an Instantiator rebuilt them for a new result.
        property var pool: []

        function growPool(count) {
            while (pool.length < count) {
                const s = seriesComponent.createObject(graph, {
                    color: root.colorFor(pool.length)
                })
                graph.addSeries(s)
                pool.push(s)
            }
        }

        Component {
            id: seriesComponent
            LineSeries {
                width: 1.5
                visible: false
            }
        }

        Label {
            x: 2
            y: 0
            text: plot.yTitle
            font.pointSize: 7
            color: palette.placeholderText
        }

        // Cursor tags above the plot area.
        Repeater {
            model: root.cursorsVisible ? [["A", root.cursorA], ["B", root.cursorB]] : []
            delegate: Label {
                required property var modelData
                visible: x >= plot.plotLeft - width / 2
                         && x <= plot.plotLeft + plot.plotWidth - width / 2
                x: plot.toPixel(modelData[1]) - width / 2
                y: 0
                text: modelData[0]
                font.pointSize: 7
                font.bold: true
            }
        }

        MouseArea {
            id: mouse
            objectName: "plotMouse"
            x: plot.plotLeft
            y: plot.plotTop
            width: plot.plotWidth
            height: plot.plotHeight
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton
            property string dragging: ""   // "A", "B" or "pan"
            property real lastX: 0

            function nearCursor(mx) {
                if (!root.cursorsVisible)
                    return ""
                const px = mx + plot.plotLeft
                if (Math.abs(px - plot.toPixel(root.cursorA)) <= 5)
                    return "A"
                if (Math.abs(px - plot.toPixel(root.cursorB)) <= 5)
                    return "B"
                return ""
            }
            function clampX(x) {
                return Math.max(root.x0, Math.min(root.x1, x))
            }

            cursorShape: dragging === "pan" ? Qt.ClosedHandCursor
                       : (dragging !== "" || nearCursor(mouseX) !== "") ? Qt.SizeHorCursor
                       : Qt.ArrowCursor

            onPressed: event => {
                dragging = nearCursor(event.x) || "pan"
                lastX = event.x
            }
            onReleased: dragging = ""
            onPositionChanged: event => {
                if (dragging === "A")
                    root.cursorA = clampX(plot.toX(event.x + plot.plotLeft))
                else if (dragging === "B")
                    root.cursorB = clampX(plot.toX(event.x + plot.plotLeft))
                else if (dragging === "pan") {
                    root.pan(-(event.x - lastX) / plot.plotWidth * (root.x1 - root.x0))
                    lastX = event.x
                }
            }
            onDoubleClicked: root.fit()
            onWheel: event => {
                const factor = event.angleDelta.y > 0 ? 0.8 : 1.25
                root.zoom(factor, plot.toX(event.x + plot.plotLeft))
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Trace list
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
                model: WaveformModel.traces
                delegate: CheckBox {
                    required property var modelData
                    required property int index
                    objectName: "trace_" + modelData.name
                    width: ListView.view.width
                    checked: modelData.visible
                    text: modelData.name
                    onToggled: WaveformModel.setVisible(modelData.name, checked)
                    indicator.implicitWidth: 16
                    Rectangle {
                        anchors.right: parent.right
                        anchors.rightMargin: 4
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14
                        height: 3
                        color: root.colorFor(index)
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
            RowLayout {
                Button {
                    objectName: "fitButton"
                    text: qsTr("Fit")
                    enabled: WaveformModel.hasAxis
                    onClicked: root.fit()
                }
                CheckBox {
                    objectName: "cursorsCheck"
                    text: qsTr("Cursors")
                    checked: root.cursorsVisible
                    onToggled: root.cursorsVisible = checked
                }
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

            // Cursor readout: A, B and B - A for every visible trace.
            Column {
                id: readout
                objectName: "cursorReadout"
                visible: root.cursorsVisible && WaveformModel.hasAxis
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.topMargin: 2

                readonly property int cellWidth: 92
                readonly property var header: root.isAc
                    ? [qsTr("frequency"), "A |H|", "B |H|", "Δ |H|", "A ∠", "B ∠", "Δ ∠"]
                    : [WaveformModel.xLabel, "A", "B", "Δ (B − A)"]

                component Cell: Label {
                    width: readout.cellWidth
                    elide: Text.ElideRight
                    font.pointSize: 8
                }

                Row {
                    Repeater {
                        model: readout.header
                        delegate: Cell {
                            required property string modelData
                            text: modelData
                            font.bold: true
                        }
                    }
                }
                Row {
                    Cell { text: root.isAc ? qsTr("cursor") : qsTr("cursor x") }
                    Cell { objectName: "cursorAx"; text: WaveformModel.formatX(root.cursorA) }
                    Cell { objectName: "cursorBx"; text: WaveformModel.formatX(root.cursorB) }
                    Cell {
                        objectName: "cursorDx"
                        width: readout.cellWidth * 2
                        text: WaveformModel.formatDeltaX(root.cursorA, root.cursorB)
                    }
                }
                Repeater {
                    model: WaveformModel.traces.filter(t => t.visible)
                    delegate: Row {
                        id: row
                        required property var modelData
                        readonly property string name: modelData.name
                        readonly property var cells: {
                            const a = WaveformModel.valueAt(name, root.cursorA, false)
                            const b = WaveformModel.valueAt(name, root.cursorB, false)
                            const list = [WaveformModel.formatY(name, a, false),
                                          WaveformModel.formatY(name, b, false),
                                          WaveformModel.formatY(name, b - a, false)]
                            if (root.isAc) {
                                const pa = WaveformModel.valueAt(name, root.cursorA, true)
                                const pb = WaveformModel.valueAt(name, root.cursorB, true)
                                list.push(WaveformModel.formatY(name, pa, true),
                                          WaveformModel.formatY(name, pb, true),
                                          WaveformModel.formatY(name, pb - pa, true))
                            }
                            return list
                        }
                        Cell {
                            text: row.name
                            color: root.colorFor(WaveformModel.traceNames.indexOf(row.name))
                        }
                        Repeater {
                            model: row.cells
                            delegate: Cell {
                                required property string modelData
                                required property int index
                                objectName: "readout_" + row.name + "_" + index
                                text: modelData
                            }
                        }
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                Label {
                    anchors.centerIn: parent
                    visible: !WaveformModel.hasAxis
                    color: palette.placeholderText
                    text: WaveformModel.analysis === "op"
                          ? qsTr("Operating point: the values are listed in the Console.")
                          : qsTr("Run a .dc, .ac or .tran analysis to see waveforms.")
                }

                ColumnLayout {
                    anchors.fill: parent
                    visible: WaveformModel.hasAxis
                    spacing: 0

                    Plot {
                        id: magnitudePlot
                        objectName: "magnitudePlot"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.verticalStretchFactor: 3
                        yTitle: root.isAc ? qsTr("dB") : ""
                    }
                    Plot {
                        id: phasePlot
                        objectName: "phasePlot"
                        visible: root.isAc
                        phase: true
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.verticalStretchFactor: 2
                        yTitle: qsTr("deg")
                    }
                }
            }
        }
    }
}
