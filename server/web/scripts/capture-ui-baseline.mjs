import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).map((arg) => {
    const [key, ...value] = arg.replace(/^--/, "").split("=");
    return [key, value.join("=")];
  }),
);

const baseURL = args.base;
const implementation = args.implementation || "current";
const outputRoot = args.output;
const focusedOnly = args.focused === "only";
const executablePath =
  args.browser || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

if (!baseURL || !outputRoot) {
  throw new Error(
    "Usage: npm run baseline:capture -- --base=http://127.0.0.1:5173 " +
      "--implementation=current --output=<directory> [--browser=<path>]",
  );
}

const viewports = {
  compact: { width: 320, height: 800 },
  mobile: { width: 390, height: 844 },
  tablet: { width: 768, height: 1024 },
  desktop: { width: 1440, height: 1000 },
  wide: { width: 1920, height: 1080 },
};

const roles = ["admin", "manager", "member", "supporter"];
const themes = ["light", "dark"];
const captureRoles = args.roles ? args.roles.split(",").filter((role) => roles.includes(role)) : roles;
const captureThemes = args.themes
  ? args.themes.split(",").filter((theme) => themes.includes(theme))
  : themes;
const captureViewportNames = args.viewports
  ? args.viewports.split(",").filter((name) => name in viewports)
  : Object.keys(viewports);
const captureRoutes = args.routes ? args.routes.split(",") : null;
const routes = {
  admin: [
    "dashboard",
    "interviews",
    "workplan",
    "performance",
    "users",
    "profiles",
    "licenses",
    "sync",
    "errors",
    "telemetry",
  ],
  manager: [
    "dashboard",
    "interviews",
    "workplan",
    "performance",
    "users",
    "profiles",
    "licenses",
    "sync",
    "errors",
    "telemetry",
  ],
  member: ["dashboard", "interviews", "workplan", "performance", "colleagues"],
  supporter: ["dashboard", "interviews", "performance"],
};

const referencePages = {
  dashboard: "dashboard.html",
  interviews: "interviews.html",
  workplan: "workplan.html",
  performance: "performance.html",
  colleagues: "colleagues.html",
  users: "users.html",
  profiles: "profiles.html",
  licenses: "licenses.html",
  sync: "sync.html",
  errors: "errors.html",
  telemetry: "telemetry.html",
};

const now = new Date("2026-08-14T15:00:00Z");
const users = roles.map((role, index) => ({
  id: `baseline-${role}`,
  email: `${role}.baseline@example.com`,
  name:
    role === "manager"
      ? "Manager With A Deliberately Long Display Name"
      : `${role[0].toUpperCase()}${role.slice(1)} Baseline`,
  role,
  avatar_url: index === 0 ? "/careercraft_icon.png" : null,
  is_active: true,
}));

const interviews = [
  {
    id: "baseline-interview-1",
    user_id: "baseline-member",
    user_name: "Member Baseline",
    user_email: "member.baseline@example.com",
    profile_id: "baseline-profile-1",
    profile_key: "baseline-profile",
    profile_name: "Baseline Candidate",
    title: "Technical interview",
    company_name: "Northwind Analytics",
    position_title: "Senior Software Engineer",
    start_time_utc: "2026-08-14T16:00:00Z",
    end_time_utc: "2026-08-14T17:00:00Z",
    timezone: "America/Chicago",
    interview_type: "Technical",
    status: "scheduled",
    interviewer_name: "Jordan Lee",
    location: "Video call",
    notes: "Deterministic baseline event",
  },
];

