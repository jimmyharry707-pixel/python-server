import { useEffect, useRef, useState, type FormEvent } from "react";
import { Modal } from "../ui";
import {
  keepSignedInPreference,
  setReauthHandler,
  signIn,
  SignedOutError,
  signOut,
} from "../session";

type Waiting = { resolve: () => void; reject: (error: Error) => void };

/**
 * When the session ends mid-work, requests wait here instead of leaving the
 * page. Signing in again lets them carry on, so unsaved work isn't lost. The
 * email is fixed: the waiting requests belong to this account.
 */
export function SignInAgain({ email }: { email: string }) {
  const [waiting, setWaiting] = useState<Waiting | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setReauthHandler(
      () =>
        new Promise<void>((resolve, reject) => {
          setError("");
          setWaiting({ resolve, reject });
        }),
    );
    return () => setReauthHandler(null);
  }, []);

  if (!waiting) return null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(email, passwordRef.current?.value || "", keepSignedInPreference());
      waiting.resolve();
      setWaiting(null);
    } catch (caught: any) {
      setError(caught?.message || "Sign in failed");
      passwordRef.current?.select();
    } finally {
      setBusy(false);
    }
  };

  const leave = () => {
    waiting.reject(new SignedOutError());
    void signOut();
  };

  return (
    <Modal
      title="Sign in again"
      description="Your session has ended. Sign in to carry on where you left off."
      size="sm"
      className="stage-sign-in-again"
      // Closing by accident would throw away unsaved work: only the buttons close it.
      onClose={() => undefined}
      footer={
        <>
          <button type="button" className="btn" onClick={leave} disabled={busy}>
            Sign out
          </button>
          <button type="submit" form="signInAgainForm" className="btn btn-primary" disabled={busy}>
            {busy && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}
            Sign in
          </button>
        </>
      }
    >
      <form id="signInAgainForm" onSubmit={submit}>
        <div className="mb-3">
          <label className="form-label" htmlFor="signInAgainEmail">
            Email
          </label>
          <input
            id="signInAgainEmail"
            type="email"
            className="form-control"
            value={email}
            autoComplete="username"
            readOnly
          />
        </div>
        <div>
          <label className="form-label" htmlFor="signInAgainPassword">
            Password
          </label>
          <input
            ref={passwordRef}
            id="signInAgainPassword"
            type="password"
            className={`form-control${error ? " is-invalid" : ""}`}
            autoComplete="current-password"
            required
            autoFocus
          />
          {error && <div className="invalid-feedback">{error}</div>}
        </div>
      </form>
    </Modal>
  );
}
