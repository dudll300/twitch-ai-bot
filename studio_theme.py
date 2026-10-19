"""Semantic theme tokens for the Windows studio and native Qt controls."""
from PySide6.QtGui import QColor, QPalette, QFont, QFontDatabase
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from paths import resource_path
from functools import lru_cache

PALETTES = {
    "dark": dict(bg="#06090F", surface="#0C121C", raised="#152438", input="#0A121E",
                 text="#EFF5FF", muted="#A9B8CE", border="#2D4058", accent="#B5DCFF",
                 primary="#C5DFFF", primaryText="#112B48", primaryHover="#DCEEFF",
                 panelTop="#14243A", panelBottom="#0A111D", glow="#3E86EA", glowSecondary="#756AC5",
                 selected="#162D44", success="#8BE0BB", successbg="#12352C",
                 error="#FFBCC8", errorbg="#40202D", warning="#F5D59A", warningbg="#382D19"),
    "light": dict(bg="#F7FAFF", surface="#FFFFFF", raised="#EEF3FA", input="#FFFFFF",
                  text="#162032", muted="#526179", border="#B8C5D7", accent="#0066CC",
                  primary="#D5E6FF", primaryText="#173459", primaryHover="#C5DDFF",
                  panelTop="#EDF4FF", panelBottom="#FFFFFF", glow="#6D9FDB", glowSecondary="#A794CB",
                  selected="#E1EEFF", success="#14623D", successbg="#E1F4E9",
                  error="#9C183A", errorbg="#FCE8EF", warning="#785006", warningbg="#FFF1D1"),
}


