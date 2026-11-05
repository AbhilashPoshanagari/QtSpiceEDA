// Spike B: draw .asy primitives with Shape on a 16-unit grid. Throwaway.
import QtQuick
import QtQuick.Shapes

Rectangle {
    id: root
    required property var symbols
    readonly property real unit: 2.5        // pixels per .asy unit
    readonly property real grid: 16 * unit  // LTspice snaps to 16 units
    color: "#fbfbf7"

    // Grid dots every 16 units.
    Shape {
        anchors.fill: parent
        ShapePath {
            strokeWidth: -1
            fillColor: "#c8c8c0"
            PathSvg {
                path: {
                    let d = "";
                    for (let x = root.grid; x < root.width; x += root.grid)
                        for (let y = root.grid; y < root.height; y += root.grid)
                            d += "M " + (x - 1) + " " + (y - 1) + " h 2 v 2 h -2 Z ";
                    return d;
                }
            }
        }
    }

    Row {
        x: root.grid
        y: root.grid
        spacing: root.grid
        Repeater {
            model: root.symbols
            delegate: Item {
                id: sym
                required property var modelData
                width: Math.ceil((modelData.maxX - modelData.minX) * root.unit / root.grid + 3) * root.grid
                height: 400

                Text {
                    text: sym.modelData.name + "\nPrefix " + sym.modelData.prefix
                          + "  Value " + sym.modelData.value
                    font.pixelSize: 12
                }

                Item {
                    // Put the symbol's (0,0) on a grid point.
                    x: Math.ceil(-sym.modelData.minX * root.unit / root.grid + 1) * root.grid
                    y: Math.ceil(-sym.modelData.minY * root.unit / root.grid + 2) * root.grid

                    Shape {
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            scale: Qt.size(root.unit, root.unit)
                            strokeColor: "#1c3d8c"
                            strokeWidth: 1.5
                            fillColor: "transparent"
                            capStyle: ShapePath.RoundCap
                            PathSvg { path: sym.modelData.path }
                        }
                    }

                    Repeater {
                        model: sym.modelData.texts
                        Text {
                            required property var modelData
                            x: modelData.x * root.unit - width / 2
                            y: modelData.y * root.unit - height / 2
                            text: modelData.text
                            font.pixelSize: 11
                        }
                    }

                    Repeater {
                        model: sym.modelData.pins
                        delegate: Item {
                            id: pin
                            required property var modelData
                            x: modelData.x * root.unit
                            y: modelData.y * root.unit
                            Rectangle {
                                width: 8; height: 8; radius: 4
                                x: -4; y: -4
                                color: "#c0392b"
                            }
                            Text {
                                x: 6; y: -14
                                text: pin.modelData.order + ":" + pin.modelData.name
                                font.pixelSize: 10
                                color: "#c0392b"
                            }
                        }
                    }
                }
            }
        }
    }
}
