import QtQuick
import QtQuick.Shapes
import QtSpice

// Wire tool (S7). Lives inside the canvas content, in sheet units.
//
// The first click starts a wire; the rubber wire runs from there to the pointer as
// two orthogonal segments (an L). Which way the L bends follows the first real
// pointer movement after each click. Every click adds the L as one undo step and
// continues from that point; clicking on a pin, a wire or a flag adds it and ends
// the wire. Points snap to the 16-unit grid, or to a pin within a few pixels.
Item {
    id: root
    objectName: "wireTool"

    property bool active: false        // a wire is being drawn
    property point anchor: Qt.point(0, 0)
    property point cursor: Qt.point(0, 0)
    property bool horizontalFirst: true
    property bool directionSet: false
    property real tolerance: 6         // pin snap distance, sheet units
    property real penWidth: 1.5

    readonly property point corner: horizontalFirst ? Qt.point(cursor.x, anchor.y)
                                                    : Qt.point(anchor.x, cursor.y)

    function snapped(p) {
        const s = SchematicController.snapPoint(p.x, p.y, tolerance)
        return Qt.point(s.x, s.y)
    }

    function segments() {
        return [[anchor.x, anchor.y, corner.x, corner.y], [corner.x, corner.y, cursor.x, cursor.y]]
    }

    // A click at sheet point p.
    function press(p) {
        const q = snapped(p)
        if (!active) {
            anchor = q
            cursor = q
            directionSet = false
            active = true
            return
        }
        cursor = q
        if (q.x === anchor.x && q.y === anchor.y) {
            stop()   // clicking the start point again ends the wire
            return
        }
        const ends = SchematicController.isConnectionPoint(q.x, q.y)
        SchematicController.addWires(segments())
        anchor = q
        directionSet = false
        if (ends)
            stop()
    }

    function move(p) {
        const q = snapped(p)
        if (!active) {
            cursor = q
            return
        }
        if (!directionSet) {
            const dx = Math.abs(q.x - anchor.x)
            const dy = Math.abs(q.y - anchor.y)
            if (dx !== dy) {
                horizontalFirst = dx > dy
                directionSet = true
            }
        }
        cursor = q
    }

    function stop() {
        active = false
        directionSet = false
    }

    Shape {
        objectName: "rubberWire"
        visible: root.active
        ShapePath {
            strokeColor: "#2d7d32"
            strokeWidth: root.penWidth
            strokeStyle: ShapePath.DashLine
            dashPattern: [4, 3]
            fillColor: "transparent"
            PathSvg {
                path: root.active
                      ? "M %1 %2 L %3 %4 L %5 %6".arg(root.anchor.x).arg(root.anchor.y)
                                                 .arg(root.corner.x).arg(root.corner.y)
                                                 .arg(root.cursor.x).arg(root.cursor.y)
                      : ""
            }
        }
    }

    // Crosshair at the snapped pointer while the tool is on.
    Rectangle {
        x: root.cursor.x - 0.5 * root.penWidth
        y: root.cursor.y - 8
        width: root.penWidth
        height: 16
        color: "#2d7d32"
    }
    Rectangle {
        x: root.cursor.x - 8
        y: root.cursor.y - 0.5 * root.penWidth
        width: 16
        height: root.penWidth
        color: "#2d7d32"
    }
}
