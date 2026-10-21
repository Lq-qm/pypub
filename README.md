# PyPub

Leitor de **EPUB** em **Qt6**, construído com **PySide6 + Kirigami (KF6)** e
renderização via **Qt WebEngine**.

## Funcionalidades (protótipo)

- Abrir e ler arquivos `.epub` (ZIP + OPF/spine);
- Navegação entre capítulos (anterior / próximo);
- Layout responsivo: **1 coluna** em janelas estreitas e **no máximo 2
  colunas** em janelas largas (CSS multi-column + media query);
- Controle de tamanho de fonte (Aa− / Aa+) e tema claro/escuro;
- Imagens e CSS relativos do EPUB embutidos no HTML como `data:` URIs
  (o PySide6 não expõe `QWebEngineUrlReply` para schema customizado).

## Requisitos do sistema

- Qt6 e Kirigami (KF6) — ex.: no Arch/EndeavourOS:
  `sudo pacman -S qt6-base qt6-declarative kirigami`
- Wayland/X11 para rodar a interface.

## Instalação e execução (com uv)

```sh
uv sync          # cria o venv e instala PySide6 + PySide6-WebEngine
uv run pypub     # abre o leitor
# ou abra um EPUB direto:
uv run pypub livro.epub
```

## Testes (headless/offscreen)

```sh
uv run python tests/test_headless.py
```

Roda com `QT_QPA_PLATFORM=offscreen` e valida: parser do EPUB, bridge
Python↔QML, renderização na WebEngineView (texto, imagem, 2 colunas em
janela larga, navegação, zoom, estado vazio) e a janela Kirigami real
(`pypub/qml/Main.qml`).

## Estrutura

```
pypub/
├── epub.py       # parser de EPUB (container.xml, OPF, spine)
├── renderer.py   # CSS/HTML + handler do esquema epub://
├── bridge.py     # ponte QObject entre Python e QML
├── app.py        # bootstrap QApplication/QML + Kirigami
├── __main__.py   # entrypoint `uv run pypub`
└── qml/
    └── Main.qml  # interface Kirigami (toolbar, navegação, view)
```