function responseFor(url, role) {
  const path = new URL(url).pathname;
  const me = users.find((user) => user.role === role);
  if (path === "/meta") return { service: "baseline", version: "baseline" };
  // Signed in: the page renews its session from the cookie on load.
  if (path.endsWith("/auth/refresh")) {
    return {
      access_token: "baseline-token",
      token_type: "bearer",
      expires_in: 900,
      user_id: me.id,
    };
  }
  if (path.endsWith("/users/me")) return me;
  if (path.includes("/interviews")) return interviews;
  if (path.includes("/admin/api/users")) return users;
  if (path.includes("/admin/profiles")) {
    return [
      {
        id: "baseline-profile-1",
        key: "baseline-profile",
        name: "Baseline Candidate",
        email: "candidate@example.com",
        location: "Chicago, IL",
        assigned_user_ids: ["baseline-member", "baseline-manager"],
      },
    ];
  }
  if (path.includes("/license")) return [];
  if (path.includes("/team/colleagues")) return { colleagues: users.slice(1, 3) };
  if (path.includes("/team/applications") || path.includes("/admin/api/applications")) {
    return { applications: [] };
  }
  if (path.includes("/tracking/metrics")) {
    return {
      totals_in_range: { applications: 12, interviews: 4 },
      per_members: [],
      per_supporters: [],
      series: { applications: [1, 3, 2, 6], interviews: [0, 1, 1, 2] },
    };
  }
  if (path.includes("/team/observability")) {
    return { totals: { applications: 12, interviews: 4 }, series: {} };
  }
  if (path.includes("/profiles/geo")) {
    return { mapped: 2, total_profiles: 2, states: [{ state: "IL", count: 2 }] };
  }
  if (path.includes("/admin/api/overview")) {
    return { users: 4, profiles: 2, clients: 3, errors_24h: 0 };
  }
  if (path.includes("/work-plans")) return { week: [], month: [], quarter: [], items: [] };
  if (path.includes("/sync/activity")) return { items: [], totals: {} };
  if (path.includes("/errors")) return { items: [], total: 0 };
  if (path.includes("/telemetry")) return { items: [], recent: [], summary: {} };
  return {};
}

function pagePath(route) {
  if (implementation === "reference") return `/${referencePages[route]}`;
  return `/${route}`;
}

const browser = await chromium.launch({ executablePath, headless: true });
let captures = 0;

