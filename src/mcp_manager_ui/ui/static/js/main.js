// ================================================ //
// FILE: src/mcp_manager_ui/ui/static/js/main.js  //
// (Упрощенная инициализация)                     //
// ================================================ //

console.log("MCP Manager UI JS loaded.");

/**
 * Показывает/скрывает поля формы в зависимости от типа сервера (stdio/http).
 * @param {string} serverType - Выбранное значение ('stdio', 'sse', 'streamable_http').
 * @param {string} formPrefix - Префикс ID элементов формы (напр., 'single-add-' или 'edit-').
 */
function toggleServerTypeSpecificFields(serverType, formPrefix = '') {
    console.debug(`[toggleServerTypeSpecificFields] Called for prefix '${formPrefix}' with type '${serverType}'`);
    const stdioFieldsContainer = document.getElementById(formPrefix + 'stdio-fields-container');
    const httpFieldsContainer = document.getElementById(formPrefix + 'http-fields-container');
    const commandInput = document.getElementById(formPrefix + 'command');
    const urlInput = document.getElementById(formPrefix + 'url');

    // Проверки на существование элементов
    if (!stdioFieldsContainer) console.warn(`[toggleServerTypeSpecificFields] Element not found: ${formPrefix}stdio-fields-container`);
    if (!httpFieldsContainer) console.warn(`[toggleServerTypeSpecificFields] Element not found: ${formPrefix}http-fields-container`);

    // Сначала все скрываем и делаем необязательными
    if (stdioFieldsContainer) stdioFieldsContainer.style.display = 'none';
    if (httpFieldsContainer) httpFieldsContainer.style.display = 'none';
    if (commandInput) commandInput.required = false;
    if (urlInput) urlInput.required = false;

    // Показываем нужные и делаем обязательными
    if (serverType === 'stdio' && stdioFieldsContainer) {
        stdioFieldsContainer.style.display = 'block';
        if (commandInput) commandInput.required = true;
        console.debug(`[toggleServerTypeSpecificFields] Showing stdio fields for ${formPrefix}`);
    } else if ((serverType === 'sse' || serverType === 'streamable_http') && httpFieldsContainer) {
        httpFieldsContainer.style.display = 'block';
        if (urlInput) urlInput.required = true;
         console.debug(`[toggleServerTypeSpecificFields] Showing http fields for ${formPrefix}`);
    } else {
         console.debug(`[toggleServerTypeSpecificFields] No specific type matched or element missing for ${formPrefix}, hiding both sections.`);
    }
}

/**
 * Добавляет поле для ввода аргумента команды.
 * @param {string} formPrefix - Префикс ID элементов формы.
 */
function addArgumentField(formPrefix = '') {
    // ... (код функции без изменений) ...
    const container = document.getElementById(formPrefix + 'args-list-container');
    if (!container) {
        console.warn(`[addArgumentField] Container not found: ${formPrefix}args-list-container`);
        return;
    }
    const newFieldRow = document.createElement('div');
    newFieldRow.classList.add('row', 'dynamic-field-row');
    newFieldRow.style.marginBottom = '5px';
    newFieldRow.innerHTML = `
        <div class="input-field col s10 m10 l10" style="margin-top:0; margin-bottom:0;">
            <input type="text" name="arg_item[]" placeholder="Аргумент">
        </div>
        <div class="col s2 m2 l2" style="padding-top: 10px;">
            <button type="button" class="btn-floating btn-small waves-effect waves-light red lighten-1" onclick="removeDynamicField(this)" title="Удалить аргумент">
                <i class="material-icons">remove</i>
            </button>
        </div>
    `;
    container.appendChild(newFieldRow);
}

/**
 * Добавляет поля для ввода переменной окружения (ключ и значение).
 * @param {string} formPrefix - Префикс ID элементов формы.
 */
function addEnvVarField(formPrefix = '') {
    // ... (код функции без изменений) ...
     const container = document.getElementById(formPrefix + 'env-vars-list-container');
    if (!container) {
         console.warn(`[addEnvVarField] Container not found: ${formPrefix}env-vars-list-container`);
         return;
     }
    const newFieldRow = document.createElement('div');
    newFieldRow.classList.add('row', 'dynamic-field-row');
    newFieldRow.style.marginBottom = '5px';
    newFieldRow.innerHTML = `
        <div class="input-field col s5 m5 l5" style="margin-top:0; margin-bottom:0;">
            <input type="text" name="env_key[]" placeholder="Имя переменной">
        </div>
        <div class="input-field col s5 m5 l5" style="margin-top:0; margin-bottom:0;">
            <input type="text" name="env_value[]" placeholder="Значение">
        </div>
        <div class="col s2 m2 l2" style="padding-top: 10px;">
            <button type="button" class="btn-floating btn-small waves-effect waves-light red lighten-1" onclick="removeDynamicField(this)" title="Удалить переменную">
                <i class="material-icons">remove</i>
            </button>
        </div>
    `;
    container.appendChild(newFieldRow);
}

/**
 * Удаляет родительский элемент .dynamic-field-row для кнопки, на которую нажали.
 * @param {HTMLElement} buttonElement - Кнопка удаления.
 */
function removeDynamicField(buttonElement) {
    // ... (код функции без изменений) ...
    const fieldRow = buttonElement.closest('.dynamic-field-row');
    if (fieldRow) {
        fieldRow.remove();
    } else {
        console.warn("[removeDynamicField] Could not find parent '.dynamic-field-row' to remove.");
    }
}

// Глобальная инициализация Materialize и начального состояния форм
document.addEventListener('DOMContentLoaded', function() {
    // Инициализируем Materialize (табы, селекты, инпуты и т.д.)
    M.AutoInit();
    console.log("Materialize components initialized via M.AutoInit().");

    // Вызываем toggleServerTypeSpecificFields для установки НАЧАЛЬНОГО состояния
    // для КАЖДОЙ формы сервера на странице (добавления или редактирования)
    const serverTypeSelects = document.querySelectorAll('select[id$="server_type"]');
    console.log(`Found ${serverTypeSelects.length} server type select(s) for initial state setup.`);

    serverTypeSelects.forEach(selectElement => {
        const formPrefix = selectElement.id.replace('server_type', '');
        console.log(`Setting initial fields visibility for prefix: '${formPrefix}' based on value: '${selectElement.value}'`);
        // Сразу вызываем функцию для установки видимости полей при загрузке
        toggleServerTypeSpecificFields(selectElement.value, formPrefix);

        // Добавляем обработчик события 'change'
        selectElement.addEventListener('change', function() {
            console.log(`Server type changed for prefix: '${formPrefix}' to value: '${this.value}'`);
            toggleServerTypeSpecificFields(this.value, formPrefix);
        });

        // Обновляем лэйблы Materialize, на случай если есть предзаполненные значения
        const wrapper = document.getElementById(formPrefix + 'form-wrapper');
         if (wrapper) {
             M.updateTextFields(wrapper);
             console.log(`Updated text fields for wrapper '${formPrefix}form-wrapper'.`);
         }
    });
});