import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Ports of a netlist (H1): tick the nodes that become pins of a circuit symbol and
// give each a direction (inputs go on the left of the symbol, outputs on the right,
// bidirectional ports on top). openFor(text) lists the netlist's top-level nodes.
// Accepting emits makeSymbol(name, ports) with ports = [{node, direction}, ...] in
// the order the nodes were ticked.
Dialog {
    id: root
    objectName: "netlistPortsDialog"
    title: qsTr("Make Symbol from Netlist")
    modal: true
    width: 420
    height: 480
    anchors.centerIn: parent
    standardButtons: Dialog.Cancel

    property string netlistText: ""
    property var problems: []
    property var tickOrder: []   // nodes in the order they were ticked
    readonly property bool canMake: nameField.text.trim() !== "" && problems.length === 0
                                    && tickOrder.length > 0

    signal makeSymbol(string name, var ports)

    function openFor(text) {
        netlistText = text
        nodes.clear()
        tickOrder = []
        for (const node of SchematicController.netlistNodes(text))
            nodes.append({ node: node, ticked: false, direction: "bidir" })
        refresh()
        open()
        nameField.forceActiveFocus()
    }

    function chosen() {
        const out = []
        for (const node of tickOrder) {
            for (let i = 0; i < nodes.count; ++i) {
                const row = nodes.get(i)
                if (row.node === node && row.ticked)
                    out.push({ node: row.node, direction: row.direction })
            }
        }
        return out
    }

    function setTicked(index, ticked) {
        const node = nodes.get(index).node
        nodes.setProperty(index, "ticked", ticked)
        const order = tickOrder.filter(n => n !== node)
        if (ticked)
            order.push(node)
        tickOrder = order
        refresh()
    }

    function setDirection(index, direction) {
        nodes.setProperty(index, "direction", direction)
        refresh()
    }

    function refresh() {
        problems = tickOrder.length > 0
                   ? SchematicController.checkNetlistPorts(netlistText, chosen()).filter(
                         p => p.severity === "error")
                   : []
    }

    function accept_() {
        if (!canMake)
            return
        const ports = chosen()
        close()
        makeSymbol(nameField.text.trim(), ports)
    }

    ListModel { id: nodes }

    contentItem: ColumnLayout {
        spacing: 6
        RowLayout {
            Label { text: qsTr("Symbol name:") }
            TextField {
                id: nameField
                objectName: "symbolNameField"
                Layout.fillWidth: true
                selectByMouse: true
                placeholderText: qsTr("e.g. rclowpass")
                onAccepted: root.accept_()
            }
        }
        Label {
            text: qsTr("Tick the nodes that become pins:")
        }
        Label {
            objectName: "noNodes"
            visible: nodes.count === 0
            text: qsTr("The netlist has no nodes (other than ground).")
            color: "#a0522d"
        }
        ListView {
            objectName: "nodeList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: nodes
            ScrollBar.vertical: ScrollBar {}
            delegate: RowLayout {
                required property int index
                required property string node
                required property bool ticked
                required property string direction
                width: ListView.view.width
                CheckBox {
                    objectName: "portTick_" + node
                    text: node
                    checked: ticked
                    Layout.fillWidth: true
                    onToggled: root.setTicked(index, checked)
                }
                ComboBox {
                    objectName: "portDir_" + node
                    enabled: ticked
                    model: ["in", "out", "bidir"]
                    currentIndex: model.indexOf(direction)
                    onActivated: root.setDirection(index, currentText)
                }
            }
        }
        Label {
            objectName: "netlistPortProblems"
            visible: root.problems.length > 0
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: "#c0392b"
            text: root.problems.map(p => "✖ " + p.message).join("\n")
        }
        Button {
            objectName: "makeSymbolOk"
            Layout.alignment: Qt.AlignRight
            text: qsTr("Make Symbol")
            enabled: root.canMake
            onClicked: root.accept_()
        }
    }
}
