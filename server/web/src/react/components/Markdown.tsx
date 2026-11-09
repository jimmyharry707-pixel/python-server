import { useEffect, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import { authFetch } from "../session";
import { Modal } from "../ui";
import "../../training.css";

// Lesson images live behind auth, so <img src> can't load them directly;
// fetch once with the bearer token and reuse the blob URL.
export const ASSET_PREFIX = "/api/v1/training/assets/";
const imageCache = new Map<string, Promise<string>>();

function loadAuthImage(src: string): Promise<string> {
  let pending = imageCache.get(src);
  if (!pending) {
    pending = authFetch(src)
      .then((res) => {
        if (!res.ok) throw new Error(`Image failed (${res.status})`);
        return res.blob();
      })
      .then((blob) => URL.createObjectURL(blob));
    pending.catch(() => imageCache.delete(src));
    imageCache.set(src, pending);
  }
  return pending;
}

export function forgetAuthImage(src: string) {
  imageCache.get(src)?.then(
    (url) => URL.revokeObjectURL(url),
    () => undefined,
  );
  imageCache.delete(src);
}

export function AuthImage({
  src,
  alt,
  className,
  onClick,
}: {
  src: string;
  alt: string;
  className?: string;
  onClick?: () => void;
}) {
  const needsAuth = src.startsWith(ASSET_PREFIX);
  const [url, setUrl] = useState<string | null>(needsAuth ? null : src);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!needsAuth) {
      setUrl(src);
      return;
    }
    let live = true;
    setFailed(false);
    loadAuthImage(src).then(
      (blobUrl) => live && setUrl(blobUrl),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [src, needsAuth]);
  if (failed)
    return (
      <span className="stage-md-image-missing">
        <i className="ti ti-photo-off me-1" aria-hidden="true" />
        {alt || "Image unavailable"}
      </span>
    );
  if (!url) return <span className="stage-md-image-loading placeholder-glow" aria-label={alt} />;
  return (
    <img
      src={url}
      alt={alt}
      className={className}
      loading="lazy"
      onClick={onClick}
      role={onClick ? "button" : undefined}
    />
  );
}

/** GitHub-style heading anchor, so `#45-remote-eligibility` links keep working. */
export function headingSlug(text: string) {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\p{M}_\- ]/gu, "")
    .replace(/ /g, "-");
}

function textOf(node: any): string {
  if (!node) return "";
  if (typeof node.value === "string") return node.value;
  return (node.children || []).map(textOf).join("");
}

function MarkdownImage({ src, alt }: { src: string; alt: string }) {
  const [zoom, setZoom] = useState(false);
  return (
    <>
      <AuthImage src={src} alt={alt} className="stage-md-image" onClick={() => setZoom(true)} />
      {zoom && (
        <Modal title={alt || "Image"} size="xl" onClose={() => setZoom(false)}>
          <AuthImage src={src} alt={alt} className="stage-md-image-zoomed" />
        </Modal>
      )}
    </>
  );
}

/**
 * Renders lesson Markdown (GitHub-flavoured: tables, task lists, strikethrough).
 * Raw HTML is not rendered. `/training/...` links become in-app navigation;
 * pass `inertLinks` in the editor preview so a click can't drop unsaved work.
 */
export function Markdown({ source, inertLinks = false }: { source: string; inertLinks?: boolean }) {
  const heading =
    (Tag: "h1" | "h2" | "h3" | "h4") =>
    ({ node, children }: { node?: any; children?: ReactNode }) => {
      const id = headingSlug(textOf(node));
      return (
        <Tag id={id} className="stage-md-heading">
          {children}
          <a className="stage-md-anchor" href={`#${id}`} aria-label="Link to this section">
            #
          </a>
        </Tag>
      );
    };
  const components: Components = {
    h1: heading("h1"),
    h2: heading("h2"),
    h3: heading("h3"),
    h4: heading("h4"),
    a: ({ href = "", children }) => {
      if (href.startsWith("#")) return <a href={href}>{children}</a>;
      if (href.startsWith("/")) {
        return inertLinks ? (
          <a href={href} title={`Course link: ${href}`} onClick={(event) => event.preventDefault()}>
            {children}
          </a>
        ) : (
          <Link to={href}>{children}</Link>
        );
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
          <i className="ti ti-external-link stage-md-external" aria-hidden="true" />
        </a>
      );
    },
    img: ({ src, alt }) =>
      typeof src === "string" && src ? <MarkdownImage src={src} alt={alt || ""} /> : null,
    table: ({ children }) => (
      <div className="table-responsive stage-md-table">
        <table className="table table-sm table-vcenter">{children}</table>
      </div>
    ),
    blockquote: ({ children }) => <blockquote className="stage-md-callout">{children}</blockquote>,
  };
  return (
    <div className="stage-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {source}
      </ReactMarkdown>
    </div>
  );
}
