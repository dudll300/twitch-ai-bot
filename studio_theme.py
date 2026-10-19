"""Semantic theme tokens for the Windows studio and native Qt controls."""
from PySide6.QtGui import QColor, QPalette
from paths import resource_path

PALETTES = {
    "dark": dict(bg="#0A0D14", surface="#141A24", raised="#1B2432", input="#0E141F",
                 text="#F3F6FC", muted="#A8B3C7", border="#334155", accent="#4DA8FF",
                 selected="#173658", success="#8BE0BB", successbg="#12352C",
                 error="#FFBCC8", errorbg="#40202D", warning="#F5D59A", warningbg="#382D19"),
    "light": dict(bg="#F4F7FB", surface="#FFFFFF", raised="#EEF3FA", input="#FFFFFF",
                  text="#162032", muted="#526179", border="#B8C5D7", accent="#0066CC",
                  selected="#E1EEFF", success="#14623D", successbg="#E1F4E9",
                  error="#9C183A", errorbg="#FCE8EF", warning="#785006", warningbg="#FFF1D1"),
}


def stylesheet(theme="dark"):
    p = PALETTES[theme]
    check = resource_path("assets/checkmark.svg").as_posix()
    return """
QWidget {{ color: {text}; font-family: 'Segoe UI'; font-size: 15px; }}
QMainWindow, QWidget#workspace, QWidget#mainArea {{ background: {bg}; }}
QFrame#sidebar {{ background: {surface}; border-right: 1px solid {border}; }}
QFrame#topbar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {surface},stop:1 {selected}); border-bottom: 1px solid {border}; }}
QFrame#card, QFrame#inspectorCard, QFrame#questionCard {{ background: {surface}; border: 1px solid {border}; border-radius: 16px; }}
QFrame#footer {{ background: {surface}; border-top: 1px solid {border}; }}
QFrame#sidebarProfile, QFrame#activityStats {{ background: {raised}; border: 1px solid {border}; border-radius: 12px; }}
QLabel {{ background: transparent; border: none; }}
QLabel[role="title"] {{ font-size: 28px; font-weight: 600; }}
QLabel[role="section"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="brand"] {{ font-size: 17px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {muted}; font-size: 13px; }}
QLabel[role="caption"] {{ font-size: 14px; }}
QLabel[role="eyebrow"], QLabel[role="statLabel"] {{ color: {muted}; font-size: 12px; }}
QLabel[role="channel"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="number"], QLabel[role="statValue"] {{ font-size: 28px; font-weight: 600; color: {accent}; }}
QLabel[role="error"] {{ color: {error}; background: {errorbg}; padding: 10px; border-radius: 8px; }}
QLabel#status, QLabel#connectionPill, QLabel[role="historyStatus"] {{ color: {muted}; background: {raised}; border: 1px solid {border}; border-radius: 8px; padding: 7px 10px; font-size: 13px; }}
QLabel#status[state="running"], QLabel[role="historyStatus"][state="sent"] {{ color: {success}; background: {successbg}; }}
QLabel#status[state="error"], QLabel[role="historyStatus"][state="error"], QLabel[role="historyStatus"][state="send_error"] {{ color: {error}; background: {errorbg}; }}
QLabel#status[state="starting"], QLabel[role="historyStatus"][state="preview"] {{ color: {accent}; background: {selected}; }}
QLabel#status[state="stopping"] {{ color: {warning}; background: {warningbg}; }}
QLabel#authHint {{ color: {text}; background: {selected}; border: 1px solid {accent}; padding: 12px; border-radius: 10px; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 10px; padding: 10px 12px; selection-background-color: #0066CC; selection-color: #FFFFFF; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: 22px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {accent}; padding: 9px 11px; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {muted}; background: {raised}; }}
QComboBox {{ padding-right: 32px; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{ background: {surface}; color: {text}; selection-background-color: {selected}; selection-color: {text}; border: 1px solid {border}; }}
QComboBox QLineEdit {{ border: none; background: transparent; padding: 0; }}
QSpinBox {{ padding-right: 36px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 28px; background: {raised}; border: none; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QPushButton {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 10px; padding: 10px 14px; font-weight: 500; min-height: 22px; }}
QPushButton:hover {{ background: {selected}; border-color: {accent}; }}
QPushButton:pressed {{ background: {selected}; }}
QPushButton:focus {{ border: 2px solid {accent}; padding: 9px 13px; }}
QPushButton:disabled {{ color: {muted}; background: {surface}; border-color: {border}; }}
QPushButton#primary, QPushButton[variant="primary"] {{ background: #0066CC; color: #FFFFFF; border-color: #0066CC; }}
QPushButton#primary:hover, QPushButton[variant="primary"]:hover {{ background: #0054AD; }}
QPushButton#primary:disabled, QPushButton[variant="primary"]:disabled {{ color: {muted}; background: {raised}; border-color: {border}; }}
QPushButton[variant="danger"] {{ color: {error}; background: {errorbg}; border-color: {error}; }}
QPushButton[variant="quiet"] {{ background: transparent; border-color: transparent; color: {accent}; text-align: left; }}
QPushButton[variant="nav"] {{ text-align: left; background: transparent; border-color: transparent; color: {muted}; font-weight: 400; padding: 9px 12px; }}
QPushButton[variant="nav"]:checked {{ background: {selected}; color: {accent}; border-color: {border}; font-weight: 600; }}
QPushButton[variant="nav"]:focus {{ border-color: {accent}; }}
QPushButton[variant="inspector"] {{ text-align: left; color: {accent}; }}
QPushButton#menuToggle {{ padding: 0; }}
QListWidget {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 10px; outline: none; }}
QListWidget::item {{ padding: 10px; color: {text}; border-radius: 8px; }}
QListWidget::item:selected {{ background: {selected}; color: {text}; }}
QListWidget::item:hover {{ background: {raised}; }}
QListWidget::item:focus {{ border: 1px solid {accent}; }}
QListWidget::indicator, QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {muted}; border-radius: 5px; background: {input}; }}
QListWidget::indicator:checked, QCheckBox::indicator:checked {{ background: #0066CC; border-color: #0066CC; image: url("{check}"); }}
QCheckBox {{ spacing: 8px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 32px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 8px; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ color: {muted}; background: {surface}; padding: 12px 16px; border-bottom: 2px solid {border}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; }}
QMessageBox {{ background: {surface}; }}
QDialog#studioQuestion {{ background: transparent; }}
QLabel#questionTitle {{ font-size: 21px; font-weight: 600; }}
QLabel#questionText {{ color: {muted}; }}
QLabel#questionMark {{ color: {accent}; font-size: 24px; }}
""".format(**p, check=check)


def apply_theme(app, theme):
    p = PALETTES[theme]
    palette = QPalette()
    for role, value in ((QPalette.Window, p["surface"]), (QPalette.WindowText, p["text"]),
                        (QPalette.Base, p["input"]), (QPalette.Text, p["text"]),
                        (QPalette.PlaceholderText, p["muted"]),
                        (QPalette.Link, p["accent"]),
                        (QPalette.Button, p["raised"]), (QPalette.ButtonText, p["text"]),
                        (QPalette.Highlight, "#0066CC"), (QPalette.HighlightedText, "#FFFFFF"),
                        (QPalette.ToolTipBase, p["surface"]), (QPalette.ToolTipText, p["text"])):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(theme))


STYLE = stylesheet()
