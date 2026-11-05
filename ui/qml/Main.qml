import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    width: 1280
    height: 800
    visible: true
    title: qsTr("QtSpice")

    // Placeholder pane: replaced by the real views in later phases.
    component Placeholder: Rectangle {
        property string text
        color: palette.base
        Label {
            anchors.centerIn: parent
            text: parent.text
            color: palette.placeholderText
        }
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            spacing: 4
            // Actions are wired to the bridge objects in later phases.
            ToolButton {
                objectName: "openButton"
                text: qsTr("Open")
                onClicked: netlistEditor.openDialog()
            }
            ToolButton { objectName: "saveButton"; text: qsTr("Save"); enabled: false }
            ToolSeparator {}
            ToolButton {
                objectName: "checkButton"
                text: qsTr("Check")
                onClicked: netlistEditor.check()
            }
            ToolButton { objectName: "runButton"; text: qsTr("Run"); enabled: false }
            ToolButton { objectName: "stopButton"; text: qsTr("Stop"); enabled: false }
            Item { Layout.fillWidth: true }
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
                }
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: leftTabs.currentIndex
                    NetlistEditor { id: netlistEditor }
                    Placeholder { text: qsTr("Block library and canvas (Phase 4-5)") }
                }
            }

            Placeholder {
                SplitView.fillWidth: true
                text: qsTr("Analysis settings (Phase 3)")
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
                Placeholder { text: qsTr("Waveform viewer (Phase 3)") }
                Placeholder { text: qsTr("Deck sent to ngspice (Phase 3)") }
                Placeholder { text: qsTr("ngspice console (Phase 3)") }
            }
        }
    }
}
