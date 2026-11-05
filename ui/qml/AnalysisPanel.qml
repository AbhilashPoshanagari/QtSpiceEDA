import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtSpice

// Analysis settings (F3): forms for .op, .dc, .ac and .tran.
// The fields follow the netlist's first analysis card until you edit them. After an
// edit the card built here replaces the netlist's analysis cards in the deck that is
// sent (Simulator.analysisCard, applied through the NetlistBuilder); the editor text
// is not changed. "Use netlist card" goes back to following the netlist.
Pane {
    id: root
    objectName: "analysisPanel"

    readonly property var kinds: ["op", "dc", "ac", "tran"]
    readonly property var variations: ["dec", "oct", "lin"]
    property bool followNetlist: true
    readonly property string card: buildCard()
    readonly property string cardError: Simulator.checkAnalysisCard(card)

    function value(field) {
        return field.text.trim()
    }

    function buildCard() {
        switch (kinds[kindBox.currentIndex]) {
        case "op":
            return ".op"
        case "dc":
            return [".dc", value(dcSource), value(dcStart), value(dcStop), value(dcStep)].join(" ")
        case "ac":
            return [".ac", variations[acVariation.currentIndex], value(acPoints),
                    value(acStart), value(acStop)].join(" ")
        case "tran": {
            const parts = [".tran", value(tranStep), value(tranStop)]
            if (value(tranStart).length > 0)
                parts.push(value(tranStart))
            return parts.join(" ")
        }
        }
        return ""
    }

    // Fill the form from a card such as ".ac dec 20 1 1meg".
    function loadCard(text) {
        const tokens = text.trim().split(/\s+/)
        const kind = tokens.length > 0 ? tokens[0].replace(".", "").toLowerCase() : ""
        const index = kinds.indexOf(kind)
        if (index < 0)
            return
        kindBox.currentIndex = index
        const arg = i => (i < tokens.length ? tokens[i] : "")
        if (kind === "dc") {
            dcSource.text = arg(1); dcStart.text = arg(2); dcStop.text = arg(3); dcStep.text = arg(4)
        } else if (kind === "ac") {
            const v = variations.indexOf(arg(1).toLowerCase())
            acVariation.currentIndex = v < 0 ? 0 : v
            acPoints.text = arg(2); acStart.text = arg(3); acStop.text = arg(4)
        } else if (kind === "tran") {
            tranStep.text = arg(1); tranStop.text = arg(2); tranStart.text = arg(3)
        }
    }

    // Called on every user edit: from now on the panel's card is the one that runs.
    function edited() {
        followNetlist = false
        Simulator.analysisCard = buildCard()
    }

    function useNetlistCard() {
        followNetlist = true
        Simulator.analysisCard = ""
        loadCard(Simulator.netlistAnalysis)
    }

    Connections {
        target: Simulator
        function onNetlistAnalysisChanged() {
            if (root.followNetlist)
                root.loadCard(Simulator.netlistAnalysis)
        }
    }

    component Field: TextField {
        Layout.fillWidth: true
        selectByMouse: true
        onTextEdited: root.edited()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Label {
            text: qsTr("Analysis")
            font.bold: true
        }

        ComboBox {
            id: kindBox
            objectName: "analysisKind"
            Layout.fillWidth: true
            model: [qsTr("Operating point (.op)"), qsTr("DC sweep (.dc)"),
                    qsTr("AC small-signal (.ac)"), qsTr("Transient (.tran)")]
            currentIndex: 3
            onActivated: root.edited()
        }

        StackLayout {
            Layout.fillWidth: true
            currentIndex: kindBox.currentIndex

            Label {
                text: qsTr("DC operating point: node voltages and branch currents are "
                           + "listed in the Console.")
                wrapMode: Text.WordWrap
            }

            GridLayout {
                columns: 2
                Label { text: qsTr("Source") }
                Field { id: dcSource; objectName: "dcSource"; placeholderText: "V1" }
                Label { text: qsTr("Start") }
                Field { id: dcStart; objectName: "dcStart"; placeholderText: "0" }
                Label { text: qsTr("Stop") }
                Field { id: dcStop; objectName: "dcStop"; placeholderText: "5" }
                Label { text: qsTr("Step") }
                Field { id: dcStep; objectName: "dcStep"; placeholderText: "0.1" }
            }

            GridLayout {
                columns: 2
                Label { text: qsTr("Sweep") }
                ComboBox {
                    id: acVariation
                    objectName: "acVariation"
                    Layout.fillWidth: true
                    model: [qsTr("dec (points per decade)"), qsTr("oct (points per octave)"),
                            qsTr("lin (total points)")]
                    onActivated: root.edited()
                }
                Label { text: qsTr("Points") }
                Field { id: acPoints; objectName: "acPoints"; placeholderText: "20" }
                Label { text: qsTr("Start frequency") }
                Field { id: acStart; objectName: "acStart"; placeholderText: "1" }
                Label { text: qsTr("Stop frequency") }
                Field { id: acStop; objectName: "acStop"; placeholderText: "1meg" }
            }

            GridLayout {
                columns: 2
                Label { text: qsTr("Step") }
                Field { id: tranStep; objectName: "tranStep"; placeholderText: "1u" }
                Label { text: qsTr("Stop time") }
                Field { id: tranStop; objectName: "tranStop"; placeholderText: "5m" }
                Label { text: qsTr("Start time (optional)") }
                Field { id: tranStart; objectName: "tranStart"; placeholderText: "0" }
            }
        }

        Label {
            objectName: "cardPreview"
            Layout.fillWidth: true
            wrapMode: Text.WrapAnywhere
            font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
            text: root.followNetlist
                  ? qsTr("Runs the netlist card: %1").arg(Simulator.netlistAnalysis || "—")
                  : qsTr("Runs: %1").arg(root.card)
        }
        Label {
            objectName: "cardError"
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            visible: !root.followNetlist && root.cardError.length > 0
            text: root.cardError
            color: "#d73a49"
        }
        Button {
            objectName: "useNetlistCard"
            text: qsTr("Use netlist card")
            enabled: !root.followNetlist
            onClicked: root.useNetlistCard()
        }

        Item { Layout.fillHeight: true }
    }
}
