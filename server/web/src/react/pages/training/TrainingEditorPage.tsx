import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type LessonInput,
  type TrainingAsset,
  type TrainingCourse,
  type TrainingDocsUpdate,
  type TrainingLesson,
} from "../../api";
import { ASSET_PREFIX, AuthImage, forgetAuthImage, Markdown } from "../../components/Markdown";
import { Confirm, ErrorBox, Loading, Modal, useNotice } from "../../ui";
import { TRAINING_QUERY_KEY } from "./TrainingCoursePage";

type Draft = { title: string; slug: string; body_md: string };
type ViewMode = "write" | "split" | "preview";

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,95}$/;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const TABLE_TEMPLATE = "\n| Column | Column |\n|--------|--------|\n| Value  | Value  |\n";

const draftOf = (lesson: TrainingLesson): Draft => ({
  title: lesson.title,
  slug: lesson.slug,
  body_md: lesson.body_md,
});
const sameDraft = (a: Draft, b: Draft) =>
  a.title === b.title && a.slug === b.slug && a.body_md === b.body_md;
const formatSize = (bytes: number) =>
  bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;

export function TrainingEditorPage() {
  const notice = useNotice();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const course = useQuery({ queryKey: TRAINING_QUERY_KEY, queryFn: api.trainingCourse });
  const lessons = course.data?.lessons ?? [];
  const selectedId = params.get("lesson") || lessons[0]?.id || "";
  const selected = lessons.find((lesson) => lesson.id === selectedId);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftFor, setDraftFor] = useState("");
  const [mode, setMode] = useState<ViewMode>(() =>
    window.matchMedia("(min-width: 1400px)").matches ? "split" : "write",
  );
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [library, setLibrary] = useState(false);
  const [updatingFromDocs, setUpdatingFromDocs] = useState(false);
  const [uploading, setUploading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Load the selected lesson into the draft once (not on every refetch, which
  // would wipe unsaved typing).
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
    !sameDraft(draft, draftOf(selected))
  );

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const setCourse = (update: (prev: TrainingCourse) => TrainingCourse) =>
    queryClient.setQueryData<TrainingCourse>(TRAINING_QUERY_KEY, (prev) => prev && update(prev));

  const save = useMutation({
    mutationFn: (input: { id: string; data: LessonInput }) =>
      api.updateLesson(input.id, input.data),
    onSuccess: (saved) => {
      setCourse((prev) => ({
        ...prev,
        lessons: prev.lessons.map((x) => (x.id === saved.id ? saved : x)),
      }));
      setDraft(draftOf(saved));
      notice.success("Lesson saved");
    },
    onError: (error: Error) => notice.error(error.message),
  });

  const reorder = useMutation({
    mutationFn: api.reorderLessons,
    onMutate: (ids) =>
      setCourse((prev) => ({
        ...prev,
        lessons: ids
          .map((id, position) => {
            const lesson = prev.lessons.find((x) => x.id === id);
            return lesson && { ...lesson, position };
          })
          .filter((x): x is TrainingLesson => !!x),
      })),
    onError: (error: Error) => notice.error(error.message),
    onSettled: () => queryClient.invalidateQueries({ queryKey: TRAINING_QUERY_KEY }),
  });

  const select = (id: string) => {
    if (id === selectedId) return;
    if (dirty && !window.confirm("Discard unsaved changes to this lesson?")) return;
    setDraftFor("");
    setParams({ lesson: id });
  };

  const doSave = () => {
    if (!draft || !selected || !dirty || save.isPending) return;
    if (!draft.title.trim()) return notice.error("Give the lesson a title");
    if (!SLUG_RE.test(draft.slug))
      return notice.error("The URL can only use lowercase letters, numbers and dashes");
    save.mutate({ id: selected.id, data: draft });
  };

  const move = (index: number, delta: number) => {
    const ids = lessons.map((x) => x.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    reorder.mutate(ids);
  };

  // Replace the textarea selection, keeping the caret where the user expects it.
  const edit = (fn: (selected: string) => { text: string; select?: [number, number] }) => {
    const area = textareaRef.current;
    if (!area || !draft) return;
    const { selectionStart: start, selectionEnd: end, value } = area;
    const result = fn(value.slice(start, end));
    const body = value.slice(0, start) + result.text + value.slice(end);
    // Functional update: uploads call this after an await, when `draft` is stale.
    setDraft((current) => current && { ...current, body_md: body });
    requestAnimationFrame(() => {
      area.focus();
      const [a, b] = result.select ?? [result.text.length, result.text.length];
      area.setSelectionRange(start + a, start + b);
    });
  };
  const wrap = (before: string, after = before, placeholder = "text") =>
    edit((sel) => {
      const inner = sel || placeholder;
      return {
        text: `${before}${inner}${after}`,
        select: [before.length, before.length + inner.length],
      };
    });
  const prefixLines = (prefix: (i: number) => string) => {
    const area = textareaRef.current;
    if (!area) return;
    // Extend the selection to whole lines first.
    const { value } = area;
    const start = value.lastIndexOf("\n", area.selectionStart - 1) + 1;
    let end = value.indexOf("\n", area.selectionEnd);
    if (end === -1) end = value.length;
    area.setSelectionRange(start, end);
    edit((sel) => {
      const text = sel
        .split("\n")
        .map((line, i) => prefix(i) + line)
        .join("\n");
      return { text };
    });
  };
  const insertBlock = (block: string) =>
    edit(() => ({ text: block.startsWith("\n") ? block : `\n${block}\n` }));

  const uploadImages = async (files: File[]) => {
    const images = files.filter((file) => IMAGE_TYPES.includes(file.type));
    if (!images.length) {
      if (files.length) notice.error("Only PNG, JPEG, GIF or WebP images can be added");
      return;
    }
    setUploading(true);
    try {
      const snippets: string[] = [];
      for (const file of images) {
        const asset = await api.uploadTrainingAsset(file);
        snippets.push(imageSnippet(asset, file.name));
      }
      insertBlock(snippets.join("\n\n"));
      queryClient.invalidateQueries({ queryKey: ["training-assets"] });
      notice.success(images.length === 1 ? "Image added" : `${images.length} images added`);
    } catch (error) {
      notice.error((error as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...event.clipboardData.files];
    if (files.some((file) => IMAGE_TYPES.includes(file.type))) {
      event.preventDefault();
      uploadImages(files);
    }
  };
  const onDrop = (event: DragEvent<HTMLTextAreaElement>) => {
    const files = [...event.dataTransfer.files];
    if (!files.length) return;
    event.preventDefault();
    uploadImages(files);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "b") {
      event.preventDefault();
      wrap("**");
    } else if (mod && event.key.toLowerCase() === "i") {
      event.preventDefault();
      wrap("_");
    } else if (event.key === "Tab" && !mod && !event.altKey) {
      event.preventDefault();
      edit(() => ({ text: "  " }));
    }
  };

  useEffect(() => {
    const onSaveKey = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        doSave();
      }
    };
    window.addEventListener("keydown", onSaveKey);
    return () => window.removeEventListener("keydown", onSaveKey);
  });

  if (course.isLoading) return <Loading />;
  if (course.error) return <ErrorBox error={course.error} />;

  const index = selected ? lessons.indexOf(selected) : -1;

  return (
    <div className="row g-2 g-md-4 stage-training-editor">
      <aside className="col-xl-3">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Lessons</h3>
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
            {lessons.map((lesson, i) => (
              <div
                key={lesson.id}
                className={`list-group-item stage-training-lesson${lesson.id === selectedId ? " active" : ""}`}
              >
                <button
                  type="button"
                  className="stage-training-lesson-select"
                  onClick={() => select(lesson.id)}
                  aria-current={lesson.id === selectedId ? "true" : undefined}
                >
                  <span className="stage-training-lesson-num">{i + 1}</span>
                  <span className="text-truncate">
                    {lesson.id === selectedId && draft ? draft.title || "Untitled" : lesson.title}
                  </span>
                  {lesson.id === selectedId && dirty && (
                    <span className="badge bg-warning-lt ms-auto" title="Unsaved changes">
                      ●
                    </span>
                  )}
                </button>
                <div className="stage-training-lesson-move">
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost-secondary btn-icon"
                    aria-label={`Move ${lesson.title} up`}
                    disabled={i === 0 || reorder.isPending}
                    onClick={() => move(i, -1)}
                  >
                    <i className="ti ti-chevron-up" />
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost-secondary btn-icon"
                    aria-label={`Move ${lesson.title} down`}
                    disabled={i === lessons.length - 1 || reorder.isPending}
                    onClick={() => move(i, 1)}
                  >
                    <i className="ti ti-chevron-down" />
                  </button>
                </div>
              </div>
            ))}
            {!lessons.length && (
              <div className="list-group-item text-secondary">No lessons yet.</div>
            )}
          </div>
          <div className="card-footer d-flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-sm btn-outline-secondary flex-fill"
              onClick={() => setLibrary(true)}
            >
              <i className="ti ti-photo me-1" aria-hidden="true" />
              Images
            </button>
            <Link
              className="btn btn-sm btn-outline-secondary flex-fill"
              to={selected ? `/training/${selected.slug}` : "/training"}
            >
              <i className="ti ti-eye me-1" aria-hidden="true" />
              View course
            </Link>
            <button
              type="button"
              className="btn btn-sm btn-outline-secondary w-100"
              title="Replace the course with the training docs deployed on the server"
              onClick={() => setUpdatingFromDocs(true)}
            >
              <i className="ti ti-file-import me-1" aria-hidden="true" />
              Update from docs
            </button>
          </div>
        </div>
      </aside>

      <section className="col-xl-9">
        {!selected || !draft ? (
          <div className="card">
            <div className="empty">
              <p className="empty-title">No lesson selected</p>
              <p className="empty-subtitle text-secondary">
                Create a lesson to start building the course.
              </p>
            </div>
          </div>
        ) : (
          <div className="card stage-training-editor-card">
            <div className="card-header d-block">
              <div className="row g-2">
                <div className="col-md-7">
                  <label className="form-label small mb-1" htmlFor="lessonTitle">
                    Lesson {index + 1} title
                  </label>
                  <input
                    id="lessonTitle"
                    className="form-control"
                    value={draft.title}
                    maxLength={255}
                    onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  />
                </div>
                <div className="col-md-5">
                  <label className="form-label small mb-1" htmlFor="lessonSlug">
                    Page URL
                  </label>
                  <div className="input-group">
                    <span className="input-group-text">/training/</span>
                    <input
                      id="lessonSlug"
                      className={`form-control${SLUG_RE.test(draft.slug) ? "" : " is-invalid"}`}
                      value={draft.slug}
                      maxLength={96}
                      onChange={(e) => setDraft({ ...draft, slug: e.target.value.toLowerCase() })}
                    />
                  </div>
                  {draft.slug !== selected.slug && (
                    <div className="form-hint text-warning">
                      Links to /training/{selected.slug} in other lessons will stop working.
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="stage-md-toolbar" role="toolbar" aria-label="Formatting">
              <ToolButton icon="ti-bold" label="Bold (Ctrl+B)" onClick={() => wrap("**")} />
              <ToolButton icon="ti-italic" label="Italic (Ctrl+I)" onClick={() => wrap("_")} />
              <ToolButton
                icon="ti-code"
                label="Inline code"
                onClick={() => wrap("`", "`", "code")}
              />
              <span className="stage-md-toolbar-sep" />
              <ToolButton
                icon="ti-h-2"
                label="Section heading"
                onClick={() => prefixLines(() => "## ")}
              />
              <ToolButton
                icon="ti-h-3"
                label="Sub-heading"
                onClick={() => prefixLines(() => "### ")}
              />
              <ToolButton
                icon="ti-list"
                label="Bullet list"
                onClick={() => prefixLines(() => "- ")}
              />
              <ToolButton
                icon="ti-list-numbers"
                label="Numbered list"
                onClick={() => prefixLines((i) => `${i + 1}. `)}
              />
              <ToolButton
                icon="ti-checkbox"
                label="Checklist"
                onClick={() => prefixLines(() => "- [ ] ")}
              />
              <ToolButton
                icon="ti-quote"
                label="Callout / note"
                onClick={() => prefixLines(() => "> ")}
              />
              <ToolButton
                icon="ti-table"
                label="Table"
                onClick={() => insertBlock(TABLE_TEMPLATE)}
              />
              <span className="stage-md-toolbar-sep" />
              <ToolButton
                icon="ti-link"
                label="Web link"
                onClick={() =>
                  edit((sel) => {
                    const label = sel || "link text";
                    return {
                      text: `[${label}](https://)`,
                      select: [label.length + 3, label.length + 11],
                    };
                  })
                }
              />
              <select
                className="form-select form-select-sm stage-md-toolbar-select"
                aria-label="Link to a lesson"
                value=""
                onChange={(e) => {
                  const target = lessons.find((x) => x.id === e.target.value);
                  if (target)
                    edit((sel) => ({ text: `[${sel || target.title}](/training/${target.slug})` }));
                }}
              >
                <option value="">Link to lesson…</option>
                {lessons.map((x, i) => (
                  <option key={x.id} value={x.id}>
                    {i + 1}. {x.title}
                  </option>
                ))}
              </select>
              <ToolButton
                icon="ti-photo-plus"
                label="Upload image (or paste / drop it into the text)"
                busy={uploading}
                onClick={() => fileRef.current?.click()}
              />
              <input
                ref={fileRef}
                type="file"
                accept={IMAGE_TYPES.join(",")}
                multiple
                className="visually-hidden"
                onChange={(e) => {
                  uploadImages([...(e.target.files ?? [])]);
                  e.target.value = "";
                }}
              />
              <div className="btn-group btn-group-sm ms-auto" role="group" aria-label="View">
                {(["write", "split", "preview"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    className={`btn ${mode === m ? "btn-primary" : "btn-outline-secondary"}`}
                    aria-pressed={mode === m}
                    onClick={() => setMode(m)}
                  >
                    {m === "write" ? "Write" : m === "split" ? "Split" : "Preview"}
                  </button>
                ))}
              </div>
            </div>

            <div className={`stage-md-editor is-${mode}`}>
              {mode !== "preview" && (
                <textarea
                  ref={textareaRef}
                  className="stage-md-source"
                  aria-label="Lesson content (Markdown)"
                  spellCheck
                  value={draft.body_md}
                  onChange={(e) => setDraft({ ...draft, body_md: e.target.value })}
                  onPaste={onPaste}
                  onDrop={onDrop}
                  onKeyDown={onKeyDown}
                />
              )}
              {mode !== "write" && (
                <div className="stage-md-preview" aria-label="Preview">
                  <h2 className="stage-course-lesson-title">{draft.title}</h2>
                  <Markdown source={draft.body_md} inertLinks />
                </div>
              )}
            </div>

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
                {dirty
                  ? "Unsaved changes"
                  : selected.updated_at
                    ? `Saved ${new Date(selected.updated_at + "Z").toLocaleString()}`
                    : ""}
              </span>
              <button
                type="button"
                className="btn btn-outline-danger ms-auto"
                onClick={() => setDeleting(true)}
              >
                <i className="ti ti-trash me-1" aria-hidden="true" />
                Delete lesson
              </button>
            </div>
          </div>
        )}
        <MarkdownTips />
      </section>

      {creating && (
        <CreateLessonModal
          onClose={() => setCreating(false)}
          onCreated={(lesson) => {
            setCourse((prev) => ({ ...prev, lessons: [...prev.lessons, lesson] }));
            setDraftFor("");
            setParams({ lesson: lesson.id });
          }}
        />
      )}
      {deleting && selected && (
        <Confirm
          text={`Delete "${selected.title}"? Supporters' progress on it is removed too. This can't be undone.`}
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await api.deleteLesson(selected.id);
            const rest = lessons.filter((x) => x.id !== selected.id);
            setCourse((prev) => ({ ...prev, lessons: rest }));
            setDraftFor("");
            setParams(rest[0] ? { lesson: rest[0].id } : {});
            notice.success("Lesson deleted");
          }}
        />
      )}
      {updatingFromDocs && (
        <DocsUpdateModal
          unsavedLesson={dirty && draft ? draft.title || "Untitled" : ""}
          onClose={() => setUpdatingFromDocs(false)}
          onUpdated={async (summary) => {
            for (const name of summary.images.changed) forgetAuthImage(ASSET_PREFIX + name);
            queryClient.invalidateQueries({ queryKey: ["training-assets"] });
            await queryClient.invalidateQueries({ queryKey: TRAINING_QUERY_KEY });
            setDraftFor(""); // reload the open lesson from the updated course
            notice.success(docsUpdateNotice(summary));
          }}
        />
      )}
      {library && (
        <ImageLibrary
          lessons={lessons}
          canInsert={!!selected && mode !== "preview"}
          onInsert={(asset) => {
            setLibrary(false);
            requestAnimationFrame(() => insertBlock(imageSnippet(asset)));
          }}
          onClose={() => setLibrary(false)}
        />
      )}
    </div>
  );
}

