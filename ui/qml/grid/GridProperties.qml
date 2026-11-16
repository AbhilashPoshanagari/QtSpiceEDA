import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Properties of the one selected bus or element (D6), from
// GridDiagramController.propertySheet: one row per field in canonical units, with the
// field's diagnostic inline under it. A field is applied when editing finishes (Enter
// or leaving it): text fields through setProperty ("" = not set), choices and check
// boxes at once, terminals ("671.1.2.3", "" = disconnected) through setTerminal, the
// name through rename. Every edit is one undo step on the designer's stack (rule 22).
// With nothing selected it shows the design: name, base frequency, file.
Pane {
    id: root
    objectName: "gridProperties"
    required property var designer
    padding: 4

    readonly property string selected: designer.selection.length === 1 ? designer.selection[0] : ""
    property var elementDiagnostics: []

    function refreshDiagnostics() {
        elementDiagnostics = selected !== "" ? designer.diagnosticsFor(selected) : []
    }
    onSelectedChanged: refreshDiagnostics()
    Connections {
        target: root.designer
        function onDiagnosticsChanged() { root.refreshDiagnostics() }
    }
    function severityColor(sev) {
        return sev === "error" ? "#d73a49" : sev === "warning" ? "#9a6700" : "#0969da"
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 4

        Label {
            objectName: "gridPropertiesTitle"
            Layout.fillWidth: true
            elide: Text.ElideRight
            font.bold: true
            text: !root.designer.hasDesign ? qsTr("Properties")
                  : root.designer.selection.length > 1
                    ? qsTr("%n items selected", "", root.designer.selection.length)
                    : root.selected !== "" ? root.selected : qsTr("Design")
        }

        // nothing selected: the design itself
        GridLayout {
            Layout.fillWidth: true
            visible: root.designer.hasDesign && root.designer.selection.length === 0
            columns: 2
            columnSpacing: 8
            Label { text: qsTr("name"); color: palette.placeholderText }
            Label { text: root.designer.designName }
            Label { text: qsTr("base frequency"); color: palette.placeholderText }
            Label { text: root.designer.baseFrequency + " Hz" }
            Label { text: qsTr("file"); color: palette.placeholderText }
            Label {
                Layout.fillWidth: true
                elide: Text.ElideMiddle
                text: root.designer.filePath !== "" ? root.designer.filePath : qsTr("not saved")
            }
            Label {
                Layout.columnSpan: 2
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.pointSize: 8
                color: palette.placeholderText
                text: qsTr("Select a bus or element on the canvas to edit it.")
            }
        }

        // diagnostics of the element that belong to no single field
        Repeater {
            model: root.elementDiagnostics
            delegate: Label {
                required property var modelData
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font.pointSize: 8
                color: root.severityColor(modelData.severity)
                text: modelData.severity + ": " + modelData.message
            }
        }

        ScrollView {
            id: scroller
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: scroller.availableWidth
                spacing: 2
                Repeater {
                    objectName: "gridPropertyRows"
                    model: root.designer.propertySheet
                    delegate: ColumnLayout {
                        id: row
                        required property var modelData
                        readonly property string field: modelData.field
                        Layout.fillWidth: true
                        spacing: 0
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Label {
                                Layout.preferredWidth: 110
                                elide: Text.ElideRight
                                text: row.modelData.label
                                color: row.modelData.diagnostic !== "" ? "#d73a49" : palette.text
                                ToolTip.visible: labelHover.hovered
                                ToolTip.delay: 600
                                ToolTip.text: row.field
                                HoverHandler { id: labelHover }
                            }
                            Loader {
                                Layout.fillWidth: true
                                sourceComponent: row.modelData.type === "choice" ? choiceEditor
                                               : row.modelData.type === "bool" ? boolEditor
                                               : textEditor
                            }
                            Label {
                                Layout.preferredWidth: 42
                                text: row.modelData.unit
                                color: palette.placeholderText
                                font.pointSize: 8
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            Layout.leftMargin: 114
                            visible: row.modelData.diagnostic !== ""
                            wrapMode: Text.WordWrap
                            font.pointSize: 8
                            color: "#d73a49"
                            text: row.modelData.diagnostic
                        }

                        Component {
                            id: textEditor
                            TextField {
                                objectName: "gridProperty_" + row.field
                                text: row.modelData.value
                                readOnly: !(row.modelData.editable || row.field === "name"
                                            || row.modelData.type === "terminal")
                                placeholderText: row.modelData.editable ? qsTr("not set") : ""
                                selectByMouse: true
                                function commit() {
                                    if (readOnly || text === row.modelData.value)
                                        return
                                    if (row.field === "name")
                                        root.designer.rename(root.selected, text.toLowerCase())
                                    else if (row.modelData.type === "terminal")
                                        root.designer.setTerminal(root.selected, row.field, text)
                                    else
                                        root.designer.setProperty(root.selected, row.field, text)
                                }
                                onEditingFinished: commit()
                                ToolTip.visible: hovered && row.modelData.type === "terminal"
                                ToolTip.delay: 600
                                ToolTip.text: qsTr("bus.node.node… (e.g. 671.1.2.3); empty disconnects")
                            }
                        }
                        Component {
                            id: choiceEditor
                            ComboBox {
                                objectName: "gridProperty_" + row.field
                                model: row.modelData.choices
                                enabled: row.modelData.editable
                                currentIndex: row.modelData.choices.indexOf(row.modelData.value)
                                displayText: currentIndex < 0 ? qsTr("not set") : currentText
                                onActivated: index => {
                                    const v = row.modelData.choices[index]
                                    if (v !== row.modelData.value)
                                        root.designer.setProperty(root.selected, row.field, v)
                                }
                            }
                        }
                        Component {
                            id: boolEditor
                            CheckBox {
                                objectName: "gridProperty_" + row.field
                                // a switch's "closed" goes through setSwitchOpen
                                enabled: row.modelData.editable || row.field === "closed"
                                checked: row.modelData.value === "true"
                                onToggled: row.field === "closed"
                                           ? root.designer.setSwitchOpen(root.selected, !checked)
                                           : root.designer.setProperty(root.selected, row.field, checked)
                            }
                        }
                    }
                }
            }
        }
    }
}
