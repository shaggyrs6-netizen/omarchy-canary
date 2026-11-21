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
    property string countryQuery: ""
    property bool browseCountries: false
    property string addedServiceId: ""
    property string removingServiceId: ""
    property string removalReturnPage: "favourites"
    readonly property bool settings: page === "settings"
    readonly property bool directory: page === "directory"
    readonly property bool pickingCountry: page === "country"
    readonly property bool addingCustom: page === "custom"
    readonly property bool confirmingRemoval: page === "remove"
    readonly property var addedService: dataModel.rows.find(row => row.id === addedServiceId)
    readonly property var removingService: dataModel.rows.find(row => row.id === removingServiceId)
    readonly property string countryLabel: (dataModel.countries || {})[dataModel.country] || "United States"
    readonly property bool searchingCountries: countryQuery.trim().length > 0
    readonly property bool showCountryResults: pickingCountry && (searchingCountries || browseCountries)
    readonly property var matchingCountries: {
        if (!showCountryResults) return []
        const countries = dataModel.countries || {}
        const term = searchText(countryQuery.trim())
        const aliases = {GB: ["uk", "great britain"], US: ["usa", "america"], TR: ["turkey"]}
        const codes = Object.keys(countries)
        const exact = codes.find(code => code.toLowerCase() === term || (aliases[code] || []).includes(term))
        if (exact) return [exact]
        return codes.filter(code => (code !== "global" || searchingCountries)
            && searchText(countries[code]).includes(term))
            .sort((a, b) => countries[a].localeCompare(countries[b]))
    }
    readonly property var visibleServices: {
        if (settings || pickingCountry || addingCustom || confirmingRemoval) return []
        if (!directory) return dataModel.rows.filter(row => row.favourite || (dataModel.showAll && row.inDirectory))
        const term = query.trim().toLowerCase()
        return dataModel.rows.filter(row => row.inDirectory
            && (row.name + " " + row.status).toLowerCase().includes(term))
    }
    property string errorMessage: ""
    property bool testAlertSent: false
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
        return new Date(timestamp * 1000).toLocaleString(Qt.locale(I18n.formatLocale))
    }

    function open() {
        page = "favourites"
        errorMessage = ""
        testAlertSent = false
        controller.show()
        run(["visit"])
    }
    function close() { controller.hide() }
    function searchText(value) {
        return value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    }
    function goBack() {
        if (confirmingRemoval) {
            page = removalReturnPage
            removingServiceId = ""
        } else if (pickingCountry || addingCustom) {
            page = "directory"
        } else if (page !== "favourites") {
            page = "favourites"
        } else {
            query = ""
            page = "directory"
        }
        errorMessage = ""
    }
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
    component Field: Controls.TextField {
        id: field
        Layout.fillWidth: true
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
        color: root.ink
        placeholderTextColor: Qt.alpha(root.ink, 0.65)
        background: Rectangle {
            radius: Style.space(5)
            color: Qt.alpha(root.ink, 0.06)
            border.color: Qt.alpha(root.ink, field.activeFocus ? 0.7 : 0.18)
        }
    }
    component CountryChoice: Action {
        id: choice
        required property string code
        readonly property bool selected: root.dataModel.country === code
        text: root.dataModel.countries?.[code] || ""
        Accessible.name: text + (selected ? ", selected" : "")
        enabled: !worker.running
        contentItem: Copy {
            text: choice.text + (choice.selected ? "  ✓" : "")
            font.bold: choice.selected
            verticalAlignment: Text.AlignVCenter
        }
        onClicked: root.run(["country", code])
    }
    Process {
        id: worker
        stdout: StdioCollector { id: output; waitForEnd: true }
        stderr: StdioCollector { id: diagnostics; waitForEnd: true }
        onExited: function(code) {
            if (diagnostics.text.trim()) console.warn(diagnostics.text.trim())
            try {
                if (code !== 0) throw new Error("failed")
                const result = JSON.parse(output.text)
                if (worker.command[2] === "test-alert") {
                    if (!result.testAlertSent) throw new Error("notification failed")
                    root.testAlertSent = true
                    return
                }
                if (result.inputError) {
                    root.errorMessage = result.inputError
                    return
                }
                root.nowSeconds = Date.now() / 1000
                root.dataModel = result
                if (worker.command[2] === "country" && root.pickingCountry)
                    root.page = "directory"
                if (result.added && root.addingCustom)
                    root.addedServiceId = result.added
                if (worker.command[2] === "remove-custom" && root.confirmingRemoval)
                    root.goBack()
                if (root.opened && !root.dataModel.attempt && worker.command[2] === "poll")
                    Qt.callLater(() => root.run(["visit"]))
            } catch (_) {
                root.errorMessage = worker.command[2] === "add-custom" ? "Could not save the service. Try again."
                    : worker.command[2] === "remove-custom" ? "Could not remove the service. Try again."
                    : worker.command[2] === "country" ? "Could not save the country. Try again."
                    : worker.command[2] === "test-alert" ? "Could not send the test alert. Try again."
                    : "Could not complete the check. Try Check now."
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
                    Action {
                        visible: !root.addingCustom && !root.pickingCountry && !root.confirmingRemoval
                        text: worker.running ? "Checking…" : "Check now"
                        enabled: !worker.running
                        onClicked: root.run(["refresh"])
                    }
                    Action {
                        text: root.page === "favourites" ? "Add a service" : "Back"
                        enabled: !worker.running || !["add-custom", "remove-custom"].includes(worker.command[2])
                        onClicked: root.goBack()
                    }
                    Action {
                        visible: root.page === "favourites"
                        text: "Settings"
                        onClicked: {
                            root.testAlertSent = false
                            root.page = "settings"
                        }
                    }
                }
                Copy {
                    Layout.fillWidth: true
                        visible: !!root.errorMessage || (!root.addingCustom && !root.pickingCountry && !root.confirmingRemoval)
                    text: root.errorMessage || (root.dataModel.attempt
                        ? "Last refresh attempt " + new Date(root.dataModel.attempt * 1000).toLocaleTimeString(Qt.locale(I18n.formatLocale))
                        : "Awaiting first check")
                    font.pixelSize: Style.font.caption
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: root.pickingCountry
                    spacing: Style.space(8)

                    Copy { text: "Choose provider country"; font.bold: true }
                    Field {
                        id: countrySearch
                        placeholderText: "Search by country name or code"
                        Accessible.name: "Search countries"
                        text: root.countryQuery
                        onTextEdited: {
                            root.countryQuery = text
                            countryResults.positionViewAtBeginning()
                        }
                        onAccepted: {
                            if (!worker.running && root.matchingCountries.length === 1)
                                root.run(["country", root.matchingCountries[0]])
                        }
                    }
                    Copy {
                        Layout.fillWidth: true
                        visible: !root.searchingCountries && !root.browseCountries
                        text: "Countries with provider suggestions"
                        font.pixelSize: Style.font.caption
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        visible: !root.searchingCountries && !root.browseCountries
                        columns: 2
                        Repeater {
                            model: root.pickingCountry ? root.dataModel.providerCountries || [] : []
                            delegate: CountryChoice {
                                required property string modelData
                                code: modelData
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: !root.searchingCountries
                        Action {
                            text: root.browseCountries ? "Quick choices" : "Browse all countries"
                            onClicked: root.browseCountries = !root.browseCountries
                        }
                        CountryChoice { code: "global" }
                    }
                    Copy {
                        visible: root.showCountryResults && !root.matchingCountries.length
                        text: "No matching countries"
                    }
                    ListView {
                        id: countryResults
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: root.showCountryResults && count > 0
                        clip: true
                        model: root.matchingCountries
                        boundsBehavior: Flickable.StopAtBounds
                        Controls.ScrollBar.vertical: Controls.ScrollBar { id: countryScrollbar }
                        delegate: CountryChoice {
                            required property string modelData
                            code: modelData
                            width: countryResults.width - countryScrollbar.width
                        }
                    }
                    Item { Layout.fillHeight: true; visible: !countryResults.visible }
                }

                Controls.ScrollView {
                    visible: !root.pickingCountry
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
                                text: worker.running && worker.command[2] === "test-alert" ? "Sending…" : "Send test alert"
                                enabled: !worker.running
                                onClicked: {
                                    root.testAlertSent = false
                                    root.run(["test-alert"])
                                }
                            }
                            Copy { visible: root.testAlertSent; text: "Test alert sent." }
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
                            RowLayout {
                                Layout.fillWidth: true
                                Action {
                                    text: root.countryLabel + " ▾"
                                    Accessible.name: "Choose provider country. Current country " + root.countryLabel
                                    enabled: !!root.dataModel.countries
                                    onClicked: {
                                        root.countryQuery = ""
                                        root.browseCountries = false
                                        root.page = "country"
                                        countrySearch.forceActiveFocus()
                                    }
                                }
                                Item { Layout.fillWidth: true }
                                Action {
                                    text: "Add my own service"
                                    onClicked: {
                                        serviceName.clear()
                                        serviceUrl.clear()
                                        root.addedServiceId = ""
                                        root.errorMessage = ""
                                        root.page = "custom"
                                        serviceName.forceActiveFocus()
                                    }
                                }
                            }
                            Field {
                                id: serviceSearch
                                placeholderText: "Search services"
                                Accessible.name: "Search services"
                                text: root.query
                                onTextEdited: root.query = text
                            }
                            Copy {
                                Layout.fillWidth: true
                                text: {
                                    const codes = root.dataModel.providerCountries
                                    return codes && !codes.includes(root.dataModel.country)
                                        ? "Global services are available here. Add your own local provider."
                                        : "Star a service to show it on the main panel."
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: root.addingCustom
                            Copy { text: "Add my own service"; font.bold: true }
                            Copy {
                                Layout.fillWidth: true
                                visible: !root.addedService
                                text: "Paste the provider’s public status page or outage checker."
                            }
                            Field {
                                id: serviceName
                                visible: !root.addedService
                                enabled: !worker.running
                                maximumLength: 80
                                placeholderText: "Service name"
                                Accessible.name: "Service name"
                                onAccepted: serviceUrl.forceActiveFocus()
                            }
                            Field {
                                id: serviceUrl
                                visible: !root.addedService
                                enabled: !worker.running
                                maximumLength: 2048
                                placeholderText: "https://"
                                Accessible.name: "Public status or outage checker address"
                                onAccepted: { if (saveService.enabled) saveService.clicked() }
                            }
                            Action {
                                id: saveService
                                visible: !root.addedService
                                enabled: !worker.running && !!serviceName.text.trim() && !!serviceUrl.text.trim()
                                text: worker.running && worker.command[2] === "add-custom" ? "Checking for a feed…" : "Save to favourites"
                                onClicked: root.run(["add-custom", serviceName.text, serviceUrl.text])
                            }
                            Copy {
                                Layout.fillWidth: true
                                visible: !!root.addedService
                                text: !root.addedService ? "" : root.addedService.name
                                    + (root.addedService.kind === "feed" ? " saved. Alerts available."
                                        : " saved. Open the outage checker from Favourites.")
                            }
                            RowLayout {
                                visible: !!root.addedService
                                Action {
                                    visible: root.addedService?.kind === "feed"
                                    enabled: !worker.running
                                    text: root.addedService?.alert ? "Disable alerts" : "Enable alerts"
                                    onClicked: root.run(["alert", root.addedService.id])
                                }
                                Action {
                                    text: "Done"
                                    onClicked: {
                                        root.errorMessage = ""
                                        root.page = "favourites"
                                    }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: root.confirmingRemoval
                            Copy {
                                Layout.fillWidth: true
                                text: "Remove %1?".arg(root.removingService?.name || "custom service")
                                font.bold: true
                            }
                            Copy {
                                Layout.fillWidth: true
                                visible: root.removingService?.kind === "feed"
                                text: "This also clears Canary alerts and saved incident history for this service."
                            }
                            RowLayout {
                                Action { text: "Cancel"; enabled: !worker.running; onClicked: root.goBack() }
                                Action {
                                    text: "Remove service"
                                    enabled: !worker.running && !!root.removingService?.custom
                                    onClicked: root.run(["remove-custom", root.removingServiceId])
                                }
                            }
                        }

                        Copy {
                            visible: root.page === "favourites"
                            text: root.dataModel.showAll ? "Services · favourites first" : "Favourites"
                            font.bold: true
                        }
                        Copy {
                            visible: (root.directory || root.page === "favourites") && !root.visibleServices.length
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
                                        text: card.modelData.alert ? "Disable alerts" : "Enable alerts"
                                        Accessible.name: text + " for " + card.modelData.name
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
                                    Action {
                                        visible: !!card.modelData.custom
                                        text: "Remove"
                                        Accessible.name: "Remove " + card.modelData.name
                                        enabled: !worker.running
                                        onClicked: {
                                            root.removalReturnPage = root.page
                                            root.removingServiceId = card.modelData.id
                                            root.errorMessage = ""
                                            root.page = "remove"
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
}
