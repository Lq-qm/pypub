# PyPub

Leitor de **EPUB** em **Qt6** — interface 100% Qt construída com
**PySide6 + Kirigami (KF6)** e renderização de conteúdo via **Qt WebEngine**.

> **Status: protótipo.** Foco em abrir um `.epub`, exibi-lo em **no máximo 2
> colunas**, adaptar-se ao tamanho da janela e rodar com testes headless.
> Ainda não tem: persistência de posição, sumário (TOC), busca, dicionário,
> exportação de notas, etc. (ver [Limitações](#limitações-e-próximos-passos)).

---

## Índice

- [Funcionalidades](#funcionalidades)
- [Stack técnica](#stack-técnica)
- [Requisitos](#requisitos)
- [Instalação e execução](#instalação-e-execução)
- [Uso](#uso)
- [Arquitetura](#arquitetura)
- [Como a renderização funciona](#como-a-renderização-funciona)
- [Testes](#testes)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Limitações e próximos passos](#limitações-e-próximos-passos)
- [Notas de compatibilidade (Qt 6.12 / PySide6)](#notas-de-compatibilidade-qt-612--pyside6)

---

## Funcionalidades

- **Abrir e ler `.epub`** (EPUB 2/3, ZIP + OCF/OPF) — sem bibliotecas de
  parseamento externas;
- **Navegação entre capítulos** (anterior / próximo), seguindo o *spine*
  linear do livro;
- **Navegação por página** (anterior / próxima) — uma “página” é o que cabe
  numa viewport de altura; funciona com 1 ou 2 colunas, com indicador
  “p. N/M” no rodapé;
- **Layout responsivo e centralizado**: **1 coluna** em janelas estreitas e,
  no máximo, **2 colunas** quando a janela é larga o suficiente (CSS
  multi-column + media query, reavaliada a cada redimensionamento); o bloco
  de leitura fica **centralizado** na janela (não colado ao canto
  superior esquerdo), mesmo em livros cujo CSS define margens no `body`;
  em janelas estreitas o texto ocupa a largura disponível com respiro
  lateral;
- **Zoom** de texto (faixa 60%–250%) — capacidade mantida na bridge
  (`zoomIn`/`zoomOut`/`resetZoom`); os botões Aa−/Aa+ não estão mais na
  toolbar (restou o “1:1” para reset);
- **Tema claro / escuro seguindo o sistema do OS** (sem alternância manual —
  aplicado à interface e ao conteúdo, e re-renderizado ao vivo se o tema do
  sistema mudar com o app aberto);
- **Recursos locais embutidos**: imagens, CSS e fontes referenciados dentro
  do EPUB são carregados de dentro do próprio arquivo;
- **Atalhos**: `Ctrl+O` (abrir), `Ctrl+←` / `Ctrl+→` (capítulos),
  `PageUp` / `PageDown` (páginas) — funcionam com a view focada (tratados
  no próprio documento) e sem foco (atalhos de janela);
- **Testes headless** (offscreen) cobrindo parser, ponte Python↔QML,
  renderização e a janela Kirigami real.

## Stack técnica

| Camada | Tecnologia |
| --- | --- |
| Linguagem | Python (≥ 3.10) |
| Bindings Qt | **PySide6 6.12** (+ `PySide6-WebEngine`) |
| Interface | **Qt Quick / QML** + **Kirigami (KF6)** |
| Renderização | **Qt WebEngine** (`WebEngineView`) |
| Parser EPUB | `zipfile` + `xml.etree` (padrão da biblioteca) |
| Empacotamento / env | `uv` + `pyproject.toml` |

## Requisitos

- **Qt6** e **Kirigami (KF6)** do sistema — por exemplo, em Arch/EndeavourOS:
  ```sh
  sudo pacman -S qt6-base qt6-declarative kirigami
  ```
- **Wayland** ou **X11** para rodar a interface;
- `uv` (para gerenciar o ambiente virtual) — ou qualquer gerenciador de venv.

> O QML do **wheel do PySide6** tem prioridade no `QML2_IMPORT_PATH` (para que
> `QtQuick`, `QtQuick.Controls` e `QtWebEngine` venham da mesma build das
> libs Qt dos bindings); o QML do **sistema** entra em seguida, de onde vem o
> Kirigami/KF6.

## Instalação e execução

```sh
uv sync            # cria o .venv e instala PySide6 + PySide6-WebEngine
uv run pypub       # abre o leitor (use "Abrir…" / Ctrl+O para escolher o .epub)
```

Ou abra um EPUB diretamente ao iniciar:

```sh
uv run pypub caminho/para/livro.epub
```

Sem `uv`:

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e .
pypub livro.epub
```

## Uso

- **Abrir**: botão “Abrir…” na toolbar ou `Ctrl+O`;
- **Navegar por capítulo**: “Cap. anterior” / “Cap. próximo” ou
  `Ctrl+←` / `Ctrl+→`;
- **Navegar por página**: “Pág. anterior” / “Pág. próxima” ou
  `PageUp` / `PageDown` (a posição exata fica no rodapé: “p. N/M”);
- **Zoom**: a capacidade segue na bridge (`zoomIn`/`zoomOut`/`resetZoom`);
  na toolbar resta apenas o “1:1” (reset) — os botões Aa−/Aa+ foram
  removidos a pedido;
- **Tema**: segue o esquema claro/escuro do sistema (ex.: `KColorScheme` no
  KDE — Breeze/BreezeDark); não há botão de alternância;
- **Rodar largo**: alargue a janela além de ~980 px para o texto virar
  **2 colunas** (nunca mais que 2).

## Arquitetura

```
┌──────────────────────────────────────────────────────────────────────┐
│  QML / Kirigami  (pypub/qml/Main.qml)                                │
│  K.ApplicationWindow + K.Page                                         │
│  ├── toolbar: ToolButtons (Abrir, capítulos, páginas, 1:1)           │
│  │            ← ToolButton/ToolBar do QtQuick.Controls               │
│  ├── WebEngineView (objectName "reader")  ←  renderiza o HTML        │
│  │   • paginação: pypub_info/nextPage/prevPage via runJavaScript     │
│  │   • JS→Python: onUrlChanged lê #pypub:cmd:<nome> (Ctrl+←/→)       │
│  ├── paginação: pypub_info/nextPage/prevPage via runJavaScript       │
│  ├── atalhos: Shortcut de janela (view sem foco) + keydown na página │
│  └── rodapé: status (capítulo/erros) + “p. N/M” + % zoom             │
└───────────────▲──────────────────────────────────────────────────────┘
                │ context property "bridge"
┌───────────────┴──────────────────────────────────────────────────────┐
│  EpubBridge  (pypub/bridge.py)  — QObject com @Property/@Slot        │
│  • estado: hasBook, bookTitle, chapterCount, currentIndex, zoom,     │
│            dark, lastError                                            │
│  • ações: openFile, closeBook, chooseFile, previous, next, goTo,     │
│           zoomIn/Out, resetZoom, contentUrl, shutdown                │
│  • tema: dark segue o OS (QStyleHints.colorScheme) + sinal ao vivo   │
│  • sinais: bookChanged, positionChanged, zoomChanged, darkChanged,   │
│            errorChanged, contentChanged(url)                         │
└───────────────▲──────────────────────────────────────────────────────┘
                │
┌───────────────┴──────────────────────────────────────────────────────┐
│  ChapterServer  (pypub/server.py)  — HTTP local em 127.0.0.1         │
│  • porta aleatória, thread daemon, só loopback                       │
│  • serve o HTML autocontido atual (recursos inline em data: URI)     │
│  • URL com ?r=<revisão> muda a cada emissão → garante reload         │
└──────────────────────────────────────────────────────────────────────┘
                │
   ┌────────────┴────────────┐
   │                         │
┌──┴───────────────┐   ┌─────┴───────────────────────────────┐
│  Epub (epub.py)  │   │  renderer.py                          │
│  parser OCF/OPF  │   │  • inline de recursos como data: URI  │
│  spine linear    │   │  • injeção de CSS responsivo (1/2col) │
│  acesso ao ZIP   │   │  • estado vazio + cor de fundo        │
└──────────────────┘   └────────────────────────────────────────┘
```

**Fluxo de conteúdo:** a *bridge* é a única dona do estado. Quando o livro,
a posição, o tema ou o zoom mudam, ela prepara o HTML **pronto** (recursos
inline + CSS do leitor + script de paginação), o **serve** no servidor local
e emite `contentChanged(url)` com a URL `http://127.0.0.1:<porta>/chapter/N?r=R`.
O QML apenas reage: `reader.url = Qt.url(url)`. O QML **não** conhece o
parser nem o renderer.

> **Por que um servidor local em vez de `loadHtml(html)`?** No Qt 6.12 a view
> QML **não expõe** `load(url)`, e `loadHtml(html)` é **instável para HTML
> grande** (por volta de ~500 KB a página carrega em branco — limite do IPC
> interno do WebEngine). Navegar para `http://127.0.0.1` usa o pipeline normal
> de carregamento do Chromium, **sem limite de tamanho** — essencial para
> capítulos reais, que podem ter vários MB (ex.: capas/imagens embutidas).

## Como a renderização funciona

- **Colunas**: o CSS do leitor usa `columns: 2` dentro de
  `@media (min-width: 980px)`. Abaixo disso, 1 coluna. O WebEngine reavalia a
  media query automaticamente a cada redimensionamento da janela — por isso a
  view “se adapta” sem recarregar a página.
- **Centralização**: o bloco de leitura tem `max-width` (44em / 70em em 2
  colunas) e `margin: 0 auto` — em janelas largas, centralizado; em janelas
  estreitas, ocupa a largura com padding lateral de 32px.
  - **Por que os seletores `body, body[class]`?** EPUBs exportados do
    Calibre colocam classes no `<body>` (ex.: `<body class="calibre">`) e o
    CSS do próprio livro define `.calibre { margin: 0 5pt; padding: 0 }` —
    classe (especificidade 0-1-0) vence regra de elemento (`body`: 0-0-1) e
    a página ficava colada à borda esquerda, esticada até o canto superior
    esquerdo. O CSS do leitor usa `body[class]` (0-1-1), que vence a classe
    do livro, para `margin`/`padding`/`max-width`/`columns`/cores.
- **Recursos locais**: o PySide6 não expõe `QWebEngineUrlReply` (schema
  customizado) nessa versão, então imagens/CSS/fontes referenciados no EPUB
  são **inlineados como `data:` URIs** (base64) no HTML. `url(...)` relativos
  dentro de folhas de CSS também são resolvidos e inlineados.
- **Tema**: segue o **sistema operacional** — a bridge lê
  `QApplication.styleHints().colorScheme()` (no KDE, a `KColorScheme` do
  Plasma; em plataforma `offscreen`, `Unknown` → claro) e se conecta ao sinal
  `colorSchemeChanged`: se o tema do OS muda com o app aberto, o capítulo é
  re-renderizado na hora. O fundo da view segue a mesma cor
  (`bridge.pageBackground`). Não há alternância manual.
  - NB (PySide6 6.12): as constantes do enum `ColorScheme` **não** são
    expostas (nem `QStyleHints.ColorScheme` nem flat), então a comparação é
    `"Dark" in str(scheme)`. E `QStyleHints` não instancia — acessa via
    `QApplication.styleHints()`.
- **Paginação**: um pequeno script (`pypub/renderer.py::_PAGE_JS`) é injetado
  no fim do `<body>` de cada capítulo e expõe na janela do documento
  `pypub_info()`, `pypub_nextPage()`, `pypub_prevPage()` e `pypub_goToPage(n)`.
  Uma **página = uma viewport de altura** (`window.innerHeight`), então a
  mecânica é idêntica em 1 ou 2 colunas. O QML chama essas funções via
  `runJavaScript` e atualiza o indicador “p. N/M”. `prevPage` no meio da
  página volta ao topo; `PageUp`/`PageDown` e os botões da toolbar usam a
  mesma API. O nº de páginas é reavaliado em redimensionamento e ao mudar o
  zoom.
- **Atalhos de teclado** — duas frentes complementares, sem página dupla:
  - com a **view focada** (estado normal de leitura), o `keydown` é tratado
    **no próprio documento** (o `<script>` injetado): `PageUp`/`PageDown`
    navegam a página; `Ctrl+←`/`Ctrl+→` escrevem a ação no fragmento da URL;
  - com a **view sem foco**, os itens `Shortcut` de janela disparam
    (ficam inativos enquanto `reader.activeFocus` — `enabled: !reader.activeFocus`).
- **Canal JS→Python (troca de capítulo pelo teclado)** — o `QWebChannel`
  não funciona neste wheel do PySide6 (o tipo QML não carrega, e a
  propriedade `webChannel` da view exige `QQmlWebChannel`, que não tem
  binding Python). O canal usado é o **fragmento da URL**: a página chama
  `history.replaceState(null, "", "#pypub:cmd:<nome>")` (sem entrada no
  histórico) e o `onUrlChanged` do QML lê o fragmento, executa a ação
  (`bridge.next()`/`bridge.previous()`) e limpa o fragmento.
- **Quando o indicador é preenchido**: a view termina o estado `loading`
  **antes** do `<script>` de paginação executar (`document.readyState` ainda
  `loading`), então um refresh único em `onLoadingChanged` ficaria em “p. 1/1”.
  O QML faz **polling** (`pageInfoTimer`, a cada 300 ms, teto ~7,5 s) até o
  documento responder com `pypub_info()`; o mesmo polling é disparado no
  `onContentChanged` como cobertura extra.

## Testes

```sh
uv run python tests/test_headless.py
```

Roda com `QT_QPA_PLATFORM=offscreen` (e `QTWEBENGINE_DISABLE_SANDBOX=1`) e
valida, sem abrir display:

1. **Parser** — título/autor, 3 capítulos, normalização de caminho,
   inline de imagem/CSS, CSS responsivo presente, rejeição de EPUB inválido;
2. **Bridge + renderização** — abre o livro, espera o conteúdo na
   `WebEngineView` (texto, imagem com `naturalWidth > 0`,
   `columnCount == 2` em janela de 1400 px), valida a **paginação**
   (`pypub_info`, `nextPage`, `prevPage`, `goToPage`, clamps), navega
   (next/previous/goTo), ajusta zoom, trata erro de arquivo inexistente,
   valida **livro real EPUB 2** (capítulo longo servido via HTTP local +
   paginação), captura screenshot e valida o estado vazio após fechar;
3. **QML Kirigami (UI real)** — carrega o `Main.qml` real, cria a janela,
   abre um livro com a UI viva e confere o título, o indicador “p. N/M”
   (polling), `nextPage`/`prevPage`, **clique em `ToolButton`** (evento de
   mouse direto no item), **teclado na página** (`PageDown`/`PageUp` via
   `keydown` do documento) e o **canal JS→Python** (`Ctrl+→` no documento →
   fragmento de URL → `bridge.next()`).

> O teste gera um EPUB de exemplo em `/tmp/pypub_sample.epub` (via
> `tools/make_sample_epub.py`) e salva a screenshot em `/tmp/pypub_shot.png`.

## Estrutura do projeto

```
.
├── pyproject.toml           # metadados, dependências, entrypoint `pypub`
├── README.md
├── pypub/
│   ├── __init__.py
│   ├── __main__.py          # `uv run pypub` / `python -m pypub`
│   ├── app.py               # bootstrap: QApplication + QQmlEngine + "bridge"
│   ├── bridge.py            # EpubBridge (QObject): estado + ações + sinais
│   ├── epub.py              # parser de EPUB (container.xml, OPF, spine)
│   ├── renderer.py          # inline de recursos + CSS responsivo + script de paginação
│   ├── server.py            # servidor HTTP local (127.0.0.1) que serve o HTML
│   └── qml/
│       └── Main.qml         # interface Kirigami (toolbar, view, rodapé)
├── tests/
│   └── test_headless.py     # testes offscreen (parser/bridge/render/QML)
└── tools/
    └── make_sample_epub.py  # gera um .epub de exemplo para os testes
```

## Limitações e próximos passos

**Fora do escopo do protótipo:**

- Persistência da posição de leitura por livro;
- Sumário (TOC) / ir para um capítulo específico;
- Busca no texto, dicionário, notas/anotações;
- Escolha de fonte/tema avançado (tema claro/escuro segue o OS, sem
  override manual);
- Arrastar-e-soltar / múltiplos livros abertos.

**Ideias de evolução:**

- `QWebEngineUrlSchemeHandler` (se/ quando exposto no PySide6) para servir
  recursos do EPUB por schema customizado em vez de inline `data:` URIs
  (menos memória em livros grandes);
- Persistência (ex.: `QSettings` ou JSON) de posição/zoom/tema;
- Testes parametrizados (pytest) e CI com `QT_QPA_PLATFORM=offscreen`.

## Solução de problemas

**`ModuleNotFoundError: No module named 'pypub'`** ao rodar `uv run pypub` —
o pacote do projeto não foi instalado de forma editável (o `.pth` está
ausente do site-packages). Corrija com:

```sh
uv sync --reinstall-package pypub   # ou: uv pip install -e .
```

**Avisos Vulkan/GLX** (ex.: `QVulkanInstance: Failed to initialize
Vulkan`, `GLX: No XCB Connection`) — inofensivos em ambientes sem display
ou sem GPU (incluindo os testes offscreen); a interface segue funcionando
com renderização por software.

## Notas de compatibilidade (Qt 6.12 / PySide6)

Decisões tomadas por limitações reais da stack nesta versão:

- **`WebEngineView` (módulo `QtWebEngine`)** — a view QML **não** expõe a
  propriedade `page` nem o método `load(url)` em 6.12; usamos API direta da
  view (`runJavaScript`, `zoomFactor`, `backgroundColor`) e a propriedade
  **setável** `url` para navegar.
- **`loadHtml(html)` instável para conteúdo grande** — por volta de ~500 KB
  a página carrega em branco (limite do IPC interno do WebEngine); por isso
  o conteúdo é servido por um **servidor HTTP local** (`pypub/server.py`)
  e a view navega para `http://127.0.0.1:<porta>/...` (sem limite de
  tamanho).
- **`loadFinished` não existe** nesta build — e a propriedade `loading` vira
  `false` **antes** do script de paginação rodar; por isso o QML não confia
  num refresh único: usa um `Timer` de polling até `pypub_info()` responder.
- **Atalhos por item `Shortcut`**, não por `K.Action { shortcut: … }` — o
  `shortcut` do Kirigami deriva de uma `QAction` nativa (`fromQAction`) e não
  dispara em ações puramente QML; itens `Shortcut` no nível da janela
  (mesmo padrão do `Ctrl+O`) funcionam. Nesta build, `Shortcut` **não tem**
  a propriedade `when` — a condição vai em `enabled`.
- **Toolbar com `ToolButton`/`ToolBar` do `QtQuick.Controls`**, e não
  `K.ActionToolBar`/`K.Action` — o plugin C++ do Kirigami do sistema é
  linkado contra o Qt do sistema, enquanto o processo PySide6 carrega as
  libs Qt do wheel; com essa mistura, o `ToolBarLayout` do Kirigami fica com
  largura zero e **nenhum** botão é renderizado (reproduzível até com uma
  `K.Action` trivial). O `ToolButton` puro (mesma lib `QtQuick.Controls` da
  view) renderiza e dispara `onClicked` normalmente — validado offscreen.
- **`QWebChannel` não funciona neste wheel** — `import QtWebChannel` falha
  (`QWebChannel is not a type`) mesmo com o plugin presente e `dlopen` OK; e
  a propriedade `webChannel` da `WebEngineView` espera `QQmlWebChannel`
  (`Unable to assign QWebChannel to QQmlWebChannel`), classe que o PySide6
  não expõe. O canal JS→Python usado é o fragmento da URL
  (`#pypub:cmd:<nome>` + `history.replaceState` + sinal `urlChanged`).
- **`runJavaScript` a partir do Python não entrega callback** na
  `QQuickWebEngineView` desta build (`javaScriptResult` nem existe no
  wrapper); chamadas QML→JS funcionam via `runJavaScript` no QML. O QML expõe
  hooks `dbgScript`/`dbgResult` no `Main.qml` para os testes inspecionarem a
  página.
- **`reader.url` chega como string** no contexto JS do QML (sem método
  `fragment()`); o fragmento é extraído por `String(reader.url)` + `indexOf("#")`.
- **`objectName: "reader"`** — `id:` não define `objectName`; o teste localiza
  a view via `findChild(QQuickItem, "reader")`.
- **`data:` URIs em vez de schema customizado** — `QWebEngineUrlReply` não
  está disponível no PySide6 6.12.
- **Root de teste = `Window`** — com root `Rectangle` o viewport da
  `WebEngineView` fica com largura 0 (media query de 2 colunas não dispara);
  `Window` garante um viewport real offscreen.
- **Priorizar o QML do wheel do PySide6** no `QML2_IMPORT_PATH` evita carregar
  plugins/ libs Qt do sistema incompatíveis com as libs dos bindings
  (erro de símbolo `undefined` em `QtWebEngineCore`).
- **Singleton `Kirigami`** pode não estar registrado em algumas builds; o QML
  usa `typeof Kirigami !== "undefined"` com fallback de cores por esquema.
