import QtQuick
import QtQuick.Shapes

// One physical wire (U1): an undirected connection between two conserving ports of
// the same domain, drawn as right-angled segments in the domain's colour. The corner
// points and the hit-testing (physicalWireAt) come from DiagramController.
Shape {
    id: wire

    required property int index
    required property string a
    required property string b
    required property string domain
    required property color color
    required property var points
    required property bool selected

    objectName: "pwire_" + a + "_" + b

    ShapePath {
        strokeColor: wire.selected ? palette.highlight : wire.color
        strokeWidth: wire.selected ? 3.5 : 2.5
        fillColor: "transparent"
        joinStyle: ShapePath.RoundJoin
        capStyle: ShapePath.RoundCap
        PathPolyline {
            path: wire.points.map(p => Qt.point(p.x, p.y))
        }
    }
}
