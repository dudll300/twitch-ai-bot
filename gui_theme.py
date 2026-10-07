"""Studio desktop palette, restrained glass rims and visible keyboard focus."""

from paths import resource_path

COLORS = {
    "background": "#0A0B0E",
    "surface": "#14161B",
    "chrome": "#101217",
    "border": "#272B35",
    "text": "#F3F5FA",
    "muted": "#98A0B0",
    "accent": "#168BFF",
    "selected": "#142943",
    "success": "#77DAB0",
}

STYLE = """
QWidget { color: #f3f5fa; font-family: 'Segoe UI'; font-size: 14px; }
QMainWindow, QWidget#workspace, QWidget#mainArea { background: #0a0b0e; }
QFrame#sidebar { background: #101217; border-right: 1px solid #272b35; }
QFrame#topbar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #121b29, stop:0.55 #101217, stop:1 #101217);
    border-bottom: 1px solid #272b35;
}
QFrame#brandMark { background: transparent; border: none; }
QFrame#card, QFrame#inspectorCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #18212e, stop:0.22 #161a22, stop:0.62 #14161b, stop:1 #14161b);
    border: 1px solid #2b303b; border-top-color: #343f50; border-radius: 22px;
}
QFrame#inspectorCard { border-radius: 20px; }
QFrame#footer { background: #0a0b0e; border-top: 1px solid #272b35; }
QFrame#headerActions { background: transparent; border: none; }
QFrame#sidebarProfile { background: #161a22; border: 1px solid #292f3b; border-radius: 14px; }
QFrame#activityStats { background: #10141b; border: 1px solid #272e3a; border-radius: 14px; }
QFrame[role="stat"] { background: transparent; border: none; }
QFrame#authCard { background: #14253a; border: 1px solid #2e527b; border-radius: 16px; }
QLabel { background: transparent; border: none; }
QLabel[role="title"] { font-size: 28px; font-weight: 500; }
QLabel[role="section"] { font-size: 16px; font-weight: 500; }
QLabel[role="brand"] { font-size: 15px; font-weight: 600; }
QLabel[role="muted"] { color: #98a0b0; font-size: 12px; }
QLabel[role="caption"] { color: #c5cbd7; font-size: 13px; }
QLabel[role="eyebrow"] { color: #98a0b0; font-size: 11px; }
QLabel[role="channel"] { color: #f3f5fa; font-size: 13px; font-weight: 500; }
QLabel[role="number"] { font-size: 28px; font-weight: 500; }
QLabel[role="statValue"] { color: #f3f5fa; font-size: 23px; font-weight: 500; }
QLabel[role="statLabel"] { color: #98a0b0; font-size: 11px; }
QLabel[role="error"] { color: #ffc3c8; background: #302029; border: 1px solid #623641; padding: 12px; border-radius: 12px; }
QLabel[role="historyStatus"] { color: #b8c1d0; background: #1b202a; border: 1px solid #303849; border-radius: 10px; padding: 7px 10px; font-size: 12px; }
QLabel[role="historyStatus"][state="sent"] { color: #8be0bb; background: #122b27; border-color: #284e42; }
QLabel[role="historyStatus"][state="preview"], QLabel[role="historyStatus"][state="generated"] { color: #90c5ff; background: #142943; border-color: #294d77; }
QLabel[role="historyStatus"][state="error"], QLabel[role="historyStatus"][state="send_error"] { color: #ffc3c8; background: #302029; border-color: #633744; }
QListWidget#historyEntries::item { padding: 10px 12px; border-radius: 10px; }
QLabel#sidebarChannel { color: #e5eaf4; font-size: 12px; font-weight: 500; }
QLabel#sidebarHint { color: #98a0b0; font-size: 11px; }
QLabel#connectionPill { background: #142943; color: #90c5ff; border: 1px solid #284669; border-radius: 10px; padding: 5px 9px; font-size: 11px; }
QLabel#authHint { color: #d9eaff; background: #14253a; border: 1px solid #2e527b; border-radius: 14px; padding: 14px; font-size: 13px; }
QLabel#status { background: #1b202a; color: #b8c1d0; border: 1px solid #303849; border-radius: 11px; padding: 7px 11px; font-size: 12px; }
QLabel#status[state="idle"] { background: #181c24; color: #aeb7c7; border-color: #2b3341; }
QLabel#status[state="running"] { background: #122b27; color: #8be0bb; border-color: #284e42; }
QLabel#status[state="starting"] { background: #142943; color: #90c5ff; border-color: #294d77; }
QLabel#status[state="stopping"] { background: #30291c; color: #e0c28a; border-color: #514331; }
QLabel#status[state="error"] { background: #302029; color: #ffc3c8; border-color: #633744; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: #0e1015; color: #f3f5fa; border: 1px solid #343b49;
    border-radius: 11px; padding: 10px 12px;
    selection-background-color: #215e99; selection-color: #ffffff;
}
QLineEdit, QComboBox, QSpinBox { min-height: 20px; }
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #52617a; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #168bff; }
QSpinBox { padding-right: 36px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 28px; background: #202936; border: none; }
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 11px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 11px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #30415a; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #778294; background: #12151b; border-color: #282e39; }
QComboBox QLineEdit { border: none; padding: 0; background: transparent; }
QComboBox { padding-right: 36px; }
QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: top right; border: none; width: 32px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { color: #f3f5fa; background: #171d28; selection-background-color: #1b426f; selection-color: #f3f5fa; border: 1px solid #445773; padding: 6px; }
QPlainTextEdit#activityLog { background: #0f131b; border-color: #252e3d; border-radius: 13px; padding: 12px 14px; color: #cbd5e4; }
QPlainTextEdit#activityLog:focus { border-color: #168bff; }
QPushButton { background: #202632; color: #e2e9f4; border: 1px solid #394457; border-radius: 11px; padding: 10px 15px; font-weight: 500; }
QPushButton:hover { background: #2b3547; border-color: #567195; }
QPushButton:pressed { background: #192536; }
QPushButton:focus { border: 1px solid #86bdff; }
QPushButton:disabled { color: #737f91; background: #171c25; border-color: #2a3240; }
QPushButton[variant="danger"] { color: #ffd0d5; background: #5f2937; border-color: #8a3d50; }
QPushButton[variant="danger"]:hover { color: #ffffff; background: #a23c52; border-color: #d56378; }
QPushButton[variant="danger"]:pressed { background: #7d2e41; }
QPushButton[variant="danger"]:focus { border-color: #ffb0c0; }
QPushButton[variant="danger"]:disabled { color: #927b85; background: #30232d; border-color: #48313e; }
QPushButton#menuToggle { background: #151b25; border-color: #2b3545; border-radius: 11px; padding: 0; font-size: 22px; font-weight: 400; }
QPushButton#menuToggle:hover { background: #202e42; border-color: #456992; }
QPushButton#menuToggle:focus { border-color: #86bdff; }
QMessageBox { background: #14161b; }
QDialog#studioQuestion { background: transparent; }
QFrame#questionCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1b2636, stop:0.4 #161b24, stop:1 #14161b);
    border: 1px solid #35445b; border-top-color: #48658a; border-radius: 22px;
}
QLabel#questionTitle { color: #f3f5fa; font-size: 21px; font-weight: 600; }
QLabel#questionText { color: #b3bdce; font-size: 14px; }
QLabel#questionMark { color: #8fc5ff; background: #142943; border: 1px solid #2d4d73; border-radius: 12px; font-size: 22px; font-weight: 600; }
QPushButton#primary, QPushButton[variant="primary"] { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2498ff, stop:1 #168bff); color: #ffffff; border-color: #4aa9ff; }
QPushButton#primary:hover, QPushButton[variant="primary"]:hover { background: #319eff; border-color: #82c4ff; }
QPushButton#primary:pressed, QPushButton[variant="primary"]:pressed { background: #0876df; border-color: #168bff; }
QPushButton#primary:focus, QPushButton[variant="primary"]:focus { border-color: #c4e3ff; }
QPushButton#primary:disabled, QPushButton[variant="primary"]:disabled { background: #203a58; border-color: #2e4f72; color: #809dbb; }
QPushButton[variant="quiet"] { background: transparent; border-color: transparent; color: #aebbd0; padding: 8px; }
QPushButton[variant="quiet"]:hover { background: #202b3c; color: #ddecff; }
QPushButton[variant="quiet"]:focus { border-color: #86bdff; }
QPushButton[variant="quiet"]:disabled { color: #6f7a8a; background: transparent; border-color: transparent; }
QPushButton[variant="nav"] { text-align: left; padding: 12px 14px; background: transparent; border: 1px solid transparent; color: #98a0b0; font-weight: 400; border-radius: 10px; }
QPushButton[variant="nav"]:hover { background: #191f2a; color: #dce8f9; }
QPushButton[variant="nav"]:checked { background: #142943; border-color: #244265; color: #8fc5ff; font-weight: 500; }
QPushButton[variant="nav"]:focus { border-color: #86bdff; }
QPushButton[variant="inspector"] { text-align: left; background: #18263a; border-color: #2d4564; color: #a7cfff; font-size: 12px; padding: 9px 12px; }
QPushButton[variant="inspector"]:hover { background: #213854; border-color: #4d78a8; }
QPushButton[variant="inspector"]:focus { border-color: #86bdff; }
QPushButton[variant="inspector"]:disabled { background: #161e2a; border-color: #293548; color: #72849d; }
QListWidget { background: #0e1015; color: #f3f5fa; border: none; border-radius: 11px; outline: none; }
QListWidget::item { padding: 12px; margin-bottom: 4px; border-radius: 11px; color: #b1bac9; }
QListWidget::item:hover { background: #1c2738; }
QListWidget::item:selected { background: #142943; color: #b1d8ff; }
QListWidget::item:focus { border: 1px solid #86bdff; }
QListWidget#profileList { background: transparent; border: none; outline: none; }
QListWidget#profileList::item { padding: 12px; margin-bottom: 4px; border-radius: 11px; color: #b1bac9; }
QListWidget#profileList::item:hover { background: #1c2738; }
QListWidget#profileList::item:selected { background: #142943; color: #b1d8ff; }
QListWidget#profileList::item:focus { border: 1px solid #86bdff; }
QListWidget#modelList { background: #0e1015; color: #f3f5fa; border: 1px solid #343b49; border-radius: 11px; padding: 6px; outline: none; }
QListWidget#modelList::item { padding: 8px; margin-bottom: 0; border-radius: 6px; }
QListWidget#modelList::item:hover { background: #1c2738; }
QListWidget#modelList::item:selected { background: #142943; color: #b1d8ff; }
QListWidget#modelList::item:disabled { color: #788394; }
QListWidget#modelList::indicator { width: 16px; height: 16px; border: 1px solid #69778d; border-radius: 4px; background: #111925; }
QListWidget#modelList::indicator:checked { background: #168bff; border-color: #4aa9ff; image: url("__CHECKMARK__"); }
QListWidget#modelList::indicator:disabled { background: #222d3d; border-color: #414d60; }
QCheckBox { spacing: 8px; color: #c5cddc; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid #69778d; background: #111925; }
QCheckBox::indicator:hover { border-color: #8abaf0; }
QCheckBox::indicator:checked { background: #168bff; border-color: #4aa9ff; image: url("__CHECKMARK__"); }
QCheckBox::indicator:checked:hover { background: #319eff; border-color: #82c4ff; }
QCheckBox::indicator:focus { border-color: #c4e3ff; }
QCheckBox::indicator:disabled { background: #222d3d; border-color: #414d60; }
QCheckBox:disabled { color: #778294; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #39465b; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #5a759a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QToolTip { color: #e4efff; background: #1b293d; border: 1px solid #4b6890; padding: 7px; }
"""

# Absolute bundle paths also work when launched from a different directory.
STYLE = STYLE.replace("__CHECKMARK__", resource_path("assets/checkmark.svg").as_posix())