try {
  if (!focusedOnly)
    for (const role of captureRoles) {
      for (const theme of captureThemes) {
        for (const viewportName of captureViewportNames) {
          const viewport = viewports[viewportName];
          const context = await browser.newContext({
            viewport,
            deviceScaleFactor: 1,
            locale: "en-US",
            timezoneId: "America/Chicago",
            reducedMotion: "reduce",
          });
          await context.addInitScript(
            ({ selectedTheme }) => {
              localStorage.setItem("baymax_stage_theme", selectedTheme);
              Date.now = () => new Date("2026-08-14T15:00:00Z").valueOf();
            },
            { selectedTheme: theme },
          );
          await context.route("**/*", async (routeHandler) => {
            const request = routeHandler.request();
            const pathname = new URL(request.url()).pathname;
            if (pathname === "/meta" || pathname.startsWith("/api/v1/")) {
              await routeHandler.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify(responseFor(request.url(), role)),
              });
              return;
            }
            await routeHandler.continue();
          });

          const page = await context.newPage();
          await page
            .addStyleTag({
              content: "*,*::before,*::after{animation:none!important;transition:none!important}",
            })
            .catch(() => {});

          for (const route of (captureRoutes || routes[role]).filter((route) =>
            routes[role].includes(route),
          )) {
            const destination = new URL(pagePath(route), baseURL).href;
            await page.goto(destination, { waitUntil: "networkidle" });
            await page.waitForTimeout(150);
            const directory = join(outputRoot, implementation, role, theme, viewportName);
            await mkdir(directory, { recursive: true });
            await page.screenshot({
              path: join(directory, `${route}--baseline.png`),
              fullPage: true,
            });
            captures += 1;
          }
          await context.close();
        }
      }
    }

  for (const theme of themes) {
    for (const viewportName of ["desktop", "mobile"]) {
      const context = await browser.newContext({
        viewport: viewports[viewportName],
        locale: "en-US",
        timezoneId: "America/Chicago",
        reducedMotion: "reduce",
      });
      await context.addInitScript(
        ({ selectedTheme }) => {
          localStorage.setItem("baymax_stage_theme", selectedTheme);
        },
        { selectedTheme: theme },
      );
      await context.route("**/*", async (routeHandler) => {
        const request = routeHandler.request();
        const pathname = new URL(request.url()).pathname;
        if (pathname === "/meta" || pathname.startsWith("/api/v1/")) {
          await routeHandler.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(responseFor(request.url(), "admin")),
          });
          return;
        }
        await routeHandler.continue();
      });
      const page = await context.newPage();
      await page.goto(new URL(pagePath("dashboard"), baseURL).href, { waitUntil: "networkidle" });
      const directory = join(outputRoot, implementation, "focused", theme, viewportName);
      await mkdir(directory, { recursive: true });

      // `force` also records layouts where another fixed element incorrectly
      // intercepts the header trigger; that overlap is itself baseline evidence.
      if (implementation === "current") {
        await page.locator("#userMenuToggle").click();
      } else {
        await page.locator("#userMenuToggle").evaluate((element) => element.click());
      }
      await page.waitForTimeout(100);
      await page.screenshot({ path: join(directory, "account-menu--open.png"), fullPage: true });
      captures += 1;

      const settingsSelector =
        implementation === "reference" ? "#profileSettingsBtn" : "text=Profile settings";
      if (implementation === "current") {
        await page.locator(settingsSelector).click();
      } else {
        await page.locator(settingsSelector).evaluate((element) => element.click());
      }
      await page.getByRole("dialog").waitFor({ state: "visible" });
      await page.screenshot({
        path: join(directory, "profile-settings--open.png"),
        fullPage: true,
      });
      captures += 1;

      if (viewportName === "mobile") {
        const closeSelector =
          implementation === "reference"
            ? "#profileSettingsModal .btn-close"
            : ".modal.show .btn-close";
        await page.locator(closeSelector).evaluate((element) => element.click());
        await page.locator(".navbar-toggler").evaluate((element) => element.click());
        await page.screenshot({ path: join(directory, "navigation--open.png"), fullPage: true });
        captures += 1;
      }
      await context.close();
    }
  }

  if (focusedOnly) {
    const context = await browser.newContext({ viewport: viewports.desktop });
    await context.route("**/api/v1/auth/login", async (routeHandler) => {
      await new Promise((resolve) => setTimeout(resolve, 800));
      await routeHandler.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid credentials" }),
      });
    });
    const page = await context.newPage();
    await page.goto(
      new URL(implementation === "reference" ? "/login.html" : "/login", baseURL).href,
      { waitUntil: "networkidle" },
    );
    await page.locator("#email").fill("invalid.baseline@example.com");
    await page.locator("#password").fill("incorrect-password");
    const directory = join(outputRoot, implementation, "public", "light", "desktop");
    await mkdir(directory, { recursive: true });
    await page.locator("#loginForm").evaluate((form) => form.requestSubmit());
    await page.waitForTimeout(100);
    await page.screenshot({ path: join(directory, "login--submitting.png"), fullPage: true });
    captures += 1;
    await page.waitForTimeout(900);
    await page.screenshot({ path: join(directory, "login--invalid.png"), fullPage: true });
    captures += 1;
    await context.close();
  }

  if (!focusedOnly) {
    const loginContext = await browser.newContext({ viewport: viewports.desktop });
    const loginPage = await loginContext.newPage();
    await loginPage.goto(
      new URL(implementation === "reference" ? "/login.html" : "/login", baseURL).href,
      { waitUntil: "networkidle" },
    );
    await loginPage.evaluate(() => localStorage.clear());
    await loginPage.reload({ waitUntil: "networkidle" });
    const loginDirectory = join(outputRoot, implementation, "public", "light", "desktop");
    await mkdir(loginDirectory, { recursive: true });
    await loginPage.screenshot({ path: join(loginDirectory, "login--empty.png"), fullPage: true });
    captures += 1;
    await loginContext.close();
  }
} finally {
  await browser.close();
}

console.log(
  JSON.stringify({ implementation, captures, outputRoot, capturedAt: now.toISOString() }),
);
