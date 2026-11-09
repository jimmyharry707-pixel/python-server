import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import {
  ANNOUNCEMENTS_KEY,
  ANNOUNCEMENTS_UNREAD_KEY,
  useAnnouncementReads,
} from "../pages/notifications/announcementReads";
import { whenUtc } from "../ui";
import { Dropdown, DropdownContent, DropdownItem } from "./primitives";

const RECENT = 5;

/** Top-bar bell for members. Blinks while there are unread announcements. */
export function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const unread = useQuery({
    queryKey: ANNOUNCEMENTS_UNREAD_KEY,
    queryFn: api.announcementsUnread,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const list = useQuery({
    queryKey: ANNOUNCEMENTS_KEY,
    queryFn: api.announcements,
    enabled: open,
  });
  const { markRead, markAllRead } = useAnnouncementReads();
  const count = unread.data?.count ?? 0;
  const items = (list.data?.items ?? []).slice(0, RECENT);

  return (
    <Dropdown.Root open={open} onOpenChange={setOpen}>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          id="notificationBell"
          className={`stage-bell${count ? " has-unread" : ""}`}
          aria-label={
            count ? `Notifications, ${count} unread` : "Notifications, no unread announcements"
          }
        >
          <i className={`ti ${count ? "ti-bell-ringing" : "ti-bell"}`} aria-hidden="true" />
          {count > 0 && (
            <span className="stage-bell-badge" aria-hidden="true">
              {count > 9 ? "9+" : count}
            </span>
          )}
        </button>
      </Dropdown.Trigger>
      <DropdownContent className="stage-bell-menu">
        <Dropdown.Label className="dropdown-item-text px-3 py-2 d-flex align-items-center">
          <span className="fw-bold">Notifications</span>
          {count > 0 && (
            <button
              type="button"
              className="btn btn-link btn-sm p-0 ms-auto"
              onClick={() => markAllRead.mutate()}
            >
              Mark all as read
            </button>
          )}
        </Dropdown.Label>
        <Dropdown.Separator className="dropdown-divider" />
        {list.isLoading && <div className="px-3 py-2 text-secondary small">Loading…</div>}
        {list.error && (
          <div className="px-3 py-2 text-danger small">Couldn't load announcements.</div>
        )}
        {list.data && !items.length && (
          <div className="px-3 py-3 text-secondary small">No announcements yet.</div>
        )}
        {items.map((item) => (
          <DropdownItem
            key={item.id}
            onSelect={() => {
              if (!item.read) markRead.mutate(item.id);
              navigate(`/notifications#announcement-${item.id}`);
            }}
          >
            <span className={`stage-bell-item${item.read ? "" : " is-unread"}`}>
              <span className="stage-bell-dot" aria-hidden="true" />
              <span className="min-w-0">
                <span className="d-block text-truncate fw-medium">{item.title}</span>
                <span className="d-block small text-secondary">{whenUtc(item.created_at)}</span>
              </span>
            </span>
          </DropdownItem>
        ))}
        <Dropdown.Separator className="dropdown-divider" />
        <DropdownItem onSelect={() => navigate("/notifications")}>
          <i className="ti ti-list me-2" aria-hidden="true" />
          View all announcements
        </DropdownItem>
      </DropdownContent>
    </Dropdown.Root>
  );
}
