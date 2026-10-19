"""One connection editor shared by every AI tool in the main window."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QVBoxLayout, QWidget

from ai_connection import (PROVIDERS, OPENAI_BASE_URL, profiles, profile_key, selected_values,
                           credentials, origin)
from model_catalog_gui import ModelCatalogControl
from ui_widgets import NoWheelComboBox, field, label


class ConnectionEditor(QWidget):
    changed = Signal()
    connection_changed = Signal()

    def __init__(self, values, get_saved):
        super().__init__()
        self.get_saved = get_saved
        self._profiles = profiles(values)
        for row in self._profiles.values():
            row['API_KEY'] = ''  # Saved keys stay in storage, never in a widget.
        self._current = values.get('AI_PROVIDER', 'compatible')
        self._revision, self._loading = 0, True
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        self.provider = NoWheelComboBox()
        self.provider.addItem('OpenAI · api.openai.com', 'openai')
        self.provider.addItem('OpenAI-совместимый API', 'compatible')
        self.provider.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.provider.setMinimumContentsLength(12)
        self.provider.setCurrentIndex(self.provider.findData(self._current))
        box.addWidget(field('Подключение AI', self.provider))
        self.base_url = QLineEdit()
        self.base_field = field('Base URL', self.base_url)
        box.addWidget(self.base_field)
        self.compatible_help = label('Любой сервис по стандарту OpenAI Chat Completions: его Base URL, его ключ и доступные у него модели, включая DeepSeek и другие.', 'muted', True)
        box.addWidget(self.compatible_help)
        self.official = label('Официальный адрес: ' + OPENAI_BASE_URL + '\nДостаточно API-ключа. Модель по умолчанию — gpt-4.1-mini; её можно изменить.', 'muted', True)
        box.addWidget(self.official)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        box.addWidget(field('API-ключ выбранного подключения', self.api_key))
        self.model_catalog = ModelCatalogControl(self._profiles[self._current]['MODEL'], self.credentials)
        box.addWidget(field('Основная модель', self.model_catalog))
        self.model = self.model_catalog.combo
        self.fallback_models = QLineEdit()
        self.fallback_models.setPlaceholderText('model-a, model-b')
        box.addWidget(field('Запасные модели · необязательно', self.fallback_models))
        box.addWidget(label('Модели и ключ сохраняются отдельно для каждого подключения. Запасные модели вызываются только в выбранном API. Запросы могут расходовать его баланс.', 'muted', True))
        self._load()
        self._loading = False
        self.provider.currentIndexChanged.connect(self._switch)
        for widget in (self.base_url, self.api_key):
            widget.textChanged.connect(self._connection_edit)
        self.model.currentTextChanged.connect(self._edit)
        self.fallback_models.textChanged.connect(self._edit)

    def _remember(self):
        self._profiles[self._current].update(BASE_URL=self.base_url.text(), API_KEY=self.api_key.text(),
            MODEL=self.model.currentText(), FALLBACK_MODELS=self.fallback_models.text())

    def _load(self):
        row = self._profiles[self._current]
        self.base_url.setText(OPENAI_BASE_URL if self._current == 'openai' else row['BASE_URL'])
        self.api_key.setText(row['API_KEY'])
        self.model.setCurrentText(row['MODEL'])
        self.fallback_models.setText(row['FALLBACK_MODELS'])
        self.base_field.setVisible(self._current == 'compatible')
        self.compatible_help.setVisible(self._current == 'compatible')
        self.official.setVisible(self._current == 'openai')
        saved = profiles(self.get_saved())[self._current]
        self.api_key.setPlaceholderText('Ключ сохранён для этого подключения' if saved['API_KEY'] else 'Ключ выбранного AI API')

    def _switch(self):
        self._remember()
        self._current = self.provider.currentData()
        self._loading = True
        self._load()
        self._loading = False
        self._connection_edit()

    def _edit(self):
        if not self._loading:
            self.changed.emit()

    def _connection_edit(self):
        if self._loading:
            return
        self._revision += 1
        self.model_catalog.invalidate()
        self.connection_changed.emit()
        self.changed.emit()

    @property
    def prompt_model(self):
        return self._profiles[self._current]['PROMPT_MODEL']

    def set_prompt_model(self, model):
        if self._profiles[self._current]['PROMPT_MODEL'] != model:
            self._profiles[self._current]['PROMPT_MODEL'] = model
            self.changed.emit()

    def values(self):
        self._remember()
        result = dict(AI_PROVIDER=self._current)
        for provider in PROVIDERS:
            row = self._profiles[provider]
            result.update({profile_key(provider, key): value for key, value in row.items() if key != 'KEY_ORIGIN'})
            if row['API_KEY'].strip() and row['BASE_URL'].strip():
                result[profile_key(provider, 'CONFIRMED_ORIGIN')] = origin(row['BASE_URL'])
        result.update({'AI_' + key: value for key, value in self._profiles[self._current].items() if key != 'KEY_ORIGIN'})
        return result

    def credentials(self):
        values = self.values()
        values = {key: value for key, value in values.items()
                  if key.startswith(profile_key(self._current, '')) or key in (
                      'AI_PROVIDER', 'AI_BASE_URL', 'AI_API_KEY', 'AI_MODEL', 'AI_FALLBACK_MODELS', 'AI_PROMPT_MODEL')}
        result = selected_values(values, self.get_saved())
        return credentials(result['AI_BASE_URL'], result['AI_API_KEY'], provider=self._current,
            revision=self._revision, model=result['AI_MODEL'], fallback_models=result['AI_FALLBACK_MODELS'])

    def saved(self, values):
        self._profiles = profiles(values)
        for row in self._profiles.values():
            row['API_KEY'] = ''
        self._loading = True
        self._load()
        self._loading = False
        self._connection_edit()
