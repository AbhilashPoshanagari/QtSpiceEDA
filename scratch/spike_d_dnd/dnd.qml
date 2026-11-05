// Spike D: drag a trace name from one pane header into another pane. Throwaway.
// Drop = move, Ctrl+drop = copy (LTspice rule). Uses Drag.Internal + DropArea.
import QtQuick

Rectangle {
    id: root
    width: 900
    height: 600
    color: "white"

    // One ListModel of trace names per pane (the real app keeps this in the bridge).
    property list<ListModel> paneModels: [
        ListModel { ListElement { name: "V(in)" } ListElement { name: "V(out)" } },
        ListModel { ListElement { name: "I(R1)" } },
        ListModel { }
    ]
    property string lastAction: ""

    // For the self-test: ["V(in),V(out)", "I(R1)", ""]
    function paneTraces() {
        const out = []
        for (const m of paneModels) {
            const names = []
            for (let i = 0; i < m.count; ++i) names.push(m.get(i).name)
            out.push(names.join(","))
        }
        return out
    }

    function dropTrace(fromPane, fromIndex, toPane, copy) {
        const name = paneModels[fromPane].get(fromIndex).name
        if (fromPane === toPane) return "none"
        const target = paneModels[toPane]
        for (let i = 0; i < target.count; ++i)
            if (target.get(i).name === name) {   // already there: a move just removes it
                if (!copy) paneModels[fromPane].remove(fromIndex)
                return copy ? "none" : "move"
            }
        target.append({ name: name })
        if (!copy) paneModels[fromPane].remove(fromIndex)
        return copy ? "copy" : "move"
    }

    Column {
        anchors.fill: parent
        Repeater {
            model: root.paneModels.length
            delegate: Rectangle {
                id: pane
                required property int index
                objectName: "pane" + index
                width: root.width
                height: root.height / root.paneModels.length
                border.color: "#d0d0d0"
                color: drop.containsDrag ? "#eef5ff" : "white"

                Text {
                    anchors.centerIn: parent
                    text: "pane " + pane.index + (drop.containsDrag ? "  (drop here)" : "")
                    color: "#a0a0a0"
                }

                // The whole pane accepts a dropped trace name.
                DropArea {
                    id: drop
                    anchors.fill: parent
                    keys: ["qtspice/trace"]
                    onDropped: (ev) => {
                        const src = ev.source
                        const copy = ev.proposedAction === Qt.CopyAction
                        root.lastAction = root.dropTrace(src.paneIndex, src.rowIndex, pane.index, copy)
                        ev.accept(copy ? Qt.CopyAction : Qt.MoveAction)
                    }
                }

                // Header with one label per trace.
                Row {
                    x: 8; y: 6
                    spacing: 12
                    Repeater {
                        model: root.paneModels[pane.index]
                        delegate: Item {
                            id: label
                            required property int index
                            required property string name
                            objectName: "trace_" + name
                            width: text.implicitWidth + 12
                            height: text.implicitHeight + 6

                            // The visual that moves; snaps back after the drop.
                            Rectangle {
                                id: chip
                                // Drag source info read by the DropArea.
                                property int paneIndex: pane.index
                                property int rowIndex: label.index
                                width: label.width
                                height: label.height
                                radius: 3
                                color: mouse.drag.active ? "#ffe9a8" : "#f2f2f2"
                                border.color: "#c8c8c8"
                                Drag.active: mouse.drag.active
                                Drag.dragType: Drag.Internal
                                Drag.keys: ["qtspice/trace"]
                                Drag.supportedActions: Qt.MoveAction | Qt.CopyAction
                                Drag.hotSpot: Qt.point(width / 2, height / 2)
                                Text {
                                    id: text
                                    anchors.centerIn: parent
                                    text: label.name
                                    color: ["#1f77b4", "#d62728", "#2ca02c"][pane.index % 3]
                                }
                                states: State {
                                    when: mouse.drag.active
                                    ParentChange { target: chip; parent: root }
                                }
                            }

                            MouseArea {
                                id: mouse
                                anchors.fill: parent
                                drag.target: chip
                                onPressed: (m) => chip.Drag.proposedAction = Qt.MoveAction
                                onPositionChanged: (m) => chip.Drag.proposedAction =
                                    (m.modifiers & Qt.ControlModifier) ? Qt.CopyAction : Qt.MoveAction
                                onReleased: (m) => {
                                    chip.Drag.proposedAction =
                                        (m.modifiers & Qt.ControlModifier) ? Qt.CopyAction : Qt.MoveAction
                                    chip.Drag.drop()
                                    chip.x = 0; chip.y = 0   // snap back if the label survives
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Text {
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 6
        text: "drag = move, Ctrl+drag = copy   last: " + root.lastAction
        font.pixelSize: 11
        color: "#606060"
    }
}
