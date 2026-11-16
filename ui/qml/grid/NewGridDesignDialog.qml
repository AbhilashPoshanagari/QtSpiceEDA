import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// File > New Grid Design… (D6). The base frequency is required and has no default
// (never 60 Hz by assumption). Optionally the design starts with a bus and a
// 3-phase source on it; its kV and R1/X1/R0/X0 are typed, never invented (rule 24).
// OK calls GridDiagramController.newDesign once; a refusal stays in the status text.
Dialog {
    id: root
    objectName: "newGridDesignDialog"
    required property var designer
    title: qsTr("New Grid Design")
    modal: true
    standardButtons: Dialog.Ok | Dialog.Cancel
    anchors.centerIn: Overlay.overlay

    function number(text) {
        const t = text.trim()
        return t === "" ? NaN : Number(t)
    }
    readonly property bool nameOk: /^[a-z0-9_]+$/.test(nameField.text.trim().toLowerCase())
    readonly property bool freqOk: number(freqField.text) > 0
    readonly property var sourceValues: [kvField, r1Field, x1Field, r0Field, x0Field]
                                        .map(f => root.number(f.text))
    readonly property bool sourceOk: !withSource.checked
                                     || (busField.text.trim() !== ""
                                         && sourceValues.every(v => isFinite(v)))
    readonly property bool valid: nameOk && freqOk && sourceOk

    onAboutToShow: {
        nameField.text = ""
        freqField.text = ""
        nameField.forceActiveFocus()
    }
    onOpened: standardButton(Dialog.Ok).enabled = Qt.binding(() => root.valid)
    onAccepted: {
        const name = nameField.text.trim().toLowerCase()
        const f = number(freqField.text)
        if (withSource.checked) {
            const v = sourceValues
            designer.newDesign(name, f, busField.text.trim().toLowerCase(), v[0], v[1], v[2], v[3], v[4])
        } else {
            designer.newDesign(name, f)
        }
    }

    component NumberField: TextField {
        Layout.preferredWidth: 110
        selectByMouse: true
        validator: DoubleValidator { notation: DoubleValidator.ScientificNotation; locale: "C" }
    }

    GridLayout {
        columns: 3
        columnSpacing: 8
        rowSpacing: 6

        Label { text: qsTr("Name") }
        TextField {
            id: nameField
            objectName: "newDesignName"
            Layout.preferredWidth: 200
            Layout.columnSpan: 2
            placeholderText: qsTr("e.g. feeder_a")
            validator: RegularExpressionValidator { regularExpression: /[A-Za-z0-9_]*/ }
        }
        Label { text: qsTr("Base frequency") }
        NumberField {
            id: freqField
            objectName: "newDesignFrequency"
            placeholderText: qsTr("required")
        }
        Label { text: "Hz" }
        Label {
            Layout.columnSpan: 3
            Layout.maximumWidth: 360
            wrapMode: Text.WordWrap
            font.pointSize: 8
            color: palette.placeholderText
            text: qsTr("Lower-case letters, digits and _. The base frequency has no default: "
                       + "type your system's (for example 50 or 60).")
        }

        CheckBox {
            id: withSource
            objectName: "newDesignWithSource"
            Layout.columnSpan: 3
            text: qsTr("Start with a source bus and a 3-phase source")
        }
        Label { text: qsTr("Source bus"); enabled: withSource.checked }
        TextField {
            id: busField
            objectName: "newDesignSourceBus"
            Layout.preferredWidth: 110
            Layout.columnSpan: 2
            enabled: withSource.checked
            text: "sourcebus"
            validator: RegularExpressionValidator { regularExpression: /[A-Za-z0-9_]*/ }
        }
        Label { text: qsTr("kV (line-line)"); enabled: withSource.checked }
        NumberField { id: kvField; objectName: "newDesignKv"; enabled: withSource.checked; placeholderText: qsTr("required") }
        Label { text: "kV"; enabled: withSource.checked }
        Label { text: "R1"; enabled: withSource.checked }
        NumberField { id: r1Field; objectName: "newDesignR1"; enabled: withSource.checked; placeholderText: qsTr("required") }
        Label { text: "ohm"; enabled: withSource.checked }
        Label { text: "X1"; enabled: withSource.checked }
        NumberField { id: x1Field; objectName: "newDesignX1"; enabled: withSource.checked; placeholderText: qsTr("required") }
        Label { text: "ohm"; enabled: withSource.checked }
        Label { text: "R0"; enabled: withSource.checked }
        NumberField { id: r0Field; objectName: "newDesignR0"; enabled: withSource.checked; placeholderText: qsTr("required") }
        Label { text: "ohm"; enabled: withSource.checked }
        Label { text: "X0"; enabled: withSource.checked }
        NumberField { id: x0Field; objectName: "newDesignX0"; enabled: withSource.checked; placeholderText: qsTr("required") }
        Label { text: "ohm"; enabled: withSource.checked }
    }
}
