#!/usr/bin/env bash
# Instala as fontes dos temas de leitura do PyPub no diretório de fontes do
# USUÁRIO (~/.local/share/fonts) — sem root, sem tocar no sistema:
#   - Mulish (tema "bege") — SIL Open Font License 1.1 (google/fonts)
#   - Bitstream Charter (tema "cinza") — licença Bitstream Inc. 1989-1992
#     (uso/cópia/redistribuição livres; manter o aviso de copyright)
# Depois atualiza o cache do fontconfig. Idempotente: rode quantas vezes quiser.
#
# Uso:  bash tools/install_theme_fonts.sh
set -euo pipefail

DEST="${HOME}/.local/share/fonts/pypub-theme-fonts"
mkdir -p "${DEST}"
cd "${DEST}"

echo ">> Baixando Mulish (google/fonts, OFL 1.1)..."
curl -fsSL --max-time 90 \
  "https://github.com/google/fonts/raw/main/ofl/mulish/Mulish%5Bwght%5D.ttf" \
  -o Mulish-VF.ttf
curl -fsSL --max-time 30 \
  "https://github.com/google/fonts/raw/main/ofl/mulish/OFL.txt" \
  -o Mulish-OFL.txt

echo ">> Baixando Bitstream Charter (build OTF do x.org 1.0.3, licença Bitstream)..."
B="https://github.com/davidseegert/Bitstream-Charter/raw/master"
for f in CharterBT-Roman.otf CharterBT-Bold.otf CharterBT-Italic.otf CharterBT-BoldItalic.otf; do
  curl -fsSL --max-time 90 "${B}/${f}" -o "${f}"
done
# o aviso de copyright é exigido pela licença Bitstream em cópias não modificadas
curl -fsSL --max-time 30 "${B}/Copyright" -o Charter-COPYRIGHT

echo ">> Validando..."
file Mulish-VF.ttf CharterBT-Roman.otf | sed 's/^/   /'

echo ">> Atualizando cache do fontconfig..."
fc-cache -f "${HOME}/.local/share/fonts" >/dev/null 2>&1 || true

echo
echo "Instalado em: ${DEST}"
echo "Famílias disponíveis:"
fc-list : family | grep -iE "mulish|charter" | sort -u | sed 's/^/   /'
echo
echo "Pronto. Reinicie o PyPub (e o QML/Qt, se já estava aberto) para as"
echo "fontes aparecem nos temas 'Bege escuro · Mulish' e 'Cinza escuro · Bitstream Charter'."
