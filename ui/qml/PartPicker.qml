import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Part picker (S6): search the symbol library, preview a symbol, click to place it.
// Clicking a name emits place(name); the schematic canvas then places that part on
// every click until Esc. Ground and net labels have their own tools.
Pane {
    id: root
    objectName: "partPicker"
    padding: 6

    signal place(string name)

    property string current: list.currentIndex >= 0 && list.currentIndex < filtered.length
                             ? filtered[list.currentIndex] : ""
    property var names: SchematicController.placeableSymbols()
    readonly property var filtered: {
        const key = search.text.trim().toLowerCase()
        return names.filter(n => key === "" || n.toLowerCase().indexOf(key) >= 0)
    }

    Connections {
        target: SchematicController
        function onSymbolsChanged() { root.names = SchematicController.placeableSymbols() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 6

        TextField {
            id: search
            objectName: "partSearch"
            Layout.fillWidth: true
            placeholderText: qsTr("Search symbols")
            selectByMouse: true
            onTextChanged: list.currentIndex = root.filtered.length > 0 ? 0 : -1
            Keys.onReturnPressed: if (root.current !== "") root.place(root.current)
            Keys.onDownPressed: list.incrementCurrentIndex()
            Keys.onUpPressed: list.decrementCurrentIndex()
        }

        ListView {
            id: list
            objectName: "partList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: root.filtered
            currentIndex: 0
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property string modelData
                required property int index
                objectName: "part_" + modelData
                width: ListView.view.width
                text: modelData
                highlighted: ListView.isCurrentItem
                onHoveredChanged: if (hovered) list.currentIndex = index
                onClicked: {
                    list.currentIndex = index
                    root.place(modelData)
                }
            }
        }

        Label {
            objectName: "partDescription"
            Layout.fillWidth: true
            text: root.current !== "" ? SchematicController.symbolDescription(root.current) : ""
            wrapMode: Text.WordWrap
            font.pixelSize: 11
        }

        // Preview: the symbol scaled to fit the box.
        Rectangle {
            id: previewBox
            objectName: "partPreview"
            Layout.fillWidth: true
            Layout.preferredHeight: 150
            color: "#fbfbf7"
            border.color: "#d0d0c8"
            clip: true
            readonly property var geo: root.current !== ""
                                       ? SchematicController.symbolGeometry(root.current) : null
            readonly property real boxW: geo ? Math.max(geo.box[2] - geo.box[0], 16) : 16
            readonly property real boxH: geo ? Math.max(geo.box[3] - geo.box[1], 16) : 16
            readonly property real fit: Math.min((width - 24) / boxW, (height - 24) / boxH, 3)

            Item {
                visible: previewBox.geo !== null
                x: (previewBox.width - previewBox.boxW * previewBox.fit) / 2
                   - (previewBox.geo ? previewBox.geo.box[0] : 0) * previewBox.fit
                y: (previewBox.height - previewBox.boxH * previewBox.fit) / 2
                   - (previewBox.geo ? previewBox.geo.box[1] : 0) * previewBox.fit
                scale: previewBox.fit
                transformOrigin: Item.TopLeft
                SymbolItem {
                    symbolName: root.current
                    penWidth: 1.5 / previewBox.fit
                }
            }
        }
    }
}
