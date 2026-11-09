import { useState, type FormEvent } from "react";
import type { Row } from "../api";
import { Modal } from "../ui";

// Shared by the admin Profiles page and the member "My profiles" page.
const basicFields = [
  ["name", "Name", "text"],
  ["email", "Profile email", "email"],
  ["phone", "Phone", "text"],
  ["location", "Location", "text"],
  ["linkedin", "LinkedIn", "url"],
] as const;
const educationFields = [
  ["university_name", "University", "text"],
  ["university_location", "University location", "text"],
  ["degree", "Degree", "text"],
  ["education_period", "Education period", "text"],
] as const;
// Optional second education history (e.g. a Master's after a Bachelor's).
// Kept as its own group, clearly labeled, so it reads as an add-on rather
// than a second required entry.
const secondaryEducationFields = [
  ["university_name_2", "University", "text"],
  ["university_location_2", "University location", "text"],
  ["degree_2", "Degree", "text"],
  ["education_period_2", "Education period", "text"],
] as const;
const documentFields = [
  ["resume_filename", "Resume file name", "resume"],
  ["cover_letter_filename", "Cover letter file name", "cover_letter"],
] as const;

// Mirrors gui/profile/store.py:person_filename_stub — keep in sync.
function personFilenameStub(fullName: string): string {
  const parts = String(fullName || "")
    .split(/\s+/)
    .filter(Boolean);
  const stub =
    parts.length === 0
      ? "Resume"
      : parts.length === 1
        ? parts[0]
        : `${parts[0]}_${parts[parts.length - 1]}`;
  return stub.replace(/[\\/:*?"<>|]/g, "-");
}
export function defaultDocumentFilename(fullName: string, kind: "resume" | "cover_letter") {
  const stub = personFilenameStub(fullName);
  return kind === "resume" ? `${stub}_resume` : `${stub}_cover_letter`;
}

export function profileName(profile: Row) {
  return String(profile.name || profile.display_name || profile.key || "Untitled profile");
}

export function maskedPhone(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "Not set";
  const digits = raw.replace(/\D/g, "");
  if (digits.length < 4) return "••••";
  return `••• ••• ${digits.slice(-4)}`;
}

/** Parse an uploaded profile-info JSON file; the server validates the shape. */
export async function readProfileJson(file: File): Promise<unknown> {
  try {
    return JSON.parse(await file.text());
  } catch {
    throw new Error("That file isn't valid JSON");
  }
}

function FieldGroup({
  fields,
  profile,
  onNameChange,
}: {
  fields: ReadonlyArray<readonly [string, string, string]>;
  profile: Row;
  onNameChange?: (value: string) => void;
}) {
  return (
    <div className="row">
      {fields.map(([field, label, type]) => (
        <div className="col-md-6" key={field}>
          <label className="form-label">{label}</label>
          <input
            className="form-control mb-3"
            name={field}
            type={type}
            required={field === "name"}
            defaultValue={profile[field] || ""}
            onChange={field === "name" ? (event) => onNameChange?.(event.target.value) : undefined}
          />
        </div>
      ))}
    </div>
  );
}

export function ProfileEditor({
  profile,
  onClose,
  onSubmit,
}: {
  profile: Row;
  onClose: () => void;
  /** Receives the form values; throw to show the error in the dialog. */
  onSubmit: (values: Row) => Promise<void>;
}) {
  const editing = Boolean(profile.key);
  const [name, setName] = useState(String(profile.name || ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await onSubmit({ ...values, name: String(values.name || "").trim() });
    } catch (caught: any) {
      setError(caught?.message || "Could not save profile");
      setBusy(false);
    }
  };
  return (
    <Modal
      title={editing ? `Edit profile · ${profileName(profile)}` : "New profile"}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        {error && <div className="alert alert-danger">{error}</div>}
        <div className="stage-wp-section-title mt-2 mb-2">Basic info</div>
        <FieldGroup fields={basicFields} profile={profile} onNameChange={setName} />
        <div className="stage-wp-section-title mt-2 mb-2">Education</div>
        <FieldGroup fields={educationFields} profile={profile} />
        <div className="stage-wp-section-title mt-2 mb-1">Second education (optional)</div>
        <div className="form-hint mb-2">
          Only fill this in if the person has a second degree to show, e.g. a Master's after a
          Bachelor's. Leave every field below blank to skip it.
        </div>
        <FieldGroup fields={secondaryEducationFields} profile={profile} />
        <div className="stage-wp-section-title mt-2 mb-2">Documents</div>
        <div className="row">
          {documentFields.map(([field, label, kind]) => (
            <div className="col-md-6" key={field}>
              <label className="form-label">{label}</label>
              <input
                className="form-control mb-1"
                name={field}
                type="text"
                defaultValue={profile[field] || ""}
                placeholder={defaultDocumentFilename(name, kind)}
              />
              <div className="form-hint mb-3">Leave blank to use the default shown above.</div>
            </div>
          ))}
        </div>
        <div className="d-flex justify-content-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : editing ? "Save changes" : "Create profile"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DetailGroup({
  title,
  fields,
  profile,
}: {
  title: string;
  fields: ReadonlyArray<readonly [string, string, ...unknown[]]>;
  profile: Row;
}) {
  return (
    <>
      <div className="stage-wp-section-title mb-2">{title}</div>
      <div className="row">
        {fields.map(([field, label]) => (
          <div className="col-md-6 mb-3" key={field}>
            <div className="stage-detail-section-label">{label}</div>
            <div className="text-break">{profile[field] || "—"}</div>
          </div>
        ))}
      </div>
    </>
  );
}

/** Read-only profile fields, grouped like the editor. */
export function ProfileDetails({ profile }: { profile: Row }) {
  const hasSecond = secondaryEducationFields.some(([field]) => profile[field]);
  const documents = {
    resume_filename: profile.resume_filename || defaultDocumentFilename(profile.name, "resume"),
    cover_letter_filename:
      profile.cover_letter_filename || defaultDocumentFilename(profile.name, "cover_letter"),
  };
  return (
    <>
      <DetailGroup title="Basic info" fields={basicFields} profile={profile} />
      <DetailGroup title="Education" fields={educationFields} profile={profile} />
      {hasSecond && (
        <DetailGroup title="Second education" fields={secondaryEducationFields} profile={profile} />
      )}
      <DetailGroup title="Documents" fields={documentFields} profile={documents} />
    </>
  );
}
