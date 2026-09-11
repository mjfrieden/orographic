const PUBLIC_EXACT = new Set([
  "/login",
  "/login/",
  "/styles.css",
  "/login-gate.css",
  "/harbor-scene.css",
  "/sigils.css",
  "/harbor-hero.png",
  "/api/login",
  "/api/logout",
  "/api/ai/sentinel",
  "/api/internal/positions/capture",
]);

export function isPublicPath(pathname) {
  return PUBLIC_EXACT.has(pathname) || pathname.startsWith("/assets/");
}
