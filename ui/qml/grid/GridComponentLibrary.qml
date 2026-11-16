import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// The designer's component library (D6): the v0.4 model classes only, plus the bus
// and the Regulator (a single-phase transformer + RegControl). Drag an item onto the
// canvas (a bus) or onto a bus (source, load, capacitor, PV system); a branch item
// (line, switch, transformer, regulator) starts the branch tool. A click does the
// same without dragging: `activated(kind)` (GridDiagramEditor.startPlace). The line
// codes of the design are listed below; a click selects one for the properties panel.
// The tooltip of each item names the values a user must type (rule 24: nothing is
// invented, there are no catalogs before v0.6).
Pane {
    id: root
    objectName: "gridComponentLibrary"
    required property var designer
    signal activated(string kind)
    padding: 4

    ColumnLayout {
        anchors.fill: parent
        spacing: 4

        Label {
            text: qsTr("Components")
            font.bold: true
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            font.pointSize: 8
            color: palette.placeholderText
            text: root.designer.hasDesign
                  ? qsTr("Drag onto the canvas (bus) or onto a bus. Branches: click bus A, then bus B.")
                  : qsTr("Create or open a design first (File > New Grid Design…).")
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 4
            rowSpacing: 4
            enabled: root.designer.hasDesign
            Repeater {
                model: root.designer.libraryItems
                delegate: Rectangle {
                    id: item
                    required property var modelData
                    readonly property string kind: modelData.kind
                    objectName: "libraryItem_" + kind
                    Layout.fillWidth: true
                    implicitHeight: 34
                    radius: 3
                    color: hover.hovered ? palette.highlight : palette.alternateBase
                    border.color: palette.mid
                    opacity: enabled ? 1 : 0.5

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 6
                        Image {
                            Layout.preferredWidth: 24
                            Layout.preferredHeight: 24
                            sourceSize: Qt.size(32, 32)
                            source: "symbols/" + item.kind + ".svg"
                        }
                        Label {
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            text: item.modelData.label
                            color: hover.hovered ? palette.highlightedText : palette.text
                        }
                    }

                    // What follows the pointer while dragging; Drag.source is this proxy.
                    Rectangle {
                        id: proxy
                        readonly property string kind: item.kind
                        width: 30; height: 30; radius: 4
                        color: "#e0e8ff"
                        border.color: "#1c3d8c"
                        visible: dragHandler.active
                        Drag.active: dragHandler.active
                        Drag.keys: ["qtspice-grid-item"]
                        Drag.hotSpot: Qt.point(15, 15)
                        Image {
                            anchors.centerIn: parent
                            width: 24; height: 24
                            sourceSize: Qt.size(32, 32)
                            source: "symbols/" + item.kind + ".svg"
                        }
                        states: State {
                            when: dragHandler.active
                            ParentChange { target: proxy; parent: Overlay.overlay }
                        }
                    }
                    DragHandler {
                        id: dragHandler
                        target: proxy
                        onActiveChanged: {
                            if (!active) {
                                proxy.Drag.drop()
                                proxy.x = 0
                                proxy.y = 0
                            }
                        }
                    }
                    HoverHandler { id: hover; cursorShape: Qt.OpenHandCursor }
                    TapHandler { onTapped: root.activated(item.kind) }
                    ToolTip.visible: hover.hovered
                    ToolTip.delay: 600
                    ToolTip.text: item.modelData.required.length > 0
                                  ? qsTr("%1. You type: %2").arg(item.modelData.label)
                                        .arg(item.modelData.required.join(", "))
                                  : item.modelData.label
                }
            }
        }

        Label {
            Layout.topMargin: 8
            text: qsTr("Line codes in this design")
            font.bold: true
            visible: root.designer.hasDesign
        }
        ListView {
            id: codes
            objectName: "designerLineCodes"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.designer.hasDesign
            clip: true
            model: root.designer.lineCodes
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property var modelData
                width: ListView.view.width
                highlighted: modelData.selected
                text: modelData.name + "  (" + qsTr("%n phase(s)", "", modelData.phases) + ")"
                      + (modelData.severity === "error" ? "  !" : "")
                onClicked: root.designer.select(modelData.id)
            }
            Label {
                anchors.centerIn: parent
                visible: codes.count === 0
                text: qsTr("None yet: add one with “Line code”.")
                color: palette.placeholderText
                font.pointSize: 8
            }
        }
    }
}
