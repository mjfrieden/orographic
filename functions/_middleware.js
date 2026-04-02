import { htmlResponse, loginRedirect, readSession } from "./_lib/auth.js";

function isPublicPath(pathname) {
  return (
    pathname === "/login" ||
    pathname === "/login/" ||
    pathname === "/api/login" ||
    pathname === "/api/logout"
  );
}

export async function onRequest(context) {
  const { request } = context;
  const url = new URL(request.url);

  if (isPublicPath(url.pathname)) {
    return context.next();
  }

  const session = await readSession(request, context.env);
  if (!session) {
    return loginRedirect(request);
  }

  if (url.pathname.startsWith("/admin") && session.role !== "admin") {
    return htmlResponse(
      `<!doctype html><html><body style="font-family: sans-serif; padding: 32px;"><h1>403</h1><p>This area requires an admin session.</p></body></html>`,
      403
    );
  }

  return context.next();
}

