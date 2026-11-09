import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ManagedAnnouncement, type ManagedAnnouncements } from "../../api";
import { MarkdownEditor } from "../../components/MarkdownEditor";
import { Confirm, ErrorBox, Loading, Modal, useNotice, whenUtc } from "../../ui";

export const ANNOUNCEMENTS_MANAGE_KEY = ["announcements-manage"];

type Draft = { title: string; body_md: string };

const draftOf = (item: ManagedAnnouncement): Draft => ({
  title: item.title,
  body_md: item.body_md,
});

export function AnnouncementsEditorPage() {
  const notice = useNotice();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const list = useQuery({ queryKey: ANNOUNCEMENTS_MANAGE_KEY, queryFn: api.manageAnnouncements });
  const items = list.data?.items ?? [];
  const memberCount = list.data?.member_count ?? 0;
  const selectedId = params.get("item") || items[0]?.id || "";
  const selected = items.find((item) => item.id === selectedId);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftFor, setDraftFor] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Load the selected announcement into the draft once (not on every refetch,
  // which would wipe unsaved typing).
  useEffect(() => {
    if (selected && draftFor !== selected.id) {
      setDraft(draftOf(selected));
      setDraftFor(selected.id);
    }
  }, [selected, draftFor]);

  const dirty = !!(
    draft &&
    selected &&
    draftFor === selected.id &&
    (draft.title !== selected.title || draft.body_md !== selected.body_md)
  );

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const setItems = (update: (prev: ManagedAnnouncements) => ManagedAnnouncements) =>
    queryClient.setQueryData<ManagedAnnouncements>(
      ANNOUNCEMENTS_MANAGE_KEY,
      (prev) => prev && update(prev),
    );

  const save = useMutation({
    mutationFn: (input: { id: string; data: Draft }) =>
      api.updateAnnouncement(input.id, input.data),
    onSuccess: (saved) => {
      setItems((prev) => ({
        ...prev,
        items: prev.items.map((x) => (x.id === saved.id ? saved : x)),
      }));
      setDraft(draftOf(saved));
      notice.success("Announcement saved");
    },
    onError: (error: Error) => notice.error(error.message),
  });

  const select = (id: string) => {
    if (id === selectedId) return;
    if (dirty && !window.confirm("Discard unsaved changes to this announcement?")) return;
    setDraftFor("");
    setParams({ item: id });
  };

  const doSave = () => {
    if (!draft || !selected || !dirty || save.isPending) return;
    if (!draft.title.trim()) return notice.error("Give the announcement a title");
    save.mutate({ id: selected.id, data: draft });
  };

  useEffect(() => {
    const onSaveKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        doSave();
      }
    };
    window.addEventListener("keydown", onSaveKey);
    return () => window.removeEventListener("keydown", onSaveKey);
  });

  if (list.isLoading) return <Loading />;
  if (list.error) return <ErrorBox error={list.error} />;

  return (
    <div className="row g-2 g-md-4 stage-training-editor">
      <aside className="col-xl-3">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Announcements</h3>
            <div className="card-actions">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => setCreating(true)}
              >
                <i className="ti ti-plus me-1" aria-hidden="true" />
                New
              </button>
            </div>
          </div>
          <div className="list-group list-group-flush stage-training-lesson-list">
            {items.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`list-group-item list-group-item-action stage-announcement-item${item.id === selectedId ? " active" : ""}`}
                aria-current={item.id === selectedId ? "true" : undefined}
                onClick={() => select(item.id)}
              >
                <span className="d-flex align-items-center gap-2">
                  <span className="fw-medium text-truncate">
                    {item.id === selectedId && draft ? draft.title || "Untitled" : item.title}
                  </span>
                  {item.id === selectedId && dirty && (
                    <span className="badge bg-warning-lt ms-auto" title="Unsaved changes">
                      ●
                    </span>
                  )}
                </span>
                <span className="d-block small text-secondary">
                  {whenUtc(item.created_at)} · read by {item.read_count}/{memberCount}
                </span>
              </button>
            ))}
            {!items.length && (
              <div className="list-group-item text-secondary">No announcements yet.</div>
            )}
          </div>
          <div className="card-footer text-secondary small">
            Announcements are shown to members only. Supporters, admins and managers don't see them.
          </div>
        </div>
      </aside>

      <section className="col-xl-9">
        {!selected || !draft ? (
          <div className="card">
            <div className="empty">
              <p className="empty-title">No announcement selected</p>
              <p className="empty-subtitle text-secondary">
                Publish an announcement to notify all members.
              </p>
            </div>
          </div>
        ) : (
          <div className="card stage-training-editor-card">
            <div className="card-header d-block">
              <label className="form-label small mb-1" htmlFor="announcementTitle">
                Title
              </label>
              <input
                id="announcementTitle"
                className="form-control"
                value={draft.title}
                maxLength={255}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </div>

            <MarkdownEditor
              label="Announcement content (Markdown)"
              value={draft.body_md}
              onChange={(body_md) => setDraft((current) => current && { ...current, body_md })}
              previewHeader={<h2 className="stage-course-lesson-title">{draft.title}</h2>}
            />

            <div className="card-footer d-flex flex-wrap align-items-center gap-2">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!dirty || save.isPending}
                onClick={doSave}
              >
                {save.isPending ? "Saving…" : "Save"}
                <span className="ms-2 small opacity-75 d-none d-md-inline">Ctrl+S</span>
              </button>
              <button
                type="button"
                className="btn btn-outline-secondary"
                disabled={!dirty || save.isPending}
                onClick={() => setDraft(draftOf(selected))}
              >
                Discard changes
              </button>
              <span className="text-secondary small ms-md-2">
                {dirty ? "Unsaved changes" : `Saved ${whenUtc(selected.updated_at)}`}
              </span>
              <button
                type="button"
                className="btn btn-outline-danger ms-auto"
                onClick={() => setDeleting(true)}
              >
                <i className="ti ti-trash me-1" aria-hidden="true" />
                Delete
              </button>
            </div>
          </div>
        )}
        <p className="text-secondary small mt-3">
          Members are notified when an announcement is published. Editing one doesn't notify them
          again. Paste or drop a screenshot into the text to add an image.
        </p>
      </section>

      {creating && (
        <CreateAnnouncementModal
          onClose={() => setCreating(false)}
          onCreated={(item) => {
            setItems((prev) => ({ ...prev, items: [item, ...prev.items] }));
            setDraftFor("");
            setParams({ item: item.id });
          }}
        />
      )}
      {deleting && selected && (
        <Confirm
          text={`Delete "${selected.title}"? Members will no longer see it. This can't be undone.`}
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await api.deleteAnnouncement(selected.id);
            const rest = items.filter((x) => x.id !== selected.id);
            setItems((prev) => ({ ...prev, items: rest }));
            setDraftFor("");
            setParams(rest[0] ? { item: rest[0].id } : {});
            notice.success("Announcement deleted");
          }}
        />
      )}
    </div>
  );
}

function CreateAnnouncementModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (item: ManagedAnnouncement) => void;
}) {
  const notice = useNotice();
  const [draft, setDraft] = useState<Draft>({ title: "", body_md: "" });
  const ready = !!draft.title.trim() && !!draft.body_md.trim();
  const create = useMutation({
    mutationFn: () => api.createAnnouncement({ title: draft.title.trim(), body_md: draft.body_md }),
    onSuccess: (item) => {
      onCreated(item);
      onClose();
      notice.success("Announcement published to members");
    },
    onError: (error: Error) => notice.error(error.message),
  });
  return (
    <Modal
      title="New announcement"
      description="Members are notified as soon as you publish."
      size="xl"
      className="stage-announcement-compose"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!ready || create.isPending}
            onClick={() => create.mutate()}
          >
            <i className="ti ti-send me-1" aria-hidden="true" />
            {create.isPending ? "Publishing…" : "Publish"}
          </button>
        </>
      }
    >
      <label className="form-label" htmlFor="newAnnouncementTitle">
        Title
      </label>
      <input
        id="newAnnouncementTitle"
        className="form-control mb-3"
        autoFocus
        maxLength={255}
        value={draft.title}
        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
      />
      <div className="card">
        <MarkdownEditor
          label="Announcement content (Markdown)"
          value={draft.body_md}
          onChange={(body_md) => setDraft((current) => ({ ...current, body_md }))}
          previewHeader={<h2 className="stage-course-lesson-title">{draft.title}</h2>}
        />
      </div>
    </Modal>
  );
}
