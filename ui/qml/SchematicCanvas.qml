import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes
import QtSpice

// Schematic sheet (S5). Everything is drawn in sheet units inside `content`, which is
// moved (panX, panY) and scaled (zoom) as one item, so panning and zooming never
// rebuild the drawing. Line widths follow the zoom only when it settles.
//
// Mouse: wheel zooms around the pointer; middle- or right-drag pans; in select mode
// a click selects (Ctrl+click toggles) and a drag on empty space is a rubber band.
// Tools: "place" (S6: each click drops a part, Ctrl+R / Ctrl+E turn it), "wire"
// (S7, see WireTool.qml), "label" (asks for a name, then each click places it) and
// "ground". Esc or a right-click without dragging ends a wire, then leaves the tool.
// Editing (S8): dragging a selected thing drags it with its wires following; the
// "move" tool drags with wires detached. Ctrl+R rotate, Ctrl+E mirror, Delete,
// Ctrl+C / Ctrl+V (the copy floats until a click), Ctrl+Z / Ctrl+Y undo and redo.
// The model, hit-testing and undo live in SchematicController.
Item {
    id: root
    objectName: "schematicCanvas"

    property real zoom: 1.0
    property real panX: 60
    property real panY: 60
    readonly property real minZoom: 0.05
    readonly property real maxZoom: 16.0
    readonly property int grid: 16
    // The zoom that line widths use; follows `zoom` once it stops changing.
    property real settledZoom: 1.0
    readonly property real penWidth: 1.5 / settledZoom
    readonly property real pickTolerance: 6 / zoom   // sheet units for ~6 px

    // "select", or a tool: "place" (S6), "wire", "label", "ground" (S7), "move",
    // "paste" (S8).
    property string mode: "select"
    property string placeSymbol: ""
    property string labelName: ""
    property point pointer: Qt.point(0, 0)   // last pointer position, sheet units
    readonly property string hint: {
        if (mode === "place")
            return qsTr("Placing %1: click to place, Ctrl+R rotate, Ctrl+E mirror, Esc to stop")
                   .arg(SchematicController.placing)
        if (mode === "wire")
            return wireTool.active ? qsTr("Click to add a corner; a pin or wire ends it; Esc ends")
                                   : qsTr("Wire: click to start; Esc to stop")
        if (mode === "label")
            return qsTr("Label %1: click a wire or pin to name its net; Esc to stop").arg(labelName)
        if (mode === "ground")
            return qsTr("Ground: click to place; Esc to stop")
        if (mode === "move")
            return qsTr("Move: drag the selection; wires stay where they are; Esc to stop")
        if (mode === "paste")
            return qsTr("Paste: click to drop the copy; Esc to cancel")
        return ""
    }

    // Rubber band in viewport pixels.
    property bool banding: false
    property point bandStart
    property point bandEnd

    function toSheet(vx, vy) {
        return Qt.point((vx - panX) / zoom, (vy - panY) / zoom)
    }

    function zoomAt(vx, vy, factor) {
        const c = toSheet(vx, vy)
        zoom = Math.max(minZoom, Math.min(maxZoom, zoom * factor))
        panX = vx - c.x * zoom
        panY = vy - c.y * zoom
    }

    function zoomToFit() {
        const b = SchematicController.contentBounds()
        if (b.length !== 4) {
            zoom = 1
            panX = viewport.width / 2
            panY = viewport.height / 2
            return
        }
        const margin = 32
        const w = Math.max(b[2] - b[0], grid) + 2 * margin
        const h = Math.max(b[3] - b[1], grid) + 2 * margin
        zoom = Math.max(minZoom, Math.min(maxZoom, Math.min(viewport.width / w, viewport.height / h)))
        panX = (viewport.width - (b[2] - b[0]) * zoom) / 2 - b[0] * zoom
        panY = (viewport.height - (b[3] - b[1]) * zoom) / 2 - b[1] * zoom
    }

    // ---- tools

    function startPlace(name) {
        stopTool()
        const at = viewport.containsPointer ? pointer
                                            : toSheet(viewport.width / 2, viewport.height / 2)
        if (SchematicController.beginPlace(name, at.x, at.y) !== "") {
            placeSymbol = name
            mode = "place"
            root.forceActiveFocus()
        }
    }

    function stopTool() {
        if (mode === "place")
            SchematicController.stopPlacing()
        if (mode === "paste")
            SchematicController.cancelLive()
        wireTool.stop()
        mode = "select"
        placeSymbol = ""
    }

    // Esc / right-click: end a wire first, then leave the tool.
    function cancelStep() {
        if (mode === "wire" && wireTool.active)
            wireTool.stop()
        else if (mode !== "select")
            stopTool()
        else
            SchematicController.clearSelection()
    }

    function startTool(name) {
        stopTool()
        if (name !== "move")
            SchematicController.clearSelection()
        mode = name
        wireTool.move(pointer)
        root.forceActiveFocus()
    }

    // Net label tool: ask for the name first (Enter or OK), then place on each click.
    function startLabel(name) {
        if (name === undefined) {
            labelField.text = labelName
            labelPopup.open()
            labelField.forceActiveFocus()
            labelField.selectAll()
            return
        }
        name = name.trim()
        if (name === "" || name.split(" ").length > 1 || name.split("\t").length > 1)
            return
        labelName = name
        startTool("label")
    }

    function snapped(p) {
        const s = SchematicController.snapPoint(p.x, p.y, pickTolerance)
        return Qt.point(s.x, s.y)
    }

    function rotate() {
        if (mode === "place" || mode === "select" || mode === "move")
            SchematicController.rotateSelection()
    }

    function mirror() {
        if (mode === "place" || mode === "select" || mode === "move")
            SchematicController.mirrorSelection()
    }

    function startPaste() {
        if (!SchematicController.canPaste)
            return
        stopTool()
        const at = viewport.containsPointer ? pointer
                                            : toSheet(viewport.width / 2, viewport.height / 2)
        if (SchematicController.beginPaste(at.x, at.y)) {
            mode = "paste"
            root.forceActiveFocus()
        }
    }

    // "Use with symbol..." in the Library Browser: place that symbol with its model.
    Connections {
        target: SchematicController
        function onPendingPartChanged() {
            const pending = SchematicController.pendingPart
            if (pending.symbol !== undefined && root.mode !== "place")
                root.startPlace(pending.symbol)
        }
    }

    Shortcut {
        sequence: "Esc"
        enabled: root.visible
        onActivated: root.cancelStep()
    }
    Shortcut {
        sequence: "Ctrl+R"
        enabled: root.visible
        onActivated: root.rotate()
    }
    Shortcut {
        sequence: "Ctrl+E"
        enabled: root.visible
        onActivated: root.mirror()
    }
    Shortcut {
        sequences: [StandardKey.Delete, "Backspace"]
        enabled: root.visible && (root.mode === "select" || root.mode === "move")
        onActivated: SchematicController.deleteSelection()
    }
    Shortcut {
        sequences: [StandardKey.Copy]
        enabled: root.visible
        onActivated: SchematicController.copySelection()
    }
    Shortcut {
        sequences: [StandardKey.Paste]
        enabled: root.visible
        onActivated: root.startPaste()
    }
    Shortcut {
        sequences: [StandardKey.Undo]
        enabled: root.visible
        onActivated: { root.stopTool(); SchematicController.undo() }
    }
    Shortcut {
        sequences: [StandardKey.Redo]   // Ctrl+Y and Ctrl+Shift+Z on Windows
        enabled: root.visible
        onActivated: { root.stopTool(); SchematicController.redo() }
    }

    onZoomChanged: settleTimer.restart()
    Timer {
        id: settleTimer
        interval: 80
        onTriggered: root.settledZoom = root.zoom
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        ToolBar {
            id: toolBar
            objectName: "schematicToolBar"
            Layout.fillWidth: true
            RowLayout {
                id: tools
                anchors.fill: parent
                spacing: 4
                ToolButton {
                    objectName: "fitButton"
                    text: qsTr("Fit")
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Zoom to fit the whole sheet")
                    onClicked: root.zoomToFit()
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "wireButton"
                    text: qsTr("Wire")
                    checkable: true
                    checked: root.mode === "wire"
                    onClicked: root.mode === "wire" ? root.stopTool() : root.startTool("wire")
                }
                ToolButton {
                    objectName: "labelButton"
                    text: qsTr("Label")
                    checkable: true
                    checked: root.mode === "label"
                    onClicked: root.mode === "label" ? root.stopTool() : root.startLabel()
                }
                ToolButton {
                    objectName: "groundButton"
                    text: qsTr("Ground")
                    checkable: true
                    checked: root.mode === "ground"
                    onClicked: root.mode === "ground" ? root.stopTool() : root.startTool("ground")
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "moveButton"
                    text: qsTr("Move")
                    checkable: true
                    checked: root.mode === "move"
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Drag the selection with its wires detached")
                    onClicked: root.mode === "move" ? root.stopTool() : root.startTool("move")
                }
                ToolButton {
                    objectName: "rotateButton"
                    text: qsTr("Rotate")
                    enabled: SchematicController.selectionCount > 0 || root.mode === "place"
                    onClicked: root.rotate()
                }
                ToolButton {
                    objectName: "mirrorButton"
                    text: qsTr("Mirror")
                    enabled: SchematicController.selectionCount > 0 || root.mode === "place"
                    onClicked: root.mirror()
                }
                ToolButton {
                    objectName: "deleteButton"
                    text: qsTr("Delete")
                    enabled: SchematicController.selectionCount > 0 && root.mode === "select"
                    onClicked: SchematicController.deleteSelection()
                }
                ToolButton {
                    objectName: "undoButton"
                    text: qsTr("Undo")
                    enabled: SchematicController.canUndo
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Undo %1").arg(SchematicController.undoText)
                    onClicked: { root.stopTool(); SchematicController.undo() }
                }
                ToolButton {
                    objectName: "redoButton"
                    text: qsTr("Redo")
                    enabled: SchematicController.canRedo
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Redo %1").arg(SchematicController.redoText)
                    onClicked: { root.stopTool(); SchematicController.redo() }
                }
                ToolSeparator {}
                Label {
                    objectName: "zoomLabel"
                    text: Math.round(root.zoom * 100) + " %"
                    Layout.preferredWidth: 56
                }
                Label {
                    objectName: "toolHint"
                    text: root.hint
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }

        Item {
            id: viewport
            objectName: "canvasViewport"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            readonly property bool containsPointer: mouse.containsMouse

            Rectangle { anchors.fill: parent; color: "#fbfbf7" }

            // Grid lines every 16 units, or every 64 when they would be denser than 8 px.
            // Lines live in viewport pixels and only move, so pan and zoom stay cheap.
            Item {
                id: gridLayer
                anchors.fill: parent
                readonly property real step: root.grid * root.zoom >= 8
                                             ? root.grid * root.zoom
                                             : root.grid * 4 * root.zoom
                readonly property bool shown: step >= 8
                readonly property real offX: ((root.panX % step) + step) % step
                readonly property real offY: ((root.panY % step) + step) % step
                visible: shown
                Repeater {
                    model: Math.ceil(viewport.width / 8) + 2
                    Rectangle {
                        required property int index
                        x: gridLayer.offX + index * gridLayer.step
                        visible: x <= viewport.width
                        width: 1
                        height: viewport.height
                        color: "#ecece4"
                    }
                }
                Repeater {
                    model: Math.ceil(viewport.height / 8) + 2
                    Rectangle {
                        required property int index
                        y: gridLayer.offY + index * gridLayer.step
                        visible: y <= viewport.height
                        height: 1
                        width: viewport.width
                        color: "#ecece4"
                    }
                }
            }

            Item {
                id: content
                objectName: "schematicContent"
                x: root.panX
                y: root.panY
                scale: root.zoom
                transformOrigin: Item.TopLeft

                Shape {
                    objectName: "wireShape"
                    ShapePath {
                        strokeColor: "#2d7d32"
                        strokeWidth: root.penWidth
                        fillColor: "transparent"
                        capStyle: ShapePath.RoundCap
                        PathSvg { path: SchematicController.wirePath }
                    }
                }
                Shape {
                    ShapePath {
                        strokeColor: "#d35400"
                        strokeWidth: root.penWidth * 2
                        fillColor: "transparent"
                        capStyle: ShapePath.RoundCap
                        PathSvg { path: SchematicController.selectedWirePath }
                    }
                }

                Repeater {
                    objectName: "instanceRepeater"
                    model: SchematicController.instances
                    delegate: SymbolItem {
                        required property var model
                        x: model.posX
                        y: model.posY
                        symbolName: model.symbolName
                        instName: model.instName
                        matrix: model.matrix
                        texts: model.texts
                        selected: model.selected
                        missing: model.missing
                        penWidth: root.penWidth
                    }
                }

                Repeater {
                    objectName: "flagRepeater"
                    model: SchematicController.flags
                    delegate: Item {
                        id: flag
                        objectName: "flagItem"
                        required property var model
                        readonly property string kind: model.kind
                        readonly property string name: model.name
                        readonly property string direction: model.direction
                        readonly property bool selected: model.selected
                        x: model.posX
                        y: model.posY
                        readonly property color color: selected ? "#d35400" : "#1c3d8c"

                        SymbolItem {
                            visible: flag.kind === "ground"
                            symbolName: flag.kind === "ground" ? "ground" : ""
                            selected: flag.selected
                            showPins: false
                            penWidth: root.penWidth
                        }
                        Rectangle {
                            visible: flag.kind !== "ground"
                            x: -3; y: -3; width: 6; height: 6
                            color: flag.color
                        }
                        Text {
                            visible: flag.kind !== "ground"
                            x: 6
                            y: -height / 2
                            text: flag.kind === "port"
                                  ? ({ "in": "▶ ", "out": "◀ ", "bidir": "◆ " }[flag.direction] || "") + flag.name
                                  : flag.name
                            color: flag.color
                            font.pixelSize: 12
                        }
                    }
                }

                // Sheet texts: SPICE directives (bold) and comments (S9 adds the tool).
                Repeater {
                    model: SchematicController.texts
                    delegate: Text {
                        required property var modelData
                        objectName: "sheetText"
                        x: modelData.posX
                        y: modelData.posY - height / 2
                        text: modelData.text
                        font.pixelSize: 12
                        font.bold: modelData.directive
                        color: modelData.directive ? "#202020" : "#606060"
                    }
                }

                WireTool {
                    id: wireTool
                    visible: root.mode === "wire"
                    tolerance: root.pickTolerance
                    penWidth: root.penWidth
                }

                Repeater {
                    objectName: "junctionRepeater"
                    model: SchematicController.junctions
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "junctionDot"
                        x: modelData.x - 4
                        y: modelData.y - 4
                        width: 8; height: 8; radius: 4
                        color: "#2d7d32"
                    }
                }
            }

            Rectangle {
                objectName: "rubberBand"
                visible: root.banding
                x: Math.min(root.bandStart.x, root.bandEnd.x)
                y: Math.min(root.bandStart.y, root.bandEnd.y)
                width: Math.abs(root.bandEnd.x - root.bandStart.x)
                height: Math.abs(root.bandEnd.y - root.bandStart.y)
                color: "#201c3d8c"
                border.color: "#1c3d8c"
            }

            MouseArea {
                id: mouse
                objectName: "canvasMouse"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
                hoverEnabled: true
                property bool panning: false
                property bool panned: false
                property bool pressedOnItem: false   // a drag from here moves the selection
                property point pressSheet
                property real lastX
                property real lastY

                onPressed: ev => {
                    root.forceActiveFocus()
                    lastX = ev.x
                    lastY = ev.y
                    const p = root.toSheet(ev.x, ev.y)
                    root.pointer = p
                    if (ev.button === Qt.MiddleButton || ev.button === Qt.RightButton) {
                        panning = true
                        panned = false
                        return
                    }
                    if (root.mode === "place") {
                        SchematicController.placeCommit(p.x, p.y)
                        return
                    }
                    if (root.mode === "wire") {
                        wireTool.press(p)
                        return
                    }
                    if (root.mode === "label") {
                        const q = root.snapped(p)
                        SchematicController.addLabel(q.x, q.y, root.labelName)
                        return
                    }
                    if (root.mode === "ground") {
                        const q = root.snapped(p)
                        SchematicController.addGround(q.x, q.y)
                        return
                    }
                    if (root.mode === "paste") {
                        SchematicController.moveTo(p.x, p.y)
                        SchematicController.endMove()
                        root.mode = "select"
                        return
                    }
                    if (root.mode !== "select" && root.mode !== "move")
                        return
                    const add = (ev.modifiers & Qt.ControlModifier) !== 0
                    const hit = SchematicController.itemAt(p.x, p.y, root.pickTolerance)
                    pressedOnItem = false
                    if (hit.kind !== undefined) {
                        const already = SchematicController.isSelected(hit.kind, hit.index)
                        if (add || !already)
                            SchematicController.select(hit.kind, hit.index, add)
                        pressedOnItem = !add && SchematicController.isSelected(hit.kind, hit.index)
                        pressSheet = p
                    } else {
                        if (!add)
                            SchematicController.clearSelection()
                        root.bandStart = Qt.point(ev.x, ev.y)
                        root.bandEnd = root.bandStart
                        root.banding = true
                    }
                }
                onPositionChanged: ev => {
                    const p = root.toSheet(ev.x, ev.y)
                    root.pointer = p
                    if (panning) {
                        if (Math.abs(ev.x - lastX) + Math.abs(ev.y - lastY) > 0)
                            panned = true
                        root.panX += ev.x - lastX
                        root.panY += ev.y - lastY
                        lastX = ev.x
                        lastY = ev.y
                    } else if (root.mode === "place") {
                        SchematicController.placeMoveTo(p.x, p.y)
                    } else if (root.mode === "wire") {
                        wireTool.move(p)
                    } else if (root.mode === "paste") {
                        SchematicController.moveTo(p.x, p.y)
                    } else if (pressedOnItem && (ev.buttons & Qt.LeftButton)) {
                        if (!SchematicController.moving) {
                            const far = Math.abs(p.x - pressSheet.x) + Math.abs(p.y - pressSheet.y)
                            if (far * root.zoom > 3)
                                SchematicController.beginMove(pressSheet.x, pressSheet.y,
                                                              root.mode !== "move")
                        }
                        if (SchematicController.moving)
                            SchematicController.moveTo(p.x, p.y)
                    } else if (root.banding) {
                        root.bandEnd = Qt.point(ev.x, ev.y)
                    }
                }
                onReleased: ev => {
                    if (panning) {
                        panning = false
                        if (!panned && ev.button === Qt.RightButton && root.mode !== "select")
                            root.cancelStep()
                        return
                    }
                    if (pressedOnItem) {
                        pressedOnItem = false
                        if (SchematicController.moving)
                            SchematicController.endMove()
                        return
                    }
                    if (root.banding) {
                        root.banding = false
                        const a = root.toSheet(root.bandStart.x, root.bandStart.y)
                        const b = root.toSheet(root.bandEnd.x, root.bandEnd.y)
                        const add = (ev.modifiers & Qt.ControlModifier) !== 0
                        if (Math.abs(root.bandEnd.x - root.bandStart.x) > 3
                                || Math.abs(root.bandEnd.y - root.bandStart.y) > 3)
                            SchematicController.selectRect(Math.min(a.x, b.x), Math.min(a.y, b.y),
                                                           Math.max(a.x, b.x), Math.max(a.y, b.y), add)
                    }
                }
                onWheel: ev => {
                    root.zoomAt(ev.x, ev.y, Math.pow(1.25, ev.angleDelta.y / 120))
                    ev.accepted = true
                }
            }
        }
    }

    Popup {
        id: labelPopup
        objectName: "labelPopup"
        anchors.centerIn: parent
        modal: true
        padding: 12
        contentItem: ColumnLayout {
            spacing: 8
            Label { text: qsTr("Net label name (one word):") }
            TextField {
                id: labelField
                objectName: "labelField"
                Layout.preferredWidth: 200
                selectByMouse: true
                onAccepted: okButton.clicked()
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: qsTr("Cancel")
                    onClicked: labelPopup.close()
                }
                Button {
                    id: okButton
                    objectName: "labelOk"
                    text: qsTr("OK")
                    enabled: labelField.text.trim() !== ""
                    onClicked: {
                        labelPopup.close()
                        root.startLabel(labelField.text)
                    }
                }
            }
        }
    }

    Component.onCompleted: Qt.callLater(zoomToFit)
    Connections {
        target: ProjectManager
        function onProjectLoaded() { Qt.callLater(root.zoomToFit) }
    }
}
