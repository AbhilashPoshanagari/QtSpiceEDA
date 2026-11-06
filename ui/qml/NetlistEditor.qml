import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtSpice

// Netlist editor (F1/F2): line numbers, syntax highlighting, diagnostics in the margin
// (hover a mark for the message), an Open dialog and drag-and-drop of netlist files.
// The text is checked 300 ms after the last edit. Diagnostics with a quick-fix (O1:
// "Insert title line") show a bar above the text with a button that applies it.
Item {
    id: root
    objectName: "netlistEditor"

    property alias text: editor.text
    readonly property var diagnostics: Simulator.diagnostics
    readonly property int errorCount: diagnostics.filter(d => d.severity === "error").length
    readonly property int warningCount: diagnostics.length - errorCount
    // Diagnostics grouped by line: {line: [diagnostic, ...]}
    readonly property var diagnosticsByLine: {
        const byLine = {}
        for (const d of diagnostics) {
            if (!byLine[d.line])
                byLine[d.line] = []
            byLine[d.line].push(d)
        }
        return byLine
    }
    // The O1 diagnostic "line 1 looks like an element", or null.
    readonly property var titleFix: diagnostics.find(d => d.fix === "insert_title") || null
    readonly property real lineHeight: editor.lineCount > 0
                                       ? editor.contentHeight / editor.lineCount
                                       : fontMetrics.lineSpacing

    function check() {
        checkTimer.stop()
        Simulator.check(editor.text)
    }
    // O1 quick-fix. TextArea.insert goes through the editor's own undo stack (Ctrl+Z).
    function insertTitleLine() {
        const title = qsTr("Title")
        editor.insert(0, "* " + title + "
")
        editor.select(2, 2 + title.length)
        editor.forceActiveFocus()
        check()
    }
    // Remove all text. TextArea.remove goes through the editor's own undo stack, so
    // Ctrl+Z in the editor brings the netlist back.
    function clearText() {
        if (editor.length > 0)
            editor.remove(0, editor.length)
        check()
    }
    function openDialog() {
        fileDialog.open()
    }
    function loadUrl(url) {
        const content = Simulator.openFile(url)
        if (Simulator.errorString.length > 0)
            return false
        editor.text = content
        check()
        return true
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

    // A project was opened (or a new one started): show its netlist.
    Connections {
        target: Simulator
        function onNetlistTextChanged() {
            if (editor.text !== Simulator.netlistText) {
                editor.text = Simulator.netlistText
                root.check()
            }
        }
    }

    Timer {
        id: checkTimer
        interval: 300
        onTriggered: root.check()
    }

    FileDialog {
        id: fileDialog
        title: qsTr("Open netlist")
        nameFilters: [qsTr("Netlists (*.cir *.net *.sp)"), qsTr("All files (*)")]
        onAccepted: root.loadUrl(selectedFile)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            id: quickFixBar
            objectName: "quickFixBar"
            Layout.fillWidth: true
            visible: root.titleFix !== null
            implicitHeight: fixRow.implicitHeight + 6
            color: "#33e3a008"

            RowLayout {
                id: fixRow
                anchors.fill: parent
                anchors.leftMargin: 6
                anchors.rightMargin: 6
                Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: root.titleFix !== null ? qsTr("Line 1: %1").arg(root.titleFix.message)
                                                 : ""
                }
                Button {
                    objectName: "insertTitleButton"
                    text: qsTr("Insert title line")
                    onClicked: root.insertTitleLine()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // Line numbers and diagnostic marks, scrolled with the text.
            Rectangle {
                id: gutter
                objectName: "gutter"
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
                    objectName: "editorText"
                    font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                    font.pointSize: 10
                    wrapMode: TextEdit.NoWrap
                    selectByMouse: true
                    persistentSelection: true
                    placeholderText: qsTr("Type or paste a SPICE netlist, or drop a "
                                          + ".cir / .net / .sp file here")
                    onTextChanged: {
                        Simulator.netlistText = text
                        checkTimer.restart()
                    }
                    Component.onCompleted: Simulator.attachHighlighter(textDocument)
                }

                ScrollBar.vertical: ScrollBar {}
                ScrollBar.horizontal: ScrollBar {}
            }
        }

        Label {
            id: status
            objectName: "editorStatus"
            Layout.fillWidth: true
            leftPadding: 6
            rightPadding: 6
            topPadding: 2
            bottomPadding: 2
            elide: Text.ElideMiddle
            text: {
                if (Simulator.errorString.length > 0)
                    return Simulator.errorString
                if (editor.text.trim().length === 0)
                    return qsTr("Empty netlist")
                const counts = qsTr("%n error(s)", "", root.errorCount) + ", "
                             + qsTr("%n warning(s)", "", root.warningCount)
                return Simulator.filePath.length > 0 ? counts + "  —  " + Simulator.filePath
                                                     : counts
            }
            color: Simulator.errorString.length > 0 || root.errorCount > 0 ? "#d73a49"
                                                                          : palette.windowText
            background: Rectangle { color: palette.alternateBase }
        }
    }

    DropArea {
        id: dropArea
        anchors.fill: parent
        onEntered: drag => {
            drag.accepted = drag.hasUrls && Simulator.isNetlistFile(drag.urls[0])
        }
        onDropped: drop => {
            if (drop.hasUrls && Simulator.isNetlistFile(drop.urls[0])) {
                root.loadUrl(drop.urls[0])
                drop.acceptProposedAction()
            }
        }
    }
}
