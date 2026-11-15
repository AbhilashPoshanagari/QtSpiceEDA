import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Schematic from Netlist (N3): start(text) draws the netlist on the schematic sheet
// (SchematicController.generateFromNetlist, one undo step). A sheet that is not empty
// is replaced only after the user agrees. A netlist with errors is not drawn: the
// errors are listed in a dialog (and failed(problems) lets Main.qml mark them in the
// netlist editor). drawn(result) follows a drawn sheet, so Main.qml can show it.
Item {
    id: root
    visible: false

    signal drawn(var result)
    signal failed(var problems)

    property string pendingText: ""

    function start(text) {
        if (SchematicController.sheetEmpty) {
            generate(text)
            return
        }
        pendingText = text
        replaceDialog.open()
    }

    function generate(text) {
        const result = SchematicController.generateFromNetlist(text)
        if (result.ok) {
            drawn(result)
            return
        }
        errorsDialog.problems = result.problems
        errorsDialog.open()
        failed(result.problems)
    }

    Dialog {
        id: replaceDialog
        objectName: "replaceSheetDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        title: qsTr("Replace the schematic?")
        modal: true
        width: 420
        Label {
            width: 380
            wrapMode: Text.WordWrap
            text: qsTr("The schematic sheet is not empty. Draw the netlist instead of it? "
                       + "Undo (Ctrl+Z) in the schematic brings the current sheet back.")
        }
        footer: DialogButtonBox {
            Button {
                objectName: "replaceSheetButton"
                text: qsTr("Replace")
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            Button {
                objectName: "keepSheetButton"
                text: qsTr("Cancel")
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
        }
        onAccepted: root.generate(root.pendingText)
    }

    Dialog {
        id: errorsDialog
        objectName: "netlistErrorsDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        title: qsTr("Schematic not drawn")
        modal: true
        width: 520
        standardButtons: Dialog.Ok
        property var problems: []
        ColumnLayout {
            width: 480
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: qsTr("Only a netlist without errors can be drawn. Fix these in the "
                           + "Netlist tab, then try again:")
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(errorText.implicitHeight + 8, 240)
                Label {
                    id: errorText
                    objectName: "netlistErrorsText"
                    width: 470
                    wrapMode: Text.WordWrap
                    color: "#d73a49"
                    text: errorsDialog.problems
                          .filter(p => p.severity === "error")
                          .map(p => "✖ " + p.message).join("\n")
                }
            }
        }
    }
}
