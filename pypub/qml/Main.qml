import QtQuick
import QtQuick.Controls
import QtWebEngine
import org.kde.kirigami as K

K.ApplicationWindow {
    id: window

    width: 1100
    height: 760
    minimumWidth: 440
    minimumHeight: 380

    property bool dark: false

    // estado de paginação (uma "página" = uma viewport de altura do conteúdo)
    property int currentPage: 1
    property int pageCount: 1

    // Espelhos nulos-seguros do estado da bridge (evita TypeErrors quando o
    // contexto ``bridge`` é destruído no teardown, com bindings ainda vivos).
    property bool hasBook: bridge ? bridge.hasBook : false
    property string bookTitle: bridge ? bridge.bookTitle : ""
    property int chapterCount: bridge ? bridge.chapterCount : 0
    property int currentIndex: bridge ? bridge.currentIndex : 0
    property string currentChapterTitle: bridge ? bridge.currentChapterTitle : ""
    property string lastError: bridge ? bridge.lastError : ""
    property real zoom: bridge ? bridge.zoom : 1.0

    title: hasBook ? (bookTitle + " — PyPub") : "PyPub"

    function setDarkMode(d) {
        dark = d
        Qt.colorScheme = d ? Qt.colorSchemeDark : Qt.colorSchemeLight
        bridge.setDark(d)
    }

    function _applyPageInfo(res) {
        if (res && res.current) {
            window.currentPage = res.current
            window.pageCount = res.total
        }
    }

    function refreshPageInfo() {
        reader.runJavaScript(
            "typeof pypub_info === 'function' ? pypub_info() : null",
            function(res) { window._applyPageInfo(res) })
    }

    function nextPage() {
        reader.runJavaScript(
            "typeof pypub_nextPage === 'function' ? pypub_nextPage() : null",
            function(res) { window._applyPageInfo(res) })
    }

    function prevPage() {
        reader.runJavaScript(
            "typeof pypub_prevPage === 'function' ? pypub_prevPage() : null",
            function(res) { window._applyPageInfo(res) })
    }

    // a página depende da altura do viewport: reavaliamos em redimensionamento
    onWidthChanged: window.refreshPageInfo()
    onHeightChanged: window.refreshPageInfo()

    pageStack.initialPage: K.Page {
        id: mainPage

        header: K.ActionToolBar {
            K.Action {
                text: qsTr("Abrir…")
                icon.name: "document-open"
                onTriggered: bridge.chooseFile()
            }
            K.Action {
                text: qsTr("Capítulo anterior")
                icon.name: "document-go-back"
                enabled: window.hasBook && window.currentIndex > 0
                shortcut: StandardKey.PrevPage
                onTriggered: bridge.previous()
            }
            K.Action {
                text: qsTr("Capítulo próximo")
                icon.name: "document-go-forward"
                enabled: window.hasBook && window.currentIndex < window.chapterCount - 1
                shortcut: StandardKey.NextPage
                onTriggered: bridge.next()
            }
            K.Action {
                text: qsTr("Página anterior")
                icon.name: "go-previous"
                enabled: window.hasBook
                shortcut: StandardKey.PageUp
                onTriggered: window.prevPage()
            }
            K.Action {
                text: qsTr("Próxima página")
                icon.name: "go-next"
                enabled: window.hasBook && window.currentPage < window.pageCount
                shortcut: StandardKey.PageDown
                onTriggered: window.nextPage()
            }
            K.Action {
                text: "Aa−"
                enabled: window.zoom > 0.6
                onTriggered: bridge.zoomOut()
            }
            K.Action {
                text: "Aa+"
                enabled: window.zoom < 2.5
                onTriggered: bridge.zoomIn()
            }
            K.Action {
                text: "1:1"
                enabled: window.zoom !== 1.0
                onTriggered: bridge.resetZoom()
            }
            K.Action {
                text: qsTr("Tema escuro")
                icon.name: "preferences-desktop-display-color"
                checkable: true
                checked: dark
                onTriggered: window.setDarkMode(checked)
            }
        }

        contentItem: WebEngineView {
            id: reader
            objectName: "reader"
            anchors.fill: parent
            zoomFactor: window.zoom
            backgroundColor: bridge ? bridge.pageBackground : "white"
            // O conteúdo é servido por um servidor local (127.0.0.1): loadHtml
            // é instável para HTML grande no Qt 6.12 e a view não expõe load().
            Component.onCompleted: {
                if (bridge && bridge.contentUrl().length > 0)
                    reader.url = Qt.url(bridge.contentUrl())
            }
            // este build não expõe loadFinished(); usa a propriedade loading
            onLoadingChanged: {
                if (!loading)
                    window.refreshPageInfo()
            }
        }

        footer: Frame {
            Row {
                id: statusRow
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: 10

                Label {
                    id: statusLabel
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(0, statusRow.width
                        - pageLabel.width - zoomLabel.width - 2 * statusRow.spacing)
                    elide: Text.ElideRight
                    text: window.hasBook
                        ? qsTr("Capítulo %1 de %2 — %3").arg(window.currentIndex + 1).arg(window.chapterCount).arg(window.currentChapterTitle)
                        : (window.lastError.length > 0
                            ? window.lastError
                            : qsTr("Abra um arquivo .epub para começar  (Ctrl+O)"))
                    // O singleton ``Kirigami`` nem sempre é registrado; usa-o se
                    // existir, senão cai em cores adaptadas ao esquema claro/escuro.
                    color: window.lastError.length > 0
                        ? (typeof Kirigami !== "undefined" ? Kirigami.errorColor : "#b03030")
                        : (typeof Kirigami !== "undefined" ? Kirigami.textColor
                            : (Qt.colorScheme === Qt.colorSchemeDark ? "#e6e6e6" : "#1c1c1c"))
                }
                Label {
                    id: pageLabel
                    anchors.verticalCenter: parent.verticalCenter
                    visible: window.hasBook
                    text: qsTr("p. %1/%2").arg(window.currentPage).arg(window.pageCount)
                }
                Label {
                    id: zoomLabel
                    anchors.verticalCenter: parent.verticalCenter
                    text: Math.round(window.zoom * 100) + " %"
                }
            }
        }
    }

    // Sincroniza o conteúdo renderizado com a ponte
    Connections {
        target: bridge
        function onContentChanged(url) {
            reader.url = Qt.url(url)
        }
        // zoom altera a altura do conteúdo → o nº de páginas muda
        function onZoomChanged() {
            window.refreshPageInfo()
        }
    }

    // Atalho global: Ctrl+O abre o diálogo de arquivos
    Shortcut {
        sequences: [StandardKey.Open]
        onActivated: bridge.chooseFile()
    }
}
