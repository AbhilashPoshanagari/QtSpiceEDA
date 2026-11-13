import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Parameter sweep (M7): pick one block parameter and a list of values (SPICE numbers
// such as "1m 2m 5m"); "Run sweep" runs the diagram once per value, one run after
// another, and the waveform window overlays the runs with a label per run.
// The sweep is a run setting kept in the project (DiagramController.setSweep).
Dialog {
    id: root
    objectName: "sweepDialog"
    modal: true
    anchors.centerIn: Overlay.overlay
    width: 420
    title: qsTr("Parameter sweep")

    property real tEnd: 1.0
    property real dt: 0.001
    property string problem: ""

    function openFor(blockId) {
        problem = ""
        const blocks = DiagramController.sweepBlocks()
        blockBox.model = blocks
        let pick = DiagramController.sweepBlock !== "" ? DiagramController.sweepBlock : blockId
        blockBox.currentIndex = Math.max(0, blocks.indexOf(pick))
        refreshParameters(DiagramController.sweepParameter)
        values.text = DiagramController.sweepValues
        open()
        values.forceActiveFocus()
    }

    function refreshParameters(prefer) {
        const names = blockBox.currentText !== ""
                      ? DiagramController.sweepParameters(blockBox.currentText) : []
        parameterBox.model = names
        parameterBox.currentIndex = Math.max(0, names.indexOf(prefer))
    }

    function apply() {
        problem = DiagramController.setSweep(blockBox.currentText, parameterBox.currentText,
                                             values.text)
        return problem === ""
    }

    contentItem: GridLayout {
        columns: 2
        columnSpacing: 8
        rowSpacing: 6
        Label { text: qsTr("Block") }
        ComboBox {
            id: blockBox
            objectName: "sweepBlock"
            Layout.fillWidth: true
            onActivated: root.refreshParameters("")
        }
        Label { text: qsTr("Parameter") }
        ComboBox {
            id: parameterBox
            objectName: "sweepParameter"
            Layout.fillWidth: true
        }
        Label { text: qsTr("Values") }
        TextField {
            id: values
            objectName: "sweepValues"
            Layout.fillWidth: true
            placeholderText: qsTr("e.g. 1m 2m 5m")
            selectByMouse: true
            onAccepted: if (root.apply()) runButton.clicked()
        }
        Label {
            objectName: "sweepProblem"
            Layout.columnSpan: 2
            Layout.fillWidth: true
            visible: root.problem !== ""
            text: root.problem
            wrapMode: Text.Wrap
            color: "#d73a49"
        }
        Label {
            Layout.columnSpan: 2
            Layout.fillWidth: true
            visible: blockBox.count === 0
            text: qsTr("The diagram has no block with a numeric parameter.")
            wrapMode: Text.Wrap
            color: palette.placeholderText
        }
    }

    footer: DialogButtonBox {
        Button {
            id: runButton
            objectName: "sweepRunButton"
            text: qsTr("Run sweep")
            enabled: !DiagramController.running && blockBox.count > 0
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: {
                if (!root.apply())
                    return
                if (DiagramController.runSweep(root.tEnd, root.dt))
                    root.close()
                else
                    root.problem = DiagramController.runStatus
            }
        }
        Button {
            objectName: "sweepClearButton"
            text: qsTr("Clear sweep")
            DialogButtonBox.buttonRole: DialogButtonBox.ResetRole
            onClicked: {
                DiagramController.clearSweep()
                values.text = ""
                root.problem = ""
            }
        }
        Button {
            text: qsTr("Close")
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            onClicked: root.close()
        }
    }
}
