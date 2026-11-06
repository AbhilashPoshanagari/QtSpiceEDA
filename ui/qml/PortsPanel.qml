import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Ports of the schematic (H1): one row per port flag with its direction, net and any
// problem. Click a row to select the flag on the sheet; double-click to edit it.
// The ports become the pins of a circuit symbol: "Make Symbol..." (H2).
Pane {
    id: root
    objectName: "portsPanel"
    padding: 6

    signal editPort(int index)
    signal makeSymbolRequested()

    readonly property var rows: SchematicController.ports

    function glyph(direction) {
        return { "in": "▶", "out": "◀", "bidir": "◆" }[direction] || ""
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: qsTr("Ports (%1)").arg(root.rows.length)
                font.bold: true
                Layout.fillWidth: true
            }
            Button {
                objectName: "makeSymbolButton"
                text: qsTr("Make Symbol…")
                enabled: root.rows.length > 0
                ToolTip.visible: hovered
                ToolTip.text: qsTr("Turn this schematic into a symbol (.sub + .asy) for other schematics")
                onClicked: root.makeSymbolRequested()
            }
        }
        Label {
            objectName: "portsEmpty"
            visible: root.rows.length === 0
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            font.pixelSize: 11
            text: qsTr("No ports. Use the Port tool to mark the nets that become the pins of a symbol.")
        }

        ListView {
            id: list
            objectName: "portList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: root.rows
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property var modelData
                objectName: "portRow_" + modelData.name
                width: ListView.view.width
                highlighted: modelData.selected
                padding: 4
                contentItem: ColumnLayout {
                    spacing: 0
                    RowLayout {
                        Label { text: root.glyph(modelData.direction); color: "#1c3d8c" }
                        Label { text: modelData.name; font.bold: true }
                        Label {
                            text: modelData.direction
                            color: "#606060"
                            font.pixelSize: 11
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: modelData.net !== "" ? qsTr("net %1").arg(modelData.net) : ""
                            color: "#606060"
                            font.pixelSize: 11
                        }
                    }
                    Label {
                        objectName: "portMessage"
                        visible: modelData.message !== ""
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        font.pixelSize: 11
                        text: (modelData.severity === "error" ? "✖ " : "⚠ ") + modelData.message
                        color: modelData.severity === "error" ? "#c0392b" : "#a0522d"
                    }
                }
                onClicked: SchematicController.select("port", modelData.index, false)
                onDoubleClicked: root.editPort(modelData.index)
            }
        }
    }
}
