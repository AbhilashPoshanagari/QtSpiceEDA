import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    width: 1280
    height: 800
    visible: true
    title: (ProjectManager.modified ? "*" : "") + ProjectManager.fileName + " — QtSpice"

    property bool quitting: false
    property var pendingAction: null  // runs once unsaved changes are saved or discarded

    Component.onCompleted: {
        Simulator.attachWaveforms(WaveformModel)
        DiagramController.attachWaveforms(WaveformModel)
        ProjectManager.attach(Simulator, DiagramController, WaveformModel)
        ProjectManager.attachSchematic(SchematicController)
        SchematicController.attachSimulator(Simulator)
        SchematicController.attachWaveforms(WaveformModel)
        WaveformController.attach(WaveformModel)
        WaveformController.attachProbes(SchematicController)
        ProjectManager.attachPlots(WaveformController)
    }

    onClosing: close => {
        if (ProjectManager.modified && !quitting) {
            close.accepted = false
            whenSaved(() => { window.quitting = true; window.close() })
        }
    }

    // ---- projects (F7)

    // Run `action` now, or after asking what to do with unsaved changes.
    function whenSaved(action) {
        if (!ProjectManager.modified) {
            action()
            return
        }
        pendingAction = action
        unsavedDialog.open()
    }
    // Save, asking for a file name if the project has none; then run `then`.
    function saveProject(then) {
        functionEditor.apply()  // a pending edit of the function editor
        if (ProjectManager.save()) {
            if (then)
                then()
        } else if (ProjectManager.filePath === "") {
            saveAs(then)
        }
    }
    function saveAs(then) {
        functionEditor.apply()
        saveDialog.then = then || null
        saveDialog.open()
    }
    function newProject() {
        whenSaved(() => ProjectManager.newProject())
    }
    // Open a project (.qsp) or a netlist (.cir, .net, .sp) file.
    function openFile(url) {
        if (ProjectManager.isProjectFile(url))
            whenSaved(() => ProjectManager.open(url))
        else
            netlistEditor.loadUrl(url)
    }

    // ---- circuit symbols (H2)
    //
    // makeSymbol() writes <name>.sub + <name>.asy into the project folder. An unsaved
    // project is saved first (Save As); an existing pair is replaced only after asking.
    function makeSymbol(request, overwrite) {
        const r = request.source === "netlist"
                ? SchematicController.makeSymbolFromNetlist(request.text, request.name,
                                                            request.ports, overwrite)
                : SchematicController.makeSymbol(request.name, overwrite)
        if (!r.ok && r.needsSave) {
            symbolResult.show(qsTr("Save the project"), r.message)
            saveAs(() => window.makeSymbol(request, false))
            return
        }
        if (!r.ok && r.exists) {
            replaceSymbolDialog.request = request
            replaceSymbolDialog.message = r.message
            replaceSymbolDialog.open()
            return
        }
        const warnings = r.warnings && r.warnings.length > 0
                         ? "\n\n" + qsTr("Warnings:") + "\n" + r.warnings.join("\n") : ""
        symbolResult.show(r.ok ? qsTr("Symbol made") : qsTr("Symbol not made"), r.message + warnings)
    }

    MakeSymbolDialog {
        id: makeSymbolDialog
        onMakeSymbol: name => window.makeSymbol({ source: "schematic", name: name }, false)
    }

    NetlistPortsDialog {
        id: netlistPortsDialog
        onMakeSymbol: (name, ports) => window.makeSymbol(
                          { source: "netlist", name: name, ports: ports,
                            text: netlistPortsDialog.netlistText }, false)
    }

    Dialog {
        id: replaceSymbolDialog
        objectName: "replaceSymbolDialog"
        property var request: null
        property string message: ""
        title: qsTr("Replace symbol?")
        width: 420
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Yes | Dialog.No
        Label {
            width: 380
            wrapMode: Text.WordWrap
            text: qsTr("%1. Replace them? Placed copies will use the new pins.")
                  .arg(replaceSymbolDialog.message)
        }
        onAccepted: window.makeSymbol(request, true)
    }

    Dialog {
        id: symbolResult
        objectName: "symbolResultDialog"
        property string message: ""
        function show(title, message) {
            symbolResult.title = title
            symbolResult.message = message
            open()
        }
        width: 460
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Ok
        Label {
            objectName: "symbolResultText"
            width: 420
            wrapMode: Text.WordWrap
            text: symbolResult.message
        }
    }

    Dialog {
        id: libraryWindow
        objectName: "libraryWindow"
        title: qsTr("Library Browser")
        anchors.centerIn: parent
        width: Math.min(900, window.width - 40)
        height: Math.min(600, window.height - 40)
        standardButtons: Dialog.Close
        LibraryBrowser { anchors.fill: parent }
    }

    FileDialog {
        id: openDialog
        title: qsTr("Open project or netlist")
        nameFilters: [qsTr("Projects and netlists (*.qsp *.cir *.net *.sp)"),
                      qsTr("QtSpice projects (*.qsp)"), qsTr("Netlists (*.cir *.net *.sp)"),
                      qsTr("All files (*)")]
        onAccepted: window.openFile(selectedFile)
    }

    FileDialog {
        id: saveDialog
        property var then: null
        title: qsTr("Save project")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "qsp"
        nameFilters: [qsTr("QtSpice projects (*.qsp)")]
        onAccepted: {
            if (ProjectManager.saveAs(selectedFile) && then)
                then()
            then = null
        }
        onRejected: then = null
    }

    Dialog {
        id: unsavedDialog
        objectName: "unsavedDialog"
        title: qsTr("Unsaved changes")
        width: 400  // a fixed width: three buttons otherwise make a width binding loop
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Save | Dialog.Discard | Dialog.Cancel
        Label {
            width: 360
            wrapMode: Text.WordWrap
            text: qsTr("Save the changes to %1?").arg(ProjectManager.fileName)
        }
        onAccepted: {
            const action = window.pendingAction
            window.pendingAction = null
            window.saveProject(action)
        }
        onDiscarded: {
            const action = window.pendingAction
            window.pendingAction = null
            close()
            action()
        }
        onRejected: window.pendingAction = null
    }

    Dialog {
        id: trustDialog
        objectName: "trustDialog"
        property string fileName: ""
        property int documents: 0
        title: qsTr("Load block code?")
        anchors.centerIn: parent
        modal: true
        closePolicy: Popup.CloseOnEscape
        standardButtons: Dialog.Yes | Dialog.No
        Label {
            width: 420
            wrapMode: Text.WordWrap
            text: qsTr("%1 contains Python code for %2 function block document(s) that "
                       + "was not saved by QtSpice on this computer. The code runs when "
                       + "the block diagram runs.\n\nLoad the code only if you trust where "
                       + "the project came from. Without it, the netlist, settings and "
                       + "results still open, as an untitled project.")
                  .arg(trustDialog.fileName).arg(trustDialog.documents)
        }
        onAccepted: ProjectManager.confirmTrust(true)
        onRejected: ProjectManager.confirmTrust(false)
    }

    Dialog {
        id: errorDialog
        objectName: "projectErrorDialog"
        title: qsTr("Project")
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Ok
        Label {
            width: 420
            wrapMode: Text.WordWrap
            text: ProjectManager.errorString
        }
    }

    // The schematic was edited while the Netlist tab holds another netlist: the project
    // keeps one netlist, so ask which one it is.
    Dialog {
        id: netlistChoiceDialog
        objectName: "netlistChoiceDialog"
        property string source: ""
        title: qsTr("Replace the netlist?")
        width: 460
        anchors.centerIn: parent
        modal: true
        closePolicy: Popup.CloseOnEscape
        Label {
            width: 420
            wrapMode: Text.WordWrap
            text: (netlistChoiceDialog.source !== ""
                   ? qsTr("The Netlist tab holds the netlist from %1.").arg(netlistChoiceDialog.source)
                   : qsTr("The Netlist tab already holds a netlist."))
                  + "\n\n"
                  + qsTr("Replace it with the schematic? The netlist is removed now, and "
                         + "Save writes the schematic's netlist into the project.\n\n"
                         + "Keep it? The netlist stays as it is and is saved as typed; "
                         + "you can still draw and run the schematic.")
        }
        footer: DialogButtonBox {
            Button {
                objectName: "replaceNetlistButton"
                text: qsTr("Replace netlist")
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            Button {
                objectName: "keepNetlistButton"
                text: qsTr("Keep netlist")
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
        }
        onAccepted: {
            ProjectManager.chooseNetlistSource(true)
            netlistEditor.clearText()
        }
        onRejected: ProjectManager.chooseNetlistSource(false)
    }

    Connections {
        target: ProjectManager
        function onNetlistChoiceRequested(source) {
            netlistChoiceDialog.source = source
            netlistChoiceDialog.open()
        }
        function onTrustRequested(fileName, documents) {
            trustDialog.fileName = fileName
            trustDialog.documents = documents
            trustDialog.open()
        }
        function onErrorStringChanged() {
            if (ProjectManager.errorString !== "")
                errorDialog.open()
        }
    }

    Shortcut { sequences: [StandardKey.New]; onActivated: window.newProject() }
    Shortcut { sequences: [StandardKey.Open]; onActivated: openDialog.open() }
    Shortcut { sequences: [StandardKey.Save]; onActivated: window.saveProject(null) }
    Shortcut { sequences: [StandardKey.SaveAs]; onActivated: window.saveAs(null) }

    menuBar: MenuBar {
        Menu {
            title: qsTr("&File")
            MenuItem { text: qsTr("&New Project"); onTriggered: window.newProject() }
            MenuItem { text: qsTr("&Open…"); onTriggered: openDialog.open() }
            Menu {
                id: recentMenu
                title: qsTr("Open &Recent")
                enabled: ProjectManager.recentFiles.length > 0
                Instantiator {
                    model: ProjectManager.recentFiles
                    delegate: MenuItem {
                        required property string modelData
                        text: modelData
                        onTriggered: window.openFile(modelData)
                    }
                    onObjectAdded: (index, object) => recentMenu.insertItem(index, object)
                    onObjectRemoved: (index, object) => recentMenu.removeItem(object)
                }
                MenuSeparator {}
                MenuItem {
                    text: qsTr("Clear Recent")
                    onTriggered: ProjectManager.clearRecentFiles()
                }
            }
            MenuSeparator {}
            MenuItem { text: qsTr("&Save"); onTriggered: window.saveProject(null) }
            MenuItem { text: qsTr("Save &As…"); onTriggered: window.saveAs(null) }
            MenuSeparator {}
            MenuItem { text: qsTr("&Quit"); onTriggered: window.close() }
        }
        Menu {
            title: qsTr("&Tools")
            MenuItem {
                objectName: "libraryBrowserMenu"
                text: qsTr("&Library Browser…")
                onTriggered: libraryWindow.open()
            }
            MenuSeparator {}
            MenuItem {
                objectName: "makeSymbolSchematicMenu"
                text: qsTr("Make Symbol from &Schematic…")
                onTriggered: makeSymbolDialog.openWith("")
            }
            MenuItem {
                objectName: "makeSymbolNetlistMenu"
                text: qsTr("Make Symbol from &Netlist…")
                onTriggered: netlistPortsDialog.openFor(netlistEditor.text)
            }
        }
    }

    footer: Label {
        objectName: "projectStatus"
        padding: 4
        leftPadding: 8
        elide: Text.ElideRight
        text: ProjectManager.statusText
    }

    // Run and Check follow the left tab: the schematic when it is shown, else the netlist.
    readonly property bool schematicShown: leftTabs.currentIndex === 2

    function runSimulation() {
        const started = schematicShown ? SchematicController.run() : Simulator.run(netlistEditor.text)
        if (started)
            bottomTabs.currentIndex = 2  // Console while it runs
    }

    Connections {
        target: Simulator
        function onResultReady() {
            bottomTabs.currentIndex = WaveformModel.hasAxis ? 0 : 2
        }
    }

    Connections {
        target: SchematicController
        function onProbed(name) { bottomTabs.currentIndex = 0 }  // Waveforms
    }

    Connections {
        target: DiagramController
        function onResultReady() {
            bottomTabs.currentIndex = 0  // Waveforms
        }
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            spacing: 4
            ToolButton {
                objectName: "openButton"
                text: qsTr("Open")
                onClicked: openDialog.open()
            }
            ToolButton {
                objectName: "saveButton"
                text: qsTr("Save")
                onClicked: window.saveProject(null)
            }
            ToolSeparator {}
            ToolButton {
                objectName: "checkButton"
                text: qsTr("Check")
                onClicked: window.schematicShown ? SchematicController.check() : netlistEditor.check()
            }
            ToolButton {
                objectName: "runButton"
                text: qsTr("Run")
                enabled: !Simulator.running
                onClicked: window.runSimulation()
            }
            ToolButton {
                objectName: "stopButton"
                text: qsTr("Stop")
                enabled: Simulator.running
                onClicked: Simulator.stop()
            }
            ProgressBar {
                objectName: "runProgress"
                Layout.preferredWidth: 120
                visible: Simulator.running
                from: 0
                to: 100
                value: Simulator.progress
                indeterminate: Simulator.progress <= 0
            }
            Label {
                objectName: "runStatus"
                Layout.fillWidth: true
                leftPadding: 8
                elide: Text.ElideRight
                text: Simulator.statusText
                color: Simulator.statusText.startsWith("Failed")
                       || Simulator.statusText.startsWith("Not started") ? "#d73a49"
                                                                          : palette.windowText
            }
        }
    }

    SplitView {
        anchors.fill: parent
        orientation: Qt.Vertical

        SplitView {
            SplitView.fillHeight: true
            SplitView.minimumHeight: 200
            orientation: Qt.Horizontal

            ColumnLayout {
                SplitView.preferredWidth: 520
                SplitView.minimumWidth: 240
                spacing: 0

                TabBar {
                    id: leftTabs
                    objectName: "leftTabs"
                    Layout.fillWidth: true
                    TabButton { text: qsTr("Netlist") }
                    TabButton { text: qsTr("Blocks") }
                    TabButton { text: qsTr("Schematic") }
                }
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: leftTabs.currentIndex
                    NetlistEditor { id: netlistEditor }
                    SplitView {
                        orientation: Qt.Vertical
                        BlockLibrary {
                            SplitView.preferredHeight: 300
                            SplitView.minimumHeight: 120
                            onEditBlock: name => functionEditor.openBlock(name)
                        }
                        FunctionEditor {
                            id: functionEditor
                            SplitView.fillHeight: true
                            SplitView.minimumHeight: 120
                        }
                    }
                    SplitView {
                        orientation: Qt.Vertical
                        PartPicker {
                            id: partPicker
                            SplitView.fillHeight: true
                            SplitView.minimumHeight: 160
                            onPlace: name => schematicCanvas.startPlace(name)
                        }
                        PortsPanel {
                            id: portsPanel
                            SplitView.preferredHeight: 150
                            SplitView.minimumHeight: 100
                            onEditPort: index => schematicCanvas.editPort(index)
                            onMakeSymbolRequested: makeSymbolDialog.openWith("")
                        }
                    }
                }
            }

            // The right side follows the left tab: analysis settings for netlists,
            // the block canvas and its properties for block diagrams.
            StackLayout {
                objectName: "rightStack"
                SplitView.fillWidth: true
                SplitView.minimumWidth: 220
                currentIndex: leftTabs.currentIndex
                AnalysisPanel {}
                SplitView {
                    orientation: Qt.Horizontal
                    BlockCanvas {
                        SplitView.fillWidth: true
                        SplitView.minimumWidth: 300
                    }
                    PropertiesPanel {
                        SplitView.preferredWidth: 220
                        SplitView.minimumWidth: 160
                    }
                }
                SchematicCanvas {
                    id: schematicCanvas
                    onPartPickerRequested: partPicker.focusSearch()
                }
            }
        }

        ColumnLayout {
            SplitView.preferredHeight: 280
            SplitView.minimumHeight: 120
            spacing: 0

            TabBar {
                id: bottomTabs
                objectName: "bottomTabs"
                Layout.fillWidth: true
                TabButton { text: qsTr("Waveforms") }
                TabButton { text: qsTr("Deck") }
                TabButton { text: qsTr("Console") }
            }
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: bottomTabs.currentIndex
                WaveformView {}
                DeckView {}
                ConsoleView {}
            }
        }
    }
}
