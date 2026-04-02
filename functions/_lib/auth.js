const COOKIE_NAME = "orographic_session";
const SESSION_TTL_SECONDS = 60 * 60 * 12;
const PBKDF2_ITERATIONS = 100000;

function textEncoder() {
  return new TextEncoder();
}

function toBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64Url(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4 || 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function parseCookies(header) {
  const values = {};
  if (!header) {
    return values;
  }
  for (const piece of header.split(";")) {
    const [rawKey, ...rest] = piece.trim().split("=");
    if (!rawKey) {
      continue;
    }
    values[rawKey] = rest.join("=");
  }
  return values;
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) {
    return false;
  }
  let result = 0;
  for (let i = 0; i < a.length; i += 1) {
    result |= a[i] ^ b[i];
  }
  return result === 0;
}

async function hmacSign(message, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, textEncoder().encode(message));
  return new Uint8Array(signature);
}

export function getAuthUsers(env) {
  const raw = String(env.OROGRAPHIC_AUTH_USERS_JSON || "").trim();
  if (!raw) {
    throw new Error("Missing OROGRAPHIC_AUTH_USERS_JSON secret");
  }
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("OROGRAPHIC_AUTH_USERS_JSON must be a JSON array");
  }
  return parsed.map((user) => ({
    username: String(user.username || "").trim().toLowerCase(),
    role: String(user.role || "viewer").trim().toLowerCase(),
    salt: String(user.salt || ""),
    hash: String(user.hash || ""),
    iterations: Number(user.iterations || PBKDF2_ITERATIONS),
  }));
}

async function pbkdf2(password, salt, iterations) {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: textEncoder().encode(salt),
      iterations,
    },
    key,
    256
  );
  return new Uint8Array(bits);
}

export async function verifyUserPassword(env, username, password) {
  const normalized = String(username || "").trim().toLowerCase();
  const user = getAuthUsers(env).find((row) => row.username === normalized);
  if (!user) {
    return null;
  }
  const actual = await pbkdf2(password, user.salt, user.iterations);
  const expected = fromBase64Url(user.hash);
  if (!timingSafeEqual(actual, expected)) {
    return null;
  }
  return {
    username: user.username,
    role: user.role,
  };
}

export async function buildSessionCookie(env, session) {
  const secret = String(env.OROGRAPHIC_SESSION_SECRET || "").trim();
  if (!secret) {
    throw new Error("Missing OROGRAPHIC_SESSION_SECRET secret");
  }
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    sub: session.username,
    role: session.role,
    iat: now,
    exp: now + SESSION_TTL_SECONDS,
  };
  const serialized = JSON.stringify(payload);
  const encoded = toBase64Url(textEncoder().encode(serialized));
  const signature = toBase64Url(await hmacSign(encoded, secret));
  const cookieValue = `${encoded}.${signature}`;
  return `${COOKIE_NAME}=${cookieValue}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_TTL_SECONDS}`;
}

export function clearSessionCookie() {
  return `${COOKIE_NAME}=deleted; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
}

export async function readSession(request, env) {
  const secret = String(env.OROGRAPHIC_SESSION_SECRET || "").trim();
  if (!secret) {
    return null;
  }
  const cookies = parseCookies(request.headers.get("Cookie"));
  const raw = cookies[COOKIE_NAME];
  if (!raw || !raw.includes(".")) {
    return null;
  }
  const [encoded, providedSignature] = raw.split(".", 2);
  const expectedSignature = toBase64Url(await hmacSign(encoded, secret));
  if (providedSignature !== expectedSignature) {
    return null;
  }
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(fromBase64Url(encoded)));
  } catch {
    return null;
  }
  const now = Math.floor(Date.now() / 1000);
  if (!payload || typeof payload !== "object" || Number(payload.exp || 0) < now) {
    return null;
  }
  return {
    username: String(payload.sub || ""),
    role: String(payload.role || "viewer"),
  };
}

export function loginRedirect(request) {
  const url = new URL(request.url);
  const next = url.pathname + url.search;
  return Response.redirect(`${url.origin}/login?next=${encodeURIComponent(next)}`, 302);
}

export function htmlResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
    },
  });
}
