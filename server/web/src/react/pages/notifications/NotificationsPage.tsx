import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { Markdown } from "../../components/Markdown";
import { ErrorBox, Loading, whenUtc } from "../../ui";
import { ANNOUNCEMENTS_KEY, useAnnouncementReads } from "./announcementReads";

export function NotificationsPage() {
  const { hash } = useLocation();
  const list = useQuery({ queryKey: ANNOUNCEMENTS_KEY, queryFn: api.announcements });
  const { markRead, markAllRead } = useAnnouncementReads();
  const items = list.data?.items ?? [];
  const unread = list.data?.unread_count ?? 0;

  // Arriving from the bell: jump to the announcement that was clicked.
  useEffect(() => {
    if (!list.data || !hash.startsWith("#announcement-")) return;
    document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [list.data, hash]);

  if (list.isLoading) return <Loading />;
  if (list.error) return <ErrorBox error={list.error} />;

  return (
    <div className="stage-notifications">
      <div className="d-flex align-items-center gap-2 mb-3">
        <span className="text-secondary">
          {unread ? `${unread} unread` : items.length ? "You're all caught up" : ""}
        </span>
        <button
          type="button"
          className="btn btn-sm btn-outline-secondary ms-auto"
          disabled={!unread || markAllRead.isPending}
          onClick={() => markAllRead.mutate()}
        >
          <i className="ti ti-checks me-1" aria-hidden="true" />
          Mark all as read
        </button>
      </div>
      {!items.length && (
        <div className="card">
          <div className="empty">
            <p className="empty-title">No announcements</p>
            <p className="empty-subtitle text-secondary">
              News from the team will show up here and on the bell.
            </p>
          </div>
        </div>
      )}
      {items.map((item) => (
        <article
          key={item.id}
          id={`announcement-${item.id}`}
          className={`card mb-3 stage-announcement${item.read ? "" : " is-unread"}`}
        >
          <div className="card-header">
            <div className="min-w-0">
              <h2 className="card-title mb-0">{item.title}</h2>
              <div className="text-secondary small">{whenUtc(item.created_at)}</div>
            </div>
            <div className="card-actions d-flex align-items-center gap-2">
              {!item.read && <span className="badge bg-primary text-white">New</span>}
              {!item.read && (
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  disabled={markRead.isPending && markRead.variables === item.id}
                  onClick={() => markRead.mutate(item.id)}
                >
                  Mark as read
                </button>
              )}
            </div>
          </div>
          <div className="card-body">
            <Markdown source={item.body_md} />
          </div>
        </article>
      ))}
    </div>
  );
}
