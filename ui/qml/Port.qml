import QtQuick
import QtQuick.Shapes

// One port of a block on the canvas (positions come from DiagramController).
//
// Signal ports (kind "signal") are triangles pointing the way the value flows: inputs
// on the block's left edge, outputs on its right edge. Drag from an output and drop on
// an input to draw a wire.
//
// Conserving ports (kind "conserving", U1) are round and filled in their domain's
// colour. They have no direction: drag from any conserving port to another one to
// draw a physical wire.
//
// While dragging, the port under the cursor turns green when the wire is valid and red
// when it is not (the canvas shows the reason).
Item {
    id: port

    property Item canvas
    property string blockId
    property string portName
    property bool isInput: true      // signal: an input; conserving: on the left edge
    property string kind: "signal"
    property string domain: ""
    property real px
    property real py
    property color tint: "#888888"
    property bool showLabel: true

    readonly property bool conserving: kind === "conserving"
    readonly property string address: blockId + "." + portName
    readonly property bool targeted: canvas !== null && canvas.wireTarget === address
    readonly property color targetColor: port.canvas !== null && port.canvas.wireHint === ""
                                         ? "#2da44e" : "#d73a49"
    readonly property real hitSize: 16

    objectName: (conserving ? "phys_" : isInput ? "in_" : "out_") + address
    x: px - hitSize / 2
    y: py - hitSize / 2
    width: hitSize
    height: hitSize

    // Conserving: a filled circle in the domain colour.
    Rectangle {
        visible: port.conserving
        anchors.centerIn: parent
        width: port.targeted ? 13 : 10
        height: width
        radius: width / 2
        color: port.targeted ? port.targetColor : port.tint
        border.color: Qt.darker(port.tint, 1.4)
        border.width: 1
    }

    // Signal: a triangle pointing right (into the block at an input, out at an output).
    Shape {
        id: triangle
        visible: !port.conserving
        anchors.centerIn: parent
        width: port.targeted ? 12 : 9
        height: width
        ShapePath {
            strokeColor: port.tint
            strokeWidth: 2
            fillColor: port.targeted ? port.targetColor
                                     : (port.isInput ? palette.base : port.tint)
            joinStyle: ShapePath.MiterJoin
            startX: 0
            startY: 0
            PathLine { x: triangle.width; y: triangle.height / 2 }
            PathLine { x: 0; y: triangle.height }
            PathLine { x: 0; y: 0 }
        }
    }

    Text {
        visible: port.showLabel && port.canvas !== null && port.canvas.zoom >= 0.7
        text: port.portName
        font.pixelSize: 9
        color: palette.placeholderText
        anchors.verticalCenter: parent.verticalCenter
        x: port.isInput ? port.hitSize : -implicitWidth
    }

    DragHandler {
        id: wireDrag
        enabled: port.conserving || !port.isInput
        target: null
        acceptedButtons: Qt.LeftButton
        cursorShape: Qt.CrossCursor
        // The block's own move handler must not take the drag over when the pointer
        // crosses the block body (a left-edge physical port is dragged to the right).
        grabPermissions: PointerHandler.TakeOverForbidden
        onActiveChanged: {
            if (active)
                port.canvas.beginWire(port.address, port.parent.x + port.px,
                                      port.parent.y + port.py, port.conserving)
            else
                port.canvas.endWire()
        }
        onCentroidChanged: if (active) port.canvas.dragWire(centroid.scenePosition)
    }

    HoverHandler {
        enabled: port.conserving || !port.isInput
        cursorShape: Qt.CrossCursor
    }
}
