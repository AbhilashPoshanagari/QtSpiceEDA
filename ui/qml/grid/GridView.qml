import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Read-only single-line view of the open feeder (G6), drawn from the bus coordinates
// in GridController.geometry. Buses are coloured by their lowest phase voltage in pu,
// lines over their loading limit are drawn thick and red, open switches are drawn
// open. Click a bus to see its phase voltages; wheel to zoom, drag to pan, double-
// click to fit. Nothing here edits the feeder (drawing feeders is out of scope).
Item {
    id: root
    objectName: "gridView"
    clip: true

    readonly property var geo: GridController.geometry
    readonly property bool hasDrawing: geo.hasCoordinates === true
    property real zoom: 1.0
    property real panX: 0
    property real panY: 0
    readonly property real margin: 30

    // ---- feeder coordinates -> pixels (y up in the file, down on screen)
    readonly property real spanX: hasDrawing ? Math.max(geo.maxX - geo.minX, 1e-9) : 1
    readonly property real spanY: hasDrawing ? Math.max(geo.maxY - geo.minY, 1e-9) : 1
    readonly property real fitScale: {
        if (!hasDrawing)
            return 1
        const w = Math.max(width - 2 * margin, 10), h = Math.max(height - 2 * margin, 10)
        const flatX = geo.maxX === geo.minX, flatY = geo.maxY === geo.minY
        if (flatX && flatY)
            return 1
        return Math.min(flatX ? Infinity : w / spanX, flatY ? Infinity : h / spanY)
    }
    function px(x) {
        const cx = width / 2 - (geo.minX + geo.maxX) / 2 * fitScale
        return (cx + x * fitScale - width / 2) * zoom + width / 2 + panX
    }
    function py(y) {
        const cy = height / 2 + (geo.minY + geo.maxY) / 2 * fitScale
        return (cy - y * fitScale - height / 2) * zoom + height / 2 + panY
    }
    // Pixel position of a bus ({x, y}, or null); for tests and the voltage panel.
    function busPoint(name) {
        if (!hasDrawing)
            return null
        for (const b of geo.buses)
            if (b.name === name)
                return Qt.point(px(b.x), py(b.y))
        return null
    }
    function busAt(x, y) {
        let best = "", bestD = 12 * 12
        for (const b of geo.buses) {
            const dx = px(b.x) - x, dy = py(b.y) - y
            const d = dx * dx + dy * dy
            if (d <= bestD) { bestD = d; best = b.name }
        }
        return best
    }
    function busColor(pu) {
        if (pu === undefined || isNaN(pu))
            return "#9e9e9e"  // no result yet
        if (pu < GridController.voltageMinPu)
            return "#d73a49"
        if (pu > GridController.voltageMaxPu)
            return "#8250df"
        const near = 0.01
        if (pu < GridController.voltageMinPu + near || pu > GridController.voltageMaxPu - near)
            return "#d4a72c"
        return "#2da44e"
    }
    function fit() { zoom = 1; panX = 0; panY = 0 }

    onGeoChanged: canvas.requestPaint()
    onZoomChanged: canvas.requestPaint()
    onPanXChanged: canvas.requestPaint()
    onPanYChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()

    Connections {
        target: GridController
        function onModelChanged() { root.fit() }
        function onSelectedBusChanged() { canvas.requestPaint() }
    }

    Rectangle { anchors.fill: parent; color: palette.base }

    Canvas {
        id: canvas
        objectName: "gridCanvas"
        anchors.fill: parent
        visible: root.hasDrawing
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (!root.hasDrawing)
                return
            ctx.lineCap = "round"
            for (const br of root.geo.branches) {
                const x1 = root.px(br.x1), y1 = root.py(br.y1)
                const x2 = root.px(br.x2), y2 = root.py(br.y2)
                ctx.strokeStyle = br.overloaded ? "#d73a49" : (br.kind === "transformer" ? "#6f42c1" : "#57606a")
                ctx.lineWidth = br.overloaded ? 5 : 1.6
                ctx.setLineDash(br.kind === "line" && br.open ? [4, 4] : [])
                if (br.kind === "switch" && br.open) {
                    // an open switch: a gap with the blade turned away
                    const ax = x1 + (x2 - x1) * 0.35, ay = y1 + (y2 - y1) * 0.35
                    const bx = x1 + (x2 - x1) * 0.65, by = y1 + (y2 - y1) * 0.65
                    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(ax, ay); ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(x2, y2); ctx.stroke()
                    const len = Math.hypot(bx - ax, by - ay), ang = Math.atan2(by - ay, bx - ax) - 0.6
                    ctx.beginPath(); ctx.moveTo(ax, ay)
                    ctx.lineTo(ax + len * Math.cos(ang), ay + len * Math.sin(ang)); ctx.stroke()
                } else {
                    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
                }
                if (br.kind === "transformer") {
                    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
                    ctx.setLineDash([])
                    ctx.lineWidth = 1.4
                    ctx.fillStyle = palette.base
                    for (const off of [-3, 3]) {
                        ctx.beginPath(); ctx.arc(mx + off, my, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke()
                    }
                }
                if (br.kind === "switch" && !br.open) {
                    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
                    ctx.setLineDash([])
                    ctx.fillStyle = "#57606a"
                    ctx.fillRect(mx - 3, my - 3, 6, 6)
                }
            }
            ctx.setLineDash([])
            const labels = root.geo.buses.length <= 60 || root.zoom >= 2
            ctx.font = "10px sans-serif"
            for (const b of root.geo.buses) {
                const x = root.px(b.x), y = root.py(b.y)
                const r = b.name === GridController.selectedBus ? 7 : 5
                ctx.fillStyle = root.busColor(b.minPu)
                ctx.strokeStyle = b.name === GridController.selectedBus ? "#0969da" : "#24292f"
                ctx.lineWidth = b.name === GridController.selectedBus ? 2.5 : 1
                ctx.beginPath()
                if (b.source)
                    ctx.rect(x - r, y - r, 2 * r, 2 * r)
                else
                    ctx.arc(x, y, r, 0, 2 * Math.PI)
                ctx.fill(); ctx.stroke()
                if (labels) {
                    ctx.fillStyle = palette.text
                    ctx.fillText(b.name, x + 7, y - 6)
                }
            }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.hasDrawing
        acceptedButtons: Qt.LeftButton
        property real lastX: 0
        property real lastY: 0
        property bool dragged: false
        onPressed: e => { lastX = e.x; lastY = e.y; dragged = false }
        onPositionChanged: e => {
            if (Math.abs(e.x - lastX) + Math.abs(e.y - lastY) > 2 || dragged) {
                dragged = true
                root.panX += e.x - lastX
                root.panY += e.y - lastY
                lastX = e.x; lastY = e.y
            }
        }
        onClicked: e => {
            if (!dragged)
                GridController.selectBus(root.busAt(e.x, e.y))
        }
        onDoubleClicked: root.fit()
        onWheel: w => {
            const f = w.angleDelta.y > 0 ? 1.2 : 1 / 1.2
            const nz = Math.min(Math.max(root.zoom * f, 0.2), 50)
            const k = nz / root.zoom
            // keep the point under the cursor where it is
            root.panX = (root.panX + root.width / 2 - w.x) * k + w.x - root.width / 2
            root.panY = (root.panY + root.height / 2 - w.y) * k + w.y - root.height / 2
            root.zoom = nz
        }
    }

    Label {
        objectName: "gridViewMessage"
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 460)
        visible: !root.hasDrawing
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        color: palette.placeholderText
        text: GridController.filePath === ""
              ? qsTr("Open a .dss file (File > Open Grid) to see its single-line view.")
              : GridController.running && root.geo.buses === undefined
                ? qsTr("Opening…")
                : root.geo.missing > 0
                  ? qsTr("This feeder has no bus coordinates, so it cannot be drawn. "
                         + "Add BusCoords to the .dss file; the results are in the table.")
                  : qsTr("Nothing to draw yet.")
    }

    // Legend
    Row {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.margins: 6
        spacing: 10
        visible: root.hasDrawing
        Repeater {
            model: [
                { c: "#2da44e", t: qsTr("within limits") },
                { c: "#d4a72c", t: qsTr("near a limit") },
                { c: "#d73a49", t: qsTr("< %1 pu").arg(GridController.voltageMinPu) },
                { c: "#8250df", t: qsTr("> %1 pu").arg(GridController.voltageMaxPu) },
                { c: "#9e9e9e", t: qsTr("not solved") }
            ]
            delegate: Row {
                required property var modelData
                spacing: 3
                Rectangle { width: 10; height: 10; radius: 5; color: modelData.c; anchors.verticalCenter: parent.verticalCenter }
                Label { text: modelData.t; font.pointSize: 8 }
            }
        }
    }

    // Phase voltages of the selected bus
    Frame {
        objectName: "busVoltagePanel"
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 6
        visible: GridController.selectedBus !== ""
        background: Rectangle { color: palette.window; border.color: palette.mid; radius: 3; opacity: 0.95 }
        ColumnLayout {
            spacing: 2
            RowLayout {
                Label { text: qsTr("Bus %1").arg(GridController.selectedBus); font.bold: true; Layout.fillWidth: true }
                ToolButton {
                    text: "×"
                    implicitWidth: 22; implicitHeight: 22
                    onClicked: GridController.selectBus("")
                }
            }
            Label {
                visible: GridController.selectedVoltages.length === 0
                text: GridController.hasResult ? qsTr("No phase nodes.") : qsTr("Run to see its voltages.")
                color: palette.placeholderText
            }
            Label {
                visible: GridController.selectedVoltages.length > 0
                font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                font.bold: true
                text: qsTr("ph        kV          pu    angle (°)")
            }
            Repeater {
                model: GridController.selectedVoltages
                delegate: Label {
                    required property var modelData
                    objectName: "busVoltage_" + modelData.phase
                    font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                    text: modelData.phase.padEnd(3) + modelData.kv.padStart(9) + " kV"
                          + modelData.pu.padStart(9) + modelData.angle.padStart(10) + "°"
                }
            }
        }
    }
}
