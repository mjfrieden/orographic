import { htmlResponse, loginRedirect, readSession } from "./_lib/auth.js";

function isPublicPath(pathname) {
  return (
    pathname === "/login" ||
    pathname === "/login/" ||
    pathname === "/styles.css" ||
    pathname === "/api/login" ||
    pathname === "/api/logout" ||
    pathname === "/api/internal/positions/capture"
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
      `<!doctype html>
      <html lang="en">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <title>Orographic Access Denied</title>
          <link rel="preconnect" href="https://fonts.googleapis.com" />
          <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
          <link
            href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700;800&family=Cormorant+Garamond:wght@400;500;600;700&family=Marcellus+SC&display=swap"
            rel="stylesheet"
          />
          <link rel="stylesheet" href="/styles.css" />
        </head>
        <body class="portal-page access-denied-page">
          <div class="scene-bloom scene-bloom-left" aria-hidden="true"></div>
          <div class="scene-bloom scene-bloom-right" aria-hidden="true"></div>
          <div class="noise"></div>
          <main class="portal-shell">
            <section class="panel portal-card">
              <div class="portal-crest" aria-hidden="true">
                <div class="portal-crest-core">403</div>
              </div>
              <p class="eyebrow">Restricted Harbor</p>
              <h1>Admin Session Required</h1>
              <p class="subhead">The route is still protected exactly as before. Your current session does not have admin access.</p>
              <div class="access-links">
                <a class="primary-action" href="/">Return to Quest Board</a>
              </div>
            </section>
          </main>
        </body>
      </html>`,
      403
    );
  }

  return context.next();
}
