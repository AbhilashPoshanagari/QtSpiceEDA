import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Make Symbol from Schematic (H2): asks for the symbol name; the sheet's port flags are
// the pins (inputs left, outputs right, bidir on top). Emits makeSymbol(name).
Dialog {
    id: root
    objectName: "makeSymbolDialog"
    title: qsTr("Make Symbol from Schematic")
    modal: true
    width: 380
    anchors.centerIn: parent
    standardButtons: Dialog.Cancel

    readonly property var ports: SchematicController.ports
    readonly property string problem: nameField.text.trim() === ""
                                      ? "" : SchematicController.symbolNameProblem(nameField.text)
    readonly property bool canMake: nameField.text.trim() !== "" && problem === ""
                                    && ports.length > 0

    signal makeSymbol(string name)

    function openWith(name) {
        nameField.text = name || ""
        open()
        nameField.forceActiveFocus()
        nameField.selectAll()
    }

    function accept_() {
        if (!canMake)
            return
        close()
        makeSymbol(nameField.text.trim())
    }

    contentItem: ColumnLayout {
        spacing: 6
        RowLayout {
            Label { text: qsTr("Symbol name:") }
            TextField {
                id: nameField
                objectName: "makeSymbolName"
                Layout.fillWidth: true
                selectByMouse: true
                onAccepted: root.accept_()
            }
        }
        Label {
            objectName: "makeSymbolProblem"
            visible: root.problem !== ""
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: "#c0392b"
            text: root.problem
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            font.pixelSize: 11
            text: root.ports.length === 0
                  ? qsTr("The schematic has no ports. Place port flags first.")
                  : qsTr("Pins: %1").arg(root.ports.map(p => p.name + " (" + p.direction + ")").join(", "))
        }
        Button {
            objectName: "makeSymbolDialogOk"
            Layout.alignment: Qt.AlignRight
            text: qsTr("Make Symbol")
            enabled: root.canMake
            onClicked: root.accept_()
        }
    }
}
