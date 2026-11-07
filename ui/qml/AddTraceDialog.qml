import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Add Trace (W8), as in LTspice: the result's vectors with a wildcard filter (* and ?;
// plain text matches anywhere); click a name to insert it into the expression at the
// caret; Plot puts the expression into the selected pane. Expressions use LTspice
// syntax (WaveformController / core/waveform/expr.py) and are checked as you type.
// editTrace() opens the dialog on an existing trace name to change its expression.
// "Plot definitions…" edits the .func / .param lines (W9); OK is one undo step.
// After a combined run (U4) the vectors are grouped: Signals, then one heading per
// physical component (inertia.A.w under "inertia").
Dialog {
    id: root
    objectName: "addTraceDialog"
    modal: true
    anchors.centerIn: Overlay.overlay
    width: 520
    height: 480
    title: editing ? qsTr("Edit trace") : qsTr("Add trace")

    property bool editing: false
    property string oldName: ""
    property int editPane: 0
    property string problem: ""
    // Re-checked when the text, the result or the plot definitions change.
    readonly property string check: {
        WaveformController.plotDefs
        WaveformController.resultTraces
        return WaveformController.hasData ? WaveformController.checkExpression(expression.text)
                                          : ""
    }

    function openFor(pane) {
        editing = false
        oldName = ""
        expression.text = ""
        filter.text = ""
        paneBox.currentIndex = Math.max(0, Math.min(pane, WaveformController.paneCount - 1))
        problem = ""
        open()
        expression.forceActiveFocus()
    }

    function editTrace(pane, name) {
        editing = true
        oldName = name
        editPane = pane
        expression.text = name
        filter.text = ""
        paneBox.currentIndex = pane
        problem = ""
        open()
        expression.forceActiveFocus()
    }

    function insertName(name) {
        const at = expression.cursorPosition
        expression.insert(at, name)
        expression.cursorPosition = at + name.length
        expression.forceActiveFocus()
    }

    function apply() {
        const text = expression.text.trim()
        const err = editing ? WaveformController.editTrace(editPane, oldName, text)
                            : WaveformController.plotExpression(text, paneBox.currentIndex)
        problem = err
        if (err === "")
            close()
        return err === ""
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            Label { text: qsTr("Only list vectors matching:") }
            TextField {
                id: filter
                objectName: "vectorFilter"
                Layout.fillWidth: true
                placeholderText: qsTr("e.g. V(*) or I(r*)")
                selectByMouse: true
            }
        }

        Frame {
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 1
            ListView {
                id: vectors
                objectName: "vectorList"
                anchors.fill: parent
                clip: true
                model: {
                    WaveformController.resultTraces  // re-read on a new result
                    return WaveformController.groupedVectorNames(filter.text)
                }
                delegate: Column {
                    id: entry
                    required property string modelData
                    required property int index
                    readonly property string group: WaveformController.vectorGroup(modelData)
                    readonly property bool firstOfGroup: group !== "" && (index === 0
                        || WaveformController.vectorGroup(ListView.view.model[index - 1]) !== group)
                    width: ListView.view.width
                    Label {
                        objectName: "vectorGroup_" + entry.group
                        visible: entry.firstOfGroup
                        height: visible ? implicitHeight + 6 : 0
                        leftPadding: 6
                        topPadding: 4
                        text: entry.group
                        font.bold: true
                        font.pointSize: 9
                    }
                    ItemDelegate {
                        objectName: "vector_" + entry.modelData
                        width: parent.width
                        height: implicitHeight - 8
                        leftPadding: entry.group !== "" ? 20 : padding
                        text: entry.modelData
                        font.pointSize: 9
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("Click to insert into the expression")
                        onClicked: root.insertName(entry.modelData)
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
        }

        Label {
            Layout.fillWidth: true
            text: qsTr("Expression (LTspice syntax: V(a,b), I(R1), & | ^ ! for Boolean, ** for power, d() derivative):")
            wrapMode: Text.WordWrap
            font.pointSize: 8
        }
        TextField {
            id: expression
            objectName: "expressionField"
            Layout.fillWidth: true
            selectByMouse: true
            font.family: "Consolas"
            placeholderText: qsTr("e.g. V(out)/V(in)")
            onTextChanged: root.problem = ""
            onAccepted: root.apply()
        }
        Label {
            objectName: "expressionError"
            Layout.fillWidth: true
            text: root.problem !== "" ? root.problem
                  : (expression.text.trim() !== "" ? root.check : "")
            color: "#d73a49"
            wrapMode: Text.WordWrap
            font.pointSize: 8
        }

        RowLayout {
            Layout.fillWidth: true
            Label { text: qsTr("Plot into:"); visible: !root.editing }
            ComboBox {
                id: paneBox
                objectName: "paneBox"
                visible: !root.editing
                model: {
                    const out = []
                    for (let i = 0; i < WaveformController.paneCount; ++i)
                        out.push(qsTr("Pane %1").arg(i + 1))
                    return out
                }
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "plotDefsButton"
                text: qsTr("Plot definitions…")
                onClicked: defsDialog.openEditor()
            }
        }
    }

    footer: DialogButtonBox {
        Button {
            objectName: "plotButton"
            text: root.editing ? qsTr("Apply") : qsTr("Plot")
            enabled: expression.text.trim() !== "" && root.check === ""
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
        }
        Button {
            text: qsTr("Cancel")
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
        onAccepted: root.apply()
        onRejected: root.close()
    }

    // ---- W9: the plot definitions file (.func and .param lines)
    Dialog {
        id: defsDialog
        objectName: "plotDefsDialog"
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: 560
        height: 420
        title: qsTr("Plot definitions")
        standardButtons: Dialog.Ok | Dialog.Cancel | Dialog.RestoreDefaults

        property var problems: WaveformController.checkPlotDefs(defsText.text)

        function openEditor() {
            defsText.text = WaveformController.plotDefs
            open()
            defsText.forceActiveFocus()
        }

        onAccepted: WaveformController.setPlotDefs(defsText.text)
        onReset: defsText.text = WaveformController.defaultPlotDefs()

        ColumnLayout {
            anchors.fill: parent
            Label {
                Layout.fillWidth: true
                text: qsTr(".func name(args) body and .param name=value lines, as in SPICE. They are saved with the project.")
                wrapMode: Text.WordWrap
                font.pointSize: 8
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TextArea {
                    id: defsText
                    objectName: "plotDefsText"
                    font.family: "Consolas"
                    selectByMouse: true
                    wrapMode: TextEdit.NoWrap
                }
            }
            Label {
                objectName: "plotDefsProblems"
                Layout.fillWidth: true
                visible: text !== ""
                text: defsDialog.problems.map(p => qsTr("line %1: %2").arg(p.line).arg(p.message))
                                         .join("\n")
                color: defsDialog.problems.some(p => p.severity === "error") ? "#d73a49" : "#9a6700"
                wrapMode: Text.WordWrap
                font.pointSize: 8
            }
        }
    }
}
