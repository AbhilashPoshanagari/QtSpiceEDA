import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Attribute dialog (S9): double-click a part to edit its name and values, as LTspice's
// Component Attribute Editor. Simple parts (R, C, L, V, I) show Value (and Value2 for
// sources); other parts also show SpiceModel, ModelFile, SpiceLine and SpiceLine2.
// Every change is checked with the same netlister the run uses; OK is one undo step.
Dialog {
    id: root
    objectName: "attributeDialog"
    modal: true
    anchors.centerIn: parent
    width: 440
    standardButtons: Dialog.Ok | Dialog.Cancel

    property int partIndex: -1
    property var info: ({})
    property string problem: ""

    title: info.name !== undefined ? qsTr("%1 (%2)").arg(info.name).arg(info.symbol) : ""

    function openFor(index) {
        info = SchematicController.partAttributes(index)
        if (info.index === undefined)
            return
        partIndex = index
        nameField.text = info.name
        fieldModel.clear()
        for (const f of info.fields)
            fieldModel.append({ key: f.key, value: f.value, fallback: f["default"] })
        check()
        open()
        nameField.forceActiveFocus()
        nameField.selectAll()
    }

    function values() {
        const out = {}
        for (let i = 0; i < fieldModel.count; ++i)
            out[fieldModel.get(i).key] = fieldModel.get(i).value
        return out
    }

    function check() {
        problem = partIndex < 0 ? "" : SchematicController.checkAttributes(partIndex, nameField.text, values())
    }

    ListModel { id: fieldModel }

    onAccepted: {
        if (SchematicController.setAttributes(partIndex, nameField.text, values()) !== "")
            return
        partIndex = -1
    }
    onRejected: partIndex = -1

    Component.onCompleted: {
        const ok = standardButton(Dialog.Ok)
        if (ok) {
            ok.objectName = "attributeOk"
            ok.enabled = Qt.binding(() => root.problem === "")
        }
    }

    contentItem: ColumnLayout {
        spacing: 8

        Label {
            text: root.info.description || ""
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            font.pixelSize: 11
        }

        GridLayout {
            columns: 2
            columnSpacing: 8
            rowSpacing: 6
            Layout.fillWidth: true

            Label { text: qsTr("InstName") }
            TextField {
                id: nameField
                objectName: "attr_InstName"
                Layout.fillWidth: true
                selectByMouse: true
                onTextChanged: root.check()
                onAccepted: if (root.problem === "") root.accept()
            }

            Repeater {
                model: fieldModel
                delegate: Label {
                    required property string key
                    text: key
                    Layout.row: index + 1
                    Layout.column: 0
                    required property int index
                }
            }
            Repeater {
                model: fieldModel
                delegate: TextField {
                    required property int index
                    required property string key
                    required property string value
                    required property string fallback
                    objectName: "attr_" + key
                    Layout.row: index + 1
                    Layout.column: 1
                    Layout.fillWidth: true
                    selectByMouse: true
                    text: value
                    placeholderText: fallback
                    onTextEdited: {
                        fieldModel.setProperty(index, "value", text)
                        root.check()
                    }
                    onAccepted: if (root.problem === "") root.accept()
                }
            }
        }

        Label {
            objectName: "attributeProblem"
            visible: root.problem !== ""
            text: root.problem
            color: "#c0392b"
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