function imageSnippet(asset: TrainingAsset, originalName?: string) {
  const alt = (originalName || asset.name).replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
  return `![${alt}](${asset.url})\n\n*Figure: describe what the image shows.*`;
}

function ToolButton({
  icon,
  label,
  onClick,
  busy = false,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      className="btn btn-sm btn-ghost-secondary btn-icon"
      title={label}
      aria-label={label}
      disabled={busy}
      onMouseDown={(e) => e.preventDefault()} // keep the textarea selection
      onClick={onClick}
    >
      {busy ? (
        <span className="spinner-border spinner-border-sm" aria-hidden="true" />
      ) : (
        <i className={`ti ${icon}`} aria-hidden="true" />
      )}
    </button>
  );
}

function CreateLessonModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (lesson: TrainingLesson) => void;
}) {
  const [title, setTitle] = useState("");
  const create = useMutation({
    mutationFn: () => api.createLesson({ title: title.trim(), body_md: `## ${title.trim()}\n\n` }),
    onSuccess: (lesson) => {
      onCreated(lesson);
      onClose();
    },
  });
  return (
    <Modal
      title="New lesson"
      description="It's added at the end of the course. Move it into place with the arrow buttons."
      size="md"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            form="newLessonForm"
            className="btn btn-primary"
            disabled={!title.trim() || create.isPending}
          >
            {create.isPending ? "Creating…" : "Create lesson"}
          </button>
        </>
      }
    >
      <form
        id="newLessonForm"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) create.mutate();
        }}
      >
        <label className="form-label" htmlFor="newLessonTitle">
          Title
        </label>
        <input
          id="newLessonTitle"
          className="form-control"
          autoFocus
          maxLength={255}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        {create.error && <div className="text-danger small mt-2">{create.error.message}</div>}
      </form>
    </Modal>
  );
}

