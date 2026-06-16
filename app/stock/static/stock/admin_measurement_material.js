(function () {
  function formatQuantity(value) {
    var numberValue = Number(value || 0);
    if (!Number.isFinite(numberValue)) {
      return "0.000";
    }
    return numberValue.toFixed(3);
  }

  function findAdminBasePath() {
    var marker = "/admin/";
    var index = window.location.pathname.indexOf(marker);
    if (index === -1) {
      return "/admin/";
    }
    return window.location.pathname.slice(0, index + marker.length);
  }

  function getAvailableEndpoint() {
    return findAdminBasePath() + "stock/stockmovement/available-balance/";
  }

  function updateRow(row) {
    var material = row.querySelector('select[id$="-material"], select#id_material');
    var location = row.querySelector('select[id$="-stock_location"], select#id_stock_location');
    var available = row.querySelector('input[id$="-available_quantity"], input#id_available_quantity');

    if (!available) {
      return;
    }
    if (!material || !location || !material.value || !location.value) {
      available.value = formatQuantity(0);
      return;
    }

    var params = new URLSearchParams({
      material: material.value,
      location: location.value,
      movement_type: "OUT",
      quantity: "0",
    });

    fetch(getAvailableEndpoint() + "?" + params.toString(), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Balance request failed");
        }
        return response.json();
      })
      .then(function (data) {
        available.value = formatQuantity(data.available_quantity);
      })
      .catch(function () {
        available.value = formatQuantity(0);
      });
  }

  function bindRow(row) {
    if (!row || row.dataset.measurementMaterialBound === "1") {
      return;
    }
    row.dataset.measurementMaterialBound = "1";
    ["material", "stock_location"].forEach(function (fieldName) {
      var field = row.querySelector('select[id$="-' + fieldName + '"], select#id_' + fieldName);
      if (field) {
        field.addEventListener("change", function () {
          updateRow(row);
        });
      }
    });
    updateRow(row);
  }

  function bindRows() {
    document.querySelectorAll(".dynamic-stock_materials, .dynamic-measurementmaterial_set").forEach(bindRow);
    document.querySelectorAll("tr, .form-row, fieldset").forEach(function (row) {
      if (
        row.querySelector('select[id$="-material"], select#id_material') &&
        row.querySelector('select[id$="-stock_location"], select#id_stock_location')
      ) {
        bindRow(row);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", bindRows);
  document.addEventListener("formset:added", function (event) {
    bindRow(event.target);
  });
})();
