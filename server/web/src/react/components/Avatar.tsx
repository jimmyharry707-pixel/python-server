import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Dropdown, DropdownContent, DropdownItem } from "./primitives";

export type AvatarSize = "xs" | "sm" | "md" | "lg" | "xl";

export type AvatarPerson = {
  id?: string;
  email?: string;
  name?: string;
  role?: string;
  avatar_url?: string | null;
};

const ringPalette = [
  "#e03131",
  "#f76707",
  "#f59f00",
  "#2f9e44",
  "#0c8599",
  "#1971c2",
  "#7048e8",
  "#e64980",
  "#ae3ec9",
  "#12b886",
  "#4263eb",
  "#d6336c",
];

const fallbackPalette = [
  ["#fee2e2", "#b91c1c"],
  ["#ffedd5", "#c2410c"],
  ["#fef3c7", "#a16207"],
  ["#dcfce7", "#15803d"],
  ["#ccfbf1", "#0f766e"],
  ["#cffafe", "#0e7490"],
  ["#dbeafe", "#1d4ed8"],
  ["#e0e7ff", "#4338ca"],
  ["#ede9fe", "#6d28d9"],
  ["#fae8ff", "#a21caf"],
  ["#fce7f3", "#be185d"],
  ["#f1f5f9", "#475569"],
] as const;

function hashSeed(seed?: string | null) {
  let hash = 0;
  for (const character of String(seed || "user")) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  return hash;
}

export function initials(value?: string | null) {
  const normalized = String(value || "?").trim();
  const parts = normalized.split(/[\s@._-]+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  return normalized.slice(0, 2).toUpperCase();
}

export function avatarRingColor(seed?: string | null) {
  return ringPalette[hashSeed(seed) % ringPalette.length];
}

export function userPerson(person: Record<string, unknown>): AvatarPerson {
  return {
    id: String(person.id || person.user_id || "") || undefined,
    name: String(person.name || person.user_name || "") || undefined,
    email: String(person.email || person.user_email || "") || undefined,
    role: String(person.role || person.user_role || "") || undefined,
    avatar_url:
      String(person.avatar_url || person.user_avatar_url || person.photo_url || "") || null,
  };
}

function normalizeAvatarUrl(value?: string | null) {
  const url = String(value || "").trim();
  if (!url) return null;
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  return `/${url.replace(/^\/+/, "")}`;
}

export function Avatar({
  person,
  size = "sm",
  showImage = true,
  showManagerCrown = true,
  className = "",
}: {
  person: AvatarPerson;
  size?: AvatarSize;
  showImage?: boolean;
  showManagerCrown?: boolean;
  className?: string;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const seed = person.id || person.email || person.name || "user";
  const label = person.name || person.email || person.id || "User";
  const imageUrl = showImage && !imageFailed ? normalizeAvatarUrl(person.avatar_url) : null;
  const role = person.role?.trim().toLowerCase();
  const manager = showManagerCrown && role === "manager";
  const supporter = role === "supporter";
  const style = useMemo(() => {
    const hash = hashSeed(seed);
    const [background, foreground] = fallbackPalette[hash % fallbackPalette.length];
    return {
      "--avatar-ring": manager
        ? "#f59f00"
        : supporter
          ? "#0c8599"
          : ringPalette[hash % ringPalette.length],
      "--avatar-bg": background,
      "--avatar-fg": foreground,
    } as CSSProperties;
  }, [manager, seed, supporter]);

  useEffect(() => setImageFailed(false), [person.avatar_url]);

  return (
    <span
      className={`stage-avatar-wrap stage-avatar-${size}${manager ? " is-manager" : ""}${supporter ? " is-supporter" : ""}${className ? ` ${className}` : ""}`}
      style={style}
      title={label}
    >
      {manager && (
        <span className="stage-avatar-crown" title="Manager" aria-hidden="true">
          <i className="ti ti-crown" />
        </span>
      )}
      {supporter && (
        <span className="stage-avatar-role-badge" title="Supporter" aria-hidden="true">
          <i className="ti ti-headset" />
        </span>
      )}
      <span
        className={`avatar avatar-${size}${imageUrl ? " avatar-img-filled" : ""}`}
        aria-label={label}
        role="img"
      >
        {imageUrl ? (
          <img src={imageUrl} alt="" onError={() => setImageFailed(true)} />
        ) : (
          initials(label)
        )}
      </span>
    </span>
  );
}

export function UserIdentity({
  person,
  size = "sm",
  secondary,
  trailing,
  className = "",
}: {
  person: AvatarPerson;
  size?: AvatarSize;
  secondary?: ReactNode;
  trailing?: ReactNode;
  className?: string;
}) {
  const primary = person.name || person.email || "Unknown user";
  const detail = secondary ?? (person.name ? person.email : undefined);
  return (
    <span className={`stage-user-identity${className ? ` ${className}` : ""}`}>
      <Avatar person={person} size={size} />
      <span className="stage-user-identity-copy min-w-0">
        <span className="stage-user-identity-name text-truncate">{primary}</span>
        {detail && <span className="stage-user-identity-meta text-truncate">{detail}</span>}
      </span>
      {trailing}
    </span>
  );
}

export function AvatarGroup({
  people,
  limit = 4,
  empty = "—",
}: {
  people: AvatarPerson[];
  limit?: number;
  empty?: ReactNode;
}) {
  if (!people.length) return <span className="text-secondary">{empty}</span>;
  return (
    <span className="stage-assign-avatars" aria-label={`${people.length} people`}>
      {people.slice(0, limit).map((person, index) => (
        <Avatar
          key={person.id || person.email || `${person.name}-${index}`}
          person={person}
          size="sm"
          className="stage-assign-avatar"
        />
      ))}
      {people.length > limit && (
        <span className="avatar avatar-sm stage-assign-more">+{people.length - limit}</span>
      )}
    </span>
  );
}

/** A `<select>`-style dropdown whose options render an avatar + name, since a
 * native `<option>` can't hold an `<img>`. Pass a leading `allLabel` for the
 * "everyone/unassigned" option; `value` is the selected person's id, `""` for
 * that leading option. */
export function PersonSelect({
  people,
  value,
  onChange,
  allLabel,
  placeholder = "Select a person",
  size = "sm",
  disabled = false,
  ariaLabel,
}: {
  people: AvatarPerson[];
  value: string;
  onChange: (id: string) => void;
  allLabel: string;
  placeholder?: string;
  size?: AvatarSize;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const selected = value ? people.find((person) => person.id === value) : undefined;
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          className="form-select form-select-sm stage-person-select-trigger"
          disabled={disabled}
          aria-label={ariaLabel}
        >
          {selected ? (
            <UserIdentity person={selected} size={size} />
          ) : value ? (
            <span className="text-secondary">{placeholder}</span>
          ) : (
            <span>{allLabel}</span>
          )}
        </button>
      </Dropdown.Trigger>
      <DropdownContent align="start" className="stage-person-select-content">
        <DropdownItem onSelect={() => onChange("")}>{allLabel}</DropdownItem>
        {people.length > 0 && <Dropdown.Separator className="dropdown-divider" />}
        {people.map((person) => (
          <DropdownItem
            key={person.id || person.email}
            onSelect={() => onChange(person.id || "")}
          >
            <UserIdentity person={person} size={size} />
          </DropdownItem>
        ))}
      </DropdownContent>
    </Dropdown.Root>
  );
}
