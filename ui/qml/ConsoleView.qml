import QtQuick
import QtQuick.Controls
import QtSpice

// ngspice console output of the last run (read-only, follows the end while running).
ScrollView {
    id: root
    objectName: "consoleView"

    TextArea {
        id: console_
        objectName: "consoleText"
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.NoWrap
        font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
        font.pointSize: 9
        text: Simulator.consoleText
        placeholderText: Simulator.available
                         ? qsTr("ngspice output appears here.")
                         : qsTr("ngspice shared library not found: simulations are disabled.")
        onTextChanged: cursorPosition = length
    }
}
