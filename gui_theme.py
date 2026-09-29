"""Neutral desktop palette, consistent spacing and visible keyboard focus."""

STYLE = """
QWidget { color: #e9eaec; font-family: 'Segoe UI'; font-size: 14px; }
QMainWindow, QWidget#workspace { background: #141517; }
QFrame#sidebar { background: #101113; border-right: 1px solid #27292d; }
QFrame#card { background: #1d1e21; border: 1px solid #303236; border-radius: 20px; }
QFrame#footer { background: #141517; border-top: 1px solid #2b2d31; }
QLabel { background: transparent; border: none; }
QLabel[role="title"] { font-size: 28px; font-weight: 600; }
QLabel[role="section"] { font-size: 17px; font-weight: 600; }
QLabel[role="brand"] { font-size: 20px; font-weight: 600; }
QLabel[role="muted"] { color: #a3a6ae; font-size: 12px; }
QLabel[role="caption"] { color: #c9cbd0; font-size: 13px; }
QLabel[role="number"] { font-size: 28px; font-weight: 600; }
QLabel[role="error"] { color: #f0aaaa; background: #302123; padding: 12px; border-radius: 12px; }
QLabel#status { background: #24262a; color: #c8cbd1; border-radius: 12px; padding: 10px 12px; font-size: 12px; }
QLineEdit, QPlainTextEdit, QComboBox {
    background: #141517; color: #e9eaec; border: 1px solid #3b3e44;
    border-radius: 12px; padding: 10px 12px;
    selection-background-color: #50555e; selection-color: #ffffff;
}
QLineEdit, QComboBox { min-height: 20px; }
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover { border-color: #60656e; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #b7bbc4; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled { color: #858993; border-color: #303238; }
QComboBox QLineEdit { border: none; padding: 0; background: transparent; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView { background: #24262a; selection-background-color: #3b3e44; border: 1px solid #50545c; padding: 6px; }
QPushButton { background: #2b2d32; color: #e5e7eb; border: 1px solid #40434a; border-radius: 12px; padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #36393f; border-color: #60656e; }
QPushButton:pressed { background: #212328; }
QPushButton:focus { border: 1px solid #c6cad2; }
QPushButton:disabled { color: #737781; background: #222429; border-color: #303238; }
QPushButton[variant="danger"] { color: #ffd0d0; background: #692c34; border-color: #99434e; }
QPushButton[variant="danger"]:hover { color: #ffffff; background: #c44251; border-color: #e45a69; }
QPushButton[variant="danger"]:pressed { background: #9e3340; }
QPushButton[variant="danger"]:disabled { color: #92787c; background: #38292d; border-color: #493037; }
QPushButton#menuToggle { border-radius: 14px; padding: 0; font-size: 22px; font-weight: 400; }
QMessageBox { background: #1d1e21; }
QPushButton#primary { background: #e4e6eb; color: #17181b; border-color: #e4e6eb; }
QPushButton#primary:hover { background: #ffffff; border-color: #ffffff; }
QPushButton#primary:disabled { background: #3a3d43; border-color: #3a3d43; color: #858993; }
QPushButton[variant="quiet"] { background: transparent; border-color: transparent; color: #bdc1ca; padding: 8px; }
QPushButton[variant="quiet"]:hover { background: #2c2f35; color: #ffffff; }
QPushButton[variant="quiet"]:focus { border-color: #c6cad2; }
QPushButton[variant="nav"] { text-align: left; padding: 13px 16px; background: transparent; border: 1px solid transparent; color: #9fa4af; font-weight: 400; }
QPushButton[variant="nav"]:hover { background: #1d1f23; color: #e9eaec; }
QPushButton[variant="nav"]:checked { background: #292c32; color: #f2f3f5; font-weight: 600; }
QPushButton[variant="nav"]:focus { border-color: #737a86; }
QListWidget#profileList { background: transparent; border: none; outline: none; }
QListWidget#profileList::item { padding: 12px; margin-bottom: 4px; border-radius: 12px; color: #aeb3bd; }
QListWidget#profileList::item:hover { background: #24272c; }
QListWidget#profileList::item:selected { background: #343840; color: #f3f4f6; }
QListWidget#profileList::item:focus { border: 1px solid #9299a6; }
QCheckBox { spacing: 8px; color: #c5c9d1; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid #747b88; background: #191b1f; }
QCheckBox::indicator:checked { background: #d2d6de; border: 4px solid #575f6c; }
QCheckBox::indicator:focus { border-color: #ffffff; }
QCheckBox:disabled { color: #767c87; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #454a54; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #69717f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QToolTip { color: #f0f1f4; background: #30343b; border: 1px solid #5c6370; padding: 6px; }
"""
