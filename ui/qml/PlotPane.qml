import QtQuick
import QtQuick.Controls
import QtGraphs
import QtSpice

// One pane of the waveform window (W2): trace names on top, one GraphsView below.
// All panes share WaveformController's X range; each pane has its own Y axis. An AC
// pane also draws each trace's phase, scaled onto the magnitude axis by
// WaveformController.fillPane and labelled on the right (QtGraphs 6.8 has one Y axis
// per GraphsView).
// GraphsView 6.8 has no plot-area property: with labels shown its axes take a fixed
// 60 px on the left and 40 px at the bottom (tests/ui/test_waveform_view.py checks it).
FocusScope {
    id: pane
    required property int paneIndex
    readonly property var info: WaveformController.panes[paneIndex] || ({})
    readonly property bool isAc: WaveformController.isAc
    readonly property real x0: WaveformController.xMin
    readonly property real x1: WaveformController.xMax > x0 ? WaveformController.xMax : x0 + 1
    readonly property real plotLeft: graph.x + graph.marginLeft + 60
    readonly property real plotTop: graph.y + graph.marginTop
    readonly property real plotWidth: Math.max(1, graph.width - graph.marginRight
                                                  - graph.marginLeft - 60)
    readonly property real plotHeight: Math.max(1, graph.height - graph.marginBottom - 40
                                                   - graph.marginTop)
    readonly property real yMin: yAxis.min
    readonly property real yMax: yAxis.max
    // After the last refresh: [{name, kind, count}], points hidden on a log Y axis,
    // and the phase axis labels [{y, text}] (for tests and the right axis).
    property var seriesState: []
    property int hiddenPoints: 0
    property var rightTicks: []

    signal status(string text)  // the mouse position, for the status bar
    signal configure(int index)  // "Plot settings…" (WaveformView opens the dialog)

    function toPixelX(x) { return plotLeft + (x - x0) / (x1 - x0) * plotWidth }
    function toX(px) { return x0 + (px - plotLeft) / plotWidth * (x1 - x0) }
    function toPixelY(y) { return plotTop + (yAxis.max - y) / (yAxis.max - yAxis.min) * plotHeight }
    function toY(py) { return yAxis.max - (py - plotTop) / plotHeight * (yAxis.max - yAxis.min) }

    function refresh() {
        growPool(info.seriesNeeded || 0)
        const r = WaveformController.fillPane(paneIndex, pool, Math.round(plotWidth))
        yAxis.min = r.yMin
        yAxis.max = r.yMax
        seriesState = r.series
        hiddenPoints = r.hidden
        rightTicks = r.rightTicks
    }

    // Trace series: a pool that only grows and lives as long as the graph. Series are
    // never removed while the graph is up: in QtGraphs 6.8 a LineSeries destroyed while
    // still in a GraphsView aborts the process (pure virtual call in removeSeries).
    property var pool: []
    function growPool(count) {
        while (pool.length < count) {
            const s = seriesComponent.createObject(graph)
            graph.addSeries(s)
            pool.push(s)
        }
    }
    Component {
        id: seriesComponent
        LineSeries { width: 1.5; visible: false }
    }

    onPlotWidthChanged: refreshTimer.restart()
    onPlotHeightChanged: refreshTimer.restart()
    Timer {
        id: refreshTimer
        interval: 16
        onTriggered: pane.refresh()
    }
    Connections {
        target: WaveformController
        function onLayoutChanged() { refreshTimer.restart() }
        function onDataChanged() { refreshTimer.restart() }
    }
    Component.onCompleted: refreshTimer.restart()

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: pane.info.active && WaveformController.paneCount > 1 ? "#7aa7e0" : "transparent"
        border.width: 1
        z: 5
    }

    // ---- trace names
    Flow {
        id: header
        objectName: "paneHeader"
        x: pane.plotLeft
        y: 2
        width: Math.max(10, pane.width - pane.plotLeft - 8)
        spacing: 12
        Repeater {
            model: pane.info.traces || []
            delegate: Label {
                required property var modelData
                objectName: "trace_" + pane.paneIndex + "_" + modelData.name
                text: modelData.name
                color: modelData.color
                font.pointSize: 9
                font.strikeout: !modelData.present
            }
        }
        Label {
            visible: (pane.info.traces || []).length === 0
            text: pane.info.title ? "" : qsTr("(empty pane)")
            color: palette.placeholderText
            font.pointSize: 8
        }
    }
    Label {
        objectName: "paneTitle"
        visible: (pane.info.title || "") !== ""
        anchors.right: parent.right
        anchors.rightMargin: 8
        y: 2
        text: pane.info.title || ""
        font.pointSize: 8
        font.bold: true
    }

    GraphsView {
        id: graph
        x: 0
        y: header.height + 4
        width: pane.width
        height: Math.max(40, pane.height - y)
        marginLeft: 4
        marginRight: pane.isAc ? 52 : 12
        marginTop: 6
        marginBottom: 2
        theme: GraphsTheme {
            colorScheme: GraphsTheme.ColorScheme.Light
            gridVisible: pane.info.grid !== false
        }
        axisX: ValueAxis {
            min: pane.x0
            max: pane.x1
            tickAnchor: 0
            // One label per decade on a log axis (at least two decades shown).
            tickInterval: WaveformController.xLog && pane.x1 - pane.x0 >= 2 ? 1 : 0
            subTickCount: WaveformController.xLog ? 8 : 4
            labelFormat: "%.9g"
            labelDelegate: Item {
                property string text
                implicitWidth: xText.implicitWidth
                implicitHeight: xText.implicitHeight
                Label {
                    id: xText
                    anchors.centerIn: parent
                    font.pointSize: 8
                    // formatX depends on the layout (log axis, unit): re-evaluate on change.
                    text: WaveformController.xLog !== undefined && WaveformController.xLabel
                          && parent.text.length > 0
                          ? WaveformController.formatX(Number(parent.text)) : ""
                }
            }
        }
        axisY: ValueAxis {
            id: yAxis
            min: -1
            max: 1
            tickAnchor: 0
            tickInterval: pane.info.yLog && max - min >= 2 ? 1 : 0
            subTickCount: pane.info.yLog ? 8 : 4
            labelFormat: "%.9g"
            labelDelegate: Item {
                property string text
                implicitWidth: 56
                implicitHeight: yText.implicitHeight
                Label {
                    id: yText
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    font.pointSize: 8
                    text: pane.info.yLog !== undefined && parent.text.length > 0
                          ? WaveformController.formatY(pane.paneIndex, Number(parent.text)) : ""
                }
            }
        }
    }

    // Phase axis (AC) on the right.
    Repeater {
        model: pane.isAc ? pane.rightTicks : []
        delegate: Label {
            required property var modelData
            readonly property real py: pane.toPixelY(modelData.y)
            visible: py >= pane.plotTop - 1 && py <= pane.plotTop + pane.plotHeight + 1
            x: pane.plotLeft + pane.plotWidth + 4
            y: py - height / 2
            text: modelData.text
            font.pointSize: 8
            color: "#606060"
        }
    }

    // Values <= 0 on a log Y axis (W5).
    Label {
        objectName: "hiddenNote"
        visible: pane.hiddenPoints > 0
        x: pane.plotLeft + 4
        y: pane.plotTop + 2
        text: qsTr("%n point(s) <= 0 hidden", "", pane.hiddenPoints)
        font.pointSize: 8
        color: "#b06000"
    }

    // ---- mouse
    MouseArea {
        id: mouse
        objectName: "plotMouse"
        x: pane.plotLeft
        y: pane.plotTop
        width: pane.plotWidth
        height: pane.plotHeight
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        property bool panning: false
        property real lastX: 0

        cursorShape: panning ? Qt.ClosedHandCursor : Qt.CrossCursor

        function dataX(mx) { return pane.toX(mx + pane.plotLeft) }
        function dataY(my) { return pane.toY(my + pane.plotTop) }

        onPressed: event => {
            pane.forceActiveFocus()
            WaveformController.setActivePane(pane.paneIndex)
            if (event.button === Qt.RightButton) {
                paneMenu.popup()
                return
            }
            panning = true
            lastX = event.x
        }
        onReleased: panning = false
        onPositionChanged: event => {
            pane.status(WaveformController.formatX(dataX(event.x)) + ",  "
                        + WaveformController.formatY(pane.paneIndex, dataY(event.y)))
            if (panning) {
                WaveformController.panX(-(event.x - lastX) / pane.plotWidth * (pane.x1 - pane.x0))
                lastX = event.x
            }
        }
        onExited: pane.status("")
        onDoubleClicked: WaveformController.autoRange()
        onWheel: event => {
            WaveformController.zoomX(event.angleDelta.y > 0 ? 0.8 : 1.25, dataX(event.x))
        }
    }

    Menu {
        id: paneMenu
        objectName: "paneMenu"
        MenuItem {
            text: qsTr("Add pane above")
            onTriggered: WaveformController.addPane(pane.paneIndex, true)
        }
        MenuItem {
            text: qsTr("Add pane below")
            onTriggered: WaveformController.addPane(pane.paneIndex, false)
        }
        MenuItem {
            text: qsTr("Delete pane")
            enabled: WaveformController.paneCount > 1
            onTriggered: WaveformController.deletePane(pane.paneIndex)
        }
        MenuSeparator {}
        MenuItem {
            text: qsTr("Auto-range")
            onTriggered: WaveformController.autoRange()
        }
        MenuSeparator {}
        MenuItem {
            text: qsTr("Plot settings…")
            onTriggered: pane.configure(pane.paneIndex)
        }
    }
}
