import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type MemberAnnouncements } from "../../api";

export const ANNOUNCEMENTS_KEY = ["announcements"];
export const ANNOUNCEMENTS_UNREAD_KEY = ["announcements-unread"];

/** Mark one or all announcements read, updating the bell and the list at once. */
export function useAnnouncementReads() {
  const queryClient = useQueryClient();
  const settle = () => {
    queryClient.invalidateQueries({ queryKey: ANNOUNCEMENTS_KEY });
    queryClient.invalidateQueries({ queryKey: ANNOUNCEMENTS_UNREAD_KEY });
  };
  const apply = (isRead: (id: string) => boolean) => {
    queryClient.setQueryData<MemberAnnouncements>(ANNOUNCEMENTS_KEY, (prev) => {
      if (!prev) return prev;
      const items = prev.items.map((x) => ({ ...x, read: x.read || isRead(x.id) }));
      return { items, unread_count: items.filter((x) => !x.read).length };
    });
    const list = queryClient.getQueryData<MemberAnnouncements>(ANNOUNCEMENTS_KEY);
    if (list) queryClient.setQueryData(ANNOUNCEMENTS_UNREAD_KEY, { count: list.unread_count });
  };
  const markRead = useMutation({
    mutationFn: api.markAnnouncementRead,
    onMutate: (id: string) => apply((x) => x === id),
    onSettled: settle,
  });
  const markAllRead = useMutation({
    mutationFn: api.markAllAnnouncementsRead,
    onMutate: () => {
      apply(() => true);
      queryClient.setQueryData(ANNOUNCEMENTS_UNREAD_KEY, { count: 0 });
    },
    onSettled: settle,
  });
  return { markRead, markAllRead };
}
