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
// S9: double-click a part for its attributes, a label to rename it, a text to edit it;
// the "text" tool asks for a SPICE directive (or comment) and places it on a click.
// S10: LTspice keys (see shortcutList, also shown by F1 / the "?" button), a delete
// tool and an explicit drag tool; the problems of the last Check/Run are listed under
// the sheet.
// S11: after a schematic run the canvas probes ("probe" mode): a wire gives V(net), a
// two-terminal part its current, a pin the current into it, a drag from net to net
// V(a,b), Alt+click a part its power. A badge at the pointer shows which. Editing the
// sheet ends probing until the next run.
// H1: the "port" tool asks for a port name and direction (in / out / bidir), then
// each click places a port flag; double-click a port to edit it. Ports become the pins
// of a circuit symbol (see PortsPanel.qml).
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
    // "paste" (S8), "text" (S9), "delete", "drag" (S10), "probe" (S11), "port" (H1).
    property string mode: "select"
    property string placeSymbol: ""
    property string labelName: ""
    property int renameLabelIndex: -1     // label popup: -1 = new label, else rename
    property int editTextIndex: -1        // text popup: -1 = new text, else edit
    property string portName: ""
    property string portDirection: "in"
    property int editPortIndex: -1        // port popup: -1 = new port, else edit
    property string pendingText: ""
    property string probeBadge: ""          // "V", "I", "P", "V(a,b)" or ""
    property string probeStartNet: ""       // net under the press, for V(a,b) drags
    property point probeStart
    property bool probeDragging: false
    property bool pendingDirective: true
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
        if (mode === "port")
            return qsTr("Port %1 (%2): click a wire or pin; Esc to stop").arg(portName).arg(portDirection)
        if (mode === "move")
            return qsTr("Move: drag the selection; wires stay where they are; Esc to stop")
        if (mode === "paste")
            return qsTr("Paste: click to drop the copy; Esc to cancel")
        if (mode === "probe")
            return SchematicController.probeMessage !== ""
                   ? SchematicController.probeMessage
                   : qsTr("Probe: wire = V, part = I, pin = I into it, drag net to net = V(a,b), Alt+click = power")
        if (mode === "delete")
            return qsTr("Delete: click things to delete them; Esc to stop")
        if (mode === "drag")
            return qsTr("Drag: drag the selection; wires follow; Esc to stop")
        if (mode === "text")
            return qsTr("Click to place the %1; Esc to cancel")
                   .arg(pendingDirective ? qsTr("SPICE directive") : qsTr("comment"))
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

    // S10: the LTspice keys. Letters only act while the canvas has focus, so typing in
    // a search box or dialog never places parts.
    readonly property var shortcutList: [
        ["R", qsTr("Place a resistor")],
        ["C", qsTr("Place a capacitor")],
        ["L", qsTr("Place an inductor")],
        ["D", qsTr("Place a diode")],
        ["G", qsTr("Ground tool")],
        ["F2", qsTr("Pick a part (part picker)")],
        ["F3", qsTr("Wire tool")],
        ["F4", qsTr("Net label tool")],
        ["F5", qsTr("Delete tool: click things to delete them")],
        ["F7", qsTr("Move tool: drag with wires detached")],
        ["F8", qsTr("Drag tool: drag with wires following")],
        ["F9 / Ctrl+Z", qsTr("Undo")],
        ["Shift+F9 / Ctrl+Y", qsTr("Redo")],
        ["Ctrl+R / Ctrl+E", qsTr("Rotate / mirror the selection or the part being placed")],
        ["Delete", qsTr("Delete the selection")],
        ["Ctrl+C / Ctrl+V", qsTr("Copy / paste (click to drop the copy)")],
        ["Esc / right-click", qsTr("End a wire, then leave the tool")],
        ["Double-click", qsTr("Edit a part's attributes, a label, a port or a text")],
        ["Wheel / middle-drag", qsTr("Zoom around the pointer / pan")],
        ["F1", qsTr("This list")]
    ]

    signal partPickerRequested()

    focus: true
    Keys.onPressed: ev => {
        if (ev.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            return
        const parts = { [Qt.Key_R]: "res", [Qt.Key_C]: "cap", [Qt.Key_L]: "ind", [Qt.Key_D]: "diode" }
        if (parts[ev.key] !== undefined) {
            startPlace(parts[ev.key])
            ev.accepted = true
        } else if (ev.key === Qt.Key_G) {
            startTool("ground")
            ev.accepted = true
        }
    }

    Shortcut { sequence: "F1"; enabled: root.visible; onActivated: helpPanel.open() }
    Shortcut { sequence: "F2"; enabled: root.visible; onActivated: { root.stopTool(); root.partPickerRequested() } }
    Shortcut { sequence: "F3"; enabled: root.visible; onActivated: root.startTool("wire") }
    Shortcut { sequence: "F4"; enabled: root.visible; onActivated: root.startLabel() }
    Shortcut { sequence: "F5"; enabled: root.visible; onActivated: root.startTool("delete") }
    Shortcut { sequence: "F7"; enabled: root.visible; onActivated: root.startTool("move") }
    Shortcut { sequence: "F8"; enabled: root.visible; onActivated: root.startTool("drag") }
    Shortcut {
        sequence: "F9"
        enabled: root.visible
        onActivated: { root.stopTool(); SchematicController.undo() }
    }
    Shortcut {
        sequence: "Shift+F9"
        enabled: root.visible
        onActivated: { root.stopTool(); SchematicController.redo() }
    }

    // ---- probing (S11)

    function startProbe() {
        if (!SchematicController.probeReady)
            return
        stopTool()
        mode = "probe"
        root.forceActiveFocus()
    }

    Connections {
        target: SchematicController
        function onProbeChanged() {
            if (SchematicController.probeReady && root.mode === "select")
                root.startProbe()
            else if (!SchematicController.probeReady && root.mode === "probe")
                root.stopTool()
        }
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
        probeBadge = ""
        probeDragging = false
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
        if (name !== "move" && name !== "drag")
            SchematicController.clearSelection()
        mode = name
        wireTool.move(pointer)
        root.forceActiveFocus()
    }

    // Net label tool: ask for the name first (Enter or OK), then place on each click.
    function startLabel(name) {
        if (name === undefined) {
            renameLabelIndex = -1
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

    // Port tool (H1): ask for the name and direction, then place on each click.
    function startPort() {
        editPortIndex = -1
        portField.text = portName
        directionBox.currentIndex = Math.max(0, directionBox.model.indexOf(portDirection))
        portPopup.open()
        portField.forceActiveFocus()
        portField.selectAll()
    }

    function editPort(index) {
        const p = SchematicController.portAt(index)
        if (p.index === undefined)
            return
        editPortIndex = index
        portField.text = p.name
        directionBox.currentIndex = Math.max(0, directionBox.model.indexOf(p.direction))
        portPopup.open()
        portField.forceActiveFocus()
        portField.selectAll()
    }

    function portDone() {
        const name = portField.text.trim()
        const direction = directionBox.currentText
        if (SchematicController.portNameProblem(name) !== "")
            return
        portPopup.close()
        if (editPortIndex >= 0) {
            SchematicController.setPort(editPortIndex, name, direction)
            editPortIndex = -1
            return
        }
        portName = name
        portDirection = direction
        startTool("port")
    }

    function renameLabel(index) {
        renameLabelIndex = index
        labelField.text = SchematicController.labelName(index)
        labelPopup.open()
        labelField.forceActiveFocus()
        labelField.selectAll()
    }

    // The SPICE directive / comment popup: new text (then click to place) or an edit.
    function startText() {
        stopTool()
        editTextIndex = -1
        textArea.text = pendingText
        commentBox.checked = !pendingDirective
        textPopup.open()
        textArea.forceActiveFocus()
        textArea.selectAll()
    }

    function editText(index) {
        const t = SchematicController.textAt(index)
        if (t.index === undefined)
            return
        editTextIndex = index
        textArea.text = t.text
        commentBox.checked = !t.directive
        textPopup.open()
        textArea.forceActiveFocus()
    }

    function textDone() {
        textPopup.close()
        const text = textArea.text.trim()
        if (text === "")
            return
        if (editTextIndex >= 0) {
            SchematicController.setText(editTextIndex, text, !commentBox.checked)
        } else {
            pendingText = text
            pendingDirective = !commentBox.checked
            startTool("text")
        }
    }

    // Double-click: edit what is under the pointer.
    function editAt(p) {
        const hit = SchematicController.itemAt(p.x, p.y, pickTolerance)
        if (hit.kind === "instance")
            attributeDialog.openFor(hit.index)
        else if (hit.kind === "label")
            renameLabel(hit.index)
        else if (hit.kind === "port")
            editPort(hit.index)
        else if (hit.kind === "text")
            editText(hit.index)
    }

    function snapped(p) {
        const s = SchematicController.snapPoint(p.x, p.y, pickTolerance)
        return Qt.point(s.x, s.y)
    }

    function rotate() {
        if (mode === "place" || mode === "select" || mode === "move" || mode === "drag")
            SchematicController.rotateSelection()
    }

    function mirror() {
        if (mode === "place" || mode === "select" || mode === "move" || mode === "drag")
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
        enabled: root.visible && ["select", "move", "drag"].indexOf(root.mode) >= 0
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
                    objectName: "portButton"
                    text: qsTr("Port")
                    checkable: true
                    checked: root.mode === "port"
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Place a port flag (in, out or bidir): a pin of a circuit symbol")
                    onClicked: root.mode === "port" ? root.stopTool() : root.startPort()
                }
                ToolButton {
                    objectName: "textButton"
                    text: qsTr(".op")
                    checkable: true
                    checked: root.mode === "text"
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Place a SPICE directive such as .tran 5m, or a comment")
                    onClicked: root.mode === "text" ? root.stopTool() : root.startText()
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
                ToolButton {
                    objectName: "probeButton"
                    text: qsTr("Probe")
                    checkable: true
                    checked: root.mode === "probe"
                    enabled: SchematicController.probeReady
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Probe the last run (comes on after Run)")
                    onClicked: root.mode === "probe" ? root.stopTool() : root.startProbe()
                }
                ToolButton {
                    objectName: "helpButton"
                    text: "?"
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Keyboard shortcuts (F1)")
                    onClicked: helpPanel.open()
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
                        y: modelData.posY - 8
                        text: modelData.text
                        lineHeightMode: Text.FixedHeight
                        lineHeight: 16
                        font.pixelSize: 12
                        font.bold: modelData.directive
                        color: modelData.selected ? "#d35400"
                               : (modelData.directive ? "#202020" : "#606060")
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

            Shape {
                visible: root.probeDragging
                ShapePath {
                    strokeColor: "#c0392b"
                    strokeWidth: 1.5
                    strokeStyle: ShapePath.DashLine
                    fillColor: "transparent"
                    startX: root.probeStart.x
                    startY: root.probeStart.y
                    PathLine { x: mouse.mouseX; y: mouse.mouseY }
                }
            }

            Rectangle {
                objectName: "probeBadge"
                visible: root.mode === "probe" && root.probeBadge !== "" && mouse.containsMouse
                x: mouse.mouseX + 12
                y: mouse.mouseY + 12
                width: badgeText.implicitWidth + 8
                height: badgeText.implicitHeight + 4
                radius: 3
                color: root.probeBadge === "I" ? "#1f6fb2"
                       : (root.probeBadge === "P" ? "#8e44ad" : "#c0392b")
                Text {
                    id: badgeText
                    objectName: "probeBadgeText"
                    anchors.centerIn: parent
                    text: root.probeBadge
                    color: "white"
                    font.bold: true
                    font.pixelSize: 11
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
                cursorShape: root.mode === "probe" ? Qt.CrossCursor : Qt.ArrowCursor
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
                    if (root.mode === "probe") {
                        root.probeStart = Qt.point(ev.x, ev.y)
                        root.probeStartNet = SchematicController.probeNet(p.x, p.y, root.pickTolerance)
                        root.probeDragging = false
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
                    if (root.mode === "port") {
                        const q = root.snapped(p)
                        SchematicController.addPort(q.x, q.y, root.portName, root.portDirection)
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
                    if (root.mode === "text") {
                        const q = root.snapped(p)
                        SchematicController.addText(q.x, q.y, root.pendingText, root.pendingDirective)
                        root.mode = "select"
                        return
                    }
                    if (root.mode === "delete") {
                        const target = SchematicController.itemAt(p.x, p.y, root.pickTolerance)
                        if (target.kind !== undefined) {
                            SchematicController.select(target.kind, target.index, false)
                            SchematicController.deleteSelection()
                        }
                        return
                    }
                    if (["select", "move", "drag"].indexOf(root.mode) < 0)
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
                    } else if (root.mode === "probe") {
                        const alt = (ev.modifiers & Qt.AltModifier) !== 0
                        if ((ev.buttons & Qt.LeftButton) && root.probeStartNet !== "") {
                            const far = Math.abs(ev.x - root.probeStart.x) + Math.abs(ev.y - root.probeStart.y)
                            root.probeDragging = root.probeDragging || far > 4
                        }
                        root.probeBadge = root.probeDragging
                                ? "V(a,b)"
                                : SchematicController.probeKind(p.x, p.y, root.pickTolerance, alt)
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
                    if (root.mode === "probe" && ev.button === Qt.LeftButton) {
                        const p = root.toSheet(ev.x, ev.y)
                        const a = root.toSheet(root.probeStart.x, root.probeStart.y)
                        if (root.probeDragging)
                            SchematicController.probeBetween(a.x, a.y, p.x, p.y, root.pickTolerance)
                        else
                            SchematicController.probeClick(p.x, p.y, root.pickTolerance,
                                                           (ev.modifiers & Qt.AltModifier) !== 0)
                        root.probeDragging = false
                        root.probeStartNet = ""
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
                onDoubleClicked: ev => {
                    if (ev.button === Qt.LeftButton && ["select", "move", "drag"].indexOf(root.mode) >= 0)
                        root.editAt(root.toSheet(ev.x, ev.y))
                }
                onWheel: ev => {
                    root.zoomAt(ev.x, ev.y, Math.pow(1.25, ev.angleDelta.y / 120))
                    ev.accepted = true
                }
            }
        }

        // Problems of the last Check / Run of the schematic (S10).
        ListView {
            id: problemList
            objectName: "problemList"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, 96)
            visible: count > 0
            clip: true
            model: SchematicController.problems
            ScrollBar.vertical: ScrollBar {}
            delegate: Label {
                required property var modelData
                objectName: "problemItem"
                width: ListView.view.width
                leftPadding: 8
                topPadding: 2
                bottomPadding: 2
                text: (modelData.severity === "error" ? "✖ " : "⚠ ") + modelData.message
                color: modelData.severity === "error" ? "#c0392b" : "#a0522d"
                elide: Text.ElideRight
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
                        if (root.renameLabelIndex >= 0)
                            SchematicController.renameLabel(root.renameLabelIndex, labelField.text)
                        else
                            root.startLabel(labelField.text)
                        root.renameLabelIndex = -1
                    }
                }
            }
        }
    }

    Popup {
        id: portPopup
        objectName: "portPopup"
        anchors.centerIn: parent
        modal: true
        padding: 12
        contentItem: ColumnLayout {
            spacing: 8
            Label { text: root.editPortIndex >= 0 ? qsTr("Edit port") : qsTr("Port name (one word):") }
            TextField {
                id: portField
                objectName: "portField"
                Layout.preferredWidth: 220
                selectByMouse: true
                onAccepted: root.portDone()
            }
            RowLayout {
                Label { text: qsTr("Direction:") }
                ComboBox {
                    id: directionBox
                    objectName: "portDirection"
                    model: ["in", "out", "bidir"]
                }
            }
            Label {
                objectName: "portProblem"
                readonly property string problem: portField.text.trim() === ""
                                                  ? "" : SchematicController.portNameProblem(portField.text)
                visible: problem !== ""
                text: problem
                color: "#c0392b"
                Layout.preferredWidth: 220
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: qsTr("Cancel")
                    onClicked: { portPopup.close(); root.editPortIndex = -1 }
                }
                Button {
                    objectName: "portOk"
                    text: qsTr("OK")
                    enabled: portField.text.trim() !== ""
                             && SchematicController.portNameProblem(portField.text) === ""
                    onClicked: root.portDone()
                }
            }
        }
    }

    AttributeDialog { id: attributeDialog }

    Popup {
        id: helpPanel
        objectName: "helpPanel"
        anchors.centerIn: parent
        modal: true
        padding: 12
        contentItem: ColumnLayout {
            spacing: 6
            Label { text: qsTr("Schematic shortcuts"); font.bold: true }
            GridLayout {
                columns: 2
                columnSpacing: 16
                rowSpacing: 3
                Repeater {
                    model: root.shortcutList
                    delegate: Label {
                        required property var modelData
                        required property int index
                        objectName: "shortcutKey"
                        text: modelData[0]
                        font.bold: true
                        Layout.row: index
                        Layout.column: 0
                    }
                }
                Repeater {
                    model: root.shortcutList
                    delegate: Label {
                        required property var modelData
                        required property int index
                        text: modelData[1]
                        Layout.row: index
                        Layout.column: 1
                    }
                }
            }
            Button {
                Layout.alignment: Qt.AlignRight
                text: qsTr("Close")
                onClicked: helpPanel.close()
            }
        }
    }

    Popup {
        id: textPopup
        objectName: "textPopup"
        anchors.centerIn: parent
        modal: true
        padding: 12
        contentItem: ColumnLayout {
            spacing: 8
            Label {
                text: root.editTextIndex >= 0 ? qsTr("Edit text") : qsTr("SPICE directive, e.g. .tran 5m")
            }
            ScrollView {
                Layout.preferredWidth: 360
                Layout.preferredHeight: 110
                TextArea {
                    id: textArea
                    objectName: "textArea"
                    font.family: "Consolas"
                    selectByMouse: true
                    wrapMode: TextEdit.NoWrap
                }
            }
            CheckBox {
                id: commentBox
                objectName: "commentBox"
                text: qsTr("Comment (not simulated)")
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button {
                    text: qsTr("Cancel")
                    onClicked: textPopup.close()
                }
                Button {
                    objectName: "textOk"
                    text: qsTr("OK")
                    enabled: textArea.text.trim() !== ""
                    onClicked: root.textDone()
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
