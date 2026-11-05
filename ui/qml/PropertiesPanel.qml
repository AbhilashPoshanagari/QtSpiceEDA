import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Settings of the one selected block. A field is applied when editing finishes
// (Enter or leaving the field); text that does not fit the setting's type is refused
// and the reason is shown below. Every change can be undone.
Pane {
    id: root
    objectName: "propertiesPanel"
    padding: 8

    readonly property var selection: DiagramController.selectedBlock
    readonly property bool hasBlock: selection.id !== undefined

    ColumnLayout {
        anchors.fill: parent
        spacing: 6

        Label {
            objectName: "propertiesTitle"
            Layout.fillWidth: true
            text: root.hasBlock ? root.selection.id + "  (" + root.selection.spec + ")"
                                : qsTr("Select one block to edit its settings")
            font.bold: root.hasBlock
            wrapMode: Text.Wrap
            color: root.hasBlock ? palette.windowText : palette.placeholderText
        }
        Label {
            Layout.fillWidth: true
            visible: root.hasBlock && root.selection.doc !== ""
            text: root.hasBlock ? root.selection.doc : ""
            wrapMode: Text.Wrap
            font.pointSize: 8
            color: palette.placeholderText
        }
        Label {
            visible: root.hasBlock && root.selection.settings.length === 0
            text: qsTr("No settings")
            color: palette.placeholderText
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 6
            rowSpacing: 4
            Repeater {
                model: root.hasBlock ? root.selection.settings : []
                delegate: RowLayout {
                    id: row
                    required property var modelData
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    Label {
                        text: row.modelData.name
                        Layout.preferredWidth: 80
                        elide: Text.ElideRight
                    }
                    TextField {
                        objectName: "setting_" + row.modelData.name
                        Layout.fillWidth: true
                        text: row.modelData.text
                        selectByMouse: true
                        onEditingFinished: {
                            if (text !== row.modelData.text)
                                DiagramController.setSetting(root.selection.id, row.modelData.name, text)
                        }
                    }
                }
            }
        }

        Label {
            objectName: "propertiesHint"
            Layout.fillWidth: true
            visible: root.hasBlock && DiagramController.hint !== ""
            text: DiagramController.hint
            wrapMode: Text.Wrap
            color: "#d73a49"
        }

        Item { Layout.fillHeight: true }
    }
}
