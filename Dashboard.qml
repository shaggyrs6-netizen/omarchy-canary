pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
    id: root
    moduleName: "shaggyrs6.service-outages"
    manageIpc: false
    property var anchorItem
    property var ownerWidget
    property var dataModel: ({rows: [], connection: "unchecked"})
    property string page: "favourites"
    property string query: ""
    readonly property bool settings: page === "settings"
    readonly property bool directory: page === "directory"
    readonly property var visibleServices: {
        if (settings) return []
        if (!directory) return dataModel.rows.filter(row => row.favourite || (dataModel.showAll && row.inDirectory))
        const term = query.trim().toLowerCase()
        return dataModel.rows.filter(row => row.inDirectory
            && (row.name + " " + row.status).toLowerCase().includes(term))
    }
    property string errorMessage: ""
    property real nowSeconds: Date.now() / 1000
    property int previewStep: 0
    readonly property var indicator: {
        if (!previewTimer.running) return serviceIndicator(dataModel.rows, nowSeconds)
        const affected = previewStep % 3
        const sample = serviceIndicator([
            {alert: true, healthKnown: true, checked: nowSeconds, active: affected >= 1},
            {alert: true, healthKnown: true, checked: nowSeconds, active: affected >= 2}
        ], nowSeconds)
        return {level: sample.level, detail: "Preview · " + sample.detail}
    }

    function previewColours() {
        previewStep = 0
        previewTimer.restart()
    }

    Timer {
        id: previewTimer
        interval: 8000
        repeat: true
        onTriggered: {
            root.previewStep += 1
            if (root.previewStep >= 15) stop()
        }
    }

    function serviceIndicator(rows, now) {
        const selected = rows.filter(row => row.alert)
        const known = selected.filter(row => row.healthKnown && now - row.checked <= 900)
        const affected = known.filter(row => row.active).length
        const unknown = selected.length - known.length
        const stale = selected.filter(row => row.missingActive || row.cachedActive && !known.includes(row)).length
        let level = "neutral"
        let detail = "No services selected for alerts"
        if (selected.length) {
            if (affected) {
                level = affected === 1 ? "amber" : "red"
                detail = affected === 1 ? "1 selected service affected" : affected + " selected services affected"
                if (unknown) detail += " · " + unknown + " without current status"
            } else if (stale || unknown) {
                level = "unknown"
                const count = unknown || stale
                detail = "Status unavailable for " + count + (count === 1 ? " selected service" : " selected services")
            } else {
                level = "green"
                detail = "No active incidents reported for selected services"
            }
        }
        if (stale) detail += " · " + stale + (stale === 1 ? " cached incident feed" : " cached incident feeds")
        return {level: level, detail: detail, stale: stale > 0}
    }

    Timer {
        interval: 15000
        repeat: true
        running: root.dataModel.rows.some(row => row.alert)
        onTriggered: root.nowSeconds = Date.now() / 1000
    }
    readonly property string helper: decodeURIComponent(Qt.resolvedUrl("outages.py").toString().replace(/^file:\/\//, ""))
    readonly property color ink: bar ? bar.foreground : Color.foreground

    function when(timestamp) {
        return new Date(timestamp * 1000).toLocaleString()
    }

    function open() {
        page = "favourites"
        controller.show()
        run(["visit"])
    }
    function close() { controller.hide() }
    function run(args) {
        if (worker.running) return
        errorMessage = ""
        worker.command = ["python3", helper].concat(args)
        worker.running = true
    }
    Component.onCompleted: run(["poll"])

    component Copy: Text {
        textFormat: Text.PlainText
        color: root.ink
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
        wrapMode: Text.Wrap
    }
    component Action: Controls.Button {
        id: action
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
        contentItem: Copy {
            text: action.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            opacity: action.enabled ? 1 : 0.55
        }
        background: Rectangle {
            radius: Style.space(5)
            color: action.down ? Qt.alpha(root.ink, .20) : Qt.alpha(root.ink, action.hovered ? .13 : .06)
            border.color: Qt.alpha(root.ink, action.activeFocus ? .7 : .18)
        }
    }
    Process {
        id: worker
        stdout: StdioCollector { id: output; waitForEnd: true }
        onExited: function(code) {
            try {
                if (code !== 0) throw new Error("failed")
                root.nowSeconds = Date.now() / 1000
                root.dataModel = JSON.parse(output.text)
                if (root.opened && !root.dataModel.attempt && worker.command[2] === "poll")
                    Qt.callLater(() => root.run(["visit"]))
            } catch (_) {
                root.errorMessage = "Could not complete the check. Try Check now."
            }
        }
    }
    Timer {
        interval: 300000
        repeat: true
        running: !!root.dataModel.background
        onTriggered: root.run(["poll"])
    }
    Timer {
        interval: 60000
        repeat: true
        running: root.opened && !root.dataModel.background
        onTriggered: root.run(["visit"])
    }
    IpcHandler {
        target: "shaggyrs6.service-outages"
        function status(): string { return JSON.stringify(root.dataModel) }
        function indicatorStatus(): string { return JSON.stringify(root.indicator) }
        function previewColours(): void { root.previewColours() }
        function stopPreview(): void { previewTimer.stop() }
        function show(): void { root.open() }
        function hide(): void { root.close() }
    }
    KeyboardPanel {
        id: popup
        anchorItem: root.anchorItem
        owner: root.ownerWidget || root
        bar: root.bar
        open: root.opened
        focusTarget: content
        contentWidth: popup.fittedContentWidth(Style.space(540))
        contentHeight: popup.fittedContentHeight(Style.space(620))

        FocusScope {
            id: content
            anchors.fill: parent
            Keys.onEscapePressed: root.close()

            ColumnLayout {
                anchors.fill: parent
                spacing: Style.space(10)
                RowLayout {
                    Layout.fillWidth: true
                    Copy {
                        Layout.fillWidth: true
                        text: "Omarchy Canary"
                        font.pixelSize: Style.font.subtitle
                        font.bold: true
                    }
                    Action { text: worker.running ? "Checking…" : "Check now"; enabled: !worker.running; onClicked: root.run(["refresh"]) }
                    Action {
                        text: root.page === "favourites" ? "Add a service" : "Back"
                        onClicked: {
                            root.query = ""
                            root.page = root.page === "favourites" ? "directory" : "favourites"
                        }
                    }
                    Action {
                        visible: root.page === "favourites"
                        text: "Settings"
                        onClicked: root.page = "settings"
                    }
                }
                Copy {
                    Layout.fillWidth: true
                    text: root.errorMessage || (root.dataModel.attempt
                        ? "Last refresh attempt " + new Date(root.dataModel.attempt * 1000).toLocaleTimeString()
                        : "Awaiting first check")
                    font.pixelSize: Style.font.caption
                }

                Controls.ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: availableWidth
                    clip: true
                    ColumnLayout {
                        width: parent.width
                        spacing: Style.space(8)
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: root.settings
                            Action {
                                text: "Show all services on main panel · " + (root.dataModel.showAll ? "On" : "Off")
                                enabled: !worker.running
                                onClicked: root.run(["showAll", String(!root.dataModel.showAll)])
                            }
                            Action {
                                text: "Background checks · " + (root.dataModel.background ? "On" : "Off")
                                enabled: !worker.running
                                onClicked: root.run(["background", String(!root.dataModel.background)])
                            }
                            Copy { Layout.fillWidth: true; text: "Checks every five minutes while the Omarchy bar is running." }
                            Action {
                                text: "Local connection check · " + (root.dataModel.probe ? "On" : "Off")
                                enabled: !worker.running
                                onClicked: root.run(["probe", String(!root.dataModel.probe)])
                            }
                            Copy { Layout.fillWidth: true; text: "Tests reachability from this PC using Google and Cloudflare." }
                            Action {
                                text: "Quiet hours (10 pm–8 am) · " + (root.dataModel.quiet ? "On" : "Off")
                                enabled: !worker.running
                                onClicked: root.run(["quiet", String(!root.dataModel.quiet)])
                            }
                            Action {
                                text: root.dataModel.snooze ? "Resume alerts" : "Pause alerts for one hour"
                                enabled: !worker.running
                                onClicked: root.run(["snooze", String(!root.dataModel.snooze)])
                            }
                            Action {
                                text: previewTimer.running ? "End colour preview" : "Preview bar colours"
                                onClicked: {
                                    if (previewTimer.running) previewTimer.stop()
                                    else root.previewColours()
                                }
                            }
                        }

                        Copy {
                            Layout.fillWidth: true
                            visible: root.page === "favourites"
                            text: root.dataModel.probe
                                  ? (root.dataModel.connection === "reachable" ? "Your connection · Internet reachable" : "Your connection · Reachability unverified")
                                  : "Your connection · Check off"
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: root.directory
                            Copy { text: "Provider country"; font.bold: true }
                            Flow {
                                Layout.fillWidth: true
                                spacing: Style.space(5)
                                Repeater {
                                    model: root.directory && root.dataModel.countries ? Object.keys(root.dataModel.countries) : []
                                    delegate: Action {
                                        id: countryButton
                                        required property string modelData
                                        readonly property bool selected: root.dataModel.country === modelData
                                        text: (selected ? "✓ " : "") + root.dataModel.countries[modelData]
                                        enabled: !worker.running
                                        onClicked: root.run(["country", modelData])
                                    }
                                }
                            }
                            Controls.TextField {
                                id: serviceSearch
                                Layout.fillWidth: true
                                placeholderText: "Search services"
                                Accessible.name: "Search services"
                                text: root.query
                                onTextEdited: root.query = text
                                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                                font.pixelSize: Style.font.body
                                color: root.ink
                                placeholderTextColor: Qt.alpha(root.ink, 0.65)
                                background: Rectangle {
                                    radius: Style.space(5)
                                    color: Qt.alpha(root.ink, 0.06)
                                    border.color: Qt.alpha(root.ink, serviceSearch.activeFocus ? 0.7 : 0.18)
                                }
                            }
                            Copy { Layout.fillWidth: true; text: "Star a service to show it on the main panel." }
                        }
                        Copy {
                            visible: root.page === "favourites"
                            text: root.dataModel.showAll ? "Services · favourites first" : "Favourites"
                            font.bold: true
                        }
                        Copy {
                            visible: !root.settings && !root.visibleServices.length
                            text: root.directory ? "No matching services" : "Use Add a service to choose favourites."
                        }
                        Repeater {
                            model: root.visibleServices
                            delegate: Rectangle {
                                id: card
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: row.implicitHeight + Style.space(14)
                                color: Qt.alpha(root.ink, .06)
                                radius: Style.space(6)

                                RowLayout {
                                    id: row
                                    anchors.fill: parent
                                    anchors.margins: Style.space(7)
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        Copy { Layout.fillWidth: true; text: card.modelData.name; font.bold: true }
                                        Copy { Layout.fillWidth: true; text: card.modelData.checked && root.nowSeconds - card.modelData.checked > 900
                                                  ? "Status unknown · last check is stale" : card.modelData.status
                                            font.pixelSize: Style.font.caption }
                                        Copy {
                                            Layout.fillWidth: true
                                            visible: !root.settings && card.modelData.details.length > 0
                                            text: card.modelData.details.map(entry =>
                                                entry.title + (entry.active && entry.confirmed
                                                    ? " · Last reported " + root.when(entry.confirmed) : "")).join("\n")
                                            font.pixelSize: Style.font.caption
                                        }
                                    }
                                    Action {
                                        text: card.modelData.favourite ? "★" : "☆"
                                        Accessible.name: (card.modelData.favourite ? "Unpin " : "Pin ") + card.modelData.name
                                        enabled: !worker.running
                                        onClicked: root.run(["favourite", card.modelData.id])
                                    }
                                    Action {
                                        visible: card.modelData.kind === "feed"
                                        text: card.modelData.alert ? "Alerts on" : "Alerts off"
                                        enabled: !worker.running
                                        onClicked: root.run(["alert", card.modelData.id])
                                    }
                                    Action {
                                        visible: !!card.modelData.alertHelp
                                        text: "Provider alerts"
                                        onClicked: Qt.openUrlExternally(card.modelData.alertHelp)
                                    }
                                    Action {
                                        text: card.modelData.kind === "feed" ? "Open" : "Check"
                                        onClicked: Qt.openUrlExternally(card.modelData.url)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
