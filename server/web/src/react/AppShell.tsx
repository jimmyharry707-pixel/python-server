import { Fragment, lazy, Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import brandLogo from "../assets/brand-logo.svg";
import type { CurrentUser } from "./api";
import { UserContext } from "./auth";
import { signOut } from "./session";
import { useNotice } from "./ui";
import { Avatar } from "./components/Avatar";
import { NotificationBell } from "./components/NotificationBell";
import { SignInAgain } from "./components/SignInAgain";
import { Dropdown, DropdownContent, DropdownItem } from "./components/primitives";
const AccountSettings = lazy(() =>
  import("./components/AccountSettings").then((module) => ({ default: module.AccountSettings })),
);
const nav = [
  {
    section: null,
    items: [
      ["dashboard", "Dashboard", "ti-layout-dashboard", "app"],
      ["training", "Bidder training", "ti-school", "learn"],
    ],
  },
  {
    section: "Business",
    items: [
      ["interviews", "Interviews", "ti-calendar-event", "app"],
      ["workplan", "Work plan", "ti-checklist", "work"],
      ["performance", "Performance", "ti-chart-bar", "app"],
      ["quality", "Quality review", "ti-shield-check", "manage"],
      ["colleagues", "Colleagues", "ti-users-group", "member"],
    ],
  },
  {
    section: "Manage",
    items: [
      ["users", "Users", "ti-users", "manage"],
      ["profiles", "Profiles", "ti-id", "manage"],
      ["licenses", "Licenses", "ti-license", "staff"],
      ["training-editor", "Training", "ti-school", "staff"],
      ["announcements", "Announcements", "ti-bell-ringing", "staff"],
    ],
  },
  {
    section: "Product",
    items: [
      ["releases", "Releases", "ti-package", "staff"],
      ["sync", "Sync log", "ti-refresh", "staff"],
    ],
  },
  {
    section: "Monitor",
    items: [
      ["errors", "Errors", "ti-alert-triangle", "staff"],
      ["telemetry", "Telemetry", "ti-activity", "staff"],
    ],
  },
] as const;
// Supporters only use the training course on the website; "app" is everything else.
// Members get the course too, read-only ("learn" = supporter or member).
const allowed = (rule: string, role: string) =>
  (rule === "app" && role !== "supporter") ||
  (rule === "learn" && (role === "supporter" || role === "member")) ||
  (rule === "staff" && ["admin", "manager"].includes(role)) ||
  (rule === "member" && role === "member") ||
  (rule === "manage" && ["admin", "manager", "member"].includes(role)) ||
  (rule === "work" && role !== "supporter");
const normalizeRole = (role: string) => {
  const normalized = role.trim().toLowerCase();
  return normalized === "user" ? "member" : normalized;
};
const titles: Record<string, [string, string]> = {
  dashboard: ["Overview", "Dashboard"],
  interviews: ["Business", "Interviews"],
  workplan: ["Business", "Work plan"],
  performance: ["Business", "Performance"],
  quality: ["Business", "Quality review"],
  colleagues: ["Business", "Colleagues"],
  training: ["Learning", "Bidder training"],
  "training-editor": ["Manage", "Training course"],
  announcements: ["Manage", "Announcements"],
  notifications: ["Overview", "Announcements"],
  users: ["Manage", "Users"],
  profiles: ["Manage", "Profiles"],
  licenses: ["Manage", "Licenses"],
  sync: ["Product", "Sync log"],
  errors: ["Monitor", "Errors"],
  telemetry: ["Monitor", "Telemetry"],
};
export function AppShell({ user }: { user: CurrentUser }) {
  const [settings, setSettings] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [version, setVersion] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light",
  );
  const accountTriggerRef = useRef<HTMLButtonElement>(null);
  const notice = useNotice();
  const loc = useLocation();
  const page = loc.pathname.split("/")[1] || "dashboard";
  const [pre, title] = titles[page] || ["Absurd Technologies", "Dashboard"];
  const role = normalizeRole(user.role);
  const isSupporter = role === "supporter";
  useEffect(() => {
    fetch("/meta")
      .then((response) => response.json())
      .then((meta) => setVersion(meta.version ? `v${meta.version}` : ""))
      .catch(() => setVersion(""));
  }, []);

  useEffect(() => {
    setAccountOpen(false);
    setSidebarOpen(false);
    document.title = `${title} · Absurd Technologies`;
  }, [loc.pathname, title]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, []);

  useEffect(() => {
    document.body.classList.toggle("stage-sidebar-is-open", sidebarOpen);
    return () => document.body.classList.remove("stage-sidebar-is-open");
  }, [sidebarOpen]);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", next);
    localStorage.setItem("baymax_stage_theme", next);
    setTheme(next);
  };
  const openSettings = () => {
    setAccountOpen(false);
    // Let the menu restore focus before Radix Dialog installs its focus trap.
    requestAnimationFrame(() => setSettings(true));
  };
  return (
    <UserContext.Provider value={user}>
      <div className="stage-app-bg" aria-hidden="true">
        <span className="stage-login-glow stage-login-glow--one" />
      </div>
      <div className="page">
        {sidebarOpen && (
          <button
            type="button"
            className="stage-sidebar-backdrop"
            aria-label="Close navigation"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <aside className="navbar navbar-vertical navbar-expand-lg">
          <div className="container-fluid">
            <button
              className="navbar-toggler"
              type="button"
              aria-controls="sidebar-menu"
              aria-expanded={sidebarOpen}
              aria-label="Toggle navigation"
              onClick={() => setSidebarOpen((open) => !open)}
            >
              <span className="navbar-toggler-icon" />
            </button>
            <div className="navbar-brand navbar-brand-autodark">
              <NavLink
                id="brandHome"
                to={isSupporter ? "/training" : "/dashboard"}
                className="stage-sidebar-brand"
              >
                <img src={brandLogo} alt="" width="40" height="40" />
                <span className="stage-brand-name">
                  Absurd Technologies<span className="stage-brand-dot">.</span>
                </span>
              </NavLink>
            </div>
            <div
              className={`collapse navbar-collapse${sidebarOpen ? " show" : ""}`}
              id="sidebar-menu"
            >
              <ul className="navbar-nav pt-lg-3 flex-grow-1" id="sideNav">
                {nav.map((group) => {
                  const items = group.items.filter((item) => allowed(item[3], role));
                  if (!items.length) return null;
                  return (
                    <Fragment key={group.section || "home"}>
                      {group.section && (
                        <li className="nav-item stage-nav-section" aria-hidden="true">
                          <span className="stage-nav-section-label">{group.section}</span>
                        </li>
                      )}
                      {items.map(([id, label, icon]) => (
                        <li className="nav-item" key={id}>
                          {id === "releases" ? (
                            <button
                              type="button"
                              className="nav-link w-100"
                              onClick={() => notice.show("Coming soon", "secondary")}
                            >
                              <span className="nav-link-icon">
                                <i className={`ti ${icon}`} />
                              </span>
                              <span className="nav-link-title">{label}</span>
                            </button>
                          ) : (
                            <NavLink
                              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
                              to={`/${id}`}
                              onClick={() => setSidebarOpen(false)}
                            >
                              <span className="nav-link-icon">
                                <i className={`ti ${icon}`} />
                              </span>
                              <span className="nav-link-title">{label}</span>
                            </NavLink>
                          )}
                        </li>
                      ))}
                    </Fragment>
                  );
                })}
              </ul>
            </div>
          </div>
        </aside>
        <div className="page-wrapper">
          <header className="navbar navbar-expand-md d-print-none stage-topbar">
            <div className="container-xl">
              <div className="stage-topbar-titles me-auto">
                <div className="page-pretitle mb-0" id="pagePretitle">
                  {pre}
                </div>
                <div className="fw-bold" id="pageTitle">
                  {title}
                </div>
              </div>
              <div className="navbar-nav flex-row align-items-center gap-2">
                <button
                  type="button"
                  className="stage-theme-toggle"
                  id="themeToggle"
                  onClick={toggle}
                  aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
                >
                  <span className="stage-theme-opt" data-theme-opt="light" aria-hidden="true">
                    <i className="ti ti-sun" />
                  </span>
                  <span className="stage-theme-opt" data-theme-opt="dark" aria-hidden="true">
                    <i className="ti ti-moon" />
                  </span>
                </button>
                {role === "member" && <NotificationBell />}
                <Dropdown.Root open={accountOpen} onOpenChange={setAccountOpen}>
                  <Dropdown.Trigger asChild>
                    <button
                      ref={accountTriggerRef}
                      type="button"
                      className="nav-link d-flex lh-1 p-0 px-2 align-items-center"
                      id="userMenuToggle"
                      aria-label="Open user menu"
                    >
                      {!isSupporter && <Avatar person={user} size="sm" showImage />}
                      {/* No avatar for supporters, so their name stays visible on
                          phones as the only thing to tap. */}
                      <div
                        className={`min-w-0 text-start${isSupporter ? " stage-account-plain" : " d-none d-md-block ps-2"}`}
                      >
                        <div className="text-truncate" id="whoami">
                          {user.name || user.email}
                        </div>
                        <div className="mt-1 small text-secondary" id="roleBadge">
                          {role}
                        </div>
                      </div>
                      <i className="ti ti-chevron-down stage-account-chevron" aria-hidden="true" />
                    </button>
                  </Dropdown.Trigger>
                  <DropdownContent className="stage-account-menu">
                    <Dropdown.Label className="dropdown-item-text px-3 py-2" id="userMenuHeader">
                      <div className="stage-account-menu-identity">
                        <div className="fw-bold text-truncate">{user.name || "Account"}</div>
                        <div className="text-secondary small text-truncate">{user.email}</div>
                        <div className="text-secondary small text-uppercase mt-1">{role}</div>
                        <div className="stage-menu-version">{version}</div>
                      </div>
                    </Dropdown.Label>
                    <Dropdown.Separator className="dropdown-divider" />
                    <DropdownItem onSelect={openSettings}>
                      <i className="ti ti-user-cog me-2" />
                      Profile settings
                    </DropdownItem>
                    <Dropdown.Separator className="dropdown-divider" />
                    <DropdownItem danger onSelect={() => void signOut()}>
                      <i className="ti ti-logout me-2" />
                      Sign out
                    </DropdownItem>
                  </DropdownContent>
                </Dropdown.Root>
              </div>
            </div>
          </header>
          <main className="page-body">
            <div className="container-xl">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
      {settings && (
        <Suspense fallback={null}>
          <AccountSettings
            user={user}
            hideAvatar={isSupporter}
            onClose={() => setSettings(false)}
            onNotice={(message, tone = "success") => notice.show(message, tone)}
            returnFocusRef={accountTriggerRef}
          />
        </Suspense>
      )}
      {notice.node}
      <SignInAgain email={user.email} />
    </UserContext.Provider>
  );
}
