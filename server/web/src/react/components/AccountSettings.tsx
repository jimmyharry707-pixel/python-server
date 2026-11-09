import { useEffect, useState, type ChangeEvent, type RefObject } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { api, type CurrentUser } from "../api";
import { Modal } from "../ui";
import { Avatar } from "./Avatar";
import { FormField } from "./primitives";

const avatarTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
const avatarMaxBytes = 2 * 1024 * 1024;

function profileSchema(currentEmail: string) {
  return z
    .object({
      name: z.string().trim().min(1, "Display name is required").max(255),
      email: z.string().trim().email("Enter a valid email address"),
      current_password: z.string(),
      new_password: z.string(),
      confirm: z.string(),
    })
    .superRefine((values, context) => {
      const emailChanged = values.email.toLowerCase() !== currentEmail.trim().toLowerCase();
      const changingPassword = Boolean(values.new_password);
      if ((emailChanged || changingPassword) && !values.current_password) {
        context.addIssue({
          code: "custom",
          path: ["current_password"],
          message: "Current password is required to change email or password",
        });
      }
      if (changingPassword && values.new_password.length < 8) {
        context.addIssue({
          code: "custom",
          path: ["new_password"],
          message: "New password must be at least 8 characters",
        });
      }
      if (changingPassword && values.new_password !== values.confirm) {
        context.addIssue({ code: "custom", path: ["confirm"], message: "Passwords do not match" });
      }
      if (!changingPassword && values.confirm) {
        context.addIssue({
          code: "custom",
          path: ["confirm"],
          message: "Enter a new password before confirming it",
        });
      }
    });
}

type ProfileForm = z.infer<ReturnType<typeof profileSchema>>;

