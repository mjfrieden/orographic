import { getTradierSettings, tradierRequest } from "./tradier.js";

export function isPositiveContractQuantity(value) {
  if (typeof value !== "number" && (typeof value !== "string" || !/^\d+$/.test(value.trim()))) return false;
  return Number.isSafeInteger(Number(value)) && Number(value) > 0;
}

// Read current holdings for every preview and submission. Never cache a preview's
// holdings or retry an order POST; the broker remains the final execution check.
export async function validateClosePosition(env, optionSymbol, quantity) {
  if (!isPositiveContractQuantity(quantity)) {
    throw new Error("Closing quantity must be a positive whole number of contracts. No order was sent.");
  }
  const symbol = String(optionSymbol || "").trim().toUpperCase();
  const settings = getTradierSettings(env);
  const response = await tradierRequest(env, {
    path: `/accounts/${settings.accountId}/positions`,
  });
  if (!response.ok) throw new Error("Current holdings could not be verified. No order was sent.");
  const raw = response.data?.positions?.position;
  const positions = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? [raw] : [];
  const matches = positions.filter(position => String(position?.symbol || "").trim().toUpperCase() === symbol);
  // An ambiguous/invalid position response must not authorize a close.
  if (matches.length !== 1 || !isPositiveContractQuantity(matches[0].quantity) ||
      Number(quantity) > Number(matches[0].quantity)) {
    throw new Error("Sell to close requires enough current long contracts for this exact option. No order was sent.");
  }
}
