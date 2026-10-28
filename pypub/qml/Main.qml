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
    property int fontIndex: bridge ? bridge.fontIndex : 0
    property real lineHeight: bridge ? bridge.lineHeight : 1.0

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

    // Fonte (Ctrl+1..4): aplica a família escolhida ao documento JÁ
    // CARREGADO sem recarregar (pypub_setFontIndex só toca a fonte — o zoom
    // continua dirigindo o tamanho). O re-embalinhamento pode mudar o nº de
    // páginas → reavaliamos.
    function applyFontToPage() {
        if (!window.hasBook) return
        var i = window.fontIndex | 0
        reader.runJavaScript(
            "typeof pypub_setFontIndex === 'function' ? pypub_setFontIndex(" + i + ") : false",
            function(ok) { if (ok) window.refreshPageInfo() })
    }

    // Espaçamento entre linhas (Ctrl+/Ctrl-): aplica o fator ao documento
    // JÁ CARREGADO sem recarregar (pypub_setLS multiplica a base 1.75 via
    // --pypub-ls). O re-embalinhamento muda o nº de páginas → reavaliamos.
    function applyLineHeightToPage() {
        if (!window.hasBook) return
        var f = Number(window.lineHeight)
        reader.runJavaScript(
            "typeof pypub_setLS === 'function' ? pypub_setLS(" + f + ") : false",
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

    // Sem toolbar global do KPageRow (a faixa de ~46 px no topo era o lugar
    // dos botões; sumário/toolbar saíram a pedido — a janela agora é só
    // conteúdo + rodapé). Sem essa linha, o estilo "Auto" vira ToolBar no
    // desktop e reserva o espaço mesmo sem ações.
    pageStack.globalToolBar.style: K.ApplicationHeaderStyle.None

    pageStack.initialPage: K.Page {
        id: mainPage

        // A view preenche a página inteira (sem paddings — as margens de
        // leitura vêm do CSS do capítulo, renderer._BASE_CSS: 28px em cima,
        // 32px laterais, 72px embaixo). O rodapé (footer) fica colado na base.
        padding: 0
        topPadding: 0
        leftPadding: 0
        rightPadding: 0
        bottomPadding: 0

        // Interface enxuta (sumário e botões saíram a pedido): abrir é
        // Ctrl+O (Shortcut abaixo) ou a linha de comando (pypub livro.epub);
        // capítulo é Ctrl+←/→; página é roda/setas (no <script> da página,
        // renderer._PAGE_JS); a capacidade segue na bridge
        // (chooseFile/openFile/goTo/next/previous/zoomIn/zoomOut/resetZoom).

        contentItem: WebEngineView {
            id: reader
            objectName: "reader"

                    // A página NUNCA escala: o zoom age SOMENTE sobre a fonte
                    // (18px * z) via pypub_setZoom (applyZoomToPage, acima);
                    // o enquadramento é fixo (1 coluna) — não sobre a view.
                    zoomFactor: 1.0
                    backgroundColor: bridge ? bridge.pageBackground : "white"
                    // O conteúdo é servido por um servidor local (127.0.0.1):
                    // loadHtml é instável para HTML grande no Qt 6.12 e a view
                    // não expõe load().
                    Component.onCompleted: {
                        if (bridge) {
                            if (bridge.contentUrl().length > 0)
                                reader.url = Qt.url(bridge.contentUrl())
                        }
                    }
                    // este build não expõe loadFinished(); usa a propriedade loading
                    onLoadingChanged: {
                        if (!loading) {
                            // garante o estado de zoom e de fonte após o load
                            // (a renderização inicial já sai certa; isso cobre
                            // zoom/fonte mudando no load)
                            window.applyZoomToPage()
                            window.applyFontToPage()
                            window.applyLineHeightToPage()
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
                            } else if (action === "font1") {
                                bridge ? bridge.setFontIndex(0) : undefined
                            } else if (action === "font2") {
                                bridge ? bridge.setFontIndex(1) : undefined
                            } else if (action === "font3") {
                                bridge ? bridge.setFontIndex(2) : undefined
                            } else if (action === "font4") {
                                bridge ? bridge.setFontIndex(3) : undefined
                            } else if (action === "lsin") {
                                bridge ? bridge.lineIn() : undefined
                            } else if (action === "lsout") {
                                bridge ? bridge.lineOut() : undefined
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
                    // larga o espaço exato dos indicadores da direita
                    // (cap. + p., com seus 2 espaçamentos — 3 itens, 2 gaps)
                    width: Math.max(0, statusRow.width
                        - (chapterLabel.visible ? chapterLabel.width + statusRow.spacing : 0)
                        - pageLabel.width - 2 * statusRow.spacing)
                    elide: Text.ElideRight
                    text: window.hasBook
                        ? qsTr("Capítulo %1 de %2 — %3").arg(window.currentIndex + 1).arg(window.chapterCount).arg(window.currentChapterTitle)
                        : (window.lastError.length > 0
                            ? window.lastError
                            : qsTr("Abra um arquivo .epub para começar  (Ctrl+O)"))
                    // Erro tem sempre a cor de erro do sistema; o resto segue a
                    // cor de 1º plano do Kirigami (padrão do item).
                    color: window.lastError.length > 0
                        ? (typeof K.Kirigami !== "undefined" ? K.Kirigami.errorColor : "#b03030")
                        : (typeof K.Kirigami !== "undefined" ? K.Kirigami.textColor
                            : (Qt.colorScheme === Qt.colorSchemeDark ? "#e6e6e6" : "#1c1c1c"))
                }
                // Indicador de CAPÍTULO (mesmo padrão do "p. N/M"): em qual
                // capítulo do livro o leitor está — o "p. N/M" ao lado já
                // mostra a página DENTRO daquele capítulo.
                Label {
                    id: chapterLabel
                    objectName: "chapterLabel"
                    anchors.verticalCenter: parent.verticalCenter
                    visible: window.hasBook
                    text: qsTr("cap. %1/%2").arg(window.currentIndex + 1).arg(window.chapterCount)
                }
                Label {
                    id: pageLabel
                    objectName: "pageLabel"
                    anchors.verticalCenter: parent.verticalCenter
                    visible: window.hasBook
                    text: qsTr("p. %1/%2").arg(window.currentPage).arg(window.pageCount)
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
        // fonte: aplica a família escolhida ao documento sem recarga
        // (o re-embalinhamento muda o nº de páginas → refreshPageInfo dentro)
        function onFontChanged() {
            window.applyFontToPage()
        }
        // espaçamento: aplica o fator ao documento sem recarga
        function onLineHeightChanged() {
            window.applyLineHeightToPage()
        }
    }

    // Atalhos de teclado.
    //
    // Duas frentes, sem página dupla (os Shortcut ficam inativos enquanto a
    // view tem o foco de teclado — enabled: !reader.activeFocus):
    // - com a view focada (estado normal de leitura), o keydown é tratado no
    //   <script> da própria página (renderer._PAGE_JS): setas ←/→ e
    //   PageUp/PageDown navegam a página; Ctrl+←/→ escrevem #pypub:cmd:<nome>
    //   na URL (lido pelo onUrlChanged da view, acima); Ctrl+=/Ctrl+0 é zoom
    //   (in/reset; o out segue na API); Ctrl+/Ctrl- ajusta o espaçamento
    //   entre linhas; Ctrl+1..4 troca a fonte (padrão/serif/sans/mono);
    // - com a view sem foco, estes Shortcut de janela disparam a mesma API
    //   (setas ←/→ = página; Ctrl+←/→ = capítulo; Ctrl+1..4 = fonte;
    //   Ctrl+/Ctrl- = espaçamento).
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
    // Fontes (Ctrl+1..4): 1 = padrão (a do livro), 2 = serif,
    // 3 = sans, 4 = mono — a mesma API da página (bridge.setFontIndex).
    Shortcut {
        sequences: ["Ctrl+1"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: bridge ? bridge.setFontIndex(0) : undefined
    }
    Shortcut {
        sequences: ["Ctrl+2"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: bridge ? bridge.setFontIndex(1) : undefined
    }
    Shortcut {
        sequences: ["Ctrl+3"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: bridge ? bridge.setFontIndex(2) : undefined
    }
    Shortcut {
        sequences: ["Ctrl+4"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: bridge ? bridge.setFontIndex(3) : undefined
    }
    // Espaçamento entre linhas (Ctrl+/Ctrl-): a tecla "+" (Shift+= — a
    // própria tecla do "+") aumenta; a tecla "-" diminui. (O zoom in
    // continua em Ctrl+= e o reset em Ctrl+0; o zoom out segue na API —
    // a tecla "Ctrl-" agora é do espaçamento.)
    Shortcut {
        sequences: ["Ctrl+Shift+="]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: bridge ? bridge.lineIn() : undefined
    }
    Shortcut {
        sequences: ["Ctrl+-"]
        enabled: !reader.activeFocus && window.hasBook
        onActivated: bridge ? bridge.lineOut() : undefined
    }
}
