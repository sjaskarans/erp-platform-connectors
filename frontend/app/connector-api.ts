export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/connector/v1/${path}`, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
    signal: AbortSignal.timeout(195000),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : data.detail?.code ? `Snowflake request failed (${data.detail.code}). Check this connection's identity and source permissions.`
      : "The request could not be completed. Check your configuration.";
    throw new Error(detail);
  }
  return data;
}
