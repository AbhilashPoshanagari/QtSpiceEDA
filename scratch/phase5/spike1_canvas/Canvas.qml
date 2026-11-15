import QtQuick
import QtQuick.Shapes

// S1 spike canvas. Pan/zoom follow SchematicCanvas.qml: everything is drawn inside
// `content` in pixels-at-zoom-1, and `content` is moved (panX, panY) and scaled (zoom)
// as one item, so panning and zooming never rebuild the drawing. Pen widths and SVG
// raster sizes follow the zoom only once it settles (80 ms).
//
// variant "a": one Item per glyph via Repeater, branches included (a rotated Rectangle
//              each, plus an SVG Image for switches and transformers).
// variant "b": branches are four ShapePaths (PathSvg strings from the controller);
//              buses, shunts and branch symbols are Items via Repeater.
//
// Mouse: wheel zooms around the pointer; middle- or right-drag pans; left click
// selects (Ctrl toggles); a left drag on a bus moves it (live, no undo in the spike);
// a left drag on empty space is a rubber band. Hit-testing is in Python (scene.hitTest).
Window {
    id: win
    width: 1600
    height: 900
    visible: true
    color: "#fbfbf7"
    title: "S1 canvas spike - variant " + variant

    required property var scene
    required property string variant
    property bool curveRenderer: false
    property string info: ""

    property real zoom: 1.0
    property real panX: 0
    property real panY: 0
    readonly property real minZoom: 0.02
    readonly property real maxZoom: 16.0
    readonly property int grid: 16
    property real settledZoom: 1.0
    readonly property real penWidth: 1.5 / settledZoom
    readonly property real pickTolerance: 6   // pixels at zoom 1 is scaled below
    readonly property real symPx: 20          // glyph size in pixels at zoom 1
    // Raster size of the SVG glyphs: the next power of two of their size on screen.
    readonly property int raster: {
        const want = symPx * settledZoom * Screen.devicePixelRatio
        let s = 16
        while (s < want && s < 512)
            s *= 2
        return s
    }

    property bool banding: false
    property point bandStart
    property point bandEnd
    property int dragBus: -1

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
        const b = scene.bounds()
        const margin = 32
        const w = b[2] - b[0] + 2 * margin
        const h = b[3] - b[1] + 2 * margin
        zoom = Math.max(minZoom, Math.min(maxZoom, Math.min(width / w, height / h)))
        panX = (width - (b[2] - b[0]) * zoom) / 2 - b[0] * zoom
        panY = (height - (b[3] - b[1]) * zoom) / 2 - b[1] * zoom
        settledZoom = zoom
    }

    onZoomChanged: settleTimer.restart()
    Timer {
        id: settleTimer
        interval: 80
        onTriggered: win.settledZoom = win.zoom
    }

    function symbolSource(kind, open) {
        if (kind === "switch")
            return open ? "symbols/switch_open.svg" : "symbols/switch.svg"
        return "symbols/" + kind + ".svg"
    }

    // Grid lines, as in SchematicCanvas.qml (viewport pixels, they only move).
    Item {
        id: gridLayer
        anchors.fill: parent
        readonly property real step: win.grid * win.zoom >= 8 ? win.grid * win.zoom
                                                              : win.grid * 4 * win.zoom
        readonly property real offX: ((win.panX % step) + step) % step
        readonly property real offY: ((win.panY % step) + step) % step
        visible: step >= 8
        Repeater {
            model: Math.ceil(win.width / 8) + 2
            Rectangle {
                required property int index
                x: gridLayer.offX + index * gridLayer.step
                visible: x <= win.width
                width: 1
                height: win.height
                color: "#ecece4"
            }
        }
        Repeater {
            model: Math.ceil(win.height / 8) + 2
            Rectangle {
                required property int index
                y: gridLayer.offY + index * gridLayer.step
                visible: y <= win.height
                height: 1
                width: win.width
                color: "#ecece4"
            }
        }
    }

    Item {
        id: content
        objectName: "content"
        x: win.panX
        y: win.panY
        scale: win.zoom
        transformOrigin: Item.TopLeft

        // ---- variant (b): branches as ShapePaths
        Loader {
            active: win.variant === "b"
            sourceComponent: Shape {
                objectName: "branchShape"
                preferredRendererType: win.curveRenderer ? Shape.CurveRenderer
                                                         : Shape.GeometryRenderer
                ShapePath {
                    strokeColor: "#202020"
                    strokeWidth: win.penWidth
                    fillColor: "transparent"
                    PathSvg { path: win.scene.linePath }
                }
                ShapePath {
                    strokeColor: "#37474f"
                    strokeWidth: win.penWidth
                    fillColor: "transparent"
                    PathSvg { path: win.scene.switchPath }
                }
                ShapePath {
                    strokeColor: "#c0392b"
                    strokeWidth: win.penWidth
                    strokeStyle: ShapePath.DashLine
                    fillColor: "transparent"
                    PathSvg { path: win.scene.openPath }
                }
                ShapePath {
                    strokeColor: "#8b4513"
                    strokeWidth: win.penWidth
                    fillColor: "transparent"
                    PathSvg { path: win.scene.transformerPath }
                }
                ShapePath {
                    strokeColor: "#d35400"
                    strokeWidth: win.penWidth * 2
                    fillColor: "transparent"
                    PathSvg { path: win.scene.selectedPath }
                }
            }
        }
        Repeater {
            objectName: "symbolRepeater"
            model: win.variant === "b" ? win.scene.symbolModel : null
            delegate: Item {
                required property real mx
                required property real my
                required property real angle
                required property string kind
                required property bool open
                required property bool selected
                x: mx
                y: my
                rotation: angle
                Rectangle {
                    visible: parent.selected
                    x: -win.symPx / 2 - 2; y: -win.symPx / 2 - 2
                    width: win.symPx + 4; height: win.symPx + 4
                    color: "transparent"; border.color: "#d35400"; border.width: 2
                }
                Image {
                    x: -win.symPx / 2; y: -win.symPx / 2
                    width: win.symPx; height: win.symPx
                    sourceSize: Qt.size(win.raster, win.raster)
                    source: win.symbolSource(parent.kind, parent.open)
                }
            }
        }

        // ---- variant (a): one Item per branch
        Repeater {
            objectName: "branchRepeater"
            model: win.variant === "a" ? win.scene.branchModel : null
            delegate: Item {
                id: br
                required property real x1
                required property real y1
                required property real x2
                required property real y2
                required property string kind
                required property bool open
                required property bool selected
                readonly property real len: Math.hypot(x2 - x1, y2 - y1)
                readonly property real angle: Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI
                x: x1
                y: y1
                Rectangle {
                    width: br.len
                    height: br.selected ? win.penWidth * 2 : win.penWidth
                    y: -height / 2
                    transformOrigin: Item.Left
                    rotation: br.angle
                    color: br.selected ? "#d35400"
                           : br.kind === "switch" ? (br.open ? "#c0392b" : "#37474f")
                           : br.kind === "transformer" ? "#8b4513" : "#202020"
                }
                Item {
                    visible: br.kind !== "line"
                    x: (br.x2 - br.x1) / 2
                    y: (br.y2 - br.y1) / 2
                    rotation: br.angle
                    Image {
                        x: -win.symPx / 2; y: -win.symPx / 2
                        width: win.symPx; height: win.symPx
                        sourceSize: Qt.size(win.raster, win.raster)
                        source: br.kind === "line" ? "" : win.symbolSource(br.kind, br.open)
                    }
                }
            }
        }

        // ---- both variants: buses and shunts are Items
        Repeater {
            objectName: "shuntRepeater"
            model: win.scene.shuntModel
            delegate: Item {
                id: sh
                required property real px
                required property real py
                required property real by
                required property string kind
                required property bool selected
                x: px
                y: py
                Rectangle {   // stub from the bus
                    x: -width / 2
                    y: sh.by - sh.py
                    width: win.penWidth
                    height: sh.py - sh.by - win.symPx / 2
                    color: "#202020"
                }
                Rectangle {
                    visible: sh.selected
                    x: -win.symPx / 2 - 2; y: -win.symPx / 2 - 2
                    width: win.symPx + 4; height: win.symPx + 4
                    color: "transparent"; border.color: "#d35400"; border.width: 2
                }
                Image {
                    x: -win.symPx / 2; y: -win.symPx / 2
                    width: win.symPx; height: win.symPx
                    sourceSize: Qt.size(win.raster, win.raster)
                    source: win.symbolSource(sh.kind, false)
                }
            }
        }
        Repeater {
            objectName: "busRepeater"
            model: win.scene.busModel
            delegate: Item {
                id: bus
                required property real px
                required property real py
                required property string name
                required property bool selected
                x: px
                y: py
                Rectangle {
                    x: -16; y: -2; width: 32; height: 4
                    color: bus.selected ? "#d35400" : "#1c3d8c"
                }
                Text {
                    x: 18; y: -14
                    text: bus.name
                    font.pixelSize: 10
                    color: bus.selected ? "#d35400" : "#303030"
                }
            }
        }
    }

    Rectangle {
        visible: win.banding
        x: Math.min(win.bandStart.x, win.bandEnd.x)
        y: Math.min(win.bandStart.y, win.bandEnd.y)
        width: Math.abs(win.bandEnd.x - win.bandStart.x)
        height: Math.abs(win.bandEnd.y - win.bandStart.y)
        color: "#201c3d8c"
        border.color: "#1c3d8c"
    }

    Text {
        x: 8; y: 6
        text: "variant " + win.variant + "   zoom " + Math.round(win.zoom * 100) + " %   " + win.info
        color: "#606060"
        font.pixelSize: 12
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
        property bool panning: false
        property real lastX
        property real lastY

        onPressed: ev => {
            lastX = ev.x
            lastY = ev.y
            if (ev.button !== Qt.LeftButton) {
                panning = true
                return
            }
            const p = win.toSheet(ev.x, ev.y)
            const add = (ev.modifiers & Qt.ControlModifier) !== 0
            const hit = win.scene.clickAt(p.x, p.y, win.pickTolerance / win.zoom, add)
            if (hit.kind === "bus" && !add) {
                win.dragBus = hit.index
            } else if (hit.kind === undefined) {
                win.bandStart = Qt.point(ev.x, ev.y)
                win.bandEnd = win.bandStart
                win.banding = true
            }
        }
        onPositionChanged: ev => {
            if (panning) {
                win.panX += ev.x - lastX
                win.panY += ev.y - lastY
                lastX = ev.x
                lastY = ev.y
            } else if (win.dragBus >= 0) {
                const p = win.toSheet(ev.x, ev.y)
                win.scene.moveBusTo(win.dragBus, p.x, p.y)
            } else if (win.banding) {
                win.bandEnd = Qt.point(ev.x, ev.y)
            }
        }
        onReleased: ev => {
            panning = false
            win.dragBus = -1
            if (win.banding) {
                win.banding = false
                const a = win.toSheet(win.bandStart.x, win.bandStart.y)
                const b = win.toSheet(win.bandEnd.x, win.bandEnd.y)
                if (Math.abs(win.bandEnd.x - win.bandStart.x) > 3
                        || Math.abs(win.bandEnd.y - win.bandStart.y) > 3)
                    win.scene.selectRect(Math.min(a.x, b.x), Math.min(a.y, b.y),
                                         Math.max(a.x, b.x), Math.max(a.y, b.y),
                                         (ev.modifiers & Qt.ControlModifier) !== 0)
            }
        }
        onWheel: ev => {
            win.zoomAt(ev.x, ev.y, Math.pow(1.25, ev.angleDelta.y / 120))
            ev.accepted = true
        }
    }

    Component.onCompleted: zoomToFit()
}
