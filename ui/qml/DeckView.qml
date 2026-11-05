import QtQuick
import QtQuick.Controls
import QtSpice

// The deck that was last sent to ngspice (read-only).
ScrollView {
    id: root
    objectName: "deckView"

    TextArea {
        objectName: "deckText"
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.NoWrap
        font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
        font.pointSize: 10
        text: Simulator.sentDeck
        placeholderText: qsTr("The deck sent to ngspice appears here after Run.")
    }
}