export function AccountSettings({
  user,
  hideAvatar = false,
  onClose,
  onNotice,
  returnFocusRef,
}: {
  user: CurrentUser;
  /** Supporter site: no profile photo anywhere, including here. */
  hideAvatar?: boolean;
  onClose: () => void;
  onNotice: (message: string, tone?: "success" | "danger") => void;
  returnFocusRef: RefObject<HTMLElement | null>;
}) {
  const queryClient = useQueryClient();
  const [account, setAccount] = useState(user);
  const [confirmClose, setConfirmClose] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [avatarMessage, setAvatarMessage] = useState("");
  const [avatarError, setAvatarError] = useState("");
  const {
    register,
    handleSubmit,
    formState: { errors, isDirty },
    setError,
    watch,
  } = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema(account.email)),
    defaultValues: {
      name: user.name,
      email: user.email,
      current_password: "",
      new_password: "",
      confirm: "",
    },
  });
  const emailValue = watch("email");

  useEffect(() => setAccount(user), [user]);
  useEffect(() => {
    if (emailValue.trim().toLowerCase() !== account.email.trim().toLowerCase()) {
      setPasswordOpen(true);
    }
  }, [account.email, emailValue]);

  const save = useMutation({
    mutationFn: async (values: ProfileForm) => {
      const nameChanged = values.name.trim() !== account.name;
      const emailChanged = values.email.trim().toLowerCase() !== account.email.trim().toLowerCase();
      let updated = account;
      if (nameChanged || emailChanged) {
        updated = await api.updateMe({
          name: values.name.trim(),
          email: values.email.trim(),
          current_password: values.current_password || undefined,
        });
        // Profile and password updates are separate server operations. Keep the
        // session chrome truthful if a later password update fails.
        queryClient.setQueryData(["me"], updated);
      }
      if (values.new_password) {
        await api.password({
          current_password: values.current_password,
          new_password: values.new_password,
        });
      }
      return updated;
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["me"], updated);
      setAccount(updated);
      onNotice("Profile updated", "success");
      onClose();
    },
    onError: (caught: Error) => {
      const message = caught.message || "Profile update failed";
      if (message.toLowerCase().includes("password")) {
        setError("current_password", { message });
      } else if (message.toLowerCase().includes("email")) {
        setError("email", { message });
      } else {
        onNotice(message, "danger");
      }
    },
  });

  const avatar = useMutation({
    mutationFn: (operation: { file?: File; remove?: boolean }) =>
      operation.remove ? api.removeAvatar() : api.avatar(operation.file!),
    onMutate: () => {
      setAvatarError("");
      setAvatarMessage("");
    },
    onSuccess: (updated, operation) => {
      queryClient.setQueryData(["me"], updated);
      setAccount(updated);
      setAvatarMessage(operation.remove ? "Profile photo removed." : "Profile photo uploaded.");
    },
    onError: (caught: Error) => setAvatarError(caught.message || "Avatar update failed"),
  });

  const chooseAvatar = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!avatarTypes.has(file.type)) {
      setAvatarError("Choose a JPEG, PNG, or WebP image.");
      return;
    }
    if (file.size > avatarMaxBytes) {
      setAvatarError("The image must be 2 MB or smaller.");
      return;
    }
    avatar.mutate({ file });
  };

  const requestClose = () => {
    if (save.isPending || avatar.isPending) return;
    if (isDirty) setConfirmClose(true);
    else onClose();
  };

  return (
    <>
      <Modal
        title="Profile settings"
        description="Manage your profile photo, account details, and password."
        onClose={requestClose}
        returnFocusRef={returnFocusRef}
        className="stage-profile-dialog"
        footer={
          confirmClose ? (
            <div className="stage-discard-confirm w-100">
              <span className="text-secondary me-auto">Discard your unsaved account changes?</span>
              <button type="button" className="btn" onClick={() => setConfirmClose(false)}>
                Keep editing
              </button>
              <button type="button" className="btn btn-danger" onClick={onClose}>
                Discard changes
              </button>
            </div>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-link link-secondary"
                disabled={save.isPending || avatar.isPending}
                onClick={requestClose}
              >
                Close
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                form="profileSettingsForm"
                disabled={!isDirty || save.isPending || avatar.isPending}
              >
                {save.isPending && (
                  <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
                )}
                {save.isPending ? "Saving…" : "Save changes"}
              </button>
            </>
          )
        }
      >
        <div className="row g-4 stage-profile-settings">
          {!hideAvatar && (
            <aside className="col-md-4 stage-profile-avatar-panel" aria-labelledby="avatarHeading">
              <h3 className="stage-profile-section-title" id="avatarHeading">
                Profile photo
              </h3>
              <Avatar person={account} size="xl" />
              <div className="stage-profile-photo-status">
                {account.avatar_url
                  ? "Photo is used everywhere your account appears."
                  : "Using your initials until you upload a photo."}
              </div>
              <div className="d-flex flex-wrap gap-2">
                <label
                  className={`btn btn-sm btn-outline-primary mb-0${avatar.isPending ? " disabled" : ""}`}
                >
                  <i className="ti ti-upload me-1" aria-hidden="true" />
                  Upload
                  <input
                    className="visually-hidden"
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    disabled={avatar.isPending}
                    onChange={chooseAvatar}
                  />
                </label>
                {account.avatar_url && (
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    disabled={avatar.isPending}
                    onClick={() => avatar.mutate({ remove: true })}
                  >
                    Remove
                  </button>
                )}
              </div>
              <div className="form-hint">JPEG, PNG, or WebP · maximum 2 MB</div>
              <div className="stage-profile-save-note">Photo changes save immediately.</div>
              {avatar.isPending && (
                <div className="small text-secondary" role="status">
                  Updating photo…
                </div>
              )}
              {avatarMessage && (
                <div className="small text-success" role="status">
                  {avatarMessage}
                </div>
              )}
              {avatarError && (
                <div className="small text-danger" role="alert">
                  {avatarError}
                </div>
              )}
            </aside>
          )}

          <form
            className={hideAvatar ? "col-12" : "col-md-8"}
            id="profileSettingsForm"
            noValidate
            onSubmit={handleSubmit((values) => save.mutate(values))}
          >
            <h3 className="stage-profile-section-title">Account</h3>
            <FormField label="Display name" required error={errors.name?.message}>
              {({ id, describedBy }) => (
                <input
                  {...register("name")}
                  id={id}
                  className={`form-control${errors.name ? " is-invalid" : ""}`}
                  aria-describedby={describedBy}
                  aria-invalid={Boolean(errors.name)}
                  autoComplete="name"
                  autoFocus
                />
              )}
            </FormField>
            <FormField
              label="Email"
              required
              hint="Changing your email requires your current password."
              error={errors.email?.message}
            >
              {({ id, describedBy }) => (
                <input
                  {...register("email")}
                  id={id}
                  type="email"
                  className={`form-control${errors.email ? " is-invalid" : ""}`}
                  aria-describedby={describedBy}
                  aria-invalid={Boolean(errors.email)}
                  autoComplete="email"
                />
              )}
            </FormField>

            <section className="stage-profile-security mt-4" aria-labelledby="securityHeading">
              <button
                type="button"
                className="stage-profile-security-toggle"
                aria-expanded={passwordOpen}
                aria-controls="profilePasswordFields"
                onClick={() => setPasswordOpen((open) => !open)}
              >
                <span>
                  <span className="stage-profile-section-title d-block mb-1" id="securityHeading">
                    Security
                  </span>
                  <span className="stage-profile-security-summary">
                    Change your account password
                  </span>
                </span>
                <i className={`ti ti-chevron-${passwordOpen ? "up" : "down"}`} aria-hidden="true" />
              </button>
              {passwordOpen && (
                <div className="stage-profile-password-fields" id="profilePasswordFields">
                  <FormField
                    label="Current password"
                    hint="Required when changing your email or password."
                    error={errors.current_password?.message}
                  >
                    {({ id, describedBy }) => (
                      <input
                        {...register("current_password")}
                        id={id}
                        type="password"
                        className={`form-control${errors.current_password ? " is-invalid" : ""}`}
                        aria-describedby={describedBy}
                        aria-invalid={Boolean(errors.current_password)}
                        autoComplete="current-password"
                      />
                    )}
                  </FormField>
                  <div className="row g-3">
                    <div className="col-sm-6">
                      <FormField
                        label="New password"
                        hint="At least 8 characters."
                        error={errors.new_password?.message}
                      >
                        {({ id, describedBy }) => (
                          <input
                            {...register("new_password")}
                            id={id}
                            type="password"
                            className={`form-control${errors.new_password ? " is-invalid" : ""}`}
                            aria-describedby={describedBy}
                            aria-invalid={Boolean(errors.new_password)}
                            autoComplete="new-password"
                          />
                        )}
                      </FormField>
                    </div>
                    <div className="col-sm-6">
                      <FormField label="Confirm password" error={errors.confirm?.message}>
                        {({ id, describedBy }) => (
                          <input
                            {...register("confirm")}
                            id={id}
                            type="password"
                            className={`form-control${errors.confirm ? " is-invalid" : ""}`}
                            aria-describedby={describedBy}
                            aria-invalid={Boolean(errors.confirm)}
                            autoComplete="new-password"
                          />
                        )}
                      </FormField>
                    </div>
                  </div>
                </div>
              )}
            </section>
          </form>
        </div>
      </Modal>
    </>
  );
}