@lru_cache(maxsize=2)
def stylesheet(theme="dark"):
    # One stable style tree. Theme changes update the palette, not 1300 widgets'
    # style sheets. Semantic status labels resolve their small local palette.
    p = dict(bg="palette(window)", surface="palette(alternate-base)", raised="palette(button)",
             input="palette(base)", text="palette(text)", muted="palette(link-visited)",
             border="palette(mid)", accent="palette(link)", selected="palette(highlight)",
             primary="palette(midlight)", primaryText="palette(bright-text)", primaryHover="palette(light)",
             panelTop="palette(dark)", panelBottom="palette(shadow)",
             success="palette(text)", successbg="palette(highlight)", error="palette(text)",
             errorbg="palette(button)", warning="palette(text)", warningbg="palette(button)")
    check = resource_path("assets/checkmark.svg").as_posix()
    return """
QWidget {{ color: {text}; font-family: 'Onest'; font-size: 14px; }}
QMainWindow, QWidget#workspace, QWidget#mainArea {{ background: {bg}; }}
QFrame#sidebar {{ background: {surface}; border-right: 1px solid {border}; }}
QFrame#topbar {{ background: {surface}; border-bottom: 1px solid {border}; }}
QFrame#card, QFrame#inspectorCard, QFrame#questionCard {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {panelTop},stop:.55 {surface},stop:1 {panelBottom}); border: 1px solid {border}; border-radius: 16px; }}
QFrame#footer {{ background: {surface}; border-top: 1px solid {border}; }}
QFrame#sidebarProfile, QFrame#activityStats {{ background: {raised}; border: 1px solid {border}; border-radius: 12px; }}
QLabel {{ background: transparent; border: none; }}
QLabel[role="title"] {{ font-size: 40px; font-weight: 500; }}
QLabel[role="section"] {{ font-size: 16px; font-weight: 500; }}
QFrame#panelHeading {{ background: transparent; border: none; border-bottom: 1px solid {border}; }}
QLabel[role="eyebrow"] {{ color: {accent}; }}
QLabel[role="brand"] {{ font-size: 23px; font-weight: 500; }}
QLabel[role="muted"] {{ color: {muted}; font-size: 13px; }}
QLabel[role="caption"] {{ font-size: 14px; }}
QLabel[role="eyebrow"], QLabel[role="statLabel"] {{ color: {muted}; font-size: 12px; }}
QLabel[role="channel"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="number"], QLabel[role="statValue"] {{ font-size: 32px; font-weight: 500; color: {accent}; }}
QLabel[role="heroValue"] {{ font-size: 36px; font-weight: 500; }}
QLabel[role="reply"] {{ font-size: 21px; font-weight: 400; }}
QLabel[role="error"] {{ color: {error}; background: {errorbg}; padding: 10px; border-radius: 8px; }}
QLabel#status, QLabel#connectionPill, QLabel[role="historyStatus"] {{ color: {muted}; background: {raised}; border: 1px solid {border}; border-radius: 8px; padding: 7px 10px; font-size: 13px; }}
QLabel#status[state="running"], QLabel[role="historyStatus"][state="sent"] {{ color: {success}; background: {successbg}; }}
QLabel#status[state="error"], QLabel[role="historyStatus"][state="error"], QLabel[role="historyStatus"][state="send_error"] {{ color: {error}; background: {errorbg}; }}
QLabel#status[state="starting"], QLabel[role="historyStatus"][state="preview"] {{ color: {accent}; background: {selected}; }}
QLabel#status[state="stopping"] {{ color: {warning}; background: {warningbg}; }}
QLabel#authHint {{ color: {text}; background: {selected}; border: 1px solid {accent}; padding: 12px; border-radius: 10px; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 14px; padding: 11px 14px; selection-background-color: {selected}; selection-color: {text}; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: 22px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {accent}; padding: 10px 13px; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {muted}; background: {raised}; }}
QComboBox {{ padding-right: 10px; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{ background: transparent; color: {text}; selection-background-color: {selected}; selection-color: {text}; border: none; padding: 5px; outline: none; }}
QFrame#orbitComboContainer {{ background: {raised}; border: 1px solid {border}; border-radius: 16px; padding: 3px; }}
QFrame#rule {{ background: {border}; border: none; }}
QComboBox QLineEdit {{ border: none; background: transparent; padding: 0; }}
QSpinBox {{ padding-right: 36px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 28px; height: 18px; background: transparent; border: none; }}
QSpinBox::up-button {{ subcontrol-position: top right; top: 4px; right: 4px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; bottom: 4px; right: 4px; }}
QPushButton {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 22px; padding: 10px 17px; font-size: 13px; font-weight: 500; min-height: 22px; }}
QPushButton:hover {{ background: {selected}; border-color: {accent}; }}
QPushButton:pressed {{ background: {selected}; }}
QPushButton:focus {{ border: 2px solid {accent}; padding: 9px 16px; }}
QPushButton:disabled {{ color: {muted}; background: {surface}; border-color: {border}; }}
QPushButton#primary, QPushButton[variant="primary"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {primaryHover},stop:1 {primary}); color: {primaryText}; border-color: {primary}; }}
QPushButton#primary:hover, QPushButton[variant="primary"]:hover {{ background: {primaryHover}; }}
QPushButton#primary:disabled, QPushButton[variant="primary"]:disabled {{ color: {muted}; background: {raised}; border-color: {border}; }}
QPushButton[variant="danger"] {{ color: {error}; background: {errorbg}; border-color: {error}; }}
QPushButton[variant="quiet"] {{ background: transparent; border-color: transparent; color: {accent}; text-align: left; }}
QPushButton[variant="nav"] {{ text-align: left; background: transparent; border-color: transparent; border-radius: 16px; color: {muted}; font-weight: 400; padding: 9px 12px; }}
QPushButton[variant="nav"]:checked {{ background: {selected}; color: {accent}; border-color: transparent; font-weight: 500; }}
QPushButton[variant="nav"]:focus {{ border-color: transparent; outline: none; }}
QPushButton[variant="inspector"] {{ text-align: left; color: {accent}; }}
QPushButton#menuToggle {{ padding: 0; }}
QListWidget {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 10px; outline: none; }}
QListWidget::item {{ padding: 10px; color: {text}; border-radius: 8px; }}
QListWidget::item:selected {{ background: {selected}; color: {text}; }}
QListWidget::item:hover {{ background: {raised}; }}
QListWidget::item:focus {{ border: none; }}
QListWidget::indicator, QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {muted}; border-radius: 5px; background: {input}; }}
QListWidget::indicator:checked, QCheckBox::indicator:checked {{ background: #0066CC; border-color: #0066CC; image: url("{check}"); }}
QCheckBox {{ spacing: 8px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 32px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 32px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
QSplitter::handle {{ background: transparent; width: 18px; }}
QPlainTextEdit[presentation="reading"] {{ border: none; padding: 0; border-radius: 0; background: transparent; }}
QPlainTextEdit[presentation="reading"][emphasis="true"] {{ font-size: 20px; font-weight: 400; }}
QListWidget[presentation="rows"] {{ background: transparent; border: none; border-radius: 0; padding: 0; }}
QListWidget[presentation="rows"]::item {{ border: none; border-bottom: 1px solid {border}; border-radius: 0; padding: 16px 18px; }}
QListWidget[presentation="rows"]::item:selected {{ background: {selected}; border: none; border-left: 2px solid {accent}; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 8px; }}
QMenu {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 12px; padding: 8px; }}
QMenu::item {{ padding: 10px 24px; border-radius: 8px; }}
QMenu::item:selected {{ background: {selected}; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ color: {muted}; background: transparent; padding: 12px 16px; border-bottom: 2px solid {border}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; }}
QMessageBox {{ background: {surface}; }}
QDialog#studioQuestion {{ background: transparent; }}
QLabel#questionTitle {{ font-size: 21px; font-weight: 600; }}
QLabel#questionText {{ color: {muted}; }}
QLabel#questionMark {{ color: {accent}; font-size: 24px; }}
""".format(**p, check=check)


