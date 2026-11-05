import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice

// Library browser (L8): search the models and subcircuits of the library folders,
// see a subcircuit's pins, and "Use with symbol..." to pair one with a symbol (fills
// SpiceModel and ModelFile; the schematic part picker places it). "Search paths..."
// edits the symbol, library and LTspice folders; the lists refresh when they change.
Item {
    id: root
    objectName: "libraryBrowser"

    property var results: []
    property int selectedIndex: -1
    readonly property var selected: selectedIndex >= 0 && selectedIndex < results.length
                                    ? results[selectedIndex] : null
    property string lastMessage: ""

    function search() {
        const keep = selected ? selected.name + "|" + selected.file : ""
        results = SchematicController.searchLibrary(searchField.text)
        selectedIndex = results.findIndex(e => e.name + "|" + e.file === keep)
    }

    function openUseWithSymbol() {
        if (!selected)
            return
        const suggested = SchematicController.suggestedSymbol(selected)
        const i = SchematicController.symbolNames.indexOf(suggested)
        symbolBox.currentIndex = i >= 0 ? i : 0
        symbolDialog.open()
    }

    function useWith(symbolName) {
        const r = SchematicController.useWithSymbol(selected, symbolName)
        lastMessage = r.ok
            ? qsTr("%1 with symbol %2: SpiceModel=%3, ModelFile=%4")
                  .arg(selected.name).arg(r.symbol).arg(r.attrs.SpiceModel).arg(r.attrs.ModelFile)
              + (r.message ? " (" + r.message + ")" : "")
            : r.message
        return r.ok
    }

    Component.onCompleted: search()

    Connections {
        target: SchematicController
        function onLibraryChanged() { root.search() }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: searchField
                objectName: "librarySearch"
                Layout.fillWidth: true
                placeholderText: qsTr("Search models and subcircuits")
                onTextChanged: root.search()
            }
            Button {
                objectName: "searchPathsButton"
                text: qsTr("Search paths…")
                onClicked: pathsDialog.open()
            }
        }
        Label {
            objectName: "libraryStatus"
            Layout.fillWidth: true
            elide: Text.ElideRight
            opacity: 0.7
            text: SchematicController.libraryStatus
        }

        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: resultList
                objectName: "libraryResults"
                SplitView.preferredWidth: 280
                SplitView.minimumWidth: 160
                clip: true
                model: root.results
                ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    required property int index
                    required property var modelData
                    width: ListView.view.width
                    highlighted: index === root.selectedIndex
                    text: modelData.name + "  —  " + modelData.type
                          + "  (" + modelData.fileName
                          + (modelData.section ? ", " + modelData.section : "") + ")"
                    onClicked: root.selectedIndex = index
                    onDoubleClicked: root.openUseWithSymbol()
                }
            }

            ColumnLayout {
                SplitView.fillWidth: true
                spacing: 4
                Label {
                    objectName: "selectedName"
                    font.bold: true
                    text: root.selected ? root.selected.name : qsTr("Nothing selected")
                }
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WrapAnywhere
                    visible: root.selected !== null
                    text: root.selected
                          ? (root.selected.kind === "subckt" ? qsTr("Subcircuit")
                                                             : qsTr("Model, type %1").arg(root.selected.type))
                            + "\n" + root.selected.file + ":" + root.selected.line
                            + (root.selected.section ? "\n" + qsTr(".lib section %1").arg(root.selected.section) : "")
                          : ""
                }
                Label {
                    visible: root.selected !== null && root.selected.kind === "subckt"
                    text: qsTr("Pins:")
                }
                ListView {
                    objectName: "pinList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: root.selected ? root.selected.pins : []
                    delegate: Label {
                        required property int index
                        required property string modelData
                        leftPadding: 8
                        text: (index + 1) + ". " + modelData
                    }
                }
                Button {
                    objectName: "useWithSymbolButton"
                    text: qsTr("Use with symbol…")
                    enabled: root.selected !== null
                    onClicked: root.openUseWithSymbol()
                }
                Label {
                    objectName: "useResult"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: root.lastMessage
                }
            }
        }
    }

    Dialog {
        id: symbolDialog
        objectName: "symbolDialog"
        title: qsTr("Use %1 with symbol").arg(root.selected ? root.selected.name : "")
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: root.useWith(symbolBox.currentText)

        ColumnLayout {
            ComboBox {
                id: symbolBox
                objectName: "symbolBox"
                Layout.preferredWidth: 260
                model: SchematicController.symbolNames
            }
            RowLayout {
                spacing: 24
                ColumnLayout {
                    visible: root.selected !== null && root.selected.kind === "subckt"
                    Label { text: qsTr("Subcircuit pins"); font.bold: true }
                    Repeater {
                        model: root.selected ? root.selected.pins : []
                        Label { required property string modelData; text: modelData }
                    }
                }
                ColumnLayout {
                    Label { text: qsTr("Symbol pins"); font.bold: true }
                    Repeater {
                        model: SchematicController.symbolPins(symbolBox.currentText)
                        Label { required property var modelData; text: modelData.name }
                    }
                }
            }
        }
    }

    FolderDialog {
        id: folderDialog
        property string kind: "symbols"
        onAccepted: {
            if (kind === "ltspice")
                SchematicController.setLtspiceFolderUrl(selectedFolder.toString())
            else
                SchematicController.addFolder(kind, selectedFolder.toString())
        }
    }

    component FolderList: ColumnLayout {
        id: folderList
        required property string kind
        required property string heading
        required property var folders
        Layout.fillWidth: true
        Label { text: folderList.heading; font.bold: true }
        Repeater {
            model: folderList.folders
            RowLayout {
                required property int index
                required property string modelData
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; elide: Text.ElideMiddle; text: modelData }
                Button {
                    text: qsTr("Remove")
                    onClicked: SchematicController.removeFolder(folderList.kind, index)
                }
            }
        }
        Button {
            text: qsTr("Add folder…")
            onClicked: { folderDialog.kind = folderList.kind; folderDialog.open() }
        }
    }

    Dialog {
        id: pathsDialog
        objectName: "pathsDialog"
        title: qsTr("Search paths")
        anchors.centerIn: parent
        width: Math.min(560, root.width)
        modal: true
        standardButtons: Dialog.Close

        ColumnLayout {
            anchors.fill: parent
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                opacity: 0.7
                text: qsTr("Built-in symbols come first, then your folders in order, then the "
                           + "LTspice folder. All folders are read-only.")
            }
            FolderList {
                kind: "symbols"
                heading: qsTr("Symbol folders (.asy)")
                folders: SchematicController.symbolPaths
            }
            FolderList {
                kind: "libraries"
                heading: qsTr("Library folders (.lib .sub .mod …)")
                folders: SchematicController.libraryPaths
            }
            Label { text: qsTr("LTspice library folder (optional)"); font.bold: true }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    objectName: "ltspiceFolderLabel"
                    Layout.fillWidth: true
                    elide: Text.ElideMiddle
                    text: SchematicController.ltspiceFolder || qsTr("(none)")
                }
                Button {
                    text: qsTr("Browse…")
                    onClicked: { folderDialog.kind = "ltspice"; folderDialog.open() }
                }
                Button {
                    text: qsTr("Clear")
                    enabled: SchematicController.ltspiceFolder !== ""
                    onClicked: SchematicController.setLtspiceFolderUrl("")
                }
            }
        }
    }
}
