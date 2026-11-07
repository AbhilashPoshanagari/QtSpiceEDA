import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes
import QtSpice

// Block diagram canvas (F6). Drop blocks from the library, drag them (snapped to the
// grid), drag from an output port to an input port to wire them, or from a round
// physical port to another one of the same domain for a physical wire (U1). Left-drag on empty
// space selects with a rubber band, middle-drag pans, the wheel zooms around the
// cursor. Delete removes the selection; Ctrl+Z / Ctrl+Y undo and redo.
//
// Everything is in canvas units inside `content`; `panX`, `panY` and `zoom` map them
// to the viewport. The model, hit-testing and undo live in DiagramController.
Item {
    id: root
    objectName: "blockCanvas"

    property real zoom: 1.0
    property real panX: 40
    property real panY: 40
    readonly property int grid: DiagramController.grid
    readonly property real minZoom: 0.2
    readonly property real maxZoom: 4.0

    // The wire being drawn from an output port (content coordinates).
    property string wireSource: ""
    property point wireStart
    property point wireEnd
    property string wireTarget: ""
    property string wireHint: ""
    property bool wirePhysical: false

    function toCanvas(vx, vy) {
        return Qt.point((vx - panX) / zoom, (vy - panY) / zoom)
    }

    function zoomAt(vx, vy, factor) {
        const c = toCanvas(vx, vy)
        zoom = Math.max(minZoom, Math.min(maxZoom, zoom * factor))
        panX = vx - c.x * zoom
        panY = vy - c.y * zoom
    }

    function beginWire(src, x, y, physical) {
        root.forceActiveFocus()
        wirePhysical = physical === true
        wireSource = src
        wireStart = Qt.point(x, y)
        wireEnd = wireStart
        wireTarget = ""
        wireHint = ""
    }

    function dragWire(scenePos) {
        const p = content.mapFromItem(null, scenePos.x, scenePos.y)
        wireEnd = p
        let target = wirePhysical ? DiagramController.conservingPortAt(p.x, p.y)
                                  : DiagramController.portAt(p.x, p.y)
        if (target === wireSource)
            target = ""
        if (target === "")
            wireHint = ""
        else if (wirePhysical)
            wireHint = DiagramController.canConnectPhysical(wireSource, target)
        else
            wireHint = DiagramController.canConnect(wireSource, target)
        wireTarget = target
    }

    // A refused drop leaves its reason in the canvas status (DiagramController.hint).
    function endWire() {
        if (wireSource !== "" && wireTarget !== "") {
            if (wirePhysical)
                DiagramController.connectPhysical(wireSource, wireTarget)
            else
                DiagramController.connectPorts(wireSource, wireTarget)
        }
        wireSource = ""
        wireTarget = ""
        wireHint = ""
        wirePhysical = false
    }

    function run() {
        DiagramController.run(Number(tEndField.text), Number(dtField.text))
    }

    // A project was opened: show its end time and step.
    Connections {
        target: DiagramController
        function onRunSettingsChanged() {
            if (Number(tEndField.text) !== DiagramController.tEnd)
                tEndField.text = String(DiagramController.tEnd)
            if (Number(dtField.text) !== DiagramController.dt)
                dtField.text = String(DiagramController.dt)
        }
    }

    Shortcut {
        sequences: [StandardKey.Delete, "Backspace"]
        enabled: root.visible
        onActivated: DiagramController.removeSelected()
    }
    Shortcut {
        sequences: [StandardKey.Undo]
        enabled: root.visible
        onActivated: DiagramController.undo()
    }
    Shortcut {
        sequences: [StandardKey.Redo]
        enabled: root.visible
        onActivated: DiagramController.redo()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        ToolBar {
            Layout.fillWidth: true
            RowLayout {
                anchors.fill: parent
                spacing: 4
                Label { text: qsTr("End time"); leftPadding: 6 }
                TextField {
                    id: tEndField
                    objectName: "tEndField"
                    text: String(DiagramController.tEnd)
                    onTextChanged: {
                        const v = Number(text)
                        if (text.trim() !== "" && isFinite(v) && v > 0)
                            DiagramController.tEnd = v
                    }
                    Layout.preferredWidth: 64
                    validator: DoubleValidator { bottom: 0 }
                    selectByMouse: true
                }
                Label { text: qsTr("s   Step") }
                TextField {
                    id: dtField
                    objectName: "dtField"
                    text: String(DiagramController.dt)
                    onTextChanged: {
                        const v = Number(text)
                        if (text.trim() !== "" && isFinite(v) && v > 0)
                            DiagramController.dt = v
                    }
                    Layout.preferredWidth: 64
                    validator: DoubleValidator { bottom: 0 }
                    selectByMouse: true
                }
                Label { text: qsTr("s") }
                ToolButton {
                    objectName: "diagramRunButton"
                    text: qsTr("Run")
                    enabled: !DiagramController.running
                    onClicked: root.run()
                }
                ToolButton {
                    objectName: "diagramStopButton"
                    text: qsTr("Stop")
                    enabled: DiagramController.running
                    onClicked: DiagramController.stop()
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "undoButton"
                    text: qsTr("Undo")
                    enabled: DiagramController.canUndo
                    onClicked: DiagramController.undo()
                    ToolTip.visible: hovered && DiagramController.undoText !== ""
                    ToolTip.text: qsTr("Undo ") + DiagramController.undoText
                }
                ToolButton {
                    objectName: "redoButton"
                    text: qsTr("Redo")
                    enabled: DiagramController.canRedo
                    onClicked: DiagramController.redo()
                    ToolTip.visible: hovered && DiagramController.redoText !== ""
                    ToolTip.text: qsTr("Redo ") + DiagramController.redoText
                }
                Label {
                    objectName: "canvasStatus"
                    Layout.fillWidth: true
                    leftPadding: 8
                    elide: Text.ElideRight
                    readonly property bool problem: DiagramController.hint !== ""
                        || DiagramController.runStatus.startsWith("Failed")
                        || DiagramController.runStatus.startsWith("Not started")
                    text: DiagramController.hint !== "" ? DiagramController.hint
                                                        : DiagramController.runStatus
                    color: problem ? "#d73a49" : palette.windowText
                    ToolTip.visible: statusHover.hovered && DiagramController.runDetails !== ""
                    ToolTip.text: DiagramController.runDetails
                    HoverHandler { id: statusHover }
                }
            }
        }

        Item {
            id: viewport
            objectName: "canvasViewport"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            Rectangle {
                anchors.fill: parent
                color: palette.base
            }

            Canvas {
                id: gridCanvas
                anchors.fill: parent
                readonly property color minor: Qt.rgba(0.5, 0.5, 0.5, 0.15)
                readonly property color major: Qt.rgba(0.5, 0.5, 0.5, 0.3)
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    let step = root.grid * root.zoom
                    let every = 5
                    while (step < 8) {
                        step *= 5
                    }
                    const lines = (offset, size, vertical, color, spacing) => {
                        ctx.beginPath()
                        ctx.strokeStyle = color
                        ctx.lineWidth = 1
                        let s = offset % spacing
                        if (s < 0)
                            s += spacing
                        for (let v = s; v < size; v += spacing) {
                            const p = Math.round(v) + 0.5
                            if (vertical) {
                                ctx.moveTo(p, 0)
                                ctx.lineTo(p, height)
                            } else {
                                ctx.moveTo(0, p)
                                ctx.lineTo(width, p)
                            }
                        }
                        ctx.stroke()
                    }
                    lines(root.panX, width, true, minor, step)
                    lines(root.panY, height, false, minor, step)
                    lines(root.panX, width, true, major, step * every)
                    lines(root.panY, height, false, major, step * every)
                }
                Connections {
                    target: root
                    function onPanXChanged() { gridCanvas.requestPaint() }
                    function onPanYChanged() { gridCanvas.requestPaint() }
                    function onZoomChanged() { gridCanvas.requestPaint() }
                }
            }

            // Empty-space mouse: rubber band (left), pan (middle), click to select a
            // wire or clear the selection, wheel to zoom.
            MouseArea {
                id: background
                objectName: "canvasBackground"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton
                property point pressAt
                property real startPanX
                property real startPanY
                property bool banding: false
                property bool panning: false

                onPressed: mouse => {
                    root.forceActiveFocus()
                    pressAt = Qt.point(mouse.x, mouse.y)
                    if (mouse.button === Qt.MiddleButton) {
                        panning = true
                        startPanX = root.panX
                        startPanY = root.panY
                        cursorShape = Qt.ClosedHandCursor
                    } else {
                        banding = true
                        band.x = mouse.x
                        band.y = mouse.y
                        band.width = 0
                        band.height = 0
                    }
                }
                onPositionChanged: mouse => {
                    if (panning) {
                        root.panX = startPanX + mouse.x - pressAt.x
                        root.panY = startPanY + mouse.y - pressAt.y
                    } else if (banding) {
                        band.x = Math.min(mouse.x, pressAt.x)
                        band.y = Math.min(mouse.y, pressAt.y)
                        band.width = Math.abs(mouse.x - pressAt.x)
                        band.height = Math.abs(mouse.y - pressAt.y)
                    }
                }
                onReleased: mouse => {
                    const additive = (mouse.modifiers & (Qt.ControlModifier | Qt.ShiftModifier)) !== 0
                    if (panning) {
                        panning = false
                        cursorShape = Qt.ArrowCursor
                        return
                    }
                    if (!banding)
                        return
                    banding = false
                    if (band.width < 4 && band.height < 4) {
                        const c = root.toCanvas(mouse.x, mouse.y)
                        const hit = DiagramController.wireAt(c.x, c.y, 6 / root.zoom)
                        const physical = hit < 0
                            ? DiagramController.physicalWireAt(c.x, c.y, 6 / root.zoom) : -1
                        if (hit >= 0)
                            DiagramController.selectWire(hit, additive)
                        else if (physical >= 0)
                            DiagramController.selectPhysicalWire(physical, additive)
                        else if (!additive)
                            DiagramController.clearSelection()
                    } else {
                        const a = root.toCanvas(band.x, band.y)
                        DiagramController.selectInRect(a.x, a.y, band.width / root.zoom,
                                                       band.height / root.zoom, additive)
                    }
                    band.width = 0
                    band.height = 0
                }
                onWheel: wheel => root.zoomAt(wheel.x, wheel.y, Math.pow(1.0015, wheel.angleDelta.y))
            }

            Item {
                id: content
                objectName: "canvasContent"
                x: root.panX
                y: root.panY
                scale: root.zoom
                transformOrigin: Item.TopLeft

                Repeater {
                    objectName: "wireRepeater"
                    model: DiagramController.wires
                    delegate: Wire {}
                }

                Repeater {
                    objectName: "physicalWireRepeater"
                    model: DiagramController.physicalWires
                    delegate: PhysicalWire {}
                }

                Repeater {
                    objectName: "blockRepeater"
                    model: DiagramController.blocks
                    delegate: Block { canvas: root }
                }

                // Junction dots: where two or more physical wires meet at a port.
                Repeater {
                    objectName: "junctionRepeater"
                    model: DiagramController.junctions
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "junction_" + modelData.port
                        x: modelData.x - 4
                        y: modelData.y - 4
                        z: 2
                        width: 8
                        height: 8
                        radius: 4
                        color: modelData.color
                        border.color: Qt.darker(modelData.color, 1.5)
                    }
                }

                Shape {
                    objectName: "rubberPhysicalWire"
                    visible: root.wireSource !== "" && root.wirePhysical
                    ShapePath {
                        strokeColor: root.wireTarget === "" ? "#6e7781"
                                     : root.wireHint === "" ? "#2da44e" : "#d73a49"
                        strokeWidth: 2
                        strokeStyle: ShapePath.DashLine
                        fillColor: "transparent"
                        startX: root.wireStart.x
                        startY: root.wireStart.y
                        PathLine { x: root.wireEnd.x; y: root.wireStart.y }
                        PathLine { x: root.wireEnd.x; y: root.wireEnd.y }
                    }
                }

                Shape {
                    objectName: "rubberWire"
                    visible: root.wireSource !== "" && !root.wirePhysical
                    ShapePath {
                        strokeColor: root.wireTarget === "" ? "#6e7781"
                                     : root.wireHint === "" ? "#2da44e" : "#d73a49"
                        strokeWidth: 2
                        strokeStyle: ShapePath.DashLine
                        fillColor: "transparent"
                        startX: root.wireStart.x
                        startY: root.wireStart.y
                        PathCubic {
                            readonly property real bend: Math.max(30, Math.abs(root.wireEnd.x - root.wireStart.x) / 2)
                            x: root.wireEnd.x
                            y: root.wireEnd.y
                            control1X: root.wireStart.x + bend
                            control1Y: root.wireStart.y
                            control2X: root.wireEnd.x - bend
                            control2Y: root.wireEnd.y
                        }
                    }
                }
            }

            Rectangle {
                id: band
                objectName: "rubberBand"
                visible: background.banding && (width > 0 || height > 0)
                color: Qt.rgba(palette.highlight.r, palette.highlight.g, palette.highlight.b, 0.15)
                border.color: palette.highlight
            }

            // Why the wire being drawn cannot go to the port under the cursor.
            Label {
                objectName: "wireHint"
                visible: root.wireSource !== "" && root.wireHint !== ""
                text: root.wireHint
                x: root.panX + root.wireEnd.x * root.zoom + 14
                y: root.panY + root.wireEnd.y * root.zoom + 10
                padding: 4
                color: "white"
                background: Rectangle { color: "#d73a49"; radius: 3 }
            }

            Label {
                anchors.centerIn: parent
                visible: DiagramController.blocks.count === 0
                text: qsTr("Drag blocks here from the library")
                color: palette.placeholderText
            }

            DropArea {
                objectName: "canvasDropArea"
                anchors.fill: parent
                keys: ["qtspice/block"]
                onDropped: drop => {
                    const c = root.toCanvas(drop.x, drop.y)
                    if (DiagramController.addBlock(drop.source.blockName, c.x - 30, c.y - 30) !== "")
                        drop.accept()
                }
            }
        }
    }
}