function ImageLibrary({
  lessons,
  canInsert,
  onInsert,
  onClose,
}: {
  lessons: TrainingLesson[];
  canInsert: boolean;
  onInsert: (asset: TrainingAsset) => void;
  onClose: () => void;
}) {
  const notice = useNotice();
  const queryClient = useQueryClient();
  const assets = useQuery({ queryKey: ["training-assets"], queryFn: api.trainingAssets });
  const [filter, setFilter] = useState("");
  const [removing, setRemoving] = useState<TrainingAsset | null>(null);
  const usage = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const asset of assets.data ?? [])
      map.set(
        asset.name,
        lessons.filter((x) => x.body_md.includes(asset.url)).map((x) => x.title),
      );
    return map;
  }, [assets.data, lessons]);
  const shown = (assets.data ?? []).filter((a) => a.name.includes(filter.trim().toLowerCase()));
  return (
    <Modal
      title="Images"
      description="Images used in lessons. Paste or drop a screenshot into the editor to add one."
      size="xl"
      onClose={onClose}
    >
      <input
        type="search"
        className="form-control mb-3"
        placeholder="Filter by name…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {assets.isLoading && <Loading />}
      {assets.error && <ErrorBox error={assets.error} />}
      <div className="row g-3">
        {shown.map((asset) => {
          const usedIn = usage.get(asset.name) ?? [];
          return (
            <div className="col-sm-6 col-lg-4" key={asset.name}>
              <div className="card card-sm h-100">
                <div className="stage-training-thumb">
                  <AuthImage src={asset.url} alt={asset.name} />
                </div>
                <div className="card-body">
                  <div className="fw-medium text-truncate" title={asset.name}>
                    {asset.name}
                  </div>
                  <div className="text-secondary small">
                    {formatSize(asset.size)} ·{" "}
                    {usedIn.length
                      ? `Used in ${usedIn.length} lesson${usedIn.length > 1 ? "s" : ""}`
                      : "Not used"}
                  </div>
                  <div className="d-flex flex-wrap gap-1 mt-2">
                    {canInsert && (
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => onInsert(asset)}
                      >
                        Insert
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      onClick={() =>
                        navigator.clipboard
                          .writeText(`![${asset.name}](${asset.url})`)
                          .then(() => notice.success("Markdown copied"))
                      }
                    >
                      Copy Markdown
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-danger"
                      onClick={() => setRemoving(asset)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
        {assets.data && !shown.length && (
          <div className="col-12 text-secondary text-center py-4">No images found.</div>
        )}
      </div>
      {removing && (
        <Confirm
          text={
            (usage.get(removing.name) ?? []).length
              ? `"${removing.name}" is used in: ${(usage.get(removing.name) ?? []).join(", ")}. Those lessons will show a missing image. Delete it anyway?`
              : `Delete "${removing.name}"?`
          }
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.deleteTrainingAsset(removing.name);
            forgetAuthImage(removing.url);
            queryClient.invalidateQueries({ queryKey: ["training-assets"] });
            notice.success("Image deleted");
          }}
        />
      )}
    </Modal>
  );
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function docsUpdateNotice(summary: TrainingDocsUpdate) {
  const count = (status: string) => summary.lessons.filter((x) => x.status === status).length;
  const images = summary.images.new.length + summary.images.changed.length;
  const parts = [
    count("new") && `${plural(count("new"), "lesson")} added`,
    count("changed") && `${plural(count("changed"), "lesson")} updated`,
    images && `${plural(images, "image")} updated`,
  ].filter(Boolean);
  return parts.length
    ? `Course updated from the docs: ${parts.join(", ")}`
    : "Course order updated";
}

function DocsUpdateModal({
  unsavedLesson,
  onClose,
  onUpdated,
}: {
  unsavedLesson: string;
  onClose: () => void;
  onUpdated: (summary: TrainingDocsUpdate) => Promise<void>;
}) {
  const preview = useQuery({
    queryKey: ["training-docs-update"],
    queryFn: api.trainingDocsUpdate,
    gcTime: 0,
  });
  const apply = useMutation({
    mutationFn: api.applyTrainingDocsUpdate,
    onSuccess: async (summary) => {
      await onUpdated(summary);
      onClose();
    },
  });
  const plan = preview.data;
  const added = plan?.lessons.filter((x) => x.status === "new") ?? [];
  const changed = plan?.lessons.filter((x) => x.status === "changed") ?? [];
  const moved = plan?.lessons.filter((x) => x.status === "moved") ?? [];
  const editedOnSite = changed.filter((x) => x.edited_on_site);
  const images = plan ? plan.images.new.length + plan.images.changed.length : 0;

  return (
    <Modal
      title="Update the course from the docs"
      description="Replaces the lessons and images with the training docs deployed on the server (docs/bidder-training)."
      size="lg"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {plan?.up_to_date ? "Close" : "Cancel"}
          </button>
          {plan && !plan.up_to_date && (
            <button
              type="button"
              className="btn btn-primary"
              disabled={apply.isPending}
              onClick={() => apply.mutate()}
            >
              {apply.isPending ? "Updating…" : "Update course"}
            </button>
          )}
        </>
      }
    >
      {preview.isLoading && <Loading />}
      {preview.error && <ErrorBox error={preview.error} />}
      {plan?.up_to_date && (
        <p className="mb-0">The course already matches the docs on the server.</p>
      )}
      {plan && !plan.up_to_date && (
        <>
          {editedOnSite.length > 0 && (
            <div className="alert alert-warning">
              <div>
                Edited on this website since the last docs update, and replaced by the docs version:{" "}
                <strong>{editedOnSite.map((x) => x.title).join(", ")}</strong>. Copy anything you
                want to keep first.
              </div>
            </div>
          )}
          {unsavedLesson && (
            <div className="alert alert-warning">
              <div>
                Your unsaved changes to <strong>{unsavedLesson}</strong> will be discarded.
              </div>
            </div>
          )}
          <DocsUpdateList title="New lessons" items={added.map((x) => x.title)} />
          <DocsUpdateList title="Updated lessons" items={changed.map((x) => x.title)} />
          {moved.length > 0 && (
            <p className="text-secondary">
              {plural(moved.length, "unchanged lesson")} move to the docs' order.
            </p>
          )}
          <p>
            Images: {plan.images.new.length} new, {plan.images.changed.length} updated,{" "}
            {plan.images.same} unchanged.
            {images > 0 && (
              <span className="text-secondary">
                {" "}
                Supporters may see the old pictures for up to 5 minutes.
              </span>
            )}
          </p>
          {plan.kept.length > 0 && (
            <p>
              Not in the docs, kept as they are and moved to the end:{" "}
              <strong>{plan.kept.map((x) => x.title).join(", ")}</strong>.
            </p>
          )}
          <p className="text-secondary mb-0">
            Supporters keep their progress on every lesson. Images that aren't in the docs stay in
            the image library.
          </p>
          {apply.error && <div className="text-danger small mt-2">{apply.error.message}</div>}
        </>
      )}
    </Modal>
  );
}

function DocsUpdateList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="mb-3">
      <div className="fw-medium">
        {title} ({items.length})
      </div>
      <ul className="mb-0">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function MarkdownTips() {
  return (
    <details className="card card-body mt-3 stage-md-tips">
      <summary className="fw-medium">Formatting cheatsheet</summary>
      <div className="table-responsive mt-2">
        <table className="table table-sm mb-0">
          <tbody>
            <tr>
              <td>
                <code>## Section</code> / <code>### Sub-section</code>
              </td>
              <td>
                Headings. Each one gets a link anchor, e.g. <code>#45-remote-eligibility</code>.
              </td>
            </tr>
            <tr>
              <td>
                <code>**bold**</code>, <code>_italic_</code>, <code>`code`</code>
              </td>
              <td>Text styles</td>
            </tr>
            <tr>
              <td>
                <code>- item</code>, <code>1. item</code>, <code>- [ ] task</code>
              </td>
              <td>Bullet, numbered and checklist items</td>
            </tr>
            <tr>
              <td>
                <code>&gt; Note text</code>
              </td>
              <td>Highlighted callout box</td>
            </tr>
            <tr>
              <td>
                <code>[text](/training/screening#44-the-3-day-72-hour-rule)</code>
              </td>
              <td>Link to another lesson (and optionally a section)</td>
            </tr>
            <tr>
              <td>
                <code>![alt text](/api/v1/training/assets/name.png)</code>
              </td>
              <td>Image. Paste or drop a screenshot into the editor to upload one.</td>
            </tr>
            <tr>
              <td>
                <code>| A | B |</code> + <code>|---|---|</code>
              </td>
              <td>Table (use the table button for a template)</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  );
}