def load_fonts(app):
    """Bundle the approved typeface; never depend on an OS font installation."""
    if app.property("studioFontsLoaded"):
        return
    font_id = QFontDatabase.addApplicationFont(str(resource_path("assets/fonts/Onest.ttf")))
    family = "Onest" if font_id >= 0 else "Segoe UI"
    app.setProperty("studioFontFamily", family)
    app.setProperty("studioFontsLoaded", True)
    font = QFont(family, 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)


def apply_theme(app, theme):
    load_fonts(app)
    p = PALETTES[theme]
    if app.property("studioTheme") == theme and app.palette().color(QPalette.Window).name() == p["bg"]:
        return
    app.setProperty("studioTheme", theme)
    palette = QPalette()
    for role, value in ((QPalette.Window, p["bg"]), (QPalette.WindowText, p["text"]),
                        (QPalette.Base, p["input"]), (QPalette.Text, p["text"]),
                        (QPalette.PlaceholderText, p["muted"]),
                        (QPalette.Link, p["accent"]),
                        (QPalette.LinkVisited, p["muted"]),
                        (QPalette.Midlight, p["primary"]), (QPalette.BrightText, p["primaryText"]),
                        (QPalette.Light, p["primaryHover"]),
                        (QPalette.Dark, p["panelTop"]), (QPalette.Shadow, p["panelBottom"]),
                        (QPalette.Button, p["raised"]), (QPalette.ButtonText, p["text"]),
                        (QPalette.Highlight, p["selected"]), (QPalette.HighlightedText, p["text"]),
                        (QPalette.AlternateBase, p["surface"]), (QPalette.Mid, p["border"]),
                        (QPalette.ToolTipBase, p["surface"]), (QPalette.ToolTipText, p["text"])):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    if app.styleSheet() != STYLE:
        app.setStyleSheet(STYLE)
    else:
        # Qt caches palette() brushes at polish time. Refresh them in parent
        # order without rebuilding the application's entire selector tree.
        def depth(widget):
            level = 0
            parent = widget.parentWidget()
            while parent is not None:
                level += 1
                parent = parent.parentWidget()
            return level
        for widget in sorted(app.allWidgets(), key=depth):
            if widget.testAttribute(Qt.WA_WState_Polished):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                # Item views overload update(index); call QWidget's overload.
                QWidget.update(widget)


STYLE = stylesheet()
