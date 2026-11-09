// The website's sign-in session (server/auth_sessions.py).
//
// The refresh token lives in an HttpOnly cookie that page scripts can't read.
// The short-lived access token is kept only in memory and renewed quietly a
// minute before it expires, so an open tab stays signed in. When the session
// really has ended, requests wait for the "sign in again" popup instead of
// leaving the page, then carry on, so unsaved work isn't lost.

const AUTH = "/api/v1/auth";
// The server requires this on every request that uses the cookie.
const WEB_HEADER = { "X-Requested-With": "XMLHttpRequest" };
const RENEW_EARLY_MS = 60_000;
const KEEP_SIGNED_IN_KEY = "baymax_keep_signed_in";

type Tokens = { access_token: string; expires_in: number; user_id?: string };

let accessToken: string | null = null;
let expiresAt = 0;
// The account this page was loaded for.
let pageUserId: string | null = null;
let renewing: Promise<boolean> | null = null;
let renewTimer: number | undefined;
let reauthHandler: (() => Promise<void>) | null = null;
let reauthing: Promise<void> | null = null;

// Earlier builds kept the access token in localStorage.
try {
  localStorage.removeItem("baymax_stage_token");
} catch {}

/** Thrown when there is no session and no popup to sign in again with. */
export class SignedOutError extends Error {
  constructor() {
    super("Please sign in again.");
    this.name = "SignedOutError";
  }
}

function store(tokens: Tokens) {
  if (pageUserId && tokens.user_id && tokens.user_id !== pageUserId) {
    // Someone signed in as another account in another tab. This page still
    // shows the first account's data, so start over as the new one.
    location.reload();
    return;
  }
  accessToken = tokens.access_token;
  expiresAt = Date.now() + tokens.expires_in * 1000;
  pageUserId = tokens.user_id ?? pageUserId;
  window.clearTimeout(renewTimer);
  renewTimer = window.setTimeout(
    () => void renew().catch(() => undefined),
    Math.max(5_000, tokens.expires_in * 1000 - RENEW_EARLY_MS),
  );
}

function forget() {
  accessToken = null;
  expiresAt = 0;
  window.clearTimeout(renewTimer);
}

async function failure(res: Response, fallback: string): Promise<Error> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return new Error(data.detail);
  } catch {}
  return new Error(`${fallback} (${res.status})`);
}

/** Get a new access token from the cookie. False: the session has ended. */
export function renew(): Promise<boolean> {
  if (!renewing) {
    renewing = fetch(`${AUTH}/refresh`, { method: "POST", headers: WEB_HEADER })
      .then(async (res) => {
        if (res.ok) {
          store(await res.json());
          return true;
        }
        if (res.status === 401 || res.status === 403) {
          forget();
          return false;
        }
        throw await failure(res, "Could not renew your session");
      })
      .finally(() => {
        renewing = null;
      });
  }
  return renewing;
}

/** Register the popup that signs the user in again; it resolves once they have. */
export function setReauthHandler(handler: (() => Promise<void>) | null) {
  reauthHandler = handler;
}

function signedOut(): Promise<void> {
  if (!reauthHandler) return Promise.reject(new SignedOutError());
  if (!reauthing) {
    reauthing = reauthHandler().finally(() => {
      reauthing = null;
    });
  }
  return reauthing;
}

/** A valid access token, renewing or asking the user to sign in again first. */
export async function accessTokenNow(): Promise<string> {
  if (accessToken && Date.now() < expiresAt - RENEW_EARLY_MS / 2) return accessToken;
  if (!(await renew())) await signedOut();
  if (!accessToken) throw new SignedOutError();
  return accessToken;
}

/** After a 401: renew, or wait for the user to sign in again. */
export async function recover(): Promise<void> {
  if (!(await renew())) await signedOut();
}

/** fetch() with the access token; on a 401 it renews and tries once more. */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  let used = "";
  const send = async () => {
    used = await accessTokenNow();
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${used}`);
    return fetch(url, { ...init, headers });
  };
  const res = await send();
  if (res.status !== 401) return res;
  // Drop the token that failed (unless another request already replaced it);
  // sending again then renews, or waits for the user to sign in again.
  if (accessToken === used) forget();
  return send();
}

export function keepSignedInPreference(): boolean {
  try {
    return localStorage.getItem(KEEP_SIGNED_IN_KEY) !== "0";
  } catch {
    return true;
  }
}

export async function signIn(email: string, password: string, remember: boolean): Promise<void> {
  try {
    localStorage.setItem(KEEP_SIGNED_IN_KEY, remember ? "1" : "0");
  } catch {}
  const res = await fetch(`${AUTH}/login`, {
    method: "POST",
    headers: { ...WEB_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, remember, use_cookie: true }),
  });
  if (!res.ok) throw await failure(res, "Sign in failed");
  store(await res.json());
}

export async function signOut(): Promise<void> {
  const headers: Record<string, string> = { ...WEB_HEADER };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  try {
    await fetch(`${AUTH}/logout`, { method: "POST", headers });
  } catch {}
  forget();
  location.replace("/login");
}
