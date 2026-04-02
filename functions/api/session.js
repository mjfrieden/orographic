import { readSession } from "../_lib/auth.js";

export async function onRequestGet(context) {
  const session = await readSession(context.request, context.env);
  return new Response(JSON.stringify({ authenticated: Boolean(session), session }), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
    },
  });
}

