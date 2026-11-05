import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Function editor (F5): write Python functions marked with @block. The code is read
// (never run) 300 ms after the last edit; without errors its blocks replace this
// document's previous blocks in the library. Errors are marked in the margin (hover a
// mark for the message) and the library keeps the last good version.
Item {
    id: root
    objectName: "functionEditor"

    property alias text: editor.text
    readonly property var diagnostics: DiagramController.diagnostics
    readonly property int errorCount: diagnostics.filter(d => d.severity === "error").length
    readonly property int warningCount: diagnostics.length - errorCount
    readonly property var diagnosticsByLine: {
        const byLine = {}
        for (const d of diagnostics) {
            if (!byLine[d.line])
                byLine[d.line] = []
            byLine[d.line].push(d)
        }
        return byLine
    }
    readonly property real lineHeight: editor.lineCount > 0
                                       ? editor.contentHeight / editor.lineCount
                                       : fontMetrics.lineSpacing

    // Apply the pending edit now (before switching documents).
    function apply() {
        applyTimer.stop()
        DiagramController.apply(editor.text)
    }
    function newFunction() {
        apply()
        DiagramController.newDocument()
        setTextQuietly("")
    }
    // Edit the code of user block `name`; returns false for built-in blocks.
    function openBlock(name) {
        if (DiagramController.isBuiltin(name))
            return false
        apply()
        const code = DiagramController.openBlock(name)
        setTextQuietly(code)
        editor.forceActiveFocus()
        return true
    }
    function setTextQuietly(code) {
        editor.text = code
        applyTimer.stop()
    }
    function severityAt(line) {
        const list = diagnosticsByLine[line]
        if (!list)
            return ""
        return list.some(d => d.severity === "error") ? "error" : "warning"
    }
    function messagesAt(line) {
        const list = diagnosticsByLine[line] || []
        return list.map(d => qsTr("%1 (col %2): %3").arg(d.severity).arg(d.col).arg(d.message))
                   .join("\n")
    }

    FontMetrics {
        id: fontMetrics
        font: editor.font
    }

    // A project was opened: show the code of its first document.
    Connections {
        target: DiagramController
        function onDocumentReplaced(code) {
            root.setTextQuietly(code)
        }
    }

    Timer {
        id: applyTimer
        interval: 300
        onTriggered: root.apply()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            Label {
                Layout.fillWidth: true
                leftPadding: 6
                text: qsTr("Function editor")
                font.bold: true
            }
            ToolButton {
                objectName: "newFunctionButton"
                text: qsTr("New function")
                onClicked: root.newFunction()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // Line numbers and diagnostic marks, scrolled with the text.
            Rectangle {
                id: gutter
                Layout.fillHeight: true
                Layout.preferredWidth: fontMetrics.advanceWidth("0")
                                       * Math.max(3, String(editor.lineCount).length) + 24
                color: palette.alternateBase
                clip: true

                Column {
                    y: editor.topPadding - flick.contentY
                    width: parent.width

                    Repeater {
                        model: editor.lineCount
                        delegate: Item {
                            id: row
                            required property int index
                            readonly property int line: index + 1
                            readonly property string severity: root.severityAt(line)

                            width: gutter.width
                            height: root.lineHeight

                            Rectangle {
                                anchors.fill: parent
                                visible: row.severity !== ""
                                color: row.severity === "error" ? "#33d73a49" : "#33e3a008"
                            }
                            Rectangle {
                                objectName: "diagnosticMark"
                                property int line: row.line
                                property string severity: row.severity
                                visible: row.severity !== ""
                                width: 8
                                height: 8
                                radius: 4
                                anchors.left: parent.left
                                anchors.leftMargin: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: row.severity === "error" ? "#d73a49" : "#e3a008"
                            }
                            Label {
                                anchors.right: parent.right
                                anchors.rightMargin: 4
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.line
                                font: editor.font
                                color: palette.placeholderText
                            }
                            MouseArea {
                                id: hover
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton
                            }
                            ToolTip.visible: hover.containsMouse && row.severity !== ""
                            ToolTip.delay: 200
                            ToolTip.text: root.messagesAt(row.line)
                        }
                    }
                }
            }

            Flickable {
                id: flick
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                TextArea.flickable: TextArea {
                    id: editor
                    objectName: "functionText"
                    font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                    font.pointSize: 10
                    wrapMode: TextEdit.NoWrap
                    selectByMouse: true
                    persistentSelection: true
                    placeholderText: "@block(shape=\"circle\", outputs=[\"sum\", \"diff\"])\n"
                                     + "def add_sub(a, b):\n"
                                     + "    return a + b, a - b"
                    onTextChanged: applyTimer.restart()
                    // Tab inserts four spaces: Python code.
                    Keys.onTabPressed: event => {
                        editor.insert(editor.cursorPosition, "    ")
                        event.accepted = true
                    }
                }

                ScrollBar.vertical: ScrollBar {}
                ScrollBar.horizontal: ScrollBar {}
            }
        }

        Label {
            objectName: "functionStatus"
            Layout.fillWidth: true
            leftPadding: 6
            rightPadding: 6
            topPadding: 2
            bottomPadding: 2
            elide: Text.ElideRight
            text: {
                const counts = qsTr("%n error(s)", "", root.errorCount) + ", "
                             + qsTr("%n warning(s)", "", root.warningCount)
                return DiagramController.statusText.length > 0
                        ? counts + "  —  " + DiagramController.statusText
                        : counts
            }
            color: root.errorCount > 0 ? "#d73a49"
                 : DiagramController.removedPorts.length > 0 ? "#b07d00"
                 : palette.windowText
            background: Rectangle { color: palette.alternateBase }
            ToolTip.visible: statusHover.containsMouse && text.length > 0
            ToolTip.text: text
            MouseArea {
                id: statusHover
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.NoButton
            }
        }
    }
}
