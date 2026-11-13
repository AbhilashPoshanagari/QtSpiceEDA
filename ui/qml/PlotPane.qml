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
    readonly property real yTickInterval: yAxis.tickInterval  // 1 = decade labels (log)
    // After the last refresh: [{name, kind, count}], points hidden on a log Y axis,
    // and the phase axis labels [{y, text}] (for tests and the right axis).
    property var seriesState: []
    property int hiddenPoints: 0
    property var rightTicks: []

    signal status(string text)  // the mouse position, for the status bar
    signal configure(int index)  // "Plot settings…" (WaveformView opens the dialog)
    signal cursorMoved(int which)  // a cursor was attached or moved here (W6)
    signal addTraceRequested(int index)  // "Add trace…" (W8)
    signal editTraceRequested(int index, string name)  // edit a trace's expression (W8)
    property int activeCursor: 1  // the cursor the arrow keys move
    property Item dragParent: null  // where a dragged trace name lives (the view)

    // W10: Ctrl+click on a trace name. Average and RMS over the zoomed X range, as
    // LTspice shows them; RMS only for voltage and current traces.
    function showStats(name, near) {
        const s = WaveformController.traceStats(paneIndex, name)
        statsPopup.stats = s
        const p = near.mapToItem(pane, 0, near.height + 2)
        statsPopup.x = Math.max(0, Math.min(p.x, pane.width - statsPopup.implicitWidth))
        statsPopup.y = p.y
        statsPopup.open()
    }

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

    // ---- Y axis unit (U4), e.g. "[rad/s]" or "[rpm]"; empty when the traces differ
    Label {
        objectName: "yAxisLabel"
        visible: text !== ""
        text: pane.info.yLabel || ""
        x: Math.max(2, pane.plotLeft - implicitWidth - 6)
        y: 2
        font.pointSize: 8
        color: "#404040"
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
            delegate: TraceLabel {
                required property var modelData
                paneIndex: pane.paneIndex
                name: modelData.name
                caption: modelData.label || modelData.name
                traceColor: modelData.color
                present: modelData.present
                error: modelData.error || ""
                dragParent: pane.dragParent
                onEditRequested: pane.editTraceRequested(pane.paneIndex, modelData.name)
                onStatsRequested: pane.showStats(modelData.name, this)
                onCursorRequested: which => {
                    if (WaveformController.attachCursor(which, pane.paneIndex, modelData.name))
                        pane.cursorMoved(which === 2 ? 2 : 1)
                }
            }
        }
        Label {
            objectName: "acKey"
            visible: pane.isAc && (pane.info.traces || []).length > 0
            text: qsTr("thick: magnitude (dB, left)   thin: phase (°, right)")
            color: "#808080"
            font.pointSize: 8
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
        // Waveforms first: a white plot area, thin light-grey grid lines that recede
        // behind the traces, and no tick stubs on the axes.
        theme: GraphsTheme {
            colorScheme: GraphsTheme.ColorScheme.Light
            gridVisible: pane.info.grid !== false
            backgroundColor: pane.palette.window
            plotAreaBackgroundColor: "#ffffff"
            labelTextColor: "#404040"
            grid.mainColor: "#d9d9d9"
            grid.mainWidth: 1
            grid.subColor: "#efefef"
            grid.subWidth: 1
            axisX.mainColor: "#a0a0a0"
            axisX.mainWidth: 1
            axisX.subColor: "transparent"
            axisX.labelTextColor: "#404040"
            axisY.mainColor: "#a0a0a0"
            axisY.mainWidth: 1
            axisY.subColor: "transparent"
            axisY.labelTextColor: "#404040"
        }
        axisX: ValueAxis {
            min: pane.x0
            max: pane.x1
            tickAnchor: 0
            // One label per decade on a log axis (at least two decades shown).
            tickInterval: WaveformController.xLog && pane.x1 - pane.x0 >= 1 ? 1 : 0
            subTickCount: WaveformController.xLog ? 0 : 1  // log: minor lines drawn below
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
            tickInterval: pane.info.yLog && max - min >= 1 ? 1 : 0
            subTickCount: pane.info.yLog ? 0 : 1
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

    // Minor grid of log axes (W5): 2..9 times each decade. QtGraphs sub-ticks are
    // evenly spaced, which is wrong on a log scale, so these are drawn here.
    Item {
        objectName: "logGrid"
        x: pane.plotLeft
        y: pane.plotTop
        width: pane.plotWidth
        height: pane.plotHeight
        visible: pane.info.grid !== false
        clip: true
        Repeater {
            model: WaveformController.xLog ? WaveformController.logGrid(pane.x0, pane.x1) : []
            delegate: Rectangle {
                required property real modelData
                x: pane.toPixelX(modelData) - pane.plotLeft
                width: 1
                height: parent.height
                color: Qt.rgba(0, 0, 0, 0.05)  // drawn over the traces: keep them visible
            }
        }
        Repeater {
            model: pane.info.yLog ? WaveformController.logGrid(pane.yMin, pane.yMax) : []
            delegate: Rectangle {
                required property real modelData
                y: pane.toPixelY(modelData) - pane.plotTop
                width: parent.width
                height: 1
                color: Qt.rgba(0, 0, 0, 0.05)  // drawn over the traces: keep them visible
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
        text: qsTr("%1 points ≤ 0 hidden (log Y): right-click > Plot |y| to show them")
              .arg(pane.hiddenPoints)
        font.pointSize: 8
        color: "#b06000"
    }

    // Zoom box (W3): drag with the left button; Esc cancels.
    Rectangle {
        id: zoomBox
        objectName: "zoomBox"
        visible: mouse.boxing
        x: pane.plotLeft + Math.min(mouse.startX, mouse.endX)
        y: pane.plotTop + Math.min(mouse.startY, mouse.endY)
        width: Math.abs(mouse.endX - mouse.startX)
        height: Math.abs(mouse.endY - mouse.startY)
        color: "#203a7bd5"
        border.color: "#3a7bd5"
        border.width: 1
    }

    // Arrow keys move the active cursor one data point (W6).
    Keys.onLeftPressed: event => {
        event.accepted = WaveformController.stepCursor(pane.activeCursor, -1)
    }
    Keys.onRightPressed: event => {
        event.accepted = WaveformController.stepCursor(pane.activeCursor, 1)
    }

    Keys.onEscapePressed: event => {
        if (mouse.boxing) {
            mouse.boxing = false
            mouse.cancelled = true
            pane.status("")
            event.accepted = true
        } else {
            event.accepted = false
        }
    }

    // A trace name dropped here (W4): move it from its pane, or copy it with Ctrl.
    DropArea {
        id: drop
        objectName: "paneDrop"
        anchors.fill: parent
        keys: ["qtspice/trace"]
        onDropped: ev => {
            const src = ev.source.paneIndex, name = ev.source.traceName
            const copy = ev.proposedAction === Qt.CopyAction
            ev.accept(copy ? Qt.CopyAction : Qt.MoveAction)
            const dst = pane.paneIndex
            Qt.callLater(() => WaveformController.dropTrace(src, name, dst, copy))
        }
    }
    Rectangle {
        anchors.fill: parent
        visible: drop.containsDrag
        color: "#183a7bd5"
        border.color: "#3a7bd5"
        z: 4
    }

    // ---- mouse
    // Left drag: zoom box. Middle drag: pan X. Wheel: zoom X. Double-click: auto-range.
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
        property bool boxing: false
        property bool cancelled: false
        property real lastX: 0
        property real startX: 0
        property real startY: 0
        property real endX: 0
        property real endY: 0
        readonly property real minBox: 4  // pixels; a smaller drag is a click

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
            cancelled = false
            if (event.button === Qt.MiddleButton) {
                panning = true
                lastX = event.x
                return
            }
            startX = endX = event.x
            startY = endY = event.y
        }
        onReleased: event => {
            panning = false
            if (!boxing)
                return
            boxing = false
            if (cancelled)
                return
            WaveformController.zoomBox(pane.paneIndex, dataX(startX), dataX(endX),
                                       dataY(startY), dataY(endY))
        }
        onPositionChanged: event => {
            if (pressedButtons & Qt.LeftButton && !cancelled) {
                endX = Math.max(0, Math.min(width, event.x))
                endY = Math.max(0, Math.min(height, event.y))
                if (Math.abs(endX - startX) >= minBox && Math.abs(endY - startY) >= minBox)
                    boxing = true
            }
            if (boxing) {
                pane.status(qsTr("zoom box  Δx = %1,  Δy = %2")
                            .arg(WaveformController.formatDeltaX(dataX(startX), dataX(endX)))
                            .arg(WaveformController.formatDeltaY(pane.paneIndex, dataY(startY),
                                                                 dataY(endY))))
            } else {
                pane.status(WaveformController.formatX(dataX(event.x)) + ",  "
                            + WaveformController.formatY(pane.paneIndex, dataY(event.y)))
            }
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

    // Cursors (W6): a vertical line in every pane (shared X); in the pane of the trace
    // it follows, a cross where it meets the trace (and the phase, for AC). Drag a
    // line to move its cursor.
    // Two fixed slots, so a drag is not cut off when the cursor list changes.
    Repeater {
        model: 2
        delegate: Item {
            id: cursorItem
            required property int index
            readonly property int which: index + 1
            readonly property var c: {
                for (const item of WaveformController.cursors)
                    if (item.which === which)
                        return item
                return null
            }
            readonly property real px: c ? pane.toPixelX(c.dx) : -100
            readonly property bool here: c !== null && c.pane === pane.paneIndex
            objectName: "cursorLine" + which
            visible: c !== null && px >= pane.plotLeft - 1 && px <= pane.plotLeft + pane.plotWidth + 1
            x: px - 4
            y: pane.plotTop
            width: 9
            height: pane.plotHeight
            z: 6

            Rectangle {
                x: 4
                width: 1
                height: parent.height
                color: cursorItem.here ? cursorItem.c.color : "#909090"
            }
            Label {
                x: 7
                y: 1
                text: cursorItem.which
                font.pointSize: 7
                font.bold: true
            }
            Repeater {
                model: !cursorItem.here ? []
                     : pane.isAc && cursorItem.c.dyPhase !== undefined
                       ? [cursorItem.c.dy, cursorItem.c.dyPhase]
                       : [cursorItem.c.dy]
                delegate: Rectangle {
                    required property real modelData
                    required property int index
                    objectName: "cursorCross"
                    visible: isFinite(modelData)
                    x: 4 - 6
                    y: pane.toPixelY(modelData) - pane.plotTop - 0.5
                    width: 13
                    height: 1
                    color: index === 0 ? "#202020" : "#808080"
                }
            }
            MouseArea {
                anchors.fill: parent
                enabled: cursorItem.c !== null
                cursorShape: Qt.SizeHorCursor
                preventStealing: true
                onPressed: {
                    pane.forceActiveFocus()
                    pane.cursorMoved(cursorItem.which)
                }
                onPositionChanged: event => {
                    const p = mapToItem(pane, event.x, 0)
                    WaveformController.setCursorDisplayX(cursorItem.which, pane.toX(p.x))
                }
            }
        }
    }

    Popup {
        id: statsPopup
        objectName: "statsPopup"
        property var stats: ({})
        padding: 8
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        contentItem: Label {
            objectName: "statsText"
            font.pointSize: 9
            textFormat: Text.PlainText
            text: {
                const s = statsPopup.stats
                if (!s.ok)
                    return s.message || ""
                let lines = [s.name,
                             qsTr("Interval start: %1").arg(s.fromText),
                             qsTr("Interval end: %1").arg(s.toText),
                             qsTr("Average: %1").arg(s.averageText)]
                if (s.hasRms)
                    lines.push(qsTr("RMS: %1").arg(s.rmsText))
                return lines.join("\n")
            }
        }
    }

    Menu {
        id: paneMenu
        objectName: "paneMenu"
        MenuItem {
            text: qsTr("Remove cursors")
            visible: WaveformController.cursors.length > 0
            height: visible ? implicitHeight : 0
            onTriggered: WaveformController.detachCursors()
        }
        MenuItem {
            objectName: "addTraceItem"
            text: qsTr("Add trace…")
            onTriggered: pane.addTraceRequested(pane.paneIndex)
        }
        MenuSeparator {}
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
            text: qsTr("Zoom back")
            enabled: WaveformController.canZoomBack
            onTriggered: WaveformController.zoomBack()
        }
        MenuItem {
            text: qsTr("Auto-range")
            onTriggered: WaveformController.autoRange()
        }
        MenuSeparator {}
        MenuItem {
            objectName: "logXItem"
            text: qsTr("Logarithmic X axis")
            checkable: true
            checked: WaveformController.xLog
            onTriggered: WaveformController.setXLog(checked)
        }
        MenuItem {
            objectName: "logYItem"
            text: qsTr("Logarithmic Y axis")
            visible: !pane.isAc
            height: visible ? implicitHeight : 0
            checkable: true
            checked: pane.info.yLog === true
            onTriggered: WaveformController.setYLog(pane.paneIndex, checked)
        }
        MenuItem {
            objectName: "absItem"
            text: qsTr("Plot |y| (values ≤ 0)")
            visible: !pane.isAc
            height: visible ? implicitHeight : 0
            enabled: pane.info.yLog === true
            checkable: true
            checked: pane.info.showAbs === true
            onTriggered: WaveformController.setShowAbs(pane.paneIndex, checked)
        }
        MenuSeparator {}
        MenuItem {
            text: qsTr("Plot settings…")
            onTriggered: pane.configure(pane.paneIndex)
        }
    }
}
