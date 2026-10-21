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

    property bool dark: false

    title: bridge.hasBook
        ? (bridge.bookTitle + " — PyPub")
        : "PyPub"

    function setDarkMode(d) {
        dark = d
        Qt.colorScheme = d ? Qt.colorSchemeDark : Qt.colorSchemeLight
        bridge.setDark(d)
    }

    pageStack.initialPage: K.Page {
        id: mainPage

        header: K.ActionToolBar {
            K.Action {
                text: qsTr("Abrir…")
                icon.name: "document-open"
                onTriggered: bridge.chooseFile()
            }
            K.Action {
                text: qsTr("Anterior")
                icon.name: "document-go-back"
                enabled: bridge.hasBook && bridge.currentIndex > 0
                shortcut: StandardKey.PrevPage
                onTriggered: bridge.previous()
            }
            K.Action {
                text: qsTr("Próximo")
                icon.name: "document-go-forward"
                enabled: bridge.hasBook && bridge.currentIndex < bridge.chapterCount - 1
                shortcut: StandardKey.NextPage
                onTriggered: bridge.next()
            }
            K.Action {
                text: "Aa−"
                enabled: bridge.zoom > 0.6
                onTriggered: bridge.zoomOut()
            }
            K.Action {
                text: "Aa+"
                enabled: bridge.zoom < 2.5
                onTriggered: bridge.zoomIn()
            }
            K.Action {
                text: "1:1"
                enabled: bridge.zoom !== 1.0
                onTriggered: bridge.resetZoom()
            }
            K.Action {
                text: qsTr("Tema escuro")
                icon.name: "preferences-desktop-display-color"
                checkable: true
                checked: dark
                onTriggered: window.setDarkMode(checked)
            }
        }

        contentItem: WebEngineView {
            id: reader
            objectName: "reader"
            anchors.fill: parent
            zoomFactor: bridge ? bridge.zoom : 1.0
            backgroundColor: bridge ? bridge.pageBackground : "white"
            Component.onCompleted: loadHtml(bridge.currentHtml(), Qt.url("about:blank"))
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
                    width: Math.max(0, statusRow.width - zoomLabel.width - statusRow.spacing)
                    elide: Text.ElideRight
                    text: bridge.hasBook
                        ? qsTr("Capítulo %1 de %2 — %3").arg(bridge.currentIndex + 1).arg(bridge.chapterCount).arg(bridge.currentChapterTitle)
                        : (bridge.lastError.length > 0
                            ? bridge.lastError
                            : qsTr("Abra um arquivo .epub para começar  (Ctrl+O)"))
                    // O singleton ``Kirigami`` nem sempre é registrado; usa-o se
                    // existir, senão cai em cores adaptadas ao esquema claro/escuro.
                    color: bridge.lastError.length > 0
                        ? (typeof Kirigami !== "undefined" ? Kirigami.errorColor : "#b03030")
                        : (typeof Kirigami !== "undefined" ? Kirigami.textColor
                            : (Qt.colorScheme === Qt.colorSchemeDark ? "#e6e6e6" : "#1c1c1c"))
                }
                Label {
                    id: zoomLabel
                    anchors.verticalCenter: parent.verticalCenter
                    text: Math.round(bridge.zoom * 100) + " %"
                }
            }
        }
    }

    // Sincroniza o conteúdo renderizado com a ponte
    Connections {
        target: bridge
        function onContentChanged(html) {
            reader.loadHtml(html, Qt.url("about:blank"))
        }
    }

    // Atalho global: Ctrl+O abre o diálogo de arquivos
    Shortcut {
        sequences: [StandardKey.Open]
        onActivated: bridge.chooseFile()
    }
}
