"use strict";

const config = window.APP_CONFIG ?? {};
const userApi = stripTrailingSlash(config.userServiceUrl ?? "http://localhost:8001");
const orderApi = stripTrailingSlash(config.orderServiceUrl ?? "http://localhost:8002");

const elements = {
  createUserForm: document.querySelector("#create-user-form"),
  findUserForm: document.querySelector("#find-user-form"),
  createOrderForm: document.querySelector("#create-order-form"),
  ordersHistoryForm: document.querySelector("#orders-history-form"),
  productsList: document.querySelector("#products-list"),
  ordersList: document.querySelector("#orders-list"),
  userResult: document.querySelector("#user-result"),
  refreshProducts: document.querySelector("#refresh-products"),
  productSelect: document.querySelector('#create-order-form select[name="productId"]'),
};

elements.createUserForm.addEventListener("submit", createUser);
elements.findUserForm.addEventListener("submit", findUser);
elements.createOrderForm.addEventListener("submit", createOrder);
elements.ordersHistoryForm.addEventListener("submit", loadOrderHistory);
elements.refreshProducts.addEventListener("click", loadProducts);

document.querySelector("#api-status").textContent = `API: ${userApi} · ${orderApi}`;
loadProducts();

async function createUser(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  await runFormRequest(form, "create-user", async () => {
    const user = await request(`${userApi}/users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: data.get("name"), email: data.get("email") }),
    });
    form.reset();
    setFeedback("create-user", `Пользователь создан. ID: ${user.id}`, "success");
    elements.createOrderForm.elements.userId.value = user.id;
    elements.ordersHistoryForm.elements.userId.value = user.id;
  });
}

async function findUser(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const userId = new FormData(form).get("userId");
  elements.userResult.hidden = true;
  await runFormRequest(form, "find-user", async () => {
    const user = await request(`${userApi}/users/${userId}`);
    elements.userResult.replaceChildren(
      createTextElement("strong", user.name),
      createTextElement("div", `ID: ${user.id}`),
      createTextElement("div", user.email),
    );
    elements.userResult.hidden = false;
    setFeedback("find-user", "Пользователь найден.", "success");
  });
}

async function loadProducts() {
  setLoading(elements.refreshProducts, true, "Загрузка…");
  setFeedback("products", "Загружаем каталог…");
  elements.productsList.setAttribute("aria-busy", "true");
  try {
    const products = await request(`${orderApi}/products?limit=100&offset=0`);
    renderProducts(products);
    setFeedback("products", `Товаров в каталоге: ${products.length}`, "success");
  } catch (error) {
    elements.productsList.replaceChildren(emptyState("Каталог временно недоступен."));
    setFeedback("products", error.message, "error");
  } finally {
    elements.productsList.removeAttribute("aria-busy");
    setLoading(elements.refreshProducts, false);
  }
}

function renderProducts(products) {
  elements.productsList.replaceChildren();
  elements.productSelect.replaceChildren(new Option("Выберите товар", ""));
  if (products.length === 0) {
    elements.productsList.append(emptyState("Товаров пока нет."));
    return;
  }

  for (const product of products) {
    const card = document.createElement("article");
    card.className = "product-card";
    const buyButton = createTextElement("button", "Выбрать");
    buyButton.type = "button";
    buyButton.className = "button button-secondary";
    buyButton.disabled = product.stock < 1;
    buyButton.addEventListener("click", () => {
      elements.productSelect.value = String(product.id);
      elements.createOrderForm.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    card.append(
      createTextElement("h3", product.name),
      createTextElement("p", `Цена: ${product.price}`, "price"),
      productMeta(product),
      buyButton,
    );
    elements.productsList.append(card);
    elements.productSelect.add(
      new Option(`${product.name} — ${product.price} (${product.stock} шт.)`, product.id),
    );
  }
}

async function createOrder(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  await runFormRequest(form, "create-order", async () => {
    const order = await request(`${orderApi}/orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: Number(data.get("userId")),
        product_id: Number(data.get("productId")),
        quantity: Number(data.get("quantity")),
      }),
    });
    setFeedback("create-order", `Заказ №${order.id} создан.`, "success");
    elements.ordersHistoryForm.elements.userId.value = order.user_id;
    await loadProducts();
  });
}

async function loadOrderHistory(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const userId = new FormData(form).get("userId");
  await runFormRequest(form, "orders-history", async () => {
    const orders = await request(
      `${orderApi}/orders?user_id=${encodeURIComponent(userId)}&sort_order=desc&limit=100`,
    );
    renderOrders(orders);
    setFeedback("orders-history", `Заказов найдено: ${orders.length}`, "success");
  });
}

function renderOrders(orders) {
  elements.ordersList.replaceChildren();
  if (orders.length === 0) {
    elements.ordersList.append(emptyState("У пользователя пока нет заказов."));
    return;
  }
  for (const order of orders) {
    const row = document.createElement("article");
    row.className = "order-row";
    row.append(
      createTextElement("strong", `№${order.id}`),
      createTextElement("span", `Товар #${order.product_id}`),
      createTextElement("span", `${order.quantity} шт.`),
      createTextElement("span", order.status),
      createTextElement("time", formatDate(order.created_at)),
    );
    elements.ordersList.append(row);
  }
}

async function runFormRequest(form, feedbackName, operation) {
  const button = form.querySelector('button[type="submit"]');
  setLoading(button, true, "Подождите…");
  setFeedback(feedbackName, "Выполняем запрос…");
  try {
    await operation();
  } catch (error) {
    setFeedback(feedbackName, error.message, "error");
  } finally {
    setLoading(button, false);
  }
}

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error("Не удалось подключиться к сервису. Проверьте, что backend запущен.");
  }

  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      // The service may return an empty or non-JSON error response.
    }
    throw new Error(errorMessage(response.status, body));
  }
  return response.json();
}

function errorMessage(status, body) {
  if (Array.isArray(body?.detail)) {
    return `Проверьте введённые данные (${status}).`;
  }
  if (typeof body?.detail === "string") {
    const known = {
      "User not found": "Пользователь не найден.",
      "Product not found": "Товар не найден.",
      "Insufficient stock": "Недостаточно товара на складе.",
      "A user with this email already exists": "Пользователь с таким email уже существует.",
      "User Service unavailable": "Сервис пользователей временно недоступен.",
      "User Service timed out": "Сервис пользователей не ответил вовремя.",
      "Database operation failed": "База данных временно недоступна.",
    };
    return known[body.detail] ?? body.detail;
  }
  return `Сервис вернул ошибку HTTP ${status}.`;
}

function productMeta(product) {
  const meta = document.createElement("div");
  meta.className = "product-meta";
  meta.append(
    createTextElement("span", `ID ${product.id}`),
    createTextElement("span", `На складе: ${product.stock}`),
  );
  return meta;
}

function setFeedback(name, message, type = "") {
  const element = document.querySelector(`[data-feedback="${name}"]`);
  element.textContent = message;
  element.className = `feedback ${type}`.trim();
}

function setLoading(button, isLoading, label = "") {
  if (isLoading) {
    button.dataset.originalLabel = button.textContent;
    button.textContent = label;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.originalLabel ?? button.textContent;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function createTextElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

function emptyState(message) {
  return createTextElement("div", message, "empty-state");
}

function formatDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function stripTrailingSlash(value) {
  return value.replace(/\/$/, "");
}
