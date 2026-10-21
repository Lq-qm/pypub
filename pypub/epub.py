"""Parser de EPUB 2/3 (ZIP + OCF/OPF), sem dependências externas.

Lê ``META-INF/container.xml``, localiza o pacote OPF, extrai metadados,
manifest e spine (apenas itens lineares) e expõe acesso às entradas do
arquivo ZIP (para o renderer servir imagens/CSS relativos).
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"  # idêntico no EPUB 2 e no EPUB 3


class EpubError(Exception):
    """Erro ao abrir ou ler um arquivo EPUB."""


# ------------------------------------------------------------------ utilidades

def _local(tag: object) -> str:
    """Nome local de um tag (sem namespace); vazio para tags de comentário/PI."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _find_child(el: ET.Element, name: str) -> ET.Element | None:
    """Filho imediato por **nome local**, agnóstico de namespace.

    O pacote OPF usa namespaces diferentes conforme a versão (``2007/opf``
    no EPUB 2, ``2009/opf`` no EPUB 3) — e alguns geradores omitem o
    namespace — então a busca por ``{ns}nome`` quebraria em livros válidos.
    """
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _findall_local(el: ET.Element, name: str) -> list[ET.Element]:
    """Todos os filhos imediatos cujo nome local é ``name``."""
    return [child for child in el if _local(child.tag) == name]


@dataclass(frozen=True)
class Chapter:
    """Um item de leitura do spine."""

    id: str
    path: str  # caminho normalizado dentro do ZIP


class Epub:
    """Livro EPUB aberto e pronto para leitura."""

    def __init__(
        self,
        source_path: str,
        title: str,
        creator: str,
        chapters: list[Chapter],
        zip_file: zipfile.ZipFile,
    ) -> None:
        self.source_path = source_path
        self.title = title
        self.creator = creator
        self.chapters = chapters
        self._zf = zip_file
        self._names = {posixpath.normpath(n) for n in zip_file.namelist() if not n.endswith("/")}

    # ------------------------------------------------------------------ api
    @classmethod
    def open(cls, path: str) -> "Epub":
        """Abre e valida um EPUB em ``path``."""
        try:
            zf = zipfile.ZipFile(path, "r")
        except FileNotFoundError as e:
            raise EpubError(f"Arquivo não encontrado: {path}") from e
        except zipfile.BadZipFile as e:
            raise EpubError(f"'{path}' não é um arquivo EPUB válido (ZIP corrompido ou ausente)") from e

        names = {posixpath.normpath(n) for n in zf.namelist() if not n.endswith("/")}

        container_path = "META-INF/container.xml"
        if container_path not in names:
            raise EpubError("EPUB inválido: falta META-INF/container.xml")

        try:
            container = ET.fromstring(zf.read(container_path))
        except ET.ParseError as e:
            raise EpubError(f"container.xml ilegível: {e}") from e

        rootfile = container.find(f"{_CONTAINER_NS}rootfiles/{_CONTAINER_NS}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise EpubError("EPUB inválido: container.xml sem rootfile")

        opf_path = posixpath.normpath(rootfile.get("full-path"))
        if opf_path not in names:
            raise EpubError(f"EPUB inválido: pacote OPF não encontrado ({opf_path})")

        try:
            opf = ET.fromstring(zf.read(opf_path))
        except ET.ParseError as e:
            raise EpubError(f"Pacote OPF ilegível: {e}") from e

        opf_dir = posixpath.dirname(opf_path)

        # ---- metadados -------------------------------------------------
        title = ""
        creator = ""
        for el in opf.iter():
            if el.tag == f"{_DC_NS}title" and el.text and not title:
                title = el.text.strip()
            elif el.tag == f"{_DC_NS}creator" and el.text and not creator:
                creator = el.text.strip()

        # ---- manifest ----------------------------------------------------
        manifest_el = _find_child(opf, "manifest")
        if manifest_el is None:
            raise EpubError("EPUB inválido: OPF sem <manifest>")
        manifest: dict[str, str] = {}
        for item in _findall_local(manifest_el, "item"):
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            manifest[item_id] = posixpath.normpath(posixpath.join(opf_dir, href))

        # ---- spine -------------------------------------------------------
        spine_el = _find_child(opf, "spine")
        if spine_el is None:
            raise EpubError("EPUB inválido: OPF sem <spine>")
        chapters: list[Chapter] = []
        for itemref in _findall_local(spine_el, "itemref"):
            if (itemref.get("linear") or "yes").lower() == "no":
                continue
            item_id = itemref.get("idref")
            href = manifest.get(item_id or "")
            if href is None:
                continue
            if href not in names:
                continue  # item ausente no ZIP: ignora em vez de falhar
            chapters.append(Chapter(id=item_id or href, path=href))

        if not chapters:
            raise EpubError("EPUB inválido: nenhum capítulo linear no spine")

        return cls(
            source_path=path,
            title=title or posixpath.basename(path),
            creator=creator,
            chapters=chapters,
            zip_file=zf,
        )

    @staticmethod
    def _key(path: str) -> str:
        # entradas de ZIP nunca começam com "/"; remove se vier da URL
        return posixpath.normpath(path.lstrip("/"))

    def has(self, path: str) -> bool:
        """Diz se existe uma entrada normalizada ``path`` no ZIP."""
        return self._key(path) in self._names

    def read(self, path: str) -> bytes:
        """Lê o conteúdo bruto de ``path`` do ZIP."""
        key = self._key(path)
        if key not in self._names:
            raise KeyError(path)
        return self._zf.read(key)

    def close(self) -> None:
        self._zf.close()


# --------------------------------------------------------------- títulos de
_HEADING_RE = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def chapter_title(raw: bytes, fallback: str = "") -> str:
    """Extrai um título apresentável do XHTML do capítulo (1º heading/<title>)."""
    html = raw.decode("utf-8", "replace")
    for regex in (_HEADING_RE, _TITLE_RE):
        m = regex.search(html)
        if m:
            text = re.sub(r"\s+", " ", _TAG_RE.sub(" ", m.group(1))).strip()
            if text:
                return text[:120]
    return fallback


def chapter_html(raw: bytes) -> str:
    """Decodiza o XHTML bruto para ``str`` (utf-8 com fallback)."""
    return raw.decode("utf-8", "replace")
