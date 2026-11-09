import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import brandLogo from "../../../assets/brand-logo.svg";
import { useToast } from "../../components/ToastProvider";
import { keepSignedInPreference, renew, signIn } from "../../session";

/** Where to go after signing in: the page that sent us here, else the dashboard. */
function returnPath(state: unknown): string {
  const from = (state as { from?: { pathname?: string; search?: string; hash?: string } })?.from;
  if (!from?.pathname || from.pathname === "/login") return "/dashboard";
  return `${from.pathname}${from.search || ""}${from.hash || ""}`;
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const target = returnPath(location.state);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [keepSignedIn, setKeepSignedIn] = useState(keepSignedInPreference);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light",
  );
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.title = "Sign in · Absurd Technologies";
  }, []);

  // Still signed in (from another tab or an earlier visit): skip the form.
  useEffect(() => {
    let live = true;
    renew().then(
      (signedIn) => live && signedIn && navigate(target, { replace: true }),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [navigate, target]);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", next);
    localStorage.setItem("baymax_stage_theme", next);
    setTheme(next);
  };

  const togglePassword = () => {
    setPasswordVisible((value) => !value);
    requestAnimationFrame(() => passwordRef.current?.focus({ preventScroll: true }));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    const form = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await signIn(String(form.email).trim(), String(form.password), keepSignedIn);
      navigate(target, { replace: true });
    } catch (caught: any) {
      const message = caught?.message || "Sign in failed";
      toast.error(
        message.includes("401") || message.includes("Invalid") ? "Invalid credentials" : message,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="stage-login-wrap">
      <div className="stage-login-bg" aria-hidden="true">
        <span className="stage-login-glow stage-login-glow--one" />
      </div>

      <button
        type="button"
        className="stage-login-theme-toggle"
        onClick={toggleTheme}
        aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      >
        <span className="stage-login-theme-opt" data-theme-opt="light" aria-hidden="true">
          <i className="ti ti-sun" />
        </span>
        <span className="stage-login-theme-opt" data-theme-opt="dark" aria-hidden="true">
          <i className="ti ti-moon" />
        </span>
      </button>

      <section className="stage-login-panel" aria-labelledby="loginTitle">
        <div className="stage-login-brand">
          <img
            src={brandLogo}
            alt=""
            width="52"
            height="52"
            className="stage-login-logo"
          />
          <h1 id="loginTitle" className="stage-login-title">
            Absurd Technologies<span className="stage-login-title-dot">.</span>
          </h1>
          <p className="stage-login-subtitle">Sign in to Admin Portal</p>
        </div>

        <form id="loginForm" autoComplete="on" onSubmit={submit}>
          <div className="stage-login-field">
            <label className="form-label" htmlFor="email">
              Email
            </label>
            <div className="stage-login-input-wrap">
              <i className="ti ti-user stage-login-input-icon" aria-hidden="true" />
              <input
                id="email"
                name="email"
                type="email"
                className="form-control stage-login-input"
                placeholder="name@company.com"
                autoComplete="username"
                required
                autoFocus
              />
            </div>
          </div>
          <div className="stage-login-field">
            <label className="form-label" htmlFor="password">
              Password
            </label>
            <div className="stage-login-input-wrap stage-password-field">
              <i className="ti ti-lock stage-login-input-icon" aria-hidden="true" />
              <input
                ref={passwordRef}
                id="password"
                name="password"
                type={passwordVisible ? "text" : "password"}
                className="form-control stage-login-input"
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="stage-password-toggle"
                onClick={togglePassword}
                aria-label={passwordVisible ? "Hide password" : "Show password"}
                aria-pressed={passwordVisible}
              >
                <i
                  className={`ti ${passwordVisible ? "ti-eye-off" : "ti-eye"}`}
                  aria-hidden="true"
                />
              </button>
            </div>
          </div>
          <label className="form-check stage-login-remember">
            <input
              type="checkbox"
              className="form-check-input"
              checked={keepSignedIn}
              onChange={(event) => setKeepSignedIn(event.target.checked)}
            />
            <span className="form-check-label">Keep me signed in</span>
          </label>
          <div className="stage-login-footer">
            <button
              type="submit"
              className="btn btn-primary w-100 stage-login-submit"
              disabled={busy}
            >
              {busy && (
                <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
              )}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </div>
        </form>
        <p className="stage-login-secure">
          <i className="ti ti-shield-lock" aria-hidden="true" />
          Access is restricted to authorized Absurd Technologies administrators.
        </p>
      </section>
    </main>
  );
}
