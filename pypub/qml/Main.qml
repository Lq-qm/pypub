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
        if (!res || !res.current) return false
        // descarta info de documento antigo: durante a navegação a URL da
        // view já é a nova, mas o DOM ainda é o anterior (e sua pypub_info()
        // responderia com estado velho, sobrescrevendo o indicador da
        // página nova)
        if (res.src) {
            var src = String(res.src).split("#")[0]
            var cur = String(reader.url).split("#")[0]
            if (cur && src !== cur)
                return false
        }
        window.currentPage = res.current
        window.pageCount = res.total
        return true
    }

    function refreshPageInfo() {
        reader.runJavaScript(
            "typeof pypub_info === 'function' ? pypub_info() : null",
            function(res) { window._applyPageInfo(res) })
    }

    // Zoom: aplica ao documento JÁ CARREGADO sem recarregar a página
    // (pypub_setZoom atualiza SOMENTE a fonte — o enquadramento é fixo em
    // 1 coluna — e a posição de leitura é mantida). A view nunca escala
    // (zoomFactor 1.0). Acontecido o re-layout, o nº de páginas pode mudar
    // → reavaliamos.
    function applyZoomToPage() {
        if (!window.hasBook) return
        var z = Number(window.zoom)
        reader.runJavaScript(
            "typeof pypub_setZoom === 'function' ? pypub_setZoom(" + z + ") : false",
            function(ok) { if (ok) window.refreshPageInfo() })
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
                    // só para quando a info for do documento atual (ou após
                    // o teto de tentativas) — resposta de documento antigo
                    // não vale como "pronto" (p. ex. estado vazio)
                    if (window._applyPageInfo(res) || window.pageInfoAttempts >= 25)
                        pageInfoTimer.stop()
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

        // Sumário (barra lateral): lista de capítulos, clique abre o capítulo.
        property bool showToc: false

        // Toolbar — por que ToolButton/ToolBar (QtQuick.Controls) e não
        // K.ActionToolBar + K.Action? Nesta combinação (Kirigami 6.31 do
        // sistema + Qt do wheel do PySide6), o ToolBarLayout C++ do Kirigami
        // não reconhece as ações QML: nenhuma delegação é renderizada
        // (0 botões na árvore), reproduzível até com um K.Action trivial.
        // O ToolButton puro (mesma lib QtQuick.Controls da view) renderiza
        // e dispara onClicked normalmente — verificado offscreen.
        //
        // A navegação por CAPÍTULO saiu dos botões para o sumário (sidebar);
        // a navegação por PÁGINA saiu dos botões para a roda do mouse +
        // setas ←/→ (tratadas no <script> da página — ver renderer._PAGE_JS;
        // os botões de zoom, Aa−/Aa+/"1:1", já haviam saído a pedido — a
        // capacidade segue na bridge: zoomIn/zoomOut/resetZoom).
        header: ToolBar {
            Row {
                id: headerRow
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                spacing: 2

                ToolButton {
                    objectName: "tocButton"
                    text: qsTr("Sumário")
                    icon.name: "view-list"
                    checkable: true
                    checked: mainPage.showToc
                    onClicked: mainPage.showToc = !mainPage.showToc
                }
                ToolButton {
                    text: qsTr("Abrir…")
                    icon.name: "document-open"
                    onClicked: bridge ? bridge.chooseFile() : undefined
                }
            }
        }

        contentItem: Row {
            id: contentRow
            spacing: 0

            // Sumário (sidebar): lista de capítulos clicável — substitui os
            // botões "Cap. anterior/próximo". QtQuick.Controls puro (o
            // Kirigami C++ tem conflito de binários neste ambiente).
            Rectangle {
                id: tocPanel
                objectName: "tocPanel"
                visible: mainPage.showToc
                width: visible ? 260 : 0
                height: contentRow.height
                color: (typeof Kirigami !== "undefined" ? Kirigami.frameColor
                      : (Qt.colorScheme === Qt.colorSchemeDark ? "#26292e" : "#efeee8"))
                clip: true

                Column {
                    id: tocColumn
                    anchors.fill: parent
                    anchors.topMargin: 10
                    anchors.leftMargin: 6
                    anchors.rightMargin: 6
                    spacing: 6

                    Label {
                        id: tocHeader
                        width: parent.width
                        text: qsTr("Sumário")
                        font.bold: true
                    }

                    ListView {
                        id: tocList
                        width: parent.width
                        height: Math.max(0, parent.height - tocHeader.height - tocColumn.spacing)
                        clip: true
                        model: bridge ? JSON.parse(bridge.chaptersJson) : []
                        delegate: ItemDelegate {
                            id: tocDelegate
                            // objectName estável por índice: o teste localiza
                            // a linha do capítulo via findChild("tocRow-<i>")
                            objectName: "tocRow-" + index
                            width: tocList.width
                            // NB (Qt 6.12/PySide6): com modelo de array JS, o
                            // contexto do delegate expõe o item em ``modelData``
                            // — ``model.title`` não resolve (validado offscreen).
                            text: (index + 1) + ".  " + (modelData.title || "")
                            highlighted: (bridge ? bridge.currentIndex : -1) === index
                            onClicked: { if (bridge) bridge.goTo(index) }
                        }
                    }
                }
            }

            Item {
                id: readerHolder
                width: Math.max(0, contentRow.width - tocPanel.width)
                height: contentRow.height

                WebEngineView {
                    id: reader
                    objectName: "reader"
                    anchors.fill: parent
                    // A página NUNCA escala: o zoom age SOMENTE sobre a fonte
                    // (18px * z) via pypub_setZoom (applyZoomToPage, acima);
                    // o enquadramento é fixo (1 coluna) — não sobre a view.
                    zoomFactor: 1.0
                    backgroundColor: bridge ? bridge.pageBackground : "white"
            // O conteúdo é servido por um servidor local (127.0.0.1): loadHtml
            // é instável para HTML grande no Qt 6.12 e a view não expõe load().
            Component.onCompleted: {
                if (bridge && bridge.contentUrl().length > 0)
                    reader.url = Qt.url(bridge.contentUrl())
            }
            // este build não expõe loadFinished(); usa a propriedade loading
            onLoadingChanged: {
                if (!loading) {
                    // garante o estado de zoom após o load (a renderização
                    // inicial já sai certa; isso cobre zoom mudando no load)
                    window.applyZoomToPage()
                    window.startPageInfoPolling()
                }
            }
            // Canal JS→Python: com a view focada (estado normal de leitura),
            // Ctrl+←/→ e as trocas de página (roda/setas) são tratados pelo
            // <script> da página (renderer._PAGE_JS), que escreve a ação no
            // fragmento da URL (#pypub:cmd:<nome>) via history.replaceState —
            // sem entrada no histórico. O QWebChannel não funciona neste wheel
            // do PySide6 (tipo QML quebrado; webChannel exige QQmlWebChannel,
            // sem binding Python), então o fragmento é o nosso transporte:
            // urlChanged dispara aqui e a gente executa.
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
                    } else if (action === "zoomin") {
                        bridge ? bridge.zoomIn() : undefined
                    } else if (action === "zoomout") {
                        bridge ? bridge.zoomOut() : undefined
                    } else if (action === "zoomreset") {
                        bridge ? bridge.resetZoom() : undefined
                    } else if (action.indexOf("page") === 0) {
                        // Troca de página DENTRO da página (roda do mouse /
                        // setas com foco): a página já andou (go()); aqui o QML
                        // só sincroniza o contador "p. N/M" do rodapé. O nº na
                        // fragment ("page:N") só existe para a URL mudar a cada
                        // evento — o valor de referência é o pypub_info() que
                        // o refresh lê agora.
                        window.refreshPageInfo()
                    }
                    // limpa o fragmento (sem deixar no histórico); urlChanged
                    // dispara de novo sem o prefixo → inócuo
                    reader.runJavaScript(
                        "history.replaceState(null, '', location.pathname + location.search)",
                        function() {})
                }
            }
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
        // zoom: aplica ao documento sem recarga e reavalua a paginação
        // (a altura do conteúdo muda com a fonte/enquadramento)
        function onZoomChanged() {
            window.applyZoomToPage()
        }
    }

    // Atalhos de teclado.
    //
    // Duas frentes, sem página dupla (os Shortcut ficam inativos enquanto a
    // view tem o foco de teclado — enabled: !reader.activeFocus):
    // - com a view focada (estado normal de leitura), o keydown é tratado no
    //   <script> da própria página (renderer._PAGE_JS): setas ←/→ e
    //   PageUp/PageDown navegam a página; Ctrl+←/→ escrevem #pypub:cmd:<nome>
    //   na URL (lido pelo onUrlChanged da view, acima); Ctrl+=/-/0 é zoom;
    // - com a view sem foco, estes Shortcut de janela disparam a mesma API
    //   (setas ←/→ = página; Ctrl+←/→ = capítulo).
    // A troca de página pela RODA do mouse é tratada só no <script> da página
    // (evento wheel da view → documento). Ctrl+O abre arquivo (só faz
    // sentido fora da página).
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
    // Setas simples (view sem foco): → próxima página, ← anterior
    Shortcut {
        sequences: ["Right"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: window.nextPage()
    }
    Shortcut {
        sequences: ["Left"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: window.prevPage()
    }
}
