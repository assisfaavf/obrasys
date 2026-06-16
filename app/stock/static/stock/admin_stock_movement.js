(function () {
  function getField(name) {
    return document.getElementById("id_" + name);
  }

  function formatQuantity(value) {
    var numberValue = Number(value || 0);
    if (!Number.isFinite(numberValue)) {
      return "0.000";
    }
    return numberValue.toFixed(3);
  }

  function setValue(name, value) {
    var field = getField(name);
    if (field) {
      field.value = formatQuantity(value);
    }
  }

  function updateBalances() {
    var material = getField("material");
    var location = getField("location");
    var movementType = getField("movement_type");
    var quantity = getField("quantity");

    if (!material || !location || !movementType || !quantity || !material.value || !location.value) {
      setValue("available_quantity", "0");
      setValue("balance_after_display", "0");
      return;
    }

    var params = new URLSearchParams({
      material: material.value,
      location: location.value,
      movement_type: movementType.value || "",
      quantity: quantity.value || "0",
    });

    var endpoint = window.location.pathname.replace(/(?:add\/|\d+\/change\/)?$/, "available-balance/");
    fetch(endpoint + "?" + params.toString(), {
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
        setValue("available_quantity", data.available_quantity);
        setValue("balance_after_display", data.balance_after);
      })
      .catch(function () {
        setValue("available_quantity", "0");
        setValue("balance_after_display", "0");
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    ["material", "location", "movement_type", "quantity"].forEach(function (name) {
      var field = getField(name);
      if (field) {
        field.addEventListener("change", updateBalances);
        field.addEventListener("input", updateBalances);
      }
    });
    updateBalances();
  });
})();
