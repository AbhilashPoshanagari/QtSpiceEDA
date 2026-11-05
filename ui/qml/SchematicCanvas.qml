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

    // "select" here; the part, wire, label, ground and paste tools add their own.
    property string mode: "select"

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
                Label {
                    objectName: "zoomLabel"
                    text: Math.round(root.zoom * 100) + " %"
                    Layout.preferredWidth: 56
                }
                Item { Layout.fillWidth: true }
            }
        }

        Item {
            id: viewport
            objectName: "canvasViewport"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

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
                property real lastX
                property real lastY

                onPressed: ev => {
                    root.forceActiveFocus()
                    lastX = ev.x
                    lastY = ev.y
                    if (ev.button === Qt.MiddleButton || ev.button === Qt.RightButton) {
                        panning = true
                        return
                    }
                    if (root.mode !== "select")
                        return
                    const p = root.toSheet(ev.x, ev.y)
                    const add = (ev.modifiers & Qt.ControlModifier) !== 0
                    const hit = SchematicController.itemAt(p.x, p.y, root.pickTolerance)
                    if (hit.kind !== undefined) {
                        SchematicController.select(hit.kind, hit.index, add)
                    } else {
                        if (!add)
                            SchematicController.clearSelection()
                        root.bandStart = Qt.point(ev.x, ev.y)
                        root.bandEnd = root.bandStart
                        root.banding = true
                    }
                }
                onPositionChanged: ev => {
                    if (panning) {
                        root.panX += ev.x - lastX
                        root.panY += ev.y - lastY
                        lastX = ev.x
                        lastY = ev.y
                    } else if (root.banding) {
                        root.bandEnd = Qt.point(ev.x, ev.y)
                    }
                }
                onReleased: ev => {
                    if (panning) {
                        panning = false
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

    Component.onCompleted: Qt.callLater(zoomToFit)
    Connections {
        target: ProjectManager
        function onProjectLoaded() { Qt.callLater(root.zoomToFit) }
    }
}
