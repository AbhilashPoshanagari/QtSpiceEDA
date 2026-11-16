import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes

// Results overlay (D8): a layer over a grid drawing - the designer canvas or the G6
// single-line view. It draws what the bridge computed (rule 19; GridDiagramController /
// GridController .overlay, .overlayPaths, .overlayBuses, .overlayLimits): bus colour by
// |V| pu against the voltage band, branch colour and width by loading, grey for
// de-energized parts. QML only maps a status to a colour and a width step to a pen
// width; no number or status is computed here.
//
// The host gives the mapping from its source units (diagram units, or feeder
// coordinates) to the overlay's pixels: pixel = origin + scale * value (scaleY may be
// negative: y up in a .dss file). It sets hoverId / hoverPoint for the tooltip.
// After any change the host sets `stale`: the drawing is hatched and a banner asks for
// a new run. Only the banner button and the legend's toggle take the mouse.
Item {
    id: overlay
    objectName: "gridResultsOverlay"

    property var items: ({})      // id -> {status, value, limit, unit, ratio, weight, text}
    property var paths: []        // [{key, status, weight, path}] in source units
    property var buses: []        // [{id, status, x1, y1, x2, y2}] in source units
    property var limits: ({})     // {voltageMinPu, voltageMaxPu, lineLimitPct, ...}
    property bool available: false
    property bool stale: false
    property bool canRun: false
    property bool shown: true     // the toggle
    property real originX: 0
    property real originY: 0
    property real scaleX: 1
    property real scaleY: 1
    property string hoverId: ""
    property point hoverPoint: Qt.point(0, 0)
    signal runRequested()

    readonly property bool drawn: available && shown
    readonly property var colors: ({
        pass: "#2da44e", warn: "#d4a72c", fail: "#d73a49", none: "#6e7781", off: "#b0b0b0"
    })
    function statusColor(status) {
        return colors[status] !== undefined ? colors[status] : "transparent"
    }
    function statusWord(status) {
        return ({ pass: qsTr("within limits"), warn: qsTr("near the limit"),
                  fail: qsTr("outside the limits"), none: qsTr("not checked"),
                  off: qsTr("de-energized") })[status] || ""
    }
    // pen width in pixels for a width step 0..3 (bridge: OverlayItem.weight)
    function penPixels(weight) { return 2 + 2.2 * weight }
    readonly property real unitPixels: Math.max(Math.abs(scaleX), 1e-9)

    // ---- the drawing, in source units, mapped by one transform
    Item {
        id: layer
        objectName: "gridOverlayLayer"
        visible: overlay.drawn
        opacity: overlay.stale ? 0.45 : 0.85
        transform: Matrix4x4 {
            matrix: Qt.matrix4x4(overlay.scaleX, 0, 0, overlay.originX,
                                 0, overlay.scaleY, 0, overlay.originY,
                                 0, 0, 1, 0,
                                 0, 0, 0, 1)
        }
        Repeater {
            objectName: "gridOverlayPaths"
            model: overlay.drawn ? overlay.paths : []
            delegate: Shape {
                required property var modelData
                preferredRendererType: Shape.GeometryRenderer
                ShapePath {
                    strokeColor: overlay.statusColor(modelData.status)
                    strokeWidth: overlay.penPixels(modelData.weight) / overlay.unitPixels
                    strokeStyle: overlay.stale ? ShapePath.DashLine : ShapePath.SolidLine
                    dashPattern: [3, 2]
                    capStyle: ShapePath.RoundCap
                    fillColor: "transparent"
                    PathSvg { path: modelData.path }
                }
            }
        }
    }

    // ---- buses: a rounded bar (or dot) over each bus, in pixels
    Repeater {
        objectName: "gridOverlayBuses"
        model: overlay.drawn ? overlay.buses : []
        delegate: Rectangle {
            required property var modelData
            readonly property real ax: overlay.originX + overlay.scaleX * modelData.x1
            readonly property real ay: overlay.originY + overlay.scaleY * modelData.y1
            readonly property real bx: overlay.originX + overlay.scaleX * modelData.x2
            readonly property real by: overlay.originY + overlay.scaleY * modelData.y2
            readonly property real len: Math.hypot(bx - ax, by - ay)
            objectName: "gridOverlay_" + modelData.id
            width: len + 12
            height: 12
            radius: 6
            x: (ax + bx) / 2 - width / 2
            y: (ay + by) / 2 - height / 2
            rotation: Math.atan2(by - ay, bx - ax) * 180 / Math.PI
            color: "transparent"
            border.color: overlay.statusColor(modelData.status)
            border.width: 3
            opacity: overlay.stale ? 0.45 : 0.9
        }
    }

    // ---- stale: hatching over the whole view
    Canvas {
        id: hatch
        objectName: "gridOverlayHatch"
        anchors.fill: parent
        visible: overlay.drawn && overlay.stale
        onVisibleChanged: if (visible) requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = "rgba(120, 120, 120, 0.18)"
            ctx.lineWidth = 1
            const step = 12
            ctx.beginPath()
            for (let d = -height; d < width; d += step) {
                ctx.moveTo(d, height)
                ctx.lineTo(d + height, 0)
            }
            ctx.stroke()
        }
    }

    // ---- stale banner
    Rectangle {
        objectName: "gridOverlayStaleBanner"
        visible: overlay.available && overlay.stale
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 6
        width: bannerRow.implicitWidth + 16
        height: bannerRow.implicitHeight + 8
        radius: 4
        color: "#fff8c5"
        border.color: "#d4a72c"
        RowLayout {
            id: bannerRow
            anchors.centerIn: parent
            spacing: 8
            Label {
                objectName: "gridOverlayStaleText"
                text: qsTr("Results out of date - Run again")
                color: "#7d4e00"
            }
            Button {
                objectName: "gridOverlayRunAgain"
                visible: overlay.canRun
                text: qsTr("Run")
                onClicked: overlay.runRequested()
            }
        }
    }

    // ---- tooltip with the values of the hovered bus or element
    Rectangle {
        id: tip
        objectName: "gridOverlayTip"
        readonly property var item: overlay.hoverId !== "" ? overlay.items[overlay.hoverId] : undefined
        visible: overlay.drawn && item !== undefined
        x: Math.min(overlay.hoverPoint.x + 14, overlay.width - width - 4)
        y: Math.max(4, Math.min(overlay.hoverPoint.y + 14, overlay.height - height - 4))
        width: tipColumn.implicitWidth + 12
        height: tipColumn.implicitHeight + 8
        radius: 3
        color: palette.toolTipBase
        border.color: item !== undefined ? overlay.statusColor(item.status) : palette.mid
        Column {
            id: tipColumn
            x: 6
            y: 4
            Label {
                objectName: "gridOverlayTipText"
                text: tip.item !== undefined ? tip.item.text : ""
                color: palette.toolTipText
            }
            Label {
                text: tip.item !== undefined
                      ? overlay.statusWord(tip.item.status)
                        + (overlay.stale ? qsTr(" (results out of date)") : "")
                      : ""
                color: palette.placeholderText
                font.pointSize: 8
            }
        }
    }

    // ---- legend and toggle
    Frame {
        objectName: "gridOverlayLegend"
        visible: overlay.available
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 6
        padding: 6
        background: Rectangle { color: palette.window; border.color: palette.mid; radius: 3; opacity: 0.94 }
        ColumnLayout {
            spacing: 2
            RowLayout {
                Label { text: qsTr("Results"); font.bold: true; Layout.fillWidth: true; Layout.maximumWidth: 180 }
                Switch {
                    objectName: "gridOverlayToggle"
                    checked: overlay.shown
                    onToggled: overlay.shown = checked
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Show or hide the results overlay")
                }
            }
            Repeater {
                model: overlay.shown ? [
                    { s: "pass", t: qsTr("within limits") },
                    { s: "warn", t: qsTr("loading from %1 % of the limit").arg(Number(overlay.limits.warnPct || 0).toFixed(0)) },
                    { s: "fail", t: qsTr("outside the band / over the limit") },
                    { s: "none", t: qsTr("not checked (no rating, switch)") },
                    { s: "off", t: qsTr("de-energized") }
                ] : []
                delegate: Row {
                    required property var modelData
                    spacing: 4
                    Rectangle {
                        width: 14; height: 6; radius: 3
                        anchors.verticalCenter: parent.verticalCenter
                        color: overlay.statusColor(modelData.s)
                    }
                    Label { text: modelData.t; font.pointSize: 8; width: Math.min(implicitWidth, 220); elide: Text.ElideRight }
                }
            }
            Label {
                visible: overlay.shown
                Layout.maximumWidth: 240
                wrapMode: Text.WordWrap
                font.pointSize: 8
                color: palette.placeholderText
                text: qsTr("Voltage band %1 - %2 pu; lines %3 %, transformers %4 %; thicker = more loaded")
                      .arg(overlay.limits.voltageMinPu).arg(overlay.limits.voltageMaxPu)
                      .arg(overlay.limits.lineLimitPct).arg(overlay.limits.transformerLimitPct)
            }
        }
    }
}
