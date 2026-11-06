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

    function focusSearch() {
        search.forceActiveFocus()
        search.selectAll()
    }

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

        // Wide pane: the list on the left, the description and preview on the right, so
        // both use the full height. Narrow pane: the list above a preview that shrinks
        // first, so the search results always keep at least a few rows.
        GridLayout {
            id: body
            objectName: "partPickerBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            readonly property bool wide: width >= 360
            readonly property real minListHeight: 96
            columns: wide ? 2 : 1
            rowSpacing: 6
            columnSpacing: 8

            ListView {
                id: list
                objectName: "partList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: body.minListHeight
                Layout.preferredWidth: body.wide ? body.width * 0.45 : -1
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

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: body.wide
                Layout.preferredWidth: body.wide ? body.width * 0.55 : -1
                spacing: 6

                Label {
                    id: description
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
                    Layout.fillHeight: body.wide
                    // Narrow pane: up to 150 px, but never taking the list's minimum rows.
                    Layout.preferredHeight: body.wide ? -1
                        : Math.max(0, Math.min(150, body.height - body.minListHeight
                                                    - description.height - 2 * body.rowSpacing))
                    visible: body.wide || Layout.preferredHeight >= 40
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
    }
}
