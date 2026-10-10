// Read-only, one-page projection. Never infer account-wide performance from it.
export const GAINLOSS_LIMIT = 100;
export const GAINLOSS_MAX_PAGE = 10000;
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const text = (value) => typeof value === "string" && value.trim() ? value.trim() : null;
function number(value) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function calendar(value) {
  const date = text(value);
  if (!date || !/^\d{4}-\d{2}-\d{2}(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?$/.test(date) || !Number.isFinite(Date.parse(date))) return null;
  const day = date.slice(0, 10);
  const parsed = new Date(`${day}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === day ? day : null;
}
export function parseGainlossPage(url) {
  const params = new URL(url).searchParams;
  if ([...params.keys()].some((key) => key !== "page") || params.getAll("page").length > 1) return null;
  const raw = params.get("page") ?? "1";
  if (!/^[1-9]\d{0,4}$/.test(raw)) return null;
  const page = Number(raw);
  return page <= GAINLOSS_MAX_PAGE ? page : null;
}
export function normalizeGainloss(payload, page) {
  if (!object(payload) || !own(payload, "gainloss")) throw new Error("Malformed gain/loss response");
  const node = payload.gainloss;
  // Null collection is a conventional broker empty response. A missing field is not.
  if (node !== null && (!object(node) || !own(node, "closed_position"))) throw new Error("Malformed gain/loss collection");
  const collection = node === null ? null : node.closed_position;
  const source = collection === null ? [] : Array.isArray(collection) ? collection : object(collection) ? [collection] : null;
  if (!source || source.length > GAINLOSS_LIMIT || source.some((row) => !object(row) || !Object.keys(row).some((key) => ["symbol", "gain_loss", "cost", "proceeds", "close_date"].includes(key)))) throw new Error("Malformed gain/loss rows");
  const rows = source.map((row) => {
    const result = {
      symbol: text(row.symbol),
      close_date: text(row.close_date),
      close_date_calendar: calendar(row.close_date),
      open_date: text(row.open_date),
    };
    for (const key of ["cost", "proceeds", "gain_loss", "gain_loss_percent", "quantity", "term"]) result[key] = number(row[key]);
    result.missing_fields = Object.keys(result).filter((key) => result[key] === null);
    return result;
  });
  // The published response schema has no pagination metadata or broker-as-of field.
  // Preserve only a bounded scalar subset if pagination is supplied; never treat
  // undocumented metadata or a short page as evidence of complete history.
  const reported = {};
  for (const key of ["page", "limit", "total", "total_pages", "has_more"]) {
    const value = node?.[key];
    if (typeof value === "boolean" || (Number.isSafeInteger(value) && value >= 0)) reported[key] = value;
  }
  return {
    rows,
    pagination: {
      page, limit: GAINLOSS_LIMIT, returned: rows.length,
      next_page: rows.length && page < GAINLOSS_MAX_PAGE ? page + 1 : null,
      has_more: rows.length ? null : false,
      completeness: "unknown",
      reported: Object.keys(reported).length ? reported : null,
    },
  };
}
export function normalizeGainlossRateLimits(limits) {
  return Object.fromEntries(["allowed", "used", "available", "expiry"].map((key) => {
    const value = number(limits?.[key]);
    return [key, Number.isSafeInteger(value) && value >= 0 ? value : null];
  }));
}
