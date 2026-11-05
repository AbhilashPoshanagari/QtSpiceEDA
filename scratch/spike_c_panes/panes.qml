// Spike C: three stacked GraphsViews sharing one X range and one cursor line. Throwaway.
import QtQuick
import QtGraphs

Rectangle {
    id: root
    required property QtObject store
    required property bool raw
    required property bool frozen  // axisonly: fill once, then only move the axes
    property bool animating: false

    // Shared X range (seconds). Every pane binds its axis to these.
    readonly property real fullX0: 0
    readonly property real fullX1: 1e-3
    property real zoom: 1.0                 // 1 = full range, 0.001 = 1000x
    property real centre: 0.37e-3
    readonly property real x0: Math.max(fullX0, centre - (fullX1 - fullX0) * zoom / 2)
    readonly property real x1: Math.min(fullX1, centre + (fullX1 - fullX0) * zoom / 2)
    property real cursorX: 0.5e-3

    // Same fixed plot-area offsets as v0.1 (labels take 60 px left, 40 px bottom).
    readonly property real plotLeft: 4 + 60
    readonly property real plotRight: 12
    color: "white"

    SequentialAnimation on zoom {
        running: root.animating
        loops: Animation.Infinite
        NumberAnimation { to: 0.001; duration: 1500; easing.type: Easing.InOutQuad }
        NumberAnimation { to: 1.0; duration: 1500; easing.type: Easing.InOutQuad }
    }
    NumberAnimation on cursorX {
        running: root.animating
        loops: Animation.Infinite
        from: root.fullX0; to: root.fullX1; duration: 3000
    }

    onX0Changed: refreshTimer.restart()
    onX1Changed: refreshTimer.restart()
    // Coalesce zoom steps into one refill per frame.
    Timer {
        id: refreshTimer
        interval: 0
        onTriggered: if (!root.raw && !root.frozen) for (let i = 0; i < panes.count; ++i) panes.itemAt(i).refill()
    }

    Column {
        anchors.fill: parent
        Repeater {
            id: panes
            model: 3
            delegate: Item {
                id: pane
                required property int index
                width: root.width
                height: root.height / 3
                function refill() {
                    root.store.fill(series, index, root.x0, root.x1,
                                   Math.round(width - root.plotLeft - root.plotRight))
                }
                Component.onCompleted: root.raw ? root.store.fillAll(series, index) : refill()

                GraphsView {
                    anchors.fill: parent
                    marginLeft: 4
                    marginRight: root.plotRight
                    marginTop: 8
                    marginBottom: 2
                    theme: GraphsTheme { colorScheme: GraphsTheme.ColorScheme.Light }
                    axisX: ValueAxis { min: root.x0; max: root.x1; labelFormat: "%.3g" }
                    axisY: ValueAxis { min: -2.5; max: 2.5 }
                    LineSeries {
                        id: series
                        width: 1.2
                        color: ["#1f77b4", "#d62728", "#2ca02c"][pane.index]
                    }
                }
            }
        }
    }

    // One cursor line across all panes: shared X means one pixel mapping.
    Rectangle {
        visible: root.cursorX >= root.x0 && root.cursorX <= root.x1
        x: root.plotLeft + (root.cursorX - root.x0) / (root.x1 - root.x0)
           * (root.width - root.plotLeft - root.plotRight)
        width: 1
        height: root.height
        color: "#505050"
    }

    // Interactive use: wheel zooms around the mouse, hover moves the cursor.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onPositionChanged: (m) => {
            if (!root.animating)
                root.cursorX = root.x0 + (m.x - root.plotLeft)
                        / (root.width - root.plotLeft - root.plotRight) * (root.x1 - root.x0)
        }
        onWheel: (w) => {
            root.centre = root.cursorX
            root.zoom = Math.min(1, Math.max(1e-4, root.zoom * (w.angleDelta.y > 0 ? 0.8 : 1.25)))
        }
    }
}
