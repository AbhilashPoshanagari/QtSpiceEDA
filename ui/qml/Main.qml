import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice
import "grid"

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    width: 1280
    height: 800
    visible: true
    title: (ProjectManager.modified ? "*" : "") + ProjectManager.fileName + " — QtSpice"

    property bool quitting: false
    property var pendingAction: null  // runs once unsaved changes are saved or discarded

    // ---- window layout (UI only: nothing here is saved in the project)
    //
    // The side panel (the Netlist / Blocks / Schematic / Grid tabs) can be hidden; a
    // narrow strip then still switches between the four views. The bottom panel
    // (Waveforms / Deck / Console) is "normal", "collapsed" to its tab row, "maximized"
    // (the editors are hidden) or "detached" into its own window.
    property bool sidePanelShown: true
    property string bottomState: "normal"
    property real bottomHeight: 280  // height of the bottom panel when it is normal

    function setBottomState(state) {
        if (state === bottomState)
            return
        if (bottomState === "normal" && bottomHolder.height > bottomHeader.height + 20)
            bottomHeight = bottomHolder.height
        if (bottomState === "detached") {
            bottomPanel.parent = bottomHolder
            detachedWindow.close()
        }
        bottomState = state
        if (state === "detached") {
            bottomPanel.parent = detachedWindow.contentItem
            detachedWindow.show()
            detachedWindow.raise()
        } else {
            bottomHolder.SplitView.preferredHeight = state === "collapsed"
                    ? bottomHeader.height : bottomHeight
        }
    }
    // Show a bottom tab, opening the panel if it is collapsed.
    function showBottom(index) {
        bottomTabs.currentIndex = index
        if (bottomState === "collapsed")
            setBottomState("normal")
    }
    // Layout presets: the bottom panel's share of the height and the side panel.
    function applyLayout(name) {
        const h = mainSplit.height
        if (name === "plot") {
            setBottomState("maximized")
            return
        }
        if (name === "edit") {
            sidePanelShown = true
            setBottomState("collapsed")
            return
        }
        if (bottomState === "detached")
            setBottomState("normal")
        sidePanelShown = name !== "results"
        bottomHeight = Math.max(160, Math.round(h * (name === "results" ? 0.6 : 0.4)))
        if (bottomState === "normal")
            bottomHolder.SplitView.preferredHeight = bottomHeight
        else
            setBottomState("normal")
    }

    Component.onCompleted: {
        // fit the screen: the default size is taller than a 1366 x 768 laptop screen.
        // Not on "offscreen" (tests): its 800 x 800 screen is not a real one.
        if (Qt.platform.pluginName !== "offscreen"
                && Screen.desktopAvailableWidth > 0 && Screen.desktopAvailableHeight > 0) {
            width = Math.min(width, Screen.desktopAvailableWidth - 40)
            height = Math.min(height, Screen.desktopAvailableHeight - 60)
            x = Screen.virtualX + (Screen.desktopAvailableWidth - width) / 2
            y = Screen.virtualY + Math.max(0, (Screen.desktopAvailableHeight - height) / 2 - 20)
            bottomHeight = Math.max(160, Math.round(height * 0.38))
            bottomHolder.SplitView.preferredHeight = bottomHeight
        }
        Simulator.attachWaveforms(WaveformModel)
        DiagramController.attachWaveforms(WaveformModel)
        ProjectManager.attach(Simulator, DiagramController, WaveformModel)
        ProjectManager.attachSchematic(SchematicController)
        SchematicController.attachSimulator(Simulator)
        SchematicController.attachWaveforms(WaveformModel)
        WaveformController.attach(WaveformModel)
        WaveformController.attachProbes(SchematicController)
        ProjectManager.attachPlots(WaveformController)
        GridController.attachWaveforms(WaveformModel)
        ProjectManager.attachGrid(GridController)
    }

    onClosing: close => {
        if (ProjectManager.modified && !quitting) {
            close.accepted = false
            whenSaved(() => { window.quitting = true; window.close() })
        } else if (designer.modified && !quitting) {
            close.accepted = false
            whenDesignDiscarded(() => { window.quitting = true; window.close() })
        } else {
            detachedWindow.close()
            designer.shutdown()
        }
    }

    // ---- Grid Network Designer (v0.5, D6): one design, edited in the Designer tab.
    // Not in the project file yet (D9): unsaved changes are asked about here.
    GridDiagramController {
        id: designer
        objectName: "gridDesigner"
    }
    readonly property bool designerShown: leftTabs.currentIndex === 4
    property var pendingDesignAction: null

    // Run `action` now, or after asking whether to drop the design's unsaved changes.
    function whenDesignDiscarded(action) {
        if (!designer.modified) {
            action()
            return
        }
        pendingDesignAction = action
        discardDesignDialog.open()
    }
    function showDesigner() {
        leftTabs.currentIndex = 4
        window.sidePanelShown = true
    }
    function saveDesign() {
        if (designer.filePath === "")
            designSaveDialog.open()
        else
            designer.save()
    }
    function editGridAsDesign() {
        whenDesignDiscarded(() => {
            if (designer.editGridAsDesign())
                window.showDesigner()
            else
                window.showBottom(2)
        })
    }

    Connections {
        target: designer
        function onDesignOpened() { window.showDesigner() }
        function onOpenFailed() { window.showBottom(2) }
        function onResultReady() { window.showBottom(2) }
    }

    NewGridDesignDialog {
        id: newDesignDialog
        designer: designer
        onAccepted: {
            if (designer.hasDesign)
                window.showDesigner()
        }
    }
    Dialog {
        id: discardDesignDialog
        objectName: "discardDesignDialog"
        title: qsTr("Unsaved design")
        modal: true
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Save | Dialog.Discard | Dialog.Cancel
        width: 420
        Label {
            width: parent.width
            wrapMode: Text.WordWrap
            text: qsTr("The grid design %1 has unsaved changes.").arg(designer.designName)
        }
        onAccepted: {   // Save
            const action = window.pendingDesignAction
            window.pendingDesignAction = null
            if (designer.filePath === "") {
                designSaveDialog.then = action
                designSaveDialog.open()
            } else if (designer.save() && action) {
                action()
            }
        }
        onDiscarded: {
            const action = window.pendingDesignAction
            window.pendingDesignAction = null
            close()
            if (action)
                action()
        }
        onRejected: window.pendingDesignAction = null
    }
    FileDialog {
        id: designOpenDssDialog
        objectName: "designOpenDssDialog"
        title: qsTr("Open OpenDSS feeder as a design")
        nameFilters: [qsTr("OpenDSS files (*.dss *.DSS)"), qsTr("All files (*)")]
        onAccepted: designer.openDssAsDesign(selectedFile)
    }
    FileDialog {
        id: designOpenDialog
        objectName: "designOpenDialog"
        title: qsTr("Open grid design")
        nameFilters: [qsTr("Grid designs (*.qgrid)"), qsTr("All files (*)")]
        onAccepted: designer.load(selectedFile)
    }
    FileDialog {
        id: designSaveDialog
        objectName: "designSaveDialog"
        property var then: null
        title: qsTr("Save grid design")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "qgrid"
        nameFilters: [qsTr("Grid designs (*.qgrid)")]
        onAccepted: {
            const action = then
            then = null
            if (designer.save(selectedFile) && action)
                action()
        }
        onRejected: then = null
    }
    FileDialog {
        id: designExportDialog
        objectName: "designExportDialog"
        title: qsTr("Export the design as an OpenDSS script")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "dss"
        nameFilters: [qsTr("OpenDSS files (*.dss)")]
        onAccepted: {
            if (!designer.exportDss(selectedFile))
                window.showBottom(2)
        }
    }
    Action {
        id: designUndo
        text: designer.canUndo && window.designerShown ? qsTr("&Undo %1").arg(designer.undoText)
                                                       : qsTr("&Undo")
        shortcut: StandardKey.Undo
        enabled: window.designerShown && designer.canUndo
        onTriggered: designer.undo()
    }
    Action {
        id: designRedo
        text: designer.canRedo && window.designerShown ? qsTr("&Redo %1").arg(designer.redoText)
                                                       : qsTr("&Redo")
        shortcut: "Ctrl+Y"
        enabled: window.designerShown && designer.canRedo
        onTriggered: designer.redo()
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
        componentEditor.apply()  // ... and of the component editor
        if (ProjectManager.save()) {
            if (then)
                then()
        } else if (ProjectManager.filePath === "") {
            saveAs(then)
        }
    }
    function saveAs(then) {
        functionEditor.apply()
        componentEditor.apply()
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
        id: gridOpenDialog
        objectName: "gridOpenDialog"
        title: qsTr("Open OpenDSS feeder")
        nameFilters: [qsTr("OpenDSS files (*.dss *.DSS)"), qsTr("All files (*)")]
        onAccepted: window.openGrid(selectedFile)
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

    // N3: Tools > Schematic from Netlist draws the Netlist tab's text on the sheet.
    SchematicFromNetlist {
        id: schematicFromNetlist
        objectName: "schematicFromNetlist"
        onDrawn: {
            leftTabs.currentIndex = 2
            Qt.callLater(schematicCanvas.zoomToFit)  // once the tab has its size
        }
        onFailed: netlistEditor.check()
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
    Shortcut { sequence: "Ctrl+B"; onActivated: window.sidePanelShown = !window.sidePanelShown }
    Shortcut {
        sequence: "Ctrl+J"
        onActivated: window.setBottomState(window.bottomState === "collapsed" ? "normal" : "collapsed")
    }
    Shortcut {
        sequence: "Ctrl+M"
        onActivated: window.setBottomState(window.bottomState === "maximized" ? "normal" : "maximized")
    }

    // The bottom panel in its own window (Detach); closing the window docks it again.
    Window {
        id: detachedWindow
        objectName: "detachedBottomWindow"
        title: qsTr("Waveforms — QtSpice")
        width: 960
        height: 560
        color: window.palette.window
        onClosing: {
            if (window.bottomState === "detached")
                window.setBottomState("normal")
        }
    }

    menuBar: MenuBar {
        Menu {
            title: qsTr("&File")
            MenuItem { text: qsTr("&New Project"); onTriggered: window.newProject() }
            MenuItem { text: qsTr("&Open…"); onTriggered: openDialog.open() }
            // G6: hidden without dss-python (height 0 so the menu leaves no gap)
            MenuItem {
                objectName: "openGridMenu"
                text: qsTr("Open &Grid (.dss)…")
                visible: GridController.available
                height: GridController.available ? implicitHeight : 0
                onTriggered: gridOpenDialog.open()
            }
            MenuSeparator {}
            MenuItem {
                objectName: "newGridDesignMenu"
                text: qsTr("New Grid &Design…")
                onTriggered: window.whenDesignDiscarded(() => newDesignDialog.open())
            }
            MenuItem {
                objectName: "openDssAsDesignMenu"
                text: qsTr("Open .dss as Desi&gn…")
                enabled: designer.available && !designer.busy
                onTriggered: window.whenDesignDiscarded(() => designOpenDssDialog.open())
            }
            MenuItem {
                objectName: "openDesignMenu"
                text: qsTr("Open Design (.q&grid)…")
                onTriggered: window.whenDesignDiscarded(() => designOpenDialog.open())
            }
            MenuItem {
                objectName: "saveDesignMenu"
                text: qsTr("Save Desig&n")
                enabled: designer.hasDesign
                onTriggered: window.saveDesign()
            }
            MenuItem {
                objectName: "saveDesignAsMenu"
                text: qsTr("Save Design As…")
                enabled: designer.hasDesign
                onTriggered: designSaveDialog.open()
            }
            MenuItem {
                objectName: "exportDssMenu"
                text: qsTr("E&xport .dss…")
                enabled: designer.hasDesign
                onTriggered: designExportDialog.open()
            }
            MenuSeparator {}
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
            title: qsTr("&Edit")
            MenuItem { objectName: "designUndoMenu"; action: designUndo }
            MenuItem { objectName: "designRedoMenu"; action: designRedo }
        }
        Menu {
            title: qsTr("&Grid")
            MenuItem {
                objectName: "runPowerFlowMenu"
                text: qsTr("&Run Power Flow")
                enabled: designer.canRun
                onTriggered: {
                    window.showDesigner()
                    designer.runPowerFlow()
                    window.showBottom(2)
                }
            }
            MenuItem {
                text: qsTr("Why can't I run?")
                visible: designer.hasDesign && !designer.canRun
                height: visible ? implicitHeight : 0
                onTriggered: {
                    designer.runPowerFlow()  // refuses and says why (status and Console)
                    window.showBottom(2)
                }
            }
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
            MenuSeparator {}
            MenuItem {
                objectName: "schematicFromNetlistMenu"
                text: qsTr("Schematic from Ne&tlist")
                onTriggered: schematicFromNetlist.start(netlistEditor.text)
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
    // G6: the Grid tab runs the open .dss feeder (GridController) instead.
    readonly property bool gridShown: leftTabs.currentIndex === 3

    function openGrid(url) {
        if (GridController.openFile(url))
            leftTabs.currentIndex = 3
        else
            window.showBottom(2)  // the reason is in the Console
    }

    function runSimulation() {
        if (designerShown) {
            designer.runPowerFlow()
            window.showBottom(2)
            return
        }
        if (gridShown) {
            GridController.run()
            window.showBottom(2)  // Console while it runs, or why it did not start
            return
        }
        const started = schematicShown ? SchematicController.run() : Simulator.run(netlistEditor.text)
        if (started)
            window.showBottom(2)  // Console while it runs
    }

    Connections {
        target: Simulator
        function onResultReady() {
            window.showBottom(WaveformModel.hasAxis ? 0 : 2)
        }
    }

    Connections {
        target: SchematicController
        function onProbed(name) { window.showBottom(0) }  // Waveforms
    }

    Connections {
        target: GridController
        function onResultReady() { window.showBottom(0) }  // the voltage profile
        function onLoadFailed() { window.showBottom(2) }  // the Diagnostics
    }

    Connections {
        target: DiagramController
        function onResultReady() {
            window.showBottom(0)  // Waveforms
        }
        // One Run button for every diagram (U3): the Console while it runs, and when
        // it does not start or fails (the reason and the network checks are there).
        function onRunningChanged() {
            if (DiagramController.running)
                window.showBottom(2)
        }
        function onRunStatusChanged() {
            const s = DiagramController.runStatus
            if (s.startsWith("Not started") || s.startsWith("Failed"))
                window.showBottom(2)
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
                enabled: !window.gridShown && !window.designerShown
                onClicked: window.schematicShown ? SchematicController.check() : netlistEditor.check()
            }
            ToolButton {
                objectName: "runButton"
                text: qsTr("Run")
                enabled: window.designerShown ? designer.canRun
                         : window.gridShown ? GridController.canRun : !Simulator.running
                onClicked: window.runSimulation()
            }
            ToolButton {
                objectName: "stopButton"
                text: qsTr("Stop")
                enabled: window.designerShown ? designer.busy
                         : window.gridShown ? GridController.running : Simulator.running
                onClicked: window.designerShown ? designer.stop()
                           : window.gridShown ? GridController.stop() : Simulator.stop()
            }
            ProgressBar {
                objectName: "runProgress"
                Layout.preferredWidth: 120
                visible: window.designerShown ? designer.busy
                         : window.gridShown ? GridController.running : Simulator.running
                from: 0
                to: 100
                value: window.gridShown || window.designerShown ? 0 : Simulator.progress
                indeterminate: window.gridShown || window.designerShown || Simulator.progress <= 0
            }
            Label {
                objectName: "runStatus"
                Layout.fillWidth: true
                leftPadding: 8
                elide: Text.ElideRight
                readonly property string status: window.designerShown ? designer.statusText
                                                 : window.gridShown ? GridController.statusText
                                                                    : Simulator.statusText
                text: status
                color: status.startsWith("Failed") || status.startsWith("Not started")
                       || status.startsWith("Not opened") ? "#d73a49" : palette.windowText
            }
            ToolSeparator {}
            Label {
                text: qsTr("Layout")
                color: palette.placeholderText
            }
            Repeater {
                model: [
                    { name: "edit", text: qsTr("Edit"),
                      tip: qsTr("Editors only: the bottom panel shrinks to its tabs (Ctrl+J)") },
                    { name: "balanced", text: qsTr("Balanced"),
                      tip: qsTr("Editors and results side by side") },
                    { name: "results", text: qsTr("Results"),
                      tip: qsTr("A larger bottom panel; the side panel is hidden (Ctrl+B)") },
                    { name: "plot", text: qsTr("Plot"),
                      tip: qsTr("The bottom panel fills the window (Ctrl+M)") }
                ]
                delegate: ToolButton {
                    required property var modelData
                    objectName: "layout_" + modelData.name
                    text: modelData.text
                    onClicked: window.applyLayout(modelData.name)
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: modelData.tip
                }
            }
        }
    }

    SplitView {
        id: mainSplit
        objectName: "mainSplit"
        anchors.fill: parent
        orientation: Qt.Vertical

        RowLayout {
            objectName: "topArea"
            visible: window.bottomState !== "maximized"
            SplitView.fillHeight: true
            SplitView.minimumHeight: 160
            spacing: 0

            // With the side panel hidden, this strip still switches between the views.
            Pane {
                objectName: "sideStrip"
                visible: !window.sidePanelShown
                Layout.fillHeight: true
                padding: 2
                clip: true  // a short window cuts the strip instead of overlapping the bottom panel
                ColumnLayout {
                    spacing: 2
                    ToolButton {
                        objectName: "showSidePanelButton"
                        Layout.preferredWidth: 28
                        text: "»"
                        onClicked: window.sidePanelShown = true
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("Show the side panel (Ctrl+B)")
                    }
                    Repeater {
                        model: [qsTr("Netlist"), qsTr("Blocks"), qsTr("Schematic"), qsTr("Grid"),
                                qsTr("Designer")]
                        delegate: ToolButton {
                            id: stripButton
                            required property string modelData
                            required property int index
                            objectName: "sideStrip_" + index
                            visible: index !== 3 || GridController.available
                            Layout.preferredWidth: 28
                            implicitHeight: stripLabel.implicitWidth + 20
                            checkable: true
                            checked: leftTabs.currentIndex === index
                            onClicked: leftTabs.currentIndex = index
                            contentItem: Item {
                                Label {
                                    id: stripLabel
                                    anchors.centerIn: parent
                                    rotation: -90
                                    text: stripButton.modelData
                                    font.bold: stripButton.checked
                                }
                            }
                        }
                    }
                }
            }

            SplitView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                orientation: Qt.Horizontal

                ColumnLayout {
                    objectName: "sidePanel"
                    visible: window.sidePanelShown
                    SplitView.preferredWidth: 520
                    SplitView.minimumWidth: 240
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        TabBar {
                            id: leftTabs
                            objectName: "leftTabs"
                            Layout.fillWidth: true
                            TabButton { text: qsTr("Netlist") }
                            TabButton { text: qsTr("Blocks") }
                            TabButton { text: qsTr("Schematic") }
                            TabButton {
                                objectName: "gridTab"
                                text: qsTr("Grid")
                                visible: GridController.available
                                width: GridController.available ? implicitWidth : 0
                            }
                            TabButton {
                                objectName: "designerTab"
                                text: qsTr("Designer")
                            }
                        }
                        ToolButton {
                            objectName: "hideSidePanelButton"
                            text: "«"
                            onClicked: window.sidePanelShown = false
                            ToolTip.visible: hovered
                            ToolTip.delay: 600
                            ToolTip.text: qsTr("Hide the side panel (Ctrl+B)")
                        }
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
                                onEditBlock: name => {
                                    if (componentEditor.openComponent(name))
                                        editorTabs.currentIndex = 1
                                    else if (functionEditor.openBlock(name))
                                        editorTabs.currentIndex = 0
                                }
                            }
                            ColumnLayout {
                                SplitView.fillHeight: true
                                SplitView.minimumHeight: 120
                                spacing: 0
                                TabBar {
                                    id: editorTabs
                                    objectName: "editorTabs"
                                    Layout.fillWidth: true
                                    TabButton { text: qsTr("Functions") }
                                    TabButton { text: qsTr("Components") }
                                }
                                StackLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    currentIndex: editorTabs.currentIndex
                                    FunctionEditor { id: functionEditor }
                                    ComponentEditor { id: componentEditor }
                                }
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
                        GridPanel {
                            onOpenRequested: gridOpenDialog.open()
                            onViolationsRequested: gridResults.currentIndex = 2
                        }
                        SplitView {
                            orientation: Qt.Vertical
                            GridComponentLibrary {
                                designer: designer
                                SplitView.preferredHeight: 330
                                SplitView.minimumHeight: 160
                                onActivated: kind => designEditor.startPlace(kind)
                            }
                            GridProperties {
                                designer: designer
                                SplitView.fillHeight: true
                                SplitView.minimumHeight: 120
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
                    SplitView {
                        orientation: Qt.Vertical
                        GridView {
                            SplitView.fillHeight: true
                            SplitView.minimumHeight: 120
                            onEditAsDesignRequested: window.editGridAsDesign()
                        }
                        GridResultsTable {
                            id: gridResults
                            SplitView.preferredHeight: 260
                            SplitView.minimumHeight: 100
                        }
                    }
                    SplitView {
                        orientation: Qt.Vertical
                        GridDiagramEditor {
                            id: designEditor
                            SplitView.fillHeight: true
                            SplitView.minimumHeight: 200
                            designer: designer
                            onNewDesignRequested: window.whenDesignDiscarded(() => newDesignDialog.open())
                            onOpenDssRequested: window.whenDesignDiscarded(() => designOpenDssDialog.open())
                            onConsoleRequested: window.showBottom(2)
                        }
                        // D8: Level 1 + Level 2 of the last run; a row selects and centres
                        GridFeasibilityPanel {
                            designer: designer
                            SplitView.preferredHeight: 220
                            SplitView.minimumHeight: 90
                            onElementActivated: id => designEditor.focusOn(id)
                        }
                    }
                }
            }
        }

        // The bottom panel lives in this holder, or in detachedWindow when detached.
        Item {
            id: bottomHolder
            objectName: "bottomHolder"
            visible: window.bottomState !== "detached"
            SplitView.fillHeight: window.bottomState === "maximized"
            SplitView.preferredHeight: 280
            SplitView.minimumHeight: window.bottomState === "collapsed" ? bottomHeader.height : 120
            SplitView.maximumHeight: window.bottomState === "collapsed" ? bottomHeader.height
                                                                         : Number.POSITIVE_INFINITY

            ColumnLayout {
                id: bottomPanel
                objectName: "bottomPanel"
                anchors.fill: parent
                spacing: 0

                RowLayout {
                    id: bottomHeader
                    Layout.fillWidth: true
                    spacing: 0
                    TabBar {
                        id: bottomTabs
                        objectName: "bottomTabs"
                        Layout.fillWidth: true
                        TabButton {
                            text: qsTr("Waveforms")
                            onClicked: window.showBottom(0)
                        }
                        TabButton {
                            text: qsTr("Deck")
                            onClicked: window.showBottom(1)
                        }
                        TabButton {
                            text: qsTr("Console")
                            onClicked: window.showBottom(2)
                        }
                    }
                    ToolButton {
                        objectName: "detachBottomButton"
                        text: window.bottomState === "detached" ? qsTr("Dock") : qsTr("Detach")
                        onClicked: window.setBottomState(window.bottomState === "detached"
                                                         ? "normal" : "detached")
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: window.bottomState === "detached"
                                      ? qsTr("Put the panel back into the main window")
                                      : qsTr("Move the panel into its own window")
                    }
                    ToolButton {
                        objectName: "maximizeBottomButton"
                        visible: window.bottomState !== "detached"
                        text: window.bottomState === "maximized" ? qsTr("Restore") : qsTr("Maximize")
                        onClicked: window.setBottomState(window.bottomState === "maximized"
                                                         ? "normal" : "maximized")
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("Fill the window with this panel (Ctrl+M)")
                    }
                    ToolButton {
                        objectName: "collapseBottomButton"
                        visible: window.bottomState !== "detached"
                        text: window.bottomState === "collapsed" ? "▴" : "▾"
                        onClicked: window.setBottomState(window.bottomState === "collapsed"
                                                         ? "normal" : "collapsed")
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: window.bottomState === "collapsed"
                                      ? qsTr("Show the panel (Ctrl+J)")
                                      : qsTr("Shrink the panel to its tabs (Ctrl+J)")
                    }
                }
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: window.bottomState !== "collapsed"
                    currentIndex: bottomTabs.currentIndex
                    WaveformView {}
                    DeckView {}
                    ConsoleView { designer: designer }
                }
            }
        }
    }
}
