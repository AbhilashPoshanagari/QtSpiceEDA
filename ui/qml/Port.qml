import QtQuick

// One port of a block on the canvas. Inputs sit on the block's left edge, outputs on
// its right edge (positions come from DiagramController). Drag from an output port and
// drop on an input port to draw a wire; while dragging, the input under the cursor turns
// green when the wire is valid and red when it is not.
Item {
    id: port

    property Item canvas
    property string blockId
    property string portName
    property bool isInput: true
    property real px
    property real py
    property color tint: "#888888"
    property bool showLabel: true

    readonly property string address: blockId + "." + portName
    readonly property bool targeted: canvas !== null && canvas.wireTarget === address
    readonly property real hitSize: 16

    objectName: (isInput ? "in_" : "out_") + address
    x: px - hitSize / 2
    y: py - hitSize / 2
    width: hitSize
    height: hitSize

    Rectangle {
        anchors.centerIn: parent
        width: port.targeted ? 12 : 9
        height: width
        radius: width / 2
        color: port.targeted ? (port.canvas.wireHint === "" ? "#2da44e" : "#d73a49")
                             : (port.isInput ? palette.base : port.tint)
        border.color: port.tint
        border.width: 2
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
        enabled: !port.isInput
        target: null
        acceptedButtons: Qt.LeftButton
        cursorShape: Qt.CrossCursor
        onActiveChanged: {
            if (active)
                port.canvas.beginWire(port.address, port.parent.x + port.px, port.parent.y + port.py)
            else
                port.canvas.endWire()
        }
        onCentroidChanged: if (active) port.canvas.dragWire(centroid.scenePosition)
    }

    HoverHandler {
        enabled: !port.isInput
        cursorShape: Qt.CrossCursor
    }
}
