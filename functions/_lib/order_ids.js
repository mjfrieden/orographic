// Broker identifiers are opaque strings, not quantities. Never stringify an
// unsafe Number: any precision it lost before reaching us cannot be recovered.
export function normalizeOrderId(value) {
  if (typeof value === "string") return value.trim() ? value : null;
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
    return String(value);
  }
  return null;
}

// Quote integer ID tokens before JSON.parse can round them. Consume complete
// strings (including escapes), so text inside a message is never rewritten.
// Other numeric fields retain normal JSON number semantics. JSON.parse remains
// the syntax validator; malformed numbers must not become valid quoted IDs.
export function parseOrderJson(text) {
  let key = null;
  let keyEnd = 0;
  const tokens = /"(?:\\[\s\S]|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  const lossless = text.replace(tokens, (token, offset) => {
    if (token.startsWith('"')) {
      key = JSON.parse(token);
      keyEnd = offset + token.length;
    } else if (
      (key === "id" || key === "broker_order_id") &&
      /^\s*:\s*$/.test(text.slice(keyEnd, offset)) &&
      /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(token) &&
      /^\s*[,}\]]/.test(text.slice(offset + token.length))
    ) {
      // Reject other numeric forms before rounding/underflow can turn a
      // fractional, negative or exponent ID into an apparently safe integer.
      return /^(?:0|[1-9]\d*)$/.test(token) ? JSON.stringify(token) : "null";
    }
    return token;
  });
  return JSON.parse(lossless);
}
