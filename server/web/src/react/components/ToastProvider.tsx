import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

export type ToastTone = "success" | "error" | "warning" | "info";

type Toast = {
  id: number;
  message: string;
  tone: ToastTone;
  closing: boolean;
};

type ToastApi = {
  show: (message: string, tone?: ToastTone | "danger" | "secondary") => number;
  success: (message: string) => number;
  error: (message: string) => number;
  warning: (message: string) => number;
  info: (message: string) => number;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastApi | null>(null);
const titles: Record<ToastTone, string> = {
  success: "Success",
  error: "Something went wrong",
  warning: "Attention needed",
  info: "Information",
};
const icons: Record<ToastTone, string> = {
  success: "ti-circle-check",
  error: "ti-alert-circle",
  warning: "ti-alert-triangle",
  info: "ti-info-circle",
};

function normalizeTone(tone: ToastTone | "danger" | "secondary"): ToastTone {
  if (tone === "danger") return "error";
  if (tone === "secondary") return "info";
  return tone;
}

function ToastItem({ toast, dismiss }: { toast: Toast; dismiss: (id: number) => void }) {
  const duration = toast.tone === "error" ? 6000 : 4500;
  const remaining = useRef(duration);
  const started = useRef(0);
  const timer = useRef<number | undefined>(undefined);

  const startTimer = useCallback(() => {
    started.current = Date.now();
    timer.current = window.setTimeout(() => dismiss(toast.id), remaining.current);
  }, [dismiss, toast.id]);

  const pauseTimer = () => {
    window.clearTimeout(timer.current);
    remaining.current = Math.max(0, remaining.current - (Date.now() - started.current));
  };

  useEffect(() => {
    startTimer();
    return () => window.clearTimeout(timer.current);
  }, [startTimer]);

  return (
    <div
      className={`stage-toast stage-toast--${toast.tone}${toast.closing ? " is-closing" : ""}`}
      role={toast.tone === "error" ? "alert" : "status"}
      aria-live={toast.tone === "error" ? "assertive" : "polite"}
      onMouseEnter={pauseTimer}
      onMouseLeave={startTimer}
      onFocus={pauseTimer}
      onBlur={startTimer}
    >
      <div className="stage-toast__icon" aria-hidden="true">
        <i className={`ti ${icons[toast.tone]}`} />
      </div>
      <div className="stage-toast__content">
        <div className="stage-toast__title">{titles[toast.tone]}</div>
        <div className="stage-toast__message">{toast.message}</div>
      </div>
      <button
        type="button"
        className="stage-toast__close"
        aria-label="Dismiss notification"
        onClick={() => dismiss(toast.id)}
      >
        <i className="ti ti-x" aria-hidden="true" />
      </button>
      <span
        className="stage-toast__progress"
        style={{ animationDuration: `${duration}ms` }}
        aria-hidden="true"
      />
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) =>
      current.map((toast) => (toast.id === id ? { ...toast, closing: true } : toast)),
    );
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 220);
  }, []);

  const show = useCallback(
    (message: string, tone: ToastTone | "danger" | "secondary" = "success") => {
      const id = ++nextId.current;
      setToasts((current) => [
        ...current.slice(-3),
        { id, message, tone: normalizeTone(tone), closing: false },
      ]);
      return id;
    },
    [],
  );

  const api: ToastApi = {
    show,
    success: (message) => show(message, "success"),
    error: (message) => show(message, "error"),
    warning: (message) => show(message, "warning"),
    info: (message) => show(message, "info"),
    dismiss,
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className="stage-toast-region" aria-label="Notifications">
          {toasts.map((toast) => (
            <ToastItem key={toast.id} toast={toast} dismiss={dismiss} />
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within ToastProvider");
  return context;
}
