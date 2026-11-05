import QtQuick
import QtQuick.Shapes
import QtSpice

// One placed symbol (S5). Sits at the part's origin in sheet units; the parent
// scales sheet units to pixels. The .asy shapes are one Shape + PathSvg (spike B),
// turned by the part's orientation matrix (the same map as core transform()).
// Texts (InstName, Value) stay upright at anchors computed by the controller.
Item {
    id: root
    objectName: "symbolItem"

    property string symbolName: ""
    property string instName: ""
    property var matrix: [1, 0, 0, 1]
    property var texts: []
    property bool selected: false
    property bool missing: false
    property bool showPins: true
    // Stroke width in sheet units; the canvas sets it so lines stay ~1.5 px.
    property real penWidth: 1.5
    property color color: selected ? "#d35400" : (missing ? "#c0392b" : "#1c3d8c")

    readonly property var geometry: SchematicController.symbolGeometry(symbolName)

    Item {
        id: body
        transform: Matrix4x4 {
            matrix: Qt.matrix4x4(root.matrix[0], root.matrix[1], 0, 0,
                                 root.matrix[2], root.matrix[3], 0, 0,
                                 0, 0, 1, 0,
                                 0, 0, 0, 1)
        }

        Shape {
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: root.color
                strokeWidth: root.penWidth
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: root.geometry.path }
            }
        }

        Repeater {
            model: root.showPins ? root.geometry.pins : []
            delegate: Rectangle {
                required property var modelData
                x: modelData.x - 2
                y: modelData.y - 2
                width: 4
                height: 4
                color: "transparent"
                border.color: root.color
                border.width: root.penWidth / 2
            }
        }
    }

    Repeater {
        model: root.texts
        delegate: Text {
            required property var modelData
            objectName: "symbolText"
            text: modelData.text
            color: root.selected ? "#d35400" : "#202020"
            font.pixelSize: 12 + (modelData.size - 2) * 4
            x: modelData.align === "right" ? modelData.x - width
               : (modelData.align === "left" ? modelData.x : modelData.x - width / 2)
            y: modelData.align === "top" ? modelData.y
               : (modelData.align === "bottom" ? modelData.y - height : modelData.y - height / 2)
        }
    }
}
