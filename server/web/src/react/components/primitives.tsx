import { useId, useRef, type ReactNode, type RefObject } from "react";
import { Dialog as RadixDialog, DropdownMenu as RadixMenu } from "radix-ui";

export function Dialog({
  title,
  description,
  children,
  footer,
  onClose,
  size = "lg",
  variant = "center",
  returnFocusRef,
  className = "",
}: {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  size?: "sm" | "md" | "lg" | "xl";
  variant?: "center" | "sheet";
  returnFocusRef?: RefObject<HTMLElement | null>;
  className?: string;
}) {
  const openerRef = useRef(
    typeof document === "undefined" ? null : (document.activeElement as HTMLElement | null),
  );

  return (
    <RadixDialog.Root open onOpenChange={(open) => !open && onClose()}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="modal-backdrop fade show stage-dialog-overlay" />
        <RadixDialog.Content
          className="modal modal-blur fade show d-block stage-dialog-root"
          onCloseAutoFocus={(event) => {
            const target = returnFocusRef?.current || openerRef.current;
            if (!target?.isConnected) return;
            event.preventDefault();
            target.focus({ preventScroll: true });
          }}
        >
          <div
            className={`modal-dialog ${variant === "center" ? "modal-dialog-centered" : "stage-dialog-sheet"} stage-dialog-${size}`}
            role="document"
          >
            <div className={`modal-content ${className}`}>
              <div className="modal-header">
                <div>
                  <RadixDialog.Title className="modal-title">{title}</RadixDialog.Title>
                  <RadixDialog.Description
                    className={description ? "text-secondary small mb-0 mt-1" : "visually-hidden"}
                  >
                    {description || `${title} dialog`}
                  </RadixDialog.Description>
                </div>
                <RadixDialog.Close asChild>
                  <button type="button" className="btn-close" aria-label="Close" />
                </RadixDialog.Close>
              </div>
              <div className="modal-body">{children}</div>
              {footer && <div className="modal-footer">{footer}</div>}
            </div>
          </div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export const Dropdown = {
  Root: RadixMenu.Root,
  Trigger: RadixMenu.Trigger,
  Group: RadixMenu.Group,
  Label: RadixMenu.Label,
  Separator: RadixMenu.Separator,
  Portal: RadixMenu.Portal,
};

export function DropdownContent({
  children,
  align = "end",
  sideOffset = 8,
  className = "",
}: {
  children: ReactNode;
  align?: "start" | "center" | "end";
  sideOffset?: number;
  className?: string;
}) {
  return (
    <RadixMenu.Portal>
      <RadixMenu.Content
        align={align}
        sideOffset={sideOffset}
        collisionPadding={12}
        className={`dropdown-menu show stage-dropdown-content ${className}`}
      >
        {children}
      </RadixMenu.Content>
    </RadixMenu.Portal>
  );
}

export function DropdownItem({
  children,
  danger = false,
  disabled = false,
  onSelect,
}: {
  children: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
}) {
  return (
    <RadixMenu.Item
      disabled={disabled}
      onSelect={onSelect}
      className={`dropdown-item stage-dropdown-item${danger ? " text-danger" : ""}`}
    >
      {children}
    </RadixMenu.Item>
  );
}

export function ActionMenu({
  label,
  items,
}: {
  label: string;
  items: Array<{
    label: string;
    icon: string;
    onSelect: () => void;
    danger?: boolean;
    disabled?: boolean;
    separatorBefore?: boolean;
  }>;
}) {
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          className="btn btn-sm btn-ghost-secondary btn-icon"
          aria-label={label}
        >
          <i className="ti ti-dots-vertical" aria-hidden="true" />
        </button>
      </Dropdown.Trigger>
      <DropdownContent className="stage-row-action-menu">
        {items.map((item, index) => (
          <div key={`${item.label}-${index}`}>
            {item.separatorBefore && <Dropdown.Separator className="dropdown-divider" />}
            <DropdownItem danger={item.danger} disabled={item.disabled} onSelect={item.onSelect}>
              <i className={`ti ${item.icon} me-2`} aria-hidden="true" />
              {item.label}
            </DropdownItem>
          </div>
        ))}
      </DropdownContent>
    </Dropdown.Root>
  );
}

export function FormField({
  label,
  hint,
  error,
  required,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: (ids: { id: string; describedBy?: string }) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="mb-3">
      <label className="form-label" htmlFor={id}>
        {label}
        {required && (
          <span className="text-danger ms-1" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {children({ id, describedBy })}
      {hint && (
        <div className="form-hint" id={hintId}>
          {hint}
        </div>
      )}
      {error && (
        <div className="invalid-feedback d-block" id={errorId} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

export function StateMessage({
  icon,
  title,
  description,
  action,
  tone = "secondary",
}: {
  icon?: string;
  title: string;
  description?: string;
  action?: ReactNode;
  tone?: "secondary" | "danger" | "warning";
}) {
  return (
    <div
      className={`stage-state-message text-${tone}`}
      role={tone === "danger" ? "alert" : "status"}
    >
      {icon && <i className={`ti ${icon} stage-state-icon`} aria-hidden="true" />}
      <div className="stage-state-title">{title}</div>
      {description && <p className="stage-state-description">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
