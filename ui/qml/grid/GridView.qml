import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice

// Read-only single-line view of the open feeder (G6), drawn from the bus coordinates
// in GridController.geometry. Buses are coloured by their status from the results
// overlay (D8: GridController.overlay, computed in core/grid/analysis.py; QML only maps
// the status to a colour), GridResultsOverlay draws branch loading colours and widths
// with a legend and a toggle, lines over their loading limit are drawn thick and red,
// open switches are drawn open. Click a bus to see its phase voltages, hover it for a quick readout; wheel or
// the toolbar to zoom, drag to pan, double-click or Fit to fit. Bus labels that would
// overlap another label or bus are left out (zoom in to see them). Nothing here edits
// the feeder. v0.5 (rule 28): "Edit as Design" asks for a copy of the feeder in the
// Grid Network Designer (editAsDesignRequested); the .dss file is never written.
Item {
    id: root
    objectName: "gridView"
    clip: true
    signal editAsDesignRequested()

    readonly property var geo: GridController.geometry
    readonly property bool hasDrawing: geo.hasCoordinates === true
    property real zoom: 1.0
    property real panX: 0
    property real panY: 0
    readonly property real margin: 30
    property bool labelsOn: true
    property string hoverBus: ""
    property real hoverX: 0
    property real hoverY: 0
    property int labelCount: 0  // labels drawn by the last paint (for tests)

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
    // The bus's overlay status as a colour; grey before a run or with the overlay off.
    function busColor(name) {
        const item = GridController.overlay["bus." + name]
        if (item === undefined || !resultsLayer.shown)
            return "#9e9e9e"
        return resultsLayer.statusColor(item.status)
    }
    function fit() { zoom = 1; panX = 0; panY = 0 }
    // Zoom by `f`, keeping the point (x, y) of the view where it is.
    function zoomAt(f, x, y) {
        const nz = Math.min(Math.max(zoom * f, 0.2), 50)
        const k = nz / zoom
        panX = (panX + width / 2 - x) * k + x - width / 2
        panY = (panY + height / 2 - y) * k + y - height / 2
        zoom = nz
    }
    // The first label rectangle next to a bus at (x, y) that overlaps nothing in
    // `taken` and stays inside the view; null if there is none.
    function labelSpot(x, y, w, h, taken) {
        const spots = [[x + 7, y - h - 2], [x + 7, y + 2], [x - 7 - w, y - h - 2],
                       [x - 7 - w, y + 2], [x + 9, y - h / 2], [x - 9 - w, y - h / 2]]
        for (const [sx, sy] of spots) {
            if (sx < 0 || sy < 0 || sx + w > width || sy + h > height)
                continue
            let free = true
            for (const t of taken) {
                if (sx < t.x + t.w && sx + w > t.x && sy < t.y + t.h && sy + h > t.y) {
                    free = false
                    break
                }
            }
            if (free)
                return { x: sx, y: sy, w: w, h: h }
        }
        return null
    }
    // Save the drawing, with its legend but without the toolbar, as an image file.
    function saveImage(url) {
        const path = urlToPath(url)
        toolbar.visible = false
        editAsDesign.visible = false
        hoverBus = ""
        root.grabToImage(result => {
            toolbar.visible = Qt.binding(() => root.hasDrawing)
            editAsDesign.visible = Qt.binding(() => GridController.filePath !== "")
            result.saveToFile(path)
        })
    }
    function urlToPath(url) {
        const text = url.toString()
        if (!text.startsWith("file:"))
            return text
        // file:///C:/a%20b.png -> C:/a b.png (Windows); file:///home/x.png -> /home/x.png
        return decodeURIComponent(text.replace(/^file:\/\//, "").replace(/^\/([A-Za-z]:)/, "$1"))
    }

    onGeoChanged: canvas.requestPaint()
    onZoomChanged: canvas.requestPaint()
    onPanXChanged: canvas.requestPaint()
    onPanYChanged: canvas.requestPaint()
    onLabelsOnChanged: canvas.requestPaint()
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
            ctx.font = "10px sans-serif"
            const taken = []  // bus symbols, then the labels placed so far
            for (const b of root.geo.buses) {
                const x = root.px(b.x), y = root.py(b.y)
                taken.push({ x: x - 6, y: y - 6, w: 12, h: 12 })
            }
            for (const b of root.geo.buses) {
                const x = root.px(b.x), y = root.py(b.y)
                const r = b.name === GridController.selectedBus ? 7 : 5
                ctx.fillStyle = root.busColor(b.name)
                ctx.strokeStyle = b.name === GridController.selectedBus ? "#0969da" : "#24292f"
                ctx.lineWidth = b.name === GridController.selectedBus ? 2.5 : 1
                ctx.beginPath()
                if (b.source)
                    ctx.rect(x - r, y - r, 2 * r, 2 * r)
                else
                    ctx.arc(x, y, r, 0, 2 * Math.PI)
                ctx.fill(); ctx.stroke()
            }
            // Labels: the selected bus first (always shown), then sources, then the
            // rest; a label that would overlap a bus or another label is left out.
            let drawn = 0
            if (root.labelsOn) {
                ctx.textBaseline = "top"
                const rank = b => b.name === GridController.selectedBus ? 0 : (b.source ? 1 : 2)
                const order = root.geo.buses.slice().sort((p, q) => rank(p) - rank(q))
                for (const b of order) {
                    const x = root.px(b.x), y = root.py(b.y)
                    if (x < -20 || y < -20 || x > root.width + 20 || y > root.height + 20)
                        continue
                    const w = ctx.measureText(b.name).width, h = 11
                    let spot = root.labelSpot(x, y, w, h, taken)
                    if (spot === null && b.name === GridController.selectedBus)
                        spot = { x: x + 7, y: y - h - 2, w: w, h: h }
                    if (spot === null)
                        continue
                    taken.push(spot)
                    ctx.fillStyle = b.name === GridController.selectedBus ? "#0969da" : palette.text
                    ctx.fillText(b.name, spot.x, spot.y)
                    drawn++
                }
            }
            root.labelCount = drawn
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.hasDrawing
        acceptedButtons: Qt.LeftButton
        hoverEnabled: true
        property real lastX: 0
        property real lastY: 0
        property bool dragged: false
        onPressed: e => { lastX = e.x; lastY = e.y; dragged = false }
        onPositionChanged: e => {
            if (!pressed) {
                root.hoverBus = root.busAt(e.x, e.y)
                root.hoverX = e.x
                root.hoverY = e.y
                return
            }
            root.hoverBus = ""
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
        onExited: root.hoverBus = ""
        onDoubleClicked: root.fit()
        onWheel: w => root.zoomAt(w.angleDelta.y > 0 ? 1.2 : 1 / 1.2, w.x, w.y)
    }

    // Results overlay (D8). The view is read-only, so its results are never stale.
    // px/py are linear: pixel = px(0) + (px(1) - px(0)) * x.
    GridResultsOverlay {
        id: resultsLayer
        anchors.fill: parent
        visible: root.hasDrawing
        available: GridController.hasResult
        items: GridController.overlay
        paths: GridController.overlayPaths
        buses: GridController.overlayBuses
        limits: GridController.overlayLimits
        originX: root.px(0)
        originY: root.py(0)
        scaleX: root.px(1) - root.px(0)
        scaleY: root.py(1) - root.py(0)
        onShownChanged: canvas.requestPaint()
    }

    // Toolbar, top left over the drawing
    Frame {
        id: toolbar
        objectName: "gridToolbar"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: 4
        padding: 1
        visible: root.hasDrawing
        background: Rectangle { color: palette.window; border.color: palette.mid; radius: 3; opacity: 0.92 }
        Row {
            ToolButton {
                objectName: "gridZoomIn"
                text: "+"
                implicitWidth: 28
                onClicked: root.zoomAt(1.5, root.width / 2, root.height / 2)
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: qsTr("Zoom in (or use the wheel)")
            }
            ToolButton {
                objectName: "gridZoomOut"
                text: "\u2212"
                implicitWidth: 28
                onClicked: root.zoomAt(1 / 1.5, root.width / 2, root.height / 2)
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: qsTr("Zoom out (or use the wheel)")
            }
            ToolButton {
                objectName: "gridFit"
                text: qsTr("Fit")
                onClicked: root.fit()
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: qsTr("Show the whole feeder (or double-click)")
            }
            ToolButton {
                objectName: "gridLabels"
                text: qsTr("Labels")
                checkable: true
                checked: root.labelsOn
                onToggled: root.labelsOn = checked
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: qsTr("Bus names; a name that would overlap is left out")
            }
            ToolButton {
                objectName: "gridSaveImage"
                text: qsTr("Save image\u2026")
                onClicked: imageDialog.open()
            }
        }
    }

    // v0.5: copy the open feeder into a new design (rule 28: read-only here)
    Button {
        id: editAsDesign
        objectName: "gridEditAsDesign"
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 4
        visible: GridController.filePath !== ""
        enabled: !GridController.running
        text: qsTr("Edit as Design")
        onClicked: root.editAsDesignRequested()
        ToolTip.visible: hovered
        ToolTip.delay: 600
        ToolTip.text: qsTr("Open a copy of this feeder in the Grid Network Designer; "
                           + "the .dss file is not changed")
    }

    FileDialog {
        id: imageDialog
        title: qsTr("Save the single-line view")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "png"
        nameFilters: [qsTr("PNG images (*.png)")]
        onAccepted: root.saveImage(selectedFile)
    }

    // Quick readout of the bus under the mouse
    Rectangle {
        id: hoverTip
        objectName: "gridHoverTip"
        readonly property var volts: root.hoverBus !== "" ? GridController.busVoltages(root.hoverBus) : []
        visible: root.hoverBus !== "" && root.hasDrawing
        x: Math.min(root.hoverX + 14, root.width - width - 4)
        y: Math.max(4, Math.min(root.hoverY + 14, root.height - height - 4))
        width: hoverColumn.implicitWidth + 12
        height: hoverColumn.implicitHeight + 8
        color: palette.toolTipBase
        border.color: palette.mid
        radius: 3
        Column {
            id: hoverColumn
            x: 6
            y: 4
            Label {
                text: qsTr("Bus %1").arg(root.hoverBus)
                font.bold: true
                color: palette.toolTipText
            }
            Label {
                objectName: "gridHoverText"
                color: palette.toolTipText
                text: hoverTip.volts.length === 0
                      ? (GridController.hasResult ? qsTr("no phase nodes") : qsTr("run to see its voltages"))
                      : hoverTip.volts.map(v => v.phase + " " + v.pu).join("   ") + " pu"
            }
            Label {
                objectName: "gridHoverStatus"
                readonly property var item: GridController.overlay["bus." + root.hoverBus]
                visible: item !== undefined
                color: palette.toolTipText
                text: item !== undefined ? resultsLayer.statusWord(item.status) : ""
            }
            Label {
                visible: hoverTip.volts.length > 0
                color: palette.placeholderText
                font.pointSize: 8
                text: qsTr("click for kV and angles")
            }
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
