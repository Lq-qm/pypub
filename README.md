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
- **Layout responsivo**: **1 coluna** em janelas estreitas e, no máximo,
  **2 colunas** quando a janela é larga o suficiente (CSS multi-column +
  media query, reavaliada a cada redimensionamento);
- **Zoom** de texto (Aa− / Aa+, faixa 60%–250%);
- **Tema claro / escuro** (aplicado ao conteúdo e à interface);
- **Recursos locais embutidos**: imagens, CSS e fontes referenciados dentro
  do EPUB são carregados de dentro do próprio arquivo;
- **Atalhos**: `Ctrl+O` (abrir), `Ctrl+←` / `Ctrl+→` (capítulos),
  `PageUp` / `PageDown` (páginas);
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
- **Navegar por capítulo**: “Capítulo anterior” / “Capítulo próximo” ou
  `Ctrl+←` / `Ctrl+→`;
- **Navegar por página**: “Página anterior” / “Próxima página” ou
  `PageUp` / `PageDown` (a posição exata fica no rodapé: “p. N/M”);
- **Zoom**: “Aa−” / “Aa+” / “1:1” (reset);
- **Tema**: ação “Tema escuro” (checkable) alterna claro/escuro;
- **Rodar largo**: alargue a janela além de ~980 px para o texto virar
  **2 colunas** (nunca mais que 2).

## Arquitetura

```
┌──────────────────────────────────────────────────────────────────────┐
│  QML / Kirigami  (pypub/qml/Main.qml)                                │
│  K.ApplicationWindow + K.Page                                         │
│  ├── toolbar: Abrir, capítulos, páginas, Aa−/Aa+, 1:1, Tema escuro   │
│  ├── WebEngineView (objectName "reader")  ←  renderiza o HTML        │
│  ├── paginação: pypub_info/nextPage/prevPage via runJavaScript       │
│  └── rodapé: status (capítulo/erros) + “p. N/M” + % zoom             │
└───────────────▲──────────────────────────────────────────────────────┘
                │ context property "bridge"
┌───────────────┴──────────────────────────────────────────────────────┐
│  EpubBridge  (pypub/bridge.py)  — QObject com @Property/@Slot        │
│  • estado: hasBook, bookTitle, chapterCount, currentIndex, zoom,     │
│            dark, lastError                                            │
│  • ações: openFile, closeBook, chooseFile, previous, next, goTo,     │
│           zoomIn/Out, resetZoom, setDark, contentUrl, shutdown       │
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
- **Recursos locais**: o PySide6 não expõe `QWebEngineUrlReply` (schema
  customizado) nessa versão, então imagens/CSS/fontes referenciados no EPUB
  são **inlineados como `data:` URIs** (base64) no HTML. `url(...)` relativos
  dentro de folhas de CSS também são resolvidos e inlineados.
- **Tema**: o HTML do capítulo é re-renderizado com o CSS de tema claro ou
  escuro ao alternar o tema; o fundo da view segue a mesma cor.
- **Paginação**: um pequeno script (`pypub/renderer.py::_PAGE_JS`) é injetado
  no fim do `<body>` de cada capítulo e expõe na janela do documento
  `pypub_info()`, `pypub_nextPage()`, `pypub_prevPage()` e `pypub_goToPage(n)`.
  Uma **página = uma viewport de altura** (`window.innerHeight`), então a
  mecânica é idêntica em 1 ou 2 colunas. O QML chama essas funções via
  `runJavaScript` e atualiza o indicador “p. N/M”. `prevPage` no meio da
  página volta ao topo; `PageUp`/`PageDown` e os botões da toolbar usam a
  mesma API. O nº de páginas é reavaliado em redimensionamento e ao mudar o
  zoom.

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
3. **QML Kirigami** — carrega o `Main.qml` real, cria a janela, abre um livro
   com a UI viva e confere o título.

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
- Escolha de fonte/tema avançado (apenas claro/escuro fixos);
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
- **`loadFinished` não existe** nesta build — o QML usa `onLoadingChanged`
  (propriedade `loading`) para detectar o fim do carregamento.
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
