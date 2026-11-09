import { Dropdown, DropdownContent, DropdownItem } from "./primitives";
import { resolveTimezoneOption, TIMEZONE_OPTIONS } from "../timezones";

export function TimezoneFlag({ country }: { country?: string }) {
  return country ? (
    <span className={`fi fi-${country} stage-timezone-flag`} aria-hidden="true" />
  ) : (
    <i className="ti ti-world stage-timezone-flag" aria-hidden="true" />
  );
}

/** Flag + optional offset text for a read-only context (member accounts in
 * the Users tab, the dashboard ranking tables) — no interaction, just a
 * `title` tooltip with the full name. */
export function TimezoneBadge({
  value,
  showOffset = false,
  className = "",
}: {
  value: string | null | undefined;
  showOffset?: boolean;
  className?: string;
}) {
  const option = resolveTimezoneOption(value);
  const fullLabel = option.offset ? `${option.label} (${option.offset})` : option.label;
  return (
    <span className={`stage-timezone-badge${className ? ` ${className}` : ""}`} title={fullLabel}>
      <TimezoneFlag country={option.country} />
      {showOffset && <span className="text-secondary small">{option.offset || option.label}</span>}
    </span>
  );
}

/** A `<select>`-style dropdown whose options render a real flag icon + name +
 * UTC offset, since a native `<option>` can't hold a flag image (and plain
 * emoji flags render as bare two-letter text on systems without a
 * color-emoji font). Mirrors PersonSelect in Avatar.tsx. */
export function TimezoneSelect({
  value,
  onChange,
  ariaLabel,
}: {
  value: string;
  onChange: (timezone: string) => void;
  ariaLabel?: string;
}) {
  const selected = resolveTimezoneOption(value);
  const fullLabel = selected.offset ? `${selected.label} (${selected.offset})` : selected.label;
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          className="form-select form-select-sm stage-timezone-select-trigger"
          aria-label={ariaLabel}
          title={fullLabel}
        >
          <TimezoneFlag country={selected.country} />
          <span className="text-truncate">{selected.offset || selected.label}</span>
        </button>
      </Dropdown.Trigger>
      <DropdownContent align="start" className="stage-timezone-select-content">
        {TIMEZONE_OPTIONS.map((tz) => (
          <DropdownItem key={tz.value} onSelect={() => onChange(tz.value)}>
            <TimezoneFlag country={tz.country} />
            <span className="text-truncate">
              {tz.label} ({tz.offset})
            </span>
          </DropdownItem>
        ))}
      </DropdownContent>
    </Dropdown.Root>
  );
}
