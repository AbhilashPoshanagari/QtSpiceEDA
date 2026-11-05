import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Block library (F5): the built-in blocks and the user's @block functions, each drawn
// in its shape and colour with input ports on the left and outputs on the right.
// Hover a block for its docstring and settings; click a user block to edit its code.
Item {
    id: root
    objectName: "blockLibrary"

    readonly property var builtinBlocks: DiagramController.library.filter(b => b.builtin)
    readonly property var userBlocks: DiagramController.library.filter(b => !b.builtin)

    signal editBlock(string name)

    component BlockTile: Item {
        id: tile
        objectName: "blockTile"

        required property var modelData
        readonly property string blockName: modelData.name
        readonly property string shape: modelData.shape
        readonly property var inputNames: modelData.inputs
        readonly property var outputNames: modelData.outputs
        readonly property bool builtin: modelData.builtin

        readonly property int portPitch: 16
        readonly property int ports: Math.max(1, inputNames.length, outputNames.length)
        readonly property real bodyHeight: Math.max(44, ports * portPitch + 12)
        readonly property real bodyWidth: shape === "rectangle" ? bodyHeight * 1.6 : bodyHeight
        readonly property real labelWidth: 44

        width: bodyWidth + 2 * labelWidth + 8
        height: bodyHeight + nameLabel.implicitHeight + 6

        // x offset of the body's edge at height y (in body coordinates): circles curve.
        function edgeInset(y) {
            if (shape !== "circle")
                return 0
            const r = bodyHeight / 2
            const dy = y - r
            return r - Math.sqrt(Math.max(0, r * r - dy * dy))
        }
        function portY(index, count) {
            return bodyHeight / 2 + (index - (count - 1) / 2) * portPitch
        }

        Rectangle {
            id: body
            x: tile.labelWidth + 4
            width: tile.bodyWidth
            height: tile.bodyHeight
            radius: tile.shape === "circle" ? width / 2 : 6
            color: Qt.lighter(tile.modelData.color, 1.6)
            border.color: tile.modelData.color
            border.width: hover.hovered ? 3 : 2

            Repeater {
                model: tile.inputNames
                delegate: Item {
                    required property int index
                    required property string modelData
                    readonly property real py: tile.portY(index, tile.inputNames.length)
                    Rectangle {
                        objectName: "inputPort"
                        property string portName: parent.modelData
                        x: tile.edgeInset(parent.py) - 4
                        y: parent.py - 4
                        width: 8; height: 8; radius: 4
                        color: palette.base
                        border.color: tile.modelData.color
                        border.width: 2
                    }
                    Label {
                        x: tile.edgeInset(parent.py) - tile.labelWidth - 7
                        width: tile.labelWidth
                        y: parent.py - height / 2
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideLeft
                        text: parent.modelData
                        font.pointSize: 8
                    }
                }
            }
            Repeater {
                model: tile.outputNames
                delegate: Item {
                    required property int index
                    required property string modelData
                    readonly property real py: tile.portY(index, tile.outputNames.length)
                    Rectangle {
                        objectName: "outputPort"
                        property string portName: parent.modelData
                        x: tile.bodyWidth - tile.edgeInset(parent.py) - 4
                        y: parent.py - 4
                        width: 8; height: 8; radius: 4
                        color: tile.modelData.color
                    }
                    Label {
                        x: tile.bodyWidth - tile.edgeInset(parent.py) + 7
                        width: tile.labelWidth
                        y: parent.py - height / 2
                        elide: Text.ElideRight
                        text: parent.modelData
                        font.pointSize: 8
                    }
                }
            }
        }

        Label {
            id: nameLabel
            anchors.top: body.bottom
            anchors.topMargin: 4
            anchors.horizontalCenter: body.horizontalCenter
            width: tile.width
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            text: tile.blockName
            font.bold: !tile.builtin
        }

        HoverHandler {
            id: hover
            cursorShape: tile.builtin ? Qt.OpenHandCursor : Qt.PointingHandCursor
        }
        TapHandler {
            onTapped: if (!tile.builtin) root.editBlock(tile.blockName)
        }
        // Drag a tile onto the block canvas to place the block.
        DragHandler {
            id: libraryDrag
            target: null
            onActiveChanged: {
                if (active) {
                    tile.moveProxy(centroid.scenePosition)
                    dragProxy.Drag.active = true
                } else {
                    dragProxy.Drag.drop()
                    dragProxy.Drag.active = false
                }
            }
            onCentroidChanged: if (active) tile.moveProxy(centroid.scenePosition)
        }

        function moveProxy(scenePos) {
            const p = dragProxy.parent.mapFromItem(null, scenePos.x, scenePos.y)
            dragProxy.x = p.x - dragProxy.width / 2
            dragProxy.y = p.y - dragProxy.height / 2
        }

        // What follows the cursor while dragging; lives in the window overlay so it can
        // leave the library's scroll area.
        Rectangle {
            id: dragProxy
            objectName: "dragProxy"
            readonly property string blockName: tile.blockName
            parent: Overlay.overlay
            visible: libraryDrag.active
            width: tile.bodyWidth
            height: tile.bodyHeight
            radius: tile.shape === "circle" ? width / 2 : 6
            color: Qt.lighter(tile.modelData.color, 1.6)
            border.color: tile.modelData.color
            border.width: 2
            opacity: 0.85
            Drag.keys: ["qtspice/block"]
            Drag.hotSpot.x: width / 2
            Drag.hotSpot.y: height / 2
            Label {
                anchors.centerIn: parent
                text: tile.blockName
                font.bold: true
            }
        }
        ToolTip.visible: hover.hovered
        ToolTip.delay: 400
        ToolTip.text: {
            const lines = [tile.blockName + (tile.modelData.stateful ? qsTr(" (stateful)") : "")]
            if (tile.modelData.doc.length > 0)
                lines.push(tile.modelData.doc)
            const settings = tile.modelData.settings.map(s => s.name + " = " + JSON.stringify(s.value))
            if (settings.length > 0)
                lines.push(qsTr("Settings: ") + settings.join(", "))
            lines.push(tile.builtin ? qsTr("Drag onto the canvas to place it")
                                    : qsTr("Click to edit, drag onto the canvas to place it"))
            return lines.join("\n")
        }
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 6

            Label {
                Layout.leftMargin: 6
                Layout.topMargin: 4
                text: qsTr("My blocks")
                font.bold: true
            }
            Label {
                Layout.leftMargin: 6
                visible: root.userBlocks.length === 0
                text: qsTr("Write a function with @block in the editor below.")
                color: palette.placeholderText
            }
            Flow {
                objectName: "userBlocks"
                Layout.fillWidth: true
                Layout.leftMargin: 6
                spacing: 8
                Repeater {
                    model: root.userBlocks
                    delegate: BlockTile {}
                }
            }

            Label {
                Layout.leftMargin: 6
                Layout.topMargin: 6
                text: qsTr("Built-in")
                font.bold: true
            }
            Flow {
                objectName: "builtinBlocks"
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.bottomMargin: 6
                spacing: 8
                Repeater {
                    model: root.builtinBlocks
                    delegate: BlockTile {}
                }
            }
        }
    }
}
