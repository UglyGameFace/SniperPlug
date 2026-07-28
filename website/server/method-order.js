export const METHOD_ORDER_STEP = 10;
export const METHOD_ORDER_MAX = 9999;

function normalizedOrder(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(METHOD_ORDER_MAX, Math.trunc(parsed)));
}

export function nextAutomaticMethodOrder(values = []) {
  const orders = values.map(normalizedOrder).filter((value) => value !== null);
  if (!orders.length) return METHOD_ORDER_STEP;

  const highest = Math.max(...orders);
  if (highest >= METHOD_ORDER_MAX) return METHOD_ORDER_MAX;

  const nextStep = Math.ceil((highest + 1) / METHOD_ORDER_STEP) * METHOD_ORDER_STEP;
  return Math.min(METHOD_ORDER_MAX, nextStep);
}

export function resolveAutomaticMethodOrder(existingOrder, otherOrders = []) {
  const current = normalizedOrder(existingOrder);
  return current === null ? nextAutomaticMethodOrder(otherOrders) : current;
}
