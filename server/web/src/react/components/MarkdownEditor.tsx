import {
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { api, type TrainingAsset } from "../api";
import { Markdown } from "./Markdown";
import { useNotice } from "../ui";

type ViewMode = "write" | "split" | "preview";

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const TABLE_TEMPLATE = "\n| Column | Column |\n|--------|--------|\n| Value  | Value  |\n";

const imageSnippet = (asset: TrainingAsset, originalName?: string) => {
  const alt = (originalName || asset.name).replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
  return `![${alt}](${asset.url})`;
};

/**
 * Markdown textarea with a formatting toolbar, paste/drop image upload and a
 * live preview. Images go to the shared asset store (`/training/assets`).
 * `previewHeader` renders above the preview (e.g. the title being edited).
 */
export function MarkdownEditor({
  value,
  onChange,
  label,
  previewHeader,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  previewHeader?: ReactNode;
}) {
  const notice = useNotice();
  const [mode, setMode] = useState<ViewMode>(() =>
    window.matchMedia("(min-width: 1400px)").matches ? "split" : "write",
  );
  const [uploading, setUploading] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Replace the textarea selection, keeping the caret where the user expects it.
  const edit = (fn: (selected: string) => { text: string; select?: [number, number] }) => {
    const area = textareaRef.current;
    if (!area) return;
    const { selectionStart: start, selectionEnd: end, value: current } = area;
    const result = fn(current.slice(start, end));
    onChange(current.slice(0, start) + result.text + current.slice(end));
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
    const text = area.value;
    const start = text.lastIndexOf("\n", area.selectionStart - 1) + 1;
    let end = text.indexOf("\n", area.selectionEnd);
    if (end === -1) end = text.length;
    area.setSelectionRange(start, end);
    edit((sel) => ({
      text: sel
        .split("\n")
        .map((line, i) => prefix(i) + line)
        .join("\n"),
    }));
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

  return (
    <>
      <div className="stage-md-toolbar" role="toolbar" aria-label="Formatting">
        <ToolButton icon="ti-bold" label="Bold (Ctrl+B)" onClick={() => wrap("**")} />
        <ToolButton icon="ti-italic" label="Italic (Ctrl+I)" onClick={() => wrap("_")} />
        <ToolButton icon="ti-code" label="Inline code" onClick={() => wrap("`", "`", "code")} />
        <span className="stage-md-toolbar-sep" />
        <ToolButton
          icon="ti-h-2"
          label="Section heading"
          onClick={() => prefixLines(() => "## ")}
        />
        <ToolButton icon="ti-h-3" label="Sub-heading" onClick={() => prefixLines(() => "### ")} />
        <ToolButton icon="ti-list" label="Bullet list" onClick={() => prefixLines(() => "- ")} />
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
        <ToolButton icon="ti-table" label="Table" onClick={() => insertBlock(TABLE_TEMPLATE)} />
        <span className="stage-md-toolbar-sep" />
        <ToolButton
          icon="ti-link"
          label="Web link"
          onClick={() =>
            edit((sel) => {
              const text = sel || "link text";
              return {
                text: `[${text}](https://)`,
                select: [text.length + 3, text.length + 11],
              };
            })
          }
        />
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
            aria-label={label}
            spellCheck
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onPaste={onPaste}
            onDrop={onDrop}
            onKeyDown={onKeyDown}
          />
        )}
        {mode !== "write" && (
          <div className="stage-md-preview" aria-label="Preview">
            {previewHeader}
            <Markdown source={value} inertLinks />
          </div>
        )}
      </div>
    </>
  );
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
