// Thin fetch wrapper: base URL, bearer token, JSON, and errors the UI can show verbatim.

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";
const TOKEN_KEY = "cloudopt.token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
  get isAuth() {
    return this.status === 401;
  }
  get isForbidden() {
    return this.status === 403;
  }
  get isConflict() {
    return this.status === 409;
  }
  get isNetwork() {
    return this.status === 0;
  }
}

export const tokenStore = {
  get: () => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (t: string) => {
    try {
      localStorage.setItem(TOKEN_KEY, t);
    } catch {
      /* private mode: session-only login */
    }
  },
  clear: () => {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

// Set by the auth provider so any 401 anywhere logs the user out with a clear message.
let onUnauthorized: ((reason: string) => void) | null = null;
export function setUnauthorizedHandler(fn: (reason: string) => void) {
  onUnauthorized = fn;
}

function messageFrom(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) {
      // FastAPI validation errors: [{loc, msg}]
      return d
        .map((e: { loc?: unknown[]; msg?: string }) => {
          const field = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
          return field ? `${field}: ${e.msg}` : e.msg ?? "";
        })
        .join("; ");
    }
  }
  return fallback;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = tokenStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, body });
  } catch {
    throw new ApiError(0, "Can't reach the CloudOpt server. Check your connection; we'll keep retrying.");
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const fallback =
      res.status >= 500 ? "Something went wrong on our side. Please try again." : `Request failed (${res.status})`;
    const err = new ApiError(res.status, messageFrom(data, fallback));
    if (res.status === 401 && token && onUnauthorized) onUnauthorized(err.message);
    throw err;
  }
  return data as T;
}

export async function login(email: string, password: string) {
  const form = new URLSearchParams({ username: email, password });
  return api<{ access_token: string; expires_in: number }>("/api/auth/login", {
    method: "POST",
    body: form,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
}
