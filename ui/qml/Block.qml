import QtQuick
import QtQuick.Shapes

// One block on the canvas: a square, rectangle or circle with its name in the middle,
// input ports on the left and output ports on the right. A physical block's conserving
// ports (round, in their domain colour) follow on both edges. Click selects (Ctrl/Shift
// adds), drag moves it and the rest of the selection, snapped to the grid. A block in
// an algebraic loop gets a red outline.
Item {
    id: block

    required property int index
    required property string blockId
    required property string specName
    required property real blockX
    required property real blockY
    required property real blockWidth
    required property real blockHeight
    required property string shape
    required property color color
    required property var inputs
    required property var outputs
    required property var conserving
    required property bool selected
    required property bool inLoop

    property Item canvas

    objectName: "block_" + blockId
    x: blockX
    y: blockY
    width: blockWidth
    height: blockHeight

    readonly property color outline: inLoop ? "#d73a49" : selected ? palette.highlight : color
    readonly property real outlineWidth: inLoop || selected ? 3 : 2
    readonly property color fill: Qt.lighter(color, 1.65)

    Shape {
        anchors.fill: parent
        visible: block.shape !== "circle"
        ShapePath {
            strokeColor: block.outline
            strokeWidth: block.outlineWidth
            fillColor: block.fill
            PathRectangle {
                x: 0
                y: 0
                width: block.width
                height: block.height
                radius: 6
            }
        }
    }

    Shape {
        anchors.fill: parent
        visible: block.shape === "circle"
        ShapePath {
            strokeColor: block.outline
            strokeWidth: block.outlineWidth
            fillColor: block.fill
            PathAngleArc {
                centerX: block.width / 2
                centerY: block.height / 2
                radiusX: block.width / 2
                radiusY: block.height / 2
                startAngle: 0
                sweepAngle: 360
            }
        }
    }

    Text {
        anchors.centerIn: parent
        width: block.width - 12
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        text: block.specName
        font.pixelSize: 12
        font.bold: true
        color: "#24292f"
    }

    Text {
        anchors.top: parent.bottom
        anchors.topMargin: 2
        anchors.horizontalCenter: parent.horizontalCenter
        text: block.blockId
        font.pixelSize: 10
        color: block.inLoop ? "#d73a49" : palette.placeholderText
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: DiagramController.select(block.blockId,
                                           (point.modifiers & (Qt.ControlModifier | Qt.ShiftModifier)) !== 0)
    }

    DragHandler {
        id: moveDrag
        target: null
        acceptedButtons: Qt.LeftButton
        property real startX
        property real startY
        property point pressAt
        onActiveChanged: {
            if (active) {
                if (!block.selected)
                    DiagramController.select(block.blockId, false)
                startX = block.blockX
                startY = block.blockY
                pressAt = block.parent.mapFromItem(null, centroid.scenePressPosition.x,
                                                   centroid.scenePressPosition.y)
            } else {
                DiagramController.endMove()
            }
        }
        onCentroidChanged: {
            if (!active)
                return
            const p = block.parent.mapFromItem(null, centroid.scenePosition.x, centroid.scenePosition.y)
            DiagramController.moveBlock(block.blockId, startX + p.x - pressAt.x, startY + p.y - pressAt.y)
        }
    }

    Repeater {
        model: block.inputs
        delegate: Port {
            required property var modelData
            canvas: block.canvas
            blockId: block.blockId
            portName: modelData.name
            isInput: true
            showLabel: block.inputs.length > 1
            px: modelData.x
            py: modelData.y
            tint: block.color
        }
    }

    Repeater {
        model: block.conserving
        delegate: Port {
            required property var modelData
            canvas: block.canvas
            blockId: block.blockId
            portName: modelData.name
            kind: "conserving"
            domain: modelData.domain
            isInput: modelData.side === "left"
            showLabel: block.conserving.length > 1 || block.inputs.length + block.outputs.length > 0
            px: modelData.x
            py: modelData.y
            tint: modelData.color
        }
    }

    Repeater {
        model: block.outputs
        delegate: Port {
            required property var modelData
            canvas: block.canvas
            blockId: block.blockId
            portName: modelData.name
            isInput: false
            showLabel: block.outputs.length > 1
            px: modelData.x
            py: modelData.y
            tint: block.color
        }
    }
}
