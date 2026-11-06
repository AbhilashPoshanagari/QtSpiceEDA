import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Plot settings (W2): pane titles, Y ranges and axes, the shared X axis and the pane
// order. Works on a copy of WaveformController.config(); OK applies it as one undo
// step. Numbers accept SPICE suffixes (1m, 10k, 1meg).
Dialog {
    id: root
    objectName: "plotConfigDialog"
    modal: true
    anchors.centerIn: Overlay.overlay
    width: 520
    title: qsTr("Plot settings")
    standardButtons: Dialog.Ok | Dialog.Cancel

    property var config: ({})
    property int current: 0
    property string problem: ""
    property int revision: 0  // bumped when the pane order changes
    readonly property bool isAc: config.isAc === true

    function fmt(v) { return Number(v).toPrecision(6).replace(/\.?0+(e|$)/, "$1") }

    function openFor(index) {
        config = JSON.parse(JSON.stringify(WaveformController.config()))  // a plain JS copy
        if (!config.panes)
            return
        current = Math.max(0, Math.min(index, config.panes.length - 1))
        load()
        problem = ""
        open()
    }

    // Copy the fields into the editors (X and the current pane).
    function load() {
        xAuto.checked = config.x.auto
        xLo.text = fmt(config.x.lo)
        xHi.text = fmt(config.x.hi)
        xLog.checked = config.x.log
        const p = config.panes[current]
        title.text = p.title
        yAuto.checked = p.y.auto
        yLo.text = fmt(p.y.lo)
        yHi.text = fmt(p.y.hi)
        yLog.checked = p.y.log
        yAbs.checked = p.y.abs
        grid.checked = p.grid
        phAuto.checked = p.phase.auto
        phLo.text = fmt(p.phase.lo)
        phHi.text = fmt(p.phase.hi)
    }

    // Read the editors back into `config`; false (and `problem`) on a bad number.
    function store() {
        const c = config
        const num = (field, what) => {
            const v = WaveformController.parseNumber(field.text)
            if (!isFinite(v))
                problem = qsTr("%1 is not a number").arg(what)
            return v
        }
        problem = ""
        c.x.auto = xAuto.checked
        c.x.log = xLog.checked
        if (!xAuto.checked) {
            c.x.lo = num(xLo, qsTr("X from"))
            c.x.hi = num(xHi, qsTr("X to"))
            if (problem === "" && c.x.lo >= c.x.hi)
                problem = qsTr("X: 'from' must be below 'to'")
            if (problem === "" && c.x.log && c.x.lo <= 0)
                problem = qsTr("A log X axis needs 'from' above 0")
        }
        const p = c.panes[current]
        p.title = title.text
        p.y.auto = yAuto.checked
        p.y.log = yLog.checked
        p.y.abs = yAbs.checked
        p.grid = grid.checked
        if (!yAuto.checked) {
            p.y.lo = num(yLo, qsTr("Y from"))
            p.y.hi = num(yHi, qsTr("Y to"))
            if (problem === "" && p.y.lo >= p.y.hi)
                problem = qsTr("Y: 'from' must be below 'to'")
            if (problem === "" && p.y.log && p.y.lo <= 0)
                problem = qsTr("A log Y axis needs 'from' above 0")
        }
        p.phase.auto = phAuto.checked
        if (isAc && !phAuto.checked) {
            p.phase.lo = num(phLo, qsTr("Phase from"))
            p.phase.hi = num(phHi, qsTr("Phase to"))
            if (problem === "" && p.phase.lo >= p.phase.hi)
                problem = qsTr("Phase: 'from' must be below 'to'")
        }
        return problem === ""
    }

    function select(index) {
        if (index === current || !store())
            return
        current = index
        load()
    }

    function move(delta) {
        if (!store())
            return
        const to = current + delta
        if (to < 0 || to >= config.panes.length)
            return
        const panes = config.panes
        const item = panes.splice(current, 1)[0]
        panes.splice(to, 0, item)
        current = to
        revision += 1  // refresh the list
        load()
    }

    function accept_() {
        if (!store())
            return false
        WaveformController.configure(config)
        return true
    }

    onAccepted: if (!accept_()) open()

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        GroupBox {
            title: qsTr("X axis (all panes)")
            Layout.fillWidth: true
            GridLayout {
                columns: 5
                CheckBox { id: xAuto; objectName: "xAuto"; text: qsTr("Auto") }
                Label { text: qsTr("from") }
                TextField { id: xLo; objectName: "xLo"; enabled: !xAuto.checked; Layout.preferredWidth: 100 }
                Label { text: qsTr("to") }
                TextField { id: xHi; objectName: "xHi"; enabled: !xAuto.checked; Layout.preferredWidth: 100 }
                CheckBox { id: xLog; objectName: "xLog"; text: qsTr("Logarithmic") }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            ColumnLayout {
                Layout.alignment: Qt.AlignTop
                Label { text: qsTr("Panes (top first)") }
                ListView {
                    id: paneList
                    objectName: "paneList"
                    Layout.preferredWidth: 140
                    Layout.preferredHeight: 140
                    clip: true
                    model: root.config.panes ? root.config.panes.length : 0
                    delegate: ItemDelegate {
                        required property int index
                        width: ListView.view.width
                        highlighted: index === root.current
                        text: {
                            root.revision
                            const p = root.config.panes[index]
                            return p.title !== "" ? p.title
                                 : (p.traces.length ? p.traces.join(", ") : qsTr("(empty)"))
                        }
                        onClicked: root.select(index)
                    }
                }
                RowLayout {
                    Button {
                        objectName: "paneUp"
                        text: qsTr("Up")
                        enabled: root.current > 0
                        onClicked: root.move(-1)
                    }
                    Button {
                        objectName: "paneDown"
                        text: qsTr("Down")
                        enabled: !!root.config.panes && root.current < root.config.panes.length - 1
                        onClicked: root.move(1)
                    }
                }
            }

            GroupBox {
                title: qsTr("Selected pane")
                Layout.fillWidth: true
                GridLayout {
                    columns: 5
                    Label { text: qsTr("Title") }
                    TextField { id: title; objectName: "paneTitleField"; Layout.columnSpan: 4; Layout.fillWidth: true }

                    Label { text: root.isAc ? qsTr("Magnitude") : qsTr("Y") }
                    CheckBox { id: yAuto; objectName: "yAuto"; text: qsTr("Auto") }
                    TextField { id: yLo; objectName: "yLo"; enabled: !yAuto.checked; Layout.preferredWidth: 80 }
                    Label { text: qsTr("to") }
                    TextField { id: yHi; objectName: "yHi"; enabled: !yAuto.checked; Layout.preferredWidth: 80 }

                    Item { width: 1; height: 1 }
                    CheckBox {
                        id: yLog
                        objectName: "yLog"
                        text: qsTr("Logarithmic")
                        visible: !root.isAc
                        Layout.columnSpan: 2
                    }
                    CheckBox {
                        id: yAbs
                        objectName: "yAbs"
                        text: qsTr("Plot |y| (else values ≤ 0 are hidden)")
                        visible: !root.isAc
                        enabled: yLog.checked
                        Layout.columnSpan: 2
                    }

                    Label { text: qsTr("Phase"); visible: root.isAc }
                    CheckBox { id: phAuto; objectName: "phaseAuto"; text: qsTr("Auto"); visible: root.isAc }
                    TextField { id: phLo; visible: root.isAc; enabled: !phAuto.checked; Layout.preferredWidth: 80 }
                    Label { text: qsTr("to"); visible: root.isAc }
                    TextField { id: phHi; visible: root.isAc; enabled: !phAuto.checked; Layout.preferredWidth: 80 }

                    CheckBox { id: grid; objectName: "gridCheck"; text: qsTr("Grid"); Layout.columnSpan: 5 }
                }
            }
        }

        Label {
            objectName: "configProblem"
            visible: root.problem !== ""
            text: root.problem
            color: "#d73a49"
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
    }
}
