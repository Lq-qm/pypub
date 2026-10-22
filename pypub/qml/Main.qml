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

    // A view termina "loading" ANTES do <script> de paginação executar
    // (readyState ainda "loading"), então um refresh único falha.
    // Fazemos polling até o documento responder (ou ~7,5 s de teto).
    property int pageInfoAttempts: 0

    function startPageInfoPolling() {
        window.pageInfoAttempts = 0
        pageInfoTimer.restart()
    }

    Timer {
        id: pageInfoTimer
        interval: 300
        repeat: true
        onTriggered: {
            window.pageInfoAttempts += 1
            reader.runJavaScript(
                "typeof pypub_info === 'function' ? pypub_info() : null",
                function(res) {
                    if (res && res.current) {
                        window._applyPageInfo(res)
                        pageInfoTimer.stop()
                    } else if (window.pageInfoAttempts >= 25) {
                        pageInfoTimer.stop()  // desiste (p. ex. estado vazio)
                    }
                })
        }
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

    // gancho de diagnóstico/teste: executa um script no documento e devolve o
    // resultado (o wrapper Python da view não entrega callback de
    // runJavaScript — QQuickWebEngineView —, então o teste precisa de um
    // caminho QML para inspecionar a página)
    property var dbgResult: null
    property string dbgScript: ""
    onDbgScriptChanged: {
        if (dbgScript) {
            dbgResult = null
            reader.runJavaScript(dbgScript, function(r) { dbgResult = r })
        }
    }

    pageStack.initialPage: K.Page {
        id: mainPage

        // Botões de navegação.
        //
        // Por que ToolButton/ToolBar (QtQuick.Controls) e não K.ActionToolBar
        // + K.Action? Nesta combinação (Kirigami 6.31 do sistema + Qt do wheel
        // do PySide6), o ToolBarLayout C++ do Kirigami não reconhece as
        // ações QML: nenhuma delegação é renderizada (0 botões na árvore),
        // reproduzível até com um K.Action trivial. O ToolButton puro
        // (mesma lib QtQuick.Controls da view) renderiza e dispara onClicked
        // normalmente — verificado offscreen.
        header: ToolBar {
            Row {
                id: headerRow
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                spacing: 2

                ToolButton {
                    text: qsTr("Abrir…")
                    icon.name: "document-open"
                    onClicked: bridge ? bridge.chooseFile() : undefined
                }
                ToolButton {
                    text: qsTr("Cap. anterior")
                    icon.name: "document-go-back"
                    enabled: window.hasBook && window.currentIndex > 0
                    onClicked: bridge ? bridge.previous() : undefined
                }
                ToolButton {
                    text: qsTr("Cap. próximo")
                    icon.name: "document-go-forward"
                    enabled: window.hasBook && window.currentIndex < window.chapterCount - 1
                    onClicked: bridge ? bridge.next() : undefined
                }
                ToolButton {
                    text: qsTr("Pág. anterior")
                    icon.name: "go-previous"
                    enabled: window.hasBook
                    onClicked: window.prevPage()
                }
                ToolButton {
                    id: nextButton
                    text: qsTr("Pág. próxima")
                    icon.name: "go-next"
                    enabled: window.hasBook && window.currentPage < window.pageCount
                    onClicked: window.nextPage()
                }
                ToolButton {
                    text: "Aa−"
                    enabled: window.zoom > 0.6
                    onClicked: bridge ? bridge.zoomOut() : undefined
                }
                ToolButton {
                    text: "Aa+"
                    enabled: window.zoom < 2.5
                    onClicked: bridge ? bridge.zoomIn() : undefined
                }
                ToolButton {
                    text: "1:1"
                    enabled: window.zoom !== 1.0
                    onClicked: bridge ? bridge.resetZoom() : undefined
                }
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
                    window.startPageInfoPolling()
            }
            // Canal JS→Python: com a view focada (estado normal de leitura),
            // Ctrl+←/→ são tratados pelo <script> da página (renderer._PAGE_JS),
            // que escreve a ação no fragmento da URL (#pypub:cmd:<nome>) via
            // history.replaceState — sem entrada no histórico. O QWebChannel não
            // funciona neste wheel do PySide6 (tipo QML quebrado; webChannel
            // exige QQmlWebChannel, sem binding Python), então o fragmento é o
            // nosso transporte: urlChanged dispara aqui e a gente executa.
            onUrlChanged: {
                // neste build, reader.url chega como string no contexto JS
                var u = String(reader.url)
                var hash = u.indexOf("#")
                var fragment = hash >= 0 ? u.substring(hash + 1) : ""
                if (fragment.startsWith("pypub:cmd:")) {
                    var action = fragment.substring("pypub:cmd:".length)
                    if (action === "next") {
                        bridge ? bridge.next() : undefined
                    } else if (action === "prev") {
                        bridge ? bridge.previous() : undefined
                    }
                    // limpa o fragmento (sem deixar no histórico); urlChanged
                    // dispara de novo sem o prefixo → inócuo
                    reader.runJavaScript(
                        "history.replaceState(null, '', location.pathname + location.search)",
                        function() {})
                }
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
            // cobertura extra: nem sempre onLoadingChanged dispara a tempo
            window.startPageInfoPolling()
        }
        // zoom altera a altura do conteúdo → o nº de páginas muda
        function onZoomChanged() {
            window.refreshPageInfo()
        }
    }

    // Atalhos de teclado.
    //
    // Duas frentes, sem página dupla (os Shortcut ficam inativos enquanto a
    // view tem o foco de teclado — enabled: !reader.activeFocus):
    // - com a view focada (estado normal de leitura), o keydown é tratado no
    //   <script> da própria página (renderer._PAGE_JS): PageUp/PageDown
    //   navegam a página; Ctrl+←/→ escrevem #pypub:cmd:<nome> na URL (lido
    //   pelo onUrlChanged da view, acima);
    // - com a view sem foco, estes Shortcut de janela disparam.
    // Ctrl+O abre arquivo (só faz sentido fora da página).
    Shortcut {
        sequences: [StandardKey.Open]
        onActivated: bridge ? bridge.chooseFile() : undefined
    }
    Shortcut {
        sequences: [StandardKey.PrevPage]   // Ctrl+←
        enabled: !reader.activeFocus && window.hasBook && window.currentIndex > 0
        onActivated: bridge ? bridge.previous() : undefined
    }
    Shortcut {
        sequences: [StandardKey.NextPage]   // Ctrl+→
        enabled: !reader.activeFocus && window.hasBook && window.currentIndex < window.chapterCount - 1
        onActivated: bridge ? bridge.next() : undefined
    }
    Shortcut {
        sequences: [StandardKey.PageUp]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: window.prevPage()
    }
    Shortcut {
        sequences: [StandardKey.PageDown]
        enabled: !reader.activeFocus && window.hasBook && window.currentPage < window.pageCount
        onActivated: window.nextPage()
    }
}
