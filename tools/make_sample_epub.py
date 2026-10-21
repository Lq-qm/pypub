#!/usr/bin/env python3
"""Gera um EPUB de exemplo (3 capítulos, imagem, CSS interno) para testes.

Uso: python tools/make_sample_epub.py [saida.epub]
"""

from __future__ import annotations

import struct
import sys
import zlib

OUT = sys.argv[1] if len(sys.argv) > 1 else "sample.epub"

MIMETYPE = b"application/epub+zip"

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2009/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:pypub-exemplo</dc:identifier>
    <dc:title>EPUB de Exemplo</dc:title>
    <dc:creator>Autore de Teste</dc:creator>
    <meta property="dcterms:language">pt</meta>
  </metadata>
  <manifest>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch3" href="chapter3.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
    <itemref idref="ch3"/>
  </spine>
</package>
"""

CSS = """
body { font-family: serif; color: #333; }
h1 { color: #2b6cb0; }
.caixa { border: 1px solid #999; padding: 1em; margin: 1em 0; }
"""


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """PNG sólido simples (sem dependências)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def paragraph(i: int) -> str:
    return (
        f"<p>Parágrafo {i}: o rápido avozinhento marrom salta sobre o cão preguiçoso. "
        "Este texto de preenchimento existe apenas para demonstrar o fluxo de "
        "leitura, a quebra em colunas e o ajuste a diferentes tamanhos de "
        "janela. Redimensione a janela para ver o layout alternar entre uma e "
        "duas colunas de texto, conforme a largura disponível. O conteúdo é "
        "renderizado pelo Qt WebEngine com CSS multi-column responsivo.</p>"
    )


def chapter(title: str, paragraphs: int, extra: str = "") -> str:
    body = "".join(paragraph(i + 1) for i in range(paragraphs))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="pt">
<head>
  <title>{title}</title>
  <link rel="stylesheet" href="style.css"/>
</head>
<body>
  <h1>{title}</h1>
  {extra}
  {body}
</body>
</html>
"""


def build(path: str) -> None:
    import zipfile

    ch1 = chapter(
        "Capítulo Um — A Capa",
        30,
        '<p><img src="cover.png" alt="Capa"/></p>'
        '<div class="caixa"><b>Nota:</b> esta caixa usa o CSS interno do EPUB '
        "(style.css) servido via <code>epub://</code>.</div>",
    )
    ch2 = chapter("Capítulo Dois — O Texto", 40)
    ch3 = chapter("Capítulo Três — O Fim", 25)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 'mimetype' deve ser o primeiro arquivo, sem compressão
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, MIMETYPE)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/style.css", CSS)
        zf.writestr("OEBPS/cover.png", make_png(320, 180, (43, 108, 176)))
        zf.writestr("OEBPS/chapter1.xhtml", ch1)
        zf.writestr("OEBPS/chapter2.xhtml", ch2)
        zf.writestr("OEBPS/chapter3.xhtml", ch3)

    print(f"EPUB de exemplo criado em {path}")


if __name__ == "__main__":
    build(OUT)
