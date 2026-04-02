import { buildSessionCookie, verifyUserPassword } from "../_lib/auth.js";

export async function onRequestPost(context) {
  const contentType = context.request.headers.get("content-type") || "";
  let username = "";
  let password = "";
  let next = "/";

  if (contentType.includes("application/json")) {
    const payload = await context.request.json();
    username = String(payload.username || "");
    password = String(payload.password || "");
    next = String(payload.next || "/");
  } else {
    const form = await context.request.formData();
    username = String(form.get("username") || "");
    password = String(form.get("password") || "");
    next = String(form.get("next") || "/");
  }

  const user = await verifyUserPassword(context.env, username, password);
  if (!user) {
    return new Response(JSON.stringify({ ok: false, error: "Invalid credentials" }), {
      status: 401,
      headers: {
        "content-type": "application/json; charset=utf-8",
      },
    });
  }

  const cookie = await buildSessionCookie(context.env, user);
  return new Response(JSON.stringify({ ok: true, redirectTo: next, role: user.role, username: user.username }), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "set-cookie": cookie,
    },
  });
}

