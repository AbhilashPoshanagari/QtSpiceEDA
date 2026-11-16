import QtQuick
import QtQuick.Controls
import QtSpice

// Console output of the last run (read-only, follows the end while running): the
// ngspice output of a netlist or schematic run (Simulator), or the messages of a block
// diagram run (DiagramController, U3: network checks, engine lines and the ngspice
// output of a combined run), or the messages of a grid power flow (GridController,
// G6: Diagnostics, totals, violations). Whichever wrote last is shown; `source` says
// which.
ScrollView {
    id: root
    objectName: "consoleView"

    property string source: "simulator"  // or "diagram", "grid", "designer"
    property var designer: null  // the Grid Network Designer (v0.5), if there is one

    Connections {
        target: Simulator
        function onConsoleTextChanged() { root.source = "simulator" }
    }
    Connections {
        target: DiagramController
        function onConsoleTextChanged() {
            if (DiagramController.consoleText !== "")
                root.source = "diagram"
        }
    }
    Connections {
        target: GridController
        function onConsoleTextChanged() {
            if (GridController.consoleText !== "")
                root.source = "grid"
        }
    }

    Connections {
        target: root.designer
        ignoreUnknownSignals: true
        function onConsoleTextChanged() {
            if (root.designer.consoleText !== "")
                root.source = "designer"
        }
    }

    TextArea {
        id: console_
        objectName: "consoleText"
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.NoWrap
        font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
        font.pointSize: 9
        text: root.source === "designer" && root.designer ? root.designer.consoleText
              : root.source === "diagram" ? DiagramController.consoleText
              : root.source === "grid" ? GridController.consoleText : Simulator.consoleText
        placeholderText: Simulator.available
                         ? qsTr("ngspice output appears here.")
                         : qsTr("ngspice shared library not found: simulations are disabled.")
        onTextChanged: cursorPosition = length
    }
}
