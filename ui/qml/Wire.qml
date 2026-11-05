import QtQuick
import QtQuick.Shapes

// One wire: a cubic curve from an output port to an input port. The end points and
// the bend come from DiagramController, which also does the hit-testing (wireAt), so
// this item only draws.
Shape {
    id: wire

    required property int index
    required property string src
    required property string dst
    required property real x1
    required property real y1
    required property real x2
    required property real y2
    required property real bend
    required property bool selected

    objectName: "wire_" + src + "_" + dst

    ShapePath {
        strokeColor: wire.selected ? palette.highlight : "#6e7781"
        strokeWidth: wire.selected ? 3 : 2
        fillColor: "transparent"
        startX: wire.x1
        startY: wire.y1
        PathCubic {
            x: wire.x2
            y: wire.y2
            control1X: wire.x1 + wire.bend
            control1Y: wire.y1
            control2X: wire.x2 - wire.bend
            control2Y: wire.y2
        }
    }
}
