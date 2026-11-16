import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes
import QtSpice

// Grid Network Designer canvas (D6). Everything is drawn inside `content`, in pixels
// at zoom 1 (one diagram unit = `unit` pixels), and `content` is moved (panX, panY)
// and scaled (zoom) as one item, so panning and zooming never rebuild the drawing
// (S1, as SchematicCanvas). Branches and stubs are ShapePaths fed by the controller's
// path strings (GeometryRenderer; the window has 4x MSAA); buses and element symbols
// are Items from the controller's glyph models. Pen widths and SVG raster sizes follow
// the zoom once it settles (80 ms).
//
// Mouse: wheel zooms around the pointer; middle drag, right drag or Space + drag pans;
// a click selects (Ctrl toggles); dragging a selected glyph moves the selection
// (snapped to whole units unless Snap is off; one undo step per drag); a drag on
// empty space is a rubber band; double-click a switch to open or close it.
// Tools: the branch tools (Line, Switch, Transformer, Regulator): click bus A, then
// bus B. A library item is dragged onto the canvas (a bus) or onto a bus (source,
// load, capacitor, PV), or clicked and then placed with one click.
// Keys (canvas focused): R rotate, Del delete, F2 rename, Esc leave the tool,
// Space pan. Undo / Redo are in the Edit menu (Ctrl+Z / Ctrl+Y).
//
// Results (D8): GridResultsOverlay is drawn over the canvas after a run (bus voltage
// and branch loading colours, legend, tooltip, toggle); after any change it is hatched
// with a "Run again" banner. focusOn(id) selects and centres a bus or element (the
// feasibility panel's rows).
//
// Only view state lives here (zoom, pan, tool, the live rubber band); every action
// calls exactly one GridDiagramController slot (rule 22). Hit-testing is in
// core/grid/diagram/hittest.py through the controller.
Item {
    id: root
    objectName: "gridDiagramEditor"

    required property var designer
    signal newDesignRequested()
    signal openDssRequested()
    signal consoleRequested()

    readonly property real unit: 20          // pixels per diagram unit at zoom 1
    property real zoom: 1.0
    property real panX: 40
    property real panY: 40
    readonly property real minZoom: 0.02
    readonly property real maxZoom: 16.0
    property real settledZoom: 1.0
    readonly property real penWidth: 1.5 / settledZoom          // content pixels
    readonly property real pickTolerance: 6 / (zoom * unit)    // diagram units (S1)
    readonly property int raster: {
        const want = unit * settledZoom * Screen.devicePixelRatio
        let s = 16
        while (s < want && s < 512)
            s *= 2
        return s
    }
    property bool snap: true
    property bool labelsOn: true
    readonly property bool labelsShown: labelsOn && settledZoom * 10 >= 5

    // "select", "branch" (branchKind from bus branchFrom), "place" (placeKind)
    property string tool: "select"
    property string branchKind: ""
    property string branchFrom: ""
    property point branchFromPoint              // where bus A was clicked (preview)
    property string placeKind: ""
    property point mouseSheet: Qt.point(0, 0)   // diagram units, for the branch preview
    property bool spaceDown: false
    property bool banding: false
    property point bandStart
    property point bandEnd
    property int gesture: -1                    // a drag of the selection in progress
    property point dragStart                    // diagram units
    property point dragApplied: Qt.point(0, 0)  // offset already sent

    readonly property var branchLabels: ({ line: qsTr("Line"), switch: qsTr("Switch"),
                                           transformer: qsTr("Transformer"),
                                           regulator: qsTr("Regulator") })

    function toSheet(vx, vy) {
        return Qt.point((vx - panX) / (zoom * unit), (vy - panY) / (zoom * unit))
    }
    function snapped(v) { return snap ? Math.round(v) : v }
    function zoomAt(vx, vy, factor) {
        const c = Qt.point((vx - panX) / zoom, (vy - panY) / zoom)
        zoom = Math.max(minZoom, Math.min(maxZoom, zoom * factor))
        panX = vx - c.x * zoom
        panY = vy - c.y * zoom
    }
    function zoomBy(factor) { zoomAt(canvas.width / 2, canvas.height / 2, factor) }
    function fit() {
        const b = designer.contentBounds()
        if (b.length !== 4 || canvas.width <= 0 || canvas.height <= 0) {
            zoom = 1; panX = 40; panY = 40; settledZoom = 1
            return
        }
        const margin = 30
        const w = Math.max((b[2] - b[0]) * unit, 1), h = Math.max((b[3] - b[1]) * unit, 1)
        zoom = Math.max(minZoom, Math.min(2.0, Math.min((canvas.width - 2 * margin) / w,
                                                         (canvas.height - 2 * margin) / h)))
        panX = (canvas.width - w * zoom) / 2 - b[0] * unit * zoom
        panY = (canvas.height - h * zoom) / 2 - b[1] * unit * zoom
        settledZoom = zoom
    }
    // Centre the view on (x, y) in diagram units, keeping the zoom.
    function centreOn(x, y) {
        panX = canvas.width / 2 - x * unit * zoom
        panY = canvas.height / 2 - y * unit * zoom
    }
    // Select a bus or element and centre on it (GridDiagramController.focusElement).
    function focusOn(elementId) {
        const p = designer.focusElement(elementId)
        if (p.length === 2)
            centreOn(p[0], p[1])
    }
    function startBranch(kind) {
        tool = "branch"; branchKind = kind; branchFrom = ""; placeKind = ""
        canvas.forceActiveFocus()
    }
    function startPlace(kind) {
        if (kind === "line" || kind === "switch" || kind === "transformer" || kind === "regulator") {
            startBranch(kind)
            return
        }
        if (kind === "linecode") {      // no glyph: add it at once
            designer.dropItem(kind, NaN, NaN, 0)
            return
        }
        tool = "place"; placeKind = kind; branchFrom = ""
        canvas.forceActiveFocus()
    }
    // Add through dropItem / drawBranch, asking first for the values a new element
    // cannot be added without (rule 24: typed, never defaulted).
    function drop(kind, x, y) {
        const shunt = kind === "load" || kind === "capacitor" || kind === "pvsystem" || kind === "vsource"
        const bus = shunt ? designer.busAt(x, y, pickTolerance * 2) : ""
        const need = designer.requiredValues(kind, bus)
        if (need.length > 0 && (!shunt || bus !== ""))
            valuesPopup.ask(need, { op: "drop", kind: kind, x: x, y: y })
        else
            designer.dropItem(kind, x, y, pickTolerance * 2)
    }
    function branch(kind, a, b) {
        const need = designer.requiredValues(kind, "")
        if (need.length > 0)
            valuesPopup.ask(need, { op: "branch", kind: kind, a: a, b: b })
        else
            designer.drawBranch(kind, a, b)
    }
    function stopTool() {
        tool = "select"; branchKind = ""; branchFrom = ""; placeKind = ""
    }
    function symbolSource(kind, open, regulated) {
        if (kind === "switch")
            return open ? "symbols/switch_open.svg" : "symbols/switch.svg"
        if (kind === "transformer" && regulated)
            return "symbols/regulator.svg"
        return "symbols/" + kind + ".svg"
    }
    function severityColor(sev) {
        return sev === "error" ? "#d73a49" : sev === "warning" ? "#d4a72c" : "#0969da"
    }
    function openRename() {
        if (designer.selection.length !== 1)
            return
        renamePopup.targetId = designer.selection[0]
        renameField.text = renamePopup.targetId.split(".").slice(1).join(".")
        renamePopup.open()
        renameField.selectAll()
        renameField.forceActiveFocus()
    }

    onZoomChanged: settleTimer.restart()
    Timer {
        id: settleTimer
        interval: 80
        onTriggered: root.settledZoom = root.zoom
    }

    Connections {
        target: root.designer
        function onDesignChanged() { Qt.callLater(root.fit) }
        function onDesignOpened() { Qt.callLater(root.fit) }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---------------------------------------------------------------- tool bar
        ToolBar {
            Layout.fillWidth: true
            enabled: root.designer.hasDesign
            RowLayout {
                anchors.fill: parent
                spacing: 2
                ToolButton {
                    objectName: "designerSelectTool"
                    text: qsTr("Select")
                    checkable: true
                    checked: root.tool === "select"
                    onClicked: root.stopTool()
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Select, move and rubber-band (Esc)")
                }
                Repeater {
                    model: ["line", "switch", "transformer", "regulator"]
                    delegate: ToolButton {
                        required property string modelData
                        objectName: "designerTool_" + modelData
                        text: root.branchLabels[modelData]
                        icon.source: "symbols/" + modelData + ".svg"
                        icon.color: "transparent"
                        display: AbstractButton.TextBesideIcon
                        checkable: true
                        checked: root.tool === "branch" && root.branchKind === modelData
                        onClicked: root.startBranch(modelData)
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("Draw a %1: click bus A, then bus B")
                                      .arg(root.branchLabels[modelData].toLowerCase())
                    }
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "designerRotate"
                    text: qsTr("Rotate")
                    enabled: root.designer.selection.length > 0
                    onClicked: root.designer.rotateSelection()
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Rotate the selection a quarter turn (R)")
                }
                ToolButton {
                    objectName: "designerDelete"
                    text: qsTr("Delete")
                    enabled: root.designer.selection.length > 0
                    onClicked: root.designer.deleteSelection()
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Delete the selection (Del); a bus takes what is on it")
                }
                ToolSeparator {}
                ToolButton {
                    objectName: "designerFit"
                    text: qsTr("Fit")
                    onClicked: root.fit()
                }
                ToolButton { text: "−"; onClicked: root.zoomBy(1 / 1.25) }
                Label {
                    objectName: "designerZoom"
                    text: Math.round(root.zoom * 100) + " %"
                    Layout.preferredWidth: 48
                    horizontalAlignment: Text.AlignHCenter
                }
                ToolButton { text: "+"; onClicked: root.zoomBy(1.25) }
                ToolButton {
                    objectName: "designerSnap"
                    text: qsTr("Snap")
                    checkable: true
                    checked: root.snap
                    onToggled: root.snap = checked
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Snap moved and placed glyphs to the grid")
                }
                ToolButton {
                    objectName: "designerLabels"
                    text: qsTr("Labels")
                    checkable: true
                    checked: root.labelsOn
                    onToggled: root.labelsOn = checked
                }
                Item { Layout.fillWidth: true }
                // a disabled button gets no hover: the wrapper shows why Run is off
                Item {
                    implicitWidth: runButton.implicitWidth
                    implicitHeight: runButton.implicitHeight
                    ToolButton {
                        id: runButton
                        objectName: "designerRun"
                        anchors.fill: parent
                        text: qsTr("Run Power Flow")
                        enabled: root.designer.canRun
                        onClicked: { root.designer.runPowerFlow(); root.consoleRequested() }
                    }
                    HoverHandler { id: runHover }
                    ToolTip.visible: runHover.hovered
                    ToolTip.delay: 400
                    ToolTip.text: root.designer.canRun
                                  ? qsTr("Solve this design with OpenDSS")
                                  : root.designer.runDisabledReason
                }
            }
        }

        // ---------------------------------------------------------------- canvas
        Item {
            id: canvas
            objectName: "designerCanvas"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            focus: true
            activeFocusOnTab: true

            Rectangle { anchors.fill: parent; color: "#fbfbf7" }

            // Grid lines in viewport pixels; they only move (as SchematicCanvas).
            Item {
                id: gridLayer
                objectName: "designerGridLayer"
                anchors.fill: parent
                readonly property real step: root.unit * root.zoom >= 8
                                             ? root.unit * root.zoom : root.unit * 5 * root.zoom
                readonly property real offX: ((root.panX % step) + step) % step
                readonly property real offY: ((root.panY % step) + step) % step
                visible: root.designer.hasDesign && step >= 8
                Repeater {
                    model: Math.ceil(canvas.width / 8) + 2
                    Rectangle {
                        required property int index
                        x: gridLayer.offX + index * gridLayer.step
                        visible: x <= canvas.width
                        width: 1
                        height: canvas.height
                        color: "#ecece4"
                    }
                }
                Repeater {
                    model: Math.ceil(canvas.height / 8) + 2
                    Rectangle {
                        required property int index
                        y: gridLayer.offY + index * gridLayer.step
                        visible: y <= canvas.height
                        height: 1
                        width: canvas.width
                        color: "#ecece4"
                    }
                }
            }

            Item {
                id: content
                objectName: "designerContent"
                x: root.panX
                y: root.panY
                scale: root.zoom
                transformOrigin: Item.TopLeft

                // Branches and stubs: diagram units, scaled to content pixels.
                Shape {
                    id: branchShape
                    objectName: "designerBranches"
                    scale: root.unit
                    transformOrigin: Item.TopLeft
                    preferredRendererType: Shape.GeometryRenderer
                    readonly property real w: root.penWidth / root.unit
                    readonly property var paths: root.designer.edgePaths
                    ShapePath {
                        strokeColor: "#9aa0a6"; strokeWidth: branchShape.w; fillColor: "transparent"
                        PathSvg { path: branchShape.paths.stub || "" }
                    }
                    ShapePath {
                        strokeColor: "#202020"; strokeWidth: branchShape.w; fillColor: "transparent"
                        PathSvg { path: branchShape.paths.line || "" }
                    }
                    ShapePath {
                        strokeColor: "#37474f"; strokeWidth: branchShape.w; fillColor: "transparent"
                        PathSvg { path: branchShape.paths["switch"] || "" }
                    }
                    ShapePath {
                        strokeColor: "#c0392b"; strokeWidth: branchShape.w; fillColor: "transparent"
                        strokeStyle: ShapePath.DashLine
                        dashPattern: [4, 3]
                        PathSvg { path: branchShape.paths.open || "" }
                    }
                    ShapePath {
                        strokeColor: "#8b4513"; strokeWidth: branchShape.w; fillColor: "transparent"
                        PathSvg { path: branchShape.paths.transformer || "" }
                    }
                    ShapePath {
                        strokeColor: "#d35400"; strokeWidth: branchShape.w * 2.2; fillColor: "transparent"
                        PathSvg { path: branchShape.paths.selected || "" }
                    }
                }

                // Branch tool preview: from bus A to the pointer (view state only).
                Shape {
                    visible: root.tool === "branch" && root.branchFrom !== ""
                    scale: root.unit
                    transformOrigin: Item.TopLeft
                    preferredRendererType: Shape.GeometryRenderer
                    ShapePath {
                        id: previewPath
                        strokeColor: "#1c5fb8"
                        strokeWidth: root.penWidth * 1.5 / root.unit
                        strokeStyle: ShapePath.DashLine
                        fillColor: "transparent"
                        startX: root.branchFromPoint.x
                        startY: root.branchFromPoint.y
                        PathLine { x: root.mouseSheet.x; y: root.mouseSheet.y }
                    }
                }

                // Buses: a bar `length` units long, horizontal or vertical.
                Repeater {
                    objectName: "designerBusRepeater"
                    model: root.designer.busGlyphs
                    delegate: Item {
                        id: bus
                        // Item's x/y are FINAL: the model's x/y (diagram units) via `model`
                        required property var model
                        readonly property string name: model.name
                        readonly property real length: model.length
                        readonly property bool selected: model.selected
                        readonly property string severity: model.severity
                        readonly property bool vertical: model.orientation === 90
                        readonly property bool from: root.tool === "branch" && root.branchFrom === name
                        objectName: "designerBus_" + name
                        x: model.x * root.unit
                        y: model.y * root.unit
                        Rectangle {
                            readonly property real len: bus.length * root.unit
                            readonly property real thick: Math.max(3, root.penWidth * 2.5)
                            x: bus.vertical ? -thick / 2 : -len / 2
                            y: bus.vertical ? -len / 2 : -thick / 2
                            width: bus.vertical ? thick : len
                            height: bus.vertical ? len : thick
                            radius: thick / 3
                            color: bus.selected || bus.from ? "#d35400"
                                   : bus.severity === "error" ? "#d73a49" : "#1c3d8c"
                        }
                        Text {
                            visible: root.labelsShown
                            x: bus.vertical ? 6 : bus.length * root.unit / 2 + 3
                            y: bus.vertical ? -bus.length * root.unit / 2 - 14 : -13
                            text: bus.name
                            font.pixelSize: 10
                            color: bus.selected ? "#d35400" : "#303030"
                        }
                        Rectangle {    // diagnostic badge
                            visible: bus.severity === "error" || bus.severity === "warning"
                            x: bus.vertical ? 4 : -bus.length * root.unit / 2 - 12
                            y: bus.vertical ? -bus.length * root.unit / 2 - 10 : -12
                            width: 10; height: 10; radius: 5
                            color: root.severityColor(bus.severity)
                            Text { anchors.centerIn: parent; text: "!"; color: "white"; font.pixelSize: 8; font.bold: true }
                        }
                    }
                }

                // Element symbols (a line only has its label and badge).
                Repeater {
                    objectName: "designerElementRepeater"
                    model: root.designer.elementGlyphs
                    delegate: Item {
                        id: el
                        required property var model
                        readonly property string kind: model.kind
                        readonly property string name: model.name
                        readonly property int turn: model.rotation
                        readonly property real labelDx: model.labelDx
                        readonly property real labelDy: model.labelDy
                        readonly property bool open: model.open
                        readonly property bool regulated: model.regulated
                        readonly property bool selected: model.selected
                        readonly property string severity: model.severity
                        readonly property bool shunt: kind === "load" || kind === "capacitor"
                                                      || kind === "pvsystem" || kind === "vsource"
                        readonly property real size: root.unit
                        objectName: "designerElement_" + model.glyphId
                        x: model.x * root.unit
                        y: model.y * root.unit
                        Item {
                            // 0 = lead up (shunt, bus above) / along x (branch);
                            // shunts turn the other way so 90 puts the bus on the left
                            rotation: el.shunt ? (360 - el.turn) % 360 : el.turn
                            Rectangle {
                                visible: el.selected
                                x: -el.size / 2 - 2; y: -el.size / 2 - 2
                                width: el.size + 4; height: el.size + 4
                                color: "transparent"; border.color: "#d35400"; border.width: 1.5
                            }
                            Image {
                                visible: el.kind !== "line"
                                x: -el.size / 2; y: -el.size / 2
                                width: el.size; height: el.size
                                asynchronous: true
                                sourceSize: Qt.size(root.raster, root.raster)
                                source: el.kind === "line" ? ""
                                        : root.symbolSource(el.kind, el.open, el.regulated)
                            }
                        }
                        Text {
                            visible: root.labelsShown
                            x: el.labelDx * root.unit + (el.kind === "line" ? -implicitWidth / 2 : el.size / 2 + 2)
                            y: el.labelDy * root.unit + (el.kind === "line" ? -implicitHeight / 2 : -4)
                            text: el.name
                            font.pixelSize: 9
                            color: el.selected ? "#d35400" : el.kind === "line" ? "#555555" : "#303030"
                        }
                        Rectangle {    // diagnostic badge
                            visible: el.severity === "error" || el.severity === "warning"
                            x: el.size / 2 - 4; y: -el.size / 2 - 6
                            width: 10; height: 10; radius: 5
                            color: root.severityColor(el.severity)
                            Text { anchors.centerIn: parent; text: "!"; color: "white"; font.pixelSize: 8; font.bold: true }
                        }
                    }
                }
            }

            // Rubber band, in viewport pixels.
            Rectangle {
                visible: root.banding
                x: Math.min(root.bandStart.x, root.bandEnd.x)
                y: Math.min(root.bandStart.y, root.bandEnd.y)
                width: Math.abs(root.bandEnd.x - root.bandStart.x)
                height: Math.abs(root.bandEnd.y - root.bandStart.y)
                color: "#201c3d8c"
                border.color: "#1c3d8c"
            }

            // Tool hint.
            Label {
                visible: root.tool !== "select"
                x: 8; y: 6
                padding: 4
                background: Rectangle { color: "#e8f0fe"; radius: 3 }
                text: root.tool === "place"
                      ? qsTr("Click to place: %1 (on a bus for a shunt). Esc ends.").arg(root.placeKind)
                      : root.branchFrom === ""
                        ? qsTr("%1: click bus A. Esc ends.").arg(root.branchLabels[root.branchKind])
                        : qsTr("%1 from %2: click bus B. Esc ends.").arg(root.branchLabels[root.branchKind]).arg(root.branchFrom)
            }

            // Nothing to edit yet.
            ColumnLayout {
                anchors.centerIn: parent
                visible: !root.designer.hasDesign
                spacing: 8
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: qsTr("No grid design open")
                    font.pointSize: 12
                    color: palette.placeholderText
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    Button {
                        objectName: "designerNewButton"
                        text: qsTr("New Grid Design…")
                        onClicked: root.newDesignRequested()
                    }
                    Button {
                        text: qsTr("Open .dss as Design…")
                        onClicked: root.openDssRequested()
                    }
                }
            }

            DropArea {
                objectName: "designerDropArea"
                anchors.fill: parent
                keys: ["qtspice-grid-item"]
                onDropped: drop => {
                    const kind = drop.source ? drop.source.kind : ""
                    if (!kind)
                        return
                    drop.accept()
                    if (root.branchLabels[kind] !== undefined) {
                        root.startBranch(kind)
                        return
                    }
                    const p = root.toSheet(drop.x, drop.y)
                    root.drop(kind, root.snapped(p.x), root.snapped(p.y))
                }
            }

            Keys.onPressed: ev => {
                if (ev.key === Qt.Key_Space && !ev.isAutoRepeat) {
                    root.spaceDown = true
                } else if (ev.key === Qt.Key_R && ev.modifiers === Qt.NoModifier) {
                    root.designer.rotateSelection()
                } else if (ev.key === Qt.Key_Delete || ev.key === Qt.Key_Backspace) {
                    root.designer.deleteSelection()
                } else if (ev.key === Qt.Key_F2) {
                    root.openRename()
                } else if (ev.key === Qt.Key_Escape) {
                    if (root.tool === "branch" && root.branchFrom !== "")
                        root.branchFrom = ""
                    else if (root.tool !== "select")
                        root.stopTool()
                    else
                        root.designer.clearSelection()
                } else {
                    return
                }
                ev.accepted = true
            }
            Keys.onReleased: ev => {
                if (ev.key === Qt.Key_Space && !ev.isAutoRepeat) {
                    root.spaceDown = false
                    ev.accepted = true
                }
            }

            MouseArea {
                id: mouse
                objectName: "designerMouse"
                anchors.fill: parent
                enabled: root.designer.hasDesign
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
                hoverEnabled: true
                cursorShape: panning || root.spaceDown ? Qt.ClosedHandCursor
                             : root.tool !== "select" ? Qt.CrossCursor : Qt.ArrowCursor
                property bool panning: false
                property bool moved: false
                property real lastX
                property real lastY

                onPressed: ev => {
                    canvas.forceActiveFocus()
                    lastX = ev.x; lastY = ev.y; moved = false
                    if (ev.button !== Qt.LeftButton || root.spaceDown) {
                        panning = true
                        return
                    }
                    const p = root.toSheet(ev.x, ev.y)
                    const tol = root.pickTolerance
                    if (root.tool === "branch") {
                        const bus = root.designer.busAt(p.x, p.y, tol * 2)
                        if (bus === "")
                            return
                        if (root.branchFrom === "") {
                            root.branchFrom = bus
                            root.branchFromPoint = p
                            root.mouseSheet = p
                        } else if (bus !== root.branchFrom) {
                            root.branch(root.branchKind, root.branchFrom, bus)
                            root.branchFrom = ""
                        }
                        return
                    }
                    if (root.tool === "place") {
                        root.drop(root.placeKind, root.snapped(p.x), root.snapped(p.y))
                        if (!(ev.modifiers & Qt.ShiftModifier))
                            root.stopTool()
                        return
                    }
                    const add = (ev.modifiers & Qt.ControlModifier) !== 0
                    const hit = root.designer.clickAt(p.x, p.y, tol, add)
                    if (hit !== "" && !add) {
                        root.gesture = root.designer.newGesture()
                        root.dragStart = p
                        root.dragApplied = Qt.point(0, 0)
                    } else if (hit === "") {
                        root.bandStart = Qt.point(ev.x, ev.y)
                        root.bandEnd = root.bandStart
                        root.banding = true
                    }
                }
                onPositionChanged: ev => {
                    if (Math.abs(ev.x - lastX) + Math.abs(ev.y - lastY) > 2)
                        moved = true
                    if (panning) {
                        root.panX += ev.x - lastX
                        root.panY += ev.y - lastY
                        lastX = ev.x; lastY = ev.y
                        return
                    }
                    const p = root.toSheet(ev.x, ev.y)
                    root.mouseSheet = p
                    if (resultsOverlay.drawn && !pressed) {
                        resultsOverlay.hoverId = root.designer.glyphAt(p.x, p.y, root.pickTolerance)
                        resultsOverlay.hoverPoint = Qt.point(ev.x, ev.y)
                    } else {
                        resultsOverlay.hoverId = ""
                    }
                    if (root.gesture >= 0 && (ev.buttons & Qt.LeftButton)) {
                        const tx = root.snapped(p.x - root.dragStart.x)
                        const ty = root.snapped(p.y - root.dragStart.y)
                        const dx = tx - root.dragApplied.x, dy = ty - root.dragApplied.y
                        if (dx !== 0 || dy !== 0) {
                            root.designer.moveSelection(dx, dy, root.gesture)
                            root.dragApplied = Qt.point(tx, ty)
                        }
                    } else if (root.banding) {
                        root.bandEnd = Qt.point(ev.x, ev.y)
                    }
                }
                onReleased: ev => {
                    if (panning) {
                        panning = false
                        // a right-click without a drag leaves the tool
                        if (ev.button === Qt.RightButton && !moved) {
                            if (root.branchFrom !== "")
                                root.branchFrom = ""
                            else
                                root.stopTool()
                        }
                        return
                    }
                    root.gesture = -1
                    if (root.banding) {
                        root.banding = false
                        if (moved) {
                            const a = root.toSheet(root.bandStart.x, root.bandStart.y)
                            const b = root.toSheet(root.bandEnd.x, root.bandEnd.y)
                            root.designer.selectRect(a.x, a.y, b.x, b.y,
                                                     (ev.modifiers & Qt.ControlModifier) !== 0)
                        }
                    }
                }
                onDoubleClicked: ev => {
                    if (root.tool !== "select" || ev.button !== Qt.LeftButton)
                        return
                    const p = root.toSheet(ev.x, ev.y)
                    const hit = root.designer.glyphAt(p.x, p.y, root.pickTolerance)
                    if (hit.startsWith("line."))
                        root.designer.toggleSwitch(hit)
                }
                onWheel: ev => {
                    root.zoomAt(ev.x, ev.y, Math.pow(1.25, ev.angleDelta.y / 120))
                    ev.accepted = true
                }
                onExited: resultsOverlay.hoverId = ""
            }

            // Results of the last run (D8), over everything; only its banner button
            // and legend toggle take the mouse.
            GridResultsOverlay {
                id: resultsOverlay
                anchors.fill: parent
                available: root.designer.hasResult
                stale: root.designer.resultsStale
                canRun: root.designer.canRun
                items: root.designer.overlay
                paths: root.designer.overlayPaths
                buses: root.designer.overlayBuses
                limits: root.designer.overlayLimits
                originX: root.panX
                originY: root.panY
                scaleX: root.zoom * root.unit
                scaleY: root.zoom * root.unit
                onRunRequested: root.designer.runPowerFlow()
            }
        }

        // ---------------------------------------------------------------- status bar
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusRow.implicitHeight + 6
            color: palette.window
            border.color: palette.mid
            RowLayout {
                id: statusRow
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: 12
                Label {
                    objectName: "designerCounts"
                    text: root.designer.hasDesign
                          ? qsTr("%1: %2 buses, %3 elements").arg(root.designer.designName)
                                .arg(root.designer.busCount).arg(root.designer.elementCount)
                            + (root.designer.modified ? " *" : "")
                          : qsTr("No design")
                }
                Label {
                    objectName: "designerErrors"
                    text: qsTr("%n error(s)", "", root.designer.errorCount)
                    color: root.designer.errorCount > 0 ? "#d73a49" : palette.windowText
                    font.bold: root.designer.errorCount > 0
                    TapHandler { onTapped: root.consoleRequested() }
                }
                Label {
                    objectName: "designerWarnings"
                    text: qsTr("%n warning(s)", "", root.designer.warningCount)
                    color: root.designer.warningCount > 0 ? "#9a6700" : palette.windowText
                    TapHandler { onTapped: root.consoleRequested() }
                }
                Label {
                    objectName: "designerStatus"
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: root.designer.statusText
                    color: text.startsWith("Not ") || text.startsWith("Failed") ? "#d73a49"
                                                                                : palette.placeholderText
                }
                Label {
                    text: root.designer.selection.length > 0
                          ? qsTr("%n selected", "", root.designer.selection.length) : ""
                }
            }
        }
    }

    // The values a new element needs before it can be added (requiredValues).
    Popup {
        id: valuesPopup
        objectName: "designerValuesPopup"
        property var fields: []
        property var pending: ({})
        property var texts: ({})
        function ask(fields, pending) {
            texts = {}
            this.fields = fields
            this.pending = pending
            open()
        }
        function finish() {
            const values = {}
            for (const f of fields)
                values[f.field] = texts[f.field] || ""
            const p = pending
            close()
            if (p.op === "drop")
                root.designer.dropItem(p.kind, p.x, p.y, root.pickTolerance * 2, values)
            else
                root.designer.drawBranch(p.kind, p.a, p.b, values)
        }
        anchors.centerIn: parent
        modal: true
        padding: 12
        onClosed: canvas.forceActiveFocus()
        ColumnLayout {
            Label {
                text: qsTr("New %1: type these values").arg(valuesPopup.pending.kind || "")
                font.bold: true
            }
            Label {
                text: qsTr("They have no default (nothing is invented).")
                color: palette.placeholderText
                font.pointSize: 8
            }
            GridLayout {
                columns: 3
                Repeater {
                    model: valuesPopup.fields
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 0
                        text: modelData.label
                    }
                }
                Repeater {
                    model: valuesPopup.fields
                    delegate: TextField {
                        required property var modelData
                        required property int index
                        objectName: "designerValue_" + modelData.field
                        Layout.row: index
                        Layout.column: 1
                        Layout.preferredWidth: 120
                        placeholderText: qsTr("required")
                        onTextChanged: valuesPopup.texts[modelData.field] = text
                        onAccepted: valuesPopup.finish()
                        Component.onCompleted: if (index === 0) forceActiveFocus()
                    }
                }
                Repeater {
                    model: valuesPopup.fields
                    delegate: Label {
                        required property var modelData
                        required property int index
                        Layout.row: index
                        Layout.column: 2
                        text: modelData.unit
                        color: palette.placeholderText
                    }
                }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button { text: qsTr("Cancel"); onClicked: valuesPopup.close() }
                Button {
                    objectName: "designerValuesOk"
                    text: qsTr("Add")
                    onClicked: valuesPopup.finish()
                }
            }
        }
    }

    // F2: rename the one selected bus or element.
    Popup {
        id: renamePopup
        objectName: "designerRenamePopup"
        property string targetId: ""
        anchors.centerIn: parent
        modal: true
        padding: 12
        onClosed: canvas.forceActiveFocus()
        ColumnLayout {
            Label { text: qsTr("Rename %1").arg(renamePopup.targetId) }
            TextField {
                id: renameField
                objectName: "designerRenameField"
                Layout.preferredWidth: 220
                validator: RegularExpressionValidator { regularExpression: /[A-Za-z0-9_]+/ }
                onAccepted: renameOk.clicked()
            }
            Label {
                text: qsTr("Lower-case letters, digits and _")
                color: palette.placeholderText
                font.pointSize: 8
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Button { text: qsTr("Cancel"); onClicked: renamePopup.close() }
                Button {
                    id: renameOk
                    text: qsTr("Rename")
                    enabled: renameField.text !== ""
                    onClicked: {
                        root.designer.rename(renamePopup.targetId, renameField.text.toLowerCase())
                        renamePopup.close()
                    }
                }
            }
        }
    }
}
