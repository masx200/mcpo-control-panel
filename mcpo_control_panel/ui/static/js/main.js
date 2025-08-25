// ================================================ //
// FILE: mcpo_control_panel/ui/static/js/main.js  //
// (Simplified initialization)                     //
// ================================================ //

console.log("MCP Manager UI JS loaded.");

/**
 * Shows/hides form fields depending on server type (stdio/http).
 * @param {string} serverType - Selected value ('stdio', 'sse', 'streamable_http').
 * @param {string} formPrefix - Form elements ID prefix (e.g., 'single-add-' or 'edit-').
 */
function toggleServerTypeSpecificFields(serverType, formPrefix = "") {
  const stdioFieldsContainer = document.getElementById(
    formPrefix + "stdio-fields-container",
  );
  const httpFieldsContainer = document.getElementById(
    formPrefix + "http-fields-container",
  );
  const commandInput = document.getElementById(formPrefix + "command");
  const urlInput = document.getElementById(formPrefix + "url");

  // Hide all fields and make them optional
  if (stdioFieldsContainer) stdioFieldsContainer.style.display = "none";
  if (httpFieldsContainer) httpFieldsContainer.style.display = "none";
  if (commandInput) commandInput.required = false;
  if (urlInput) urlInput.required = false;

  // Show relevant fields and make them required
  if (serverType === "stdio" && stdioFieldsContainer) {
    stdioFieldsContainer.style.display = "block";
    if (commandInput) commandInput.required = true;
  } else if (
    (serverType === "sse" || serverType === "streamable_http") &&
    httpFieldsContainer
  ) {
    httpFieldsContainer.style.display = "block";
    if (urlInput) urlInput.required = true;
  }
}

/**
 * Adds a command argument input field.
 * @param {string} formPrefix - Form elements ID prefix.
 */
function addArgumentField(formPrefix = "") {
  const container = document.getElementById(formPrefix + "args-list-container");
  if (!container) {
    return;
  }
  const newFieldRow = document.createElement("div");
  newFieldRow.classList.add("row", "dynamic-field-row");
  newFieldRow.style.marginBottom = "5px";
  newFieldRow.innerHTML = `
        <div class="input-field col s10 m10 l10" style="margin-top:0; margin-bottom:0;">
            <input type="text" name="arg_item[]" placeholder="Argument">
        </div>
        <div class="col s2 m2 l2" style="padding-top: 10px;">
            <button type="button" class="btn-floating btn-small waves-effect waves-light red lighten-1" onclick="removeDynamicField(this)" title="Remove argument">
                <i class="material-icons">remove</i>
            </button>
        </div>
    `;
  container.appendChild(newFieldRow);
}

/**
 * Adds environment variable input fields (key and value).
 * @param {string} formPrefix - Form elements ID prefix.
 */
function addEnvVarField(formPrefix = "") {
  const container = document.getElementById(
    formPrefix + "env-vars-list-container",
  );
  if (!container) {
    return;
  }
  const newFieldRow = document.createElement("div");
  newFieldRow.classList.add("row", "dynamic-field-row");
  newFieldRow.style.marginBottom = "5px";
  newFieldRow.innerHTML = `
        <div class="input-field col s5 m5 l5" style="margin-top:0; margin-bottom:0;">
            <input type="text" name="env_key[]" placeholder="Variable name">
        </div>
        <div class="input-field col s5 m5 l5" style="margin-top:0; margin-bottom:0;">
            <input type="text" name="env_value[]" placeholder="Value">
        </div>
        <div class="col s2 m2 l2" style="padding-top: 10px;">
            <button type="button" class="btn-floating btn-small waves-effect waves-light red lighten-1" onclick="removeDynamicField(this)" title="Remove variable">
                <i class="material-icons">remove</i>
            </button>
        </div>
    `;
  container.appendChild(newFieldRow);
}

/**
 * Removes the parent .dynamic-field-row element for the clicked button.
 * @param {HTMLElement} buttonElement - Remove button.
 */
function removeDynamicField(buttonElement) {
  const fieldRow = buttonElement.closest(".dynamic-field-row");
  if (fieldRow) {
    fieldRow.remove();
  }
}

// Global initialization of Materialize and initial form states
document.addEventListener("DOMContentLoaded", function () {
  // Initialize Materialize components
  M.AutoInit();

  // Set INITIAL state for EACH server form on the page (add or edit)
  const serverTypeSelects = document.querySelectorAll(
    'select[id$="server_type"]',
  );

  serverTypeSelects.forEach((selectElement) => {
    const formPrefix = selectElement.id.replace("server_type", "");
    // Set initial field visibility on load
    toggleServerTypeSpecificFields(selectElement.value, formPrefix);

    // Add change event handler
    selectElement.addEventListener("change", function () {
      toggleServerTypeSpecificFields(this.value, formPrefix);
    });

    // Update Materialize labels for pre-filled values
    const wrapper = document.getElementById(formPrefix + "form-wrapper");
    if (wrapper) {
      M.updateTextFields(wrapper);
    }
  });
});
