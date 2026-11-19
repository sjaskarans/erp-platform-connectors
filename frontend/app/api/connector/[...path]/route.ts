import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };
const allowed = /^v1\/(connections(?:\/[a-f0-9-]+(?:\/(?:test|setup|browse|selection)|\/objects\/[a-zA-Z0-9_-]+\/preview)?)?|objects(?:\/[a-zA-Z0-9_-]+\/(?:schema|preview))?|queries\/[a-zA-Z0-9_-]+)$/;

async function proxy(request: NextRequest, context: Context) {
  const hostname = request.nextUrl.hostname;
  if (!["localhost", "127.0.0.1"].includes(hostname)) return NextResponse.json({ detail: "Local access only" }, { status: 403 });
  const origin = request.headers.get("origin");
  // Next development servers may normalize nextUrl to localhost even when the
  // browser uses 127.0.0.1. Allow only the two loopback origins on our UI port.
  const uiPort = Number(process.env.ERP_FRONTEND_PORT || "3000");
  const origins = [`http://127.0.0.1:${uiPort}`, `http://localhost:${uiPort}`];
  if (origin && !origins.includes(origin)) return NextResponse.json({ detail: "Origin rejected" }, { status: 403 });
  const { path } = await context.params;
  const endpoint = path.join("/");
  if (!allowed.test(endpoint)) return NextResponse.json({ detail: "Unknown endpoint" }, { status: 404 });
  const token = process.env.ERP_API_TOKEN;
  if (!token) return NextResponse.json({ detail: "Start the application using the project launcher" }, { status: 503 });
  const limit = request.nextUrl.searchParams.get("limit");
  if (limit && !/^\d{1,3}$/.test(limit)) return NextResponse.json({ detail: "Invalid row limit" }, { status: 400 });
  const port = Number(process.env.ERP_GATEWAY_PORT || "8000");
  try {
    if (Number(request.headers.get("content-length") || 0) > 65536) return NextResponse.json({ detail: "Request too large" }, { status: 413 });
    const body = ["POST", "PUT"].includes(request.method) ? await request.text() : undefined;
    if (body && body.length > 65536) return NextResponse.json({ detail: "Request too large" }, { status: 413 });
    const response = await fetch(`http://127.0.0.1:${port}/${endpoint}${limit ? `?limit=${limit}` : ""}`, {
      method: request.method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body, cache: "no-store", signal: AbortSignal.timeout(200000),
    });
    return NextResponse.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ detail: "The connector service is unavailable. Check the launcher terminal and try again." }, { status: 502 });
  }
}
export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
