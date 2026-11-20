import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
    id: root
    moduleName: "shaggyrs6.service-outages"
    readonly property bool opened: dashboard.item ? dashboard.item.opened : false
    readonly property bool popoutSwitchClosing: dashboard.item ? dashboard.item.popoutSwitchClosing : false
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    function open() { if (dashboard.item) dashboard.item.open() }
    function close() { if (dashboard.item) dashboard.item.close() }
    function closeForPopoutSwitch() { if (dashboard.item) dashboard.item.closeForPopoutSwitch() }
    function attach() {
        if (!dashboard.item) return
        dashboard.item.bar = root.bar
        dashboard.item.ownerWidget = root
        dashboard.item.anchorItem = button
    }
    onBarChanged: attach()

    Loader {
        id: dashboard
        source: Qt.resolvedUrl("Dashboard.qml")
        visible: false
        onLoaded: root.attach()
    }
    BarIconButton {
        id: button
        bar: root.bar
        anchors.fill: parent
        readonly property var indicator: dashboard.item ? dashboard.item.indicator : null
        readonly property color canaryColor: {
            if (!indicator) return foreground
            switch (indicator.level) {
            case "green": return "#7fce8e"
            case "amber": return "#f0b65a"
            case "red": return "#f47777"
            case "unknown": return "#b3a0e8"
            default: return foreground
            }
        }
        tooltipText: indicator ? "Omarchy Canary · " + indicator.detail : "Omarchy Canary"
        iconComponent: Component {
            Item {
                Canvas {
                    id: canary
                    property color ink: button.canaryColor
                    property bool stale: !!(button.indicator && button.indicator.stale)
                    onInkChanged: requestPaint()
                    onStaleChanged: requestPaint()
                    anchors.fill: parent
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        const size = Math.min(width, height)
                        ctx.translate((width - size) / 2, (height - size) / 2)
                        ctx.scale(size / 24, size / 24)
                        ctx.fillStyle = ink

                        // A side-on songbird with a short beak and an angled tail.
                        ctx.beginPath()
                        ctx.moveTo(2, 16.8)
                        ctx.lineTo(6.7, 12.5)
                        ctx.bezierCurveTo(8.2, 10.2, 11.3, 10.1, 13.2, 10)
                        ctx.bezierCurveTo(13.2, 7.6, 14.6, 5.8, 16.9, 5.8)
                        ctx.bezierCurveTo(18.6, 5.8, 19.8, 6.7, 20.2, 8.1)
                        ctx.lineTo(23, 9.4)
                        ctx.lineTo(20.2, 10.5)
                        ctx.bezierCurveTo(19.9, 14.9, 16.7, 17.8, 12.3, 17.8)
                        ctx.bezierCurveTo(10.1, 17.8, 8.5, 17.4, 7, 16.8)
                        ctx.lineTo(2.7, 18.4)
                        ctx.closePath()
                        ctx.fill()

                        ctx.strokeStyle = ink
                        ctx.lineWidth = 1.6
                        ctx.lineCap = "round"
                        ctx.beginPath()
                        ctx.moveTo(13, 17)
                        ctx.lineTo(14.4, 20.2)
                        ctx.lineTo(17, 20.2)
                        ctx.stroke()

                        ctx.globalCompositeOperation = "destination-out"
                        ctx.beginPath()
                        ctx.arc(17.6, 8.7, 0.9, 0, Math.PI * 2)
                        ctx.fill()
                        ctx.lineWidth = 1.25
                        ctx.beginPath()
                        ctx.moveTo(9.1, 14.4)
                        ctx.quadraticCurveTo(12.4, 15.2, 15.2, 12.6)
                        ctx.stroke()
                        ctx.globalCompositeOperation = "source-over"
                        if (stale) {
                            ctx.beginPath()
                            ctx.fillStyle = "#b3a0e8"
                            ctx.arc(5, 5, 2, 0, Math.PI * 2)
                            ctx.fill()
                        }
                    }
                }
            }
        }
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.LeftButton) {
                if (root.opened) root.close()
                else root.open()
            }
        }
    }
}
