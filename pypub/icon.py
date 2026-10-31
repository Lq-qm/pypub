"""Ícone da aplicação (janela): um **livro aberto** — identidade do leitor.

O ícone segue o tema do sistema (mesma lógica do resto do app): na titlebar
**clara** ele é **escuro**, na titlebar **escura** ele é **claro** — assim tem o
mesmo contraste do 1º plano do Breeze/KDE e fica nativo em ambos os modos.

Por que SVG renderizado (e não um PNG fixo)?
--------------------------------------------
- Uma única geometria vetorial gera pixmaps nítidos em qualquer tamanho
  (16 px na titlebar até 256 px no menu de apps / escala);
- A cor é aplicada no render → o mesmo desenho vira a variante clara/escura;
- Não depende de arquivo externo nem de plugin de imagem no disco.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 1º plano do Breeze (KDE) por tema — o "cinza do texto" da interface.
_LIGHT_FG = "#232629"  # titlebar clara  → ícone escuro
_DARK_FG = "#eff0f1"   # titlebar escura → ícone claro

# Livro aberto (viewBox 24×24). Silhueta limpa: duas páginas com o lombo ao
# centro, topo em "V" (as páginas caem no meio) e base levemente rebaixada no
# lombo — o símbolo clássico de "ler". Legível mesmo em 16 px.
_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <path fill="{color}" d="M12 7
    C 9.8 5.8 6.8 5.2 3.5 5.2
    L 3.5 16.8
    C 6.8 16.8 9.8 17.4 12 18.6
    C 14.2 17.4 17.2 16.8 20.5 16.8
    L 20.5 5.2
    C 17.2 5.2 14.2 5.8 12 7
    Z"/>
</svg>
"""


def _pixmap(dark: bool, size: int) -> QPixmap:
    """Renderiza o livro aberto em ``size``×``size`` com a cor do tema."""
    color = _DARK_FG if dark else _LIGHT_FG
    data = _ICON_SVG.replace("{color}", color).encode("utf-8")
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))  # fundo transparente (só a silhueta)
    painter = QPainter(pm)
    QSvgRenderer(data).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pm


def make_window_icon(dark: bool = False) -> QIcon:
    """``QIcon`` com o livro aberto em vários tamanhos, na cor do tema."""
    icon = QIcon()
    for s in (16, 22, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_pixmap(dark, s))
    return icon
