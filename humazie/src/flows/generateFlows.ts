import type { HumazieConfig } from "../config.js";
import type { HumazieFlow, ProductMap } from "../types.js";
import { makeId, uniqueTaggedValue } from "../util/paths.js";

function baseFlow(
  partial: Omit<HumazieFlow, "id"> & { seed: string },
): HumazieFlow {
  const { seed, ...rest } = partial;
  return {
    id: makeId("flow", seed),
    ...rest,
  };
}

/**
 * Generate product-specific UI flows for Strata from the discovered map.
 */
export function generateFlows(
  map: ProductMap,
  config: HumazieConfig,
  options: { routeFilter?: string; mobile?: boolean; runId: string } = {
    runId: "local",
  },
): HumazieFlow[] {
  const runId = options.runId;
  const captureText = uniqueTaggedValue(runId, "Humazie capture idea");
  const flows: HumazieFlow[] = [];

  flows.push(
    baseFlow({
      seed: "app-loads",
      name: "Application loads successfully",
      userGoal: "Confirm Strata shell connects via the harness and shows the command bar.",
      preconditions: ["Humazie harness with fake bridge is available"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "expect_visible",
          description: "Brand is visible",
          text: "STRATA",
        },
        {
          type: "expect_visible",
          description: "Mode navigation is present",
          role: "navigation",
          name: "Application mode",
        },
      ],
      expectedResults: ["Command bar renders", "No boot error alert"],
      riskLevel: "critical",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/app/App.tsx", "frontend/src/humazie-entry.tsx"],
      category: "smoke",
    }),
  );

  flows.push(
    baseFlow({
      seed: "main-navigation-modes",
      name: "Main mode navigation works",
      userGoal: "Switch between Focus, Explore, Views, and Command without losing the shell.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Enter Focus mode",
          role: "button",
          name: "Focus",
        },
        {
          type: "expect_visible",
          description: "Focus remains pressed",
          role: "button",
          name: "Focus",
        },
        {
          type: "click",
          description: "Enter Explore mode",
          role: "button",
          name: "Explore",
        },
        {
          type: "click",
          description: "Enter Views mode",
          role: "button",
          name: "Views",
        },
        {
          type: "click",
          description: "Enter Command mode",
          role: "button",
          name: "Command",
        },
        {
          type: "expect_visible",
          description: "Shell still present",
          text: "STRATA",
        },
      ],
      expectedResults: ["Each mode button becomes active", "Shell remains usable"],
      riskLevel: "high",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: map.modes.map((m) => `#${m}`),
      relatedFiles: ["frontend/src/features/workspace/CommandBar.tsx"],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "capture-text-valid",
      name: "Capture text into the Inbox",
      userGoal:
        "Open Capture, enter a note with a reason, submit it, and verify the dialog closes.",
      preconditions: ["Writable layer available in fake bridge"],
      startingRoute: "/humazie.html",
      testData: {
        content: captureText,
        reason: "humazie product review",
      },
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Open Capture dialog",
          role: "button",
          name: "⇣ Capture",
        },
        {
          type: "expect_visible",
          description: "Capture dialog opens",
          role: "dialog",
          name: "Capture",
        },
        {
          type: "fill",
          description: "Enter capture content",
          label: "Capture content",
          value: captureText,
        },
        {
          type: "fill",
          description: "Enter capture reason",
          placeholder: "e.g. relevant to the launch decision",
          value: "humazie product review",
        },
        {
          type: "click",
          description: "Submit capture",
          role: "button",
          name: "Capture",
          exact: true,
          withinRole: "dialog",
          withinName: "Capture",
        },
        {
          type: "expect_hidden",
          description: "Dialog dismisses after success",
          role: "dialog",
          name: "Capture",
        },
      ],
      expectedResults: ["Dialog closes", "No alert error"],
      riskLevel: "high",
      safeToExecute: true,
      cleanup: ["Created captures are tagged with run ID in content"],
      relatedRoutes: ["#focus"],
      relatedFiles: ["frontend/src/features/capture/CaptureDialog.tsx"],
      category: "forms",
    }),
  );

  flows.push(
    baseFlow({
      seed: "capture-rejects-empty",
      name: "Capture rejects empty content",
      userGoal: "Confirm the Capture submit control stays disabled with nothing to send.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Open Capture",
          role: "button",
          name: "⇣ Capture",
        },
        {
          type: "expect_visible",
          description: "Dialog open",
          role: "dialog",
          name: "Capture",
        },
        {
          type: "custom",
          description: "Assert primary Capture submit is disabled when empty",
        },
      ],
      expectedResults: ["Submit stays disabled until content exists"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#focus"],
      relatedFiles: ["frontend/src/features/capture/CaptureDialog.tsx"],
      category: "form_validation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "health-dialog-open-close",
      name: "Health dialog opens and dismisses",
      userGoal: "Open the knowledge health dialog and close it again.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Open Health",
          role: "button",
          name: "◉ Health",
        },
        {
          type: "expect_visible",
          description: "Health dialog visible",
          role: "dialog",
          name: "Knowledge health",
        },
        {
          type: "click",
          description: "Close Health dialog",
          role: "button",
          name: "Close",
        },
        {
          type: "expect_hidden",
          description: "Health dialog dismissed",
          role: "dialog",
          name: "Knowledge health",
        },
      ],
      expectedResults: ["Dialog opens", "Escape or close dismisses it"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/health/HealthDialog.tsx"],
      category: "dialogs",
    }),
  );

  flows.push(
    baseFlow({
      seed: "navigator-toggle",
      name: "Navigator drawer toggles",
      userGoal: "Collapse and expand the navigator without breaking the workspace.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Toggle navigator",
          role: "button",
          name: "Toggle the navigator",
        },
        {
          type: "expect_visible",
          description: "Shell remains",
          text: "STRATA",
        },
        {
          type: "click",
          description: "Toggle navigator back",
          role: "button",
          name: "Toggle the navigator",
        },
      ],
      expectedResults: ["Navigator can hide and show"],
      riskLevel: "low",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/app/App.tsx"],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "graph-dimension-toggle",
      name: "Switch graph dimension between 2D and 3D",
      userGoal: "Toggle the graph dimension controls and keep the shell responsive.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Select Explore",
          role: "button",
          name: "Explore",
        },
        {
          type: "click",
          description: "Choose 2D",
          role: "button",
          name: "2D",
        },
        {
          type: "click",
          description: "Choose 3D",
          role: "button",
          name: "3D",
        },
        {
          type: "expect_visible",
          description: "Brand still visible",
          text: "STRATA",
        },
      ],
      expectedResults: ["Dimension buttons respond", "No crash overlay"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/workspace/CommandBar.tsx"],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "inspector-tabs",
      name: "Inspector tabs switch panels",
      userGoal: "Move between AI, Changes, Properties, and Links inspector tabs.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Open AI tab",
          role: "tab",
          name: "AI",
        },
        {
          type: "click",
          description: "Open Changes tab",
          role: "tab",
          name: "Changes",
        },
        {
          type: "click",
          description: "Open Properties tab",
          role: "tab",
          name: "Properties",
        },
        {
          type: "click",
          description: "Open Links tab",
          role: "tab",
          name: "Links",
        },
      ],
      expectedResults: ["Each tab activates without error"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#command"],
      relatedFiles: ["frontend/src/app/App.tsx"],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "keyboard-mode-presence",
      name: "Important controls expose accessible names",
      userGoal: "Verify primary controls are reachable by accessible name for keyboard users.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "expect_visible",
          description: "Capture has accessible name",
          role: "button",
          name: "⇣ Capture",
        },
        {
          type: "expect_visible",
          description: "Health has accessible name",
          role: "button",
          name: "◉ Health",
        },
        {
          type: "axe",
          description: "Run accessibility scan on shell",
        },
      ],
      expectedResults: ["No critical/serious axe violations on the shell"],
      riskLevel: "high",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/workspace/CommandBar.tsx"],
      category: "accessibility",
    }),
  );

  flows.push(
    baseFlow({
      seed: "console-clean-on-load",
      name: "Browser console stays clean on load",
      userGoal: "Load the harness and confirm no unexpected console errors appear.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "expect_visible",
          description: "Shell loaded",
          text: "STRATA",
        },
        {
          type: "custom",
          description: "Assert no unexpected console errors were collected",
        },
      ],
      expectedResults: ["No unexpected console errors"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/main.tsx", "frontend/src/humazie-entry.tsx"],
      category: "stability",
    }),
  );

  flows.push(
    baseFlow({
      seed: "search-workspace",
      name: "Search the workspace and see results panel",
      userGoal: "Type a query in Search and confirm the search surface stays usable.",
      preconditions: ["App loaded", "Fake bridge search available"],
      startingRoute: "/humazie.html",
      testData: { query: "Encryption" },
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "fill",
          description: "Type a search query",
          label: "Search the workspace",
          value: "Encryption",
        },
        {
          type: "expect_visible",
          description: "Search section remains visible",
          role: "region",
          name: "Search",
        },
      ],
      expectedResults: ["Search input accepts text", "Search panel stays on screen"],
      riskLevel: "high",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/search/SearchPanel.tsx"],
      category: "forms",
    }),
  );

  flows.push(
    baseFlow({
      seed: "views-type-switch",
      name: "Switch structured view types",
      userGoal: "Open Views mode and move between Table, Cards, and Kanban layouts.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Enter Views mode",
          role: "button",
          name: "Views",
        },
        {
          type: "expect_visible",
          description: "Views stage is present",
          role: "region",
          name: "Structured views",
        },
        {
          type: "click",
          description: "Open Cards view",
          role: "tab",
          name: "Cards",
        },
        {
          type: "click",
          description: "Open Kanban view",
          role: "tab",
          name: "Kanban",
        },
        {
          type: "click",
          description: "Return to Table view",
          role: "tab",
          name: "Table",
        },
      ],
      expectedResults: ["View type tabs respond", "Stage remains usable"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#views"],
      relatedFiles: ["frontend/src/features/views/ViewsStage.tsx"],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "create-layer-dialog-cancel",
      name: "Open New layer dialog and cancel",
      userGoal: "Open the create-layer dialog, glance at the form, then cancel without creating.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Open New layer dialog",
          title: "New layer",
        },
        {
          type: "expect_visible",
          description: "New layer dialog opens",
          role: "dialog",
          name: "New layer",
        },
        {
          type: "fill",
          description: "Type a draft layer name",
          label: "Name",
          value: uniqueTaggedValue(runId, "Scratch layer"),
        },
        {
          type: "click",
          description: "Cancel without creating",
          role: "button",
          name: "Cancel",
          withinRole: "dialog",
          withinName: "New layer",
        },
        {
          type: "expect_hidden",
          description: "Dialog closes",
          role: "dialog",
          name: "New layer",
        },
      ],
      expectedResults: ["Dialog opens", "Cancel dismisses without creating a layer"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/layers/CreateLayerDialog.tsx"],
      category: "dialogs",
    }),
  );

  flows.push(
    baseFlow({
      seed: "layer-ai-policy-select",
      name: "Change a layer AI policy from the Layers panel",
      userGoal: "Open the Layers panel AI policy for Knowledge and switch it to local only.",
      preconditions: ["App loaded with a public layer"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "expect_visible",
          description: "Layers panel is present",
          role: "region",
          name: "Layers",
        },
        {
          type: "custom",
          description: "Set Knowledge AI policy to local only",
        },
        {
          type: "expect_visible",
          description: "Layers panel still visible",
          role: "region",
          name: "Layers",
        },
      ],
      expectedResults: ["AI policy select accepts a change without crashing"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/layers/LayerPanel.tsx"],
      category: "forms",
    }),
  );

  flows.push(
    baseFlow({
      seed: "motion-preference-toggle",
      name: "Toggle reduced motion preference",
      userGoal: "Flip Motion between full and reduced and confirm the control updates.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Switch to reduced motion",
          role: "button",
          name: "Motion: full",
        },
        {
          type: "expect_visible",
          description: "Control shows reduced",
          role: "button",
          name: "Motion: reduced",
        },
        {
          type: "click",
          description: "Restore full motion",
          role: "button",
          name: "Motion: reduced",
        },
        {
          type: "expect_visible",
          description: "Control shows full again",
          role: "button",
          name: "Motion: full",
        },
      ],
      expectedResults: ["Motion toggle reflects the active preference"],
      riskLevel: "low",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: ["frontend/src/features/workspace/CommandBar.tsx"],
      category: "accessibility",
    }),
  );

  flows.push(
    baseFlow({
      seed: "inspector-drawer-toggle",
      name: "Inspector drawer toggles",
      userGoal: "Collapse and expand the inspector without breaking the workspace.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Toggle inspector",
          role: "button",
          name: "Toggle the inspector",
        },
        {
          type: "expect_visible",
          description: "Shell remains",
          text: "STRATA",
        },
        {
          type: "click",
          description: "Toggle inspector back",
          role: "button",
          name: "Toggle the inspector",
        },
      ],
      expectedResults: ["Inspector can hide and show"],
      riskLevel: "low",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#command"],
      relatedFiles: ["frontend/src/app/App.tsx"],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "capture-url-mode-validation",
      name: "Capture URL mode keeps Import disabled until a URL exists",
      userGoal: "Switch Capture to From URL and confirm Import stays disabled while empty.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Open Capture",
          role: "button",
          name: "⇣ Capture",
        },
        {
          type: "click",
          description: "Switch to From URL",
          role: "button",
          name: "From URL",
        },
        {
          type: "expect_visible",
          description: "URL field is shown",
          label: "Page URL",
        },
        {
          type: "custom",
          description: "Assert Import page is disabled when empty",
        },
      ],
      expectedResults: ["URL mode requires a URL before Import is enabled"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#focus"],
      relatedFiles: ["frontend/src/features/capture/CaptureDialog.tsx"],
      category: "form_validation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "focus-mode-editor-surface",
      name: "Focus mode shows the editor workspace",
      userGoal: "Enter Focus mode and confirm the workspace stage is ready for notes.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "click",
          description: "Enter Focus mode",
          role: "button",
          name: "Focus",
        },
        {
          type: "expect_visible",
          description: "Workspace stage is present",
          role: "main",
          name: "Workspace",
        },
        {
          type: "click",
          description: "Open Properties inspector",
          role: "tab",
          name: "Properties",
        },
      ],
      expectedResults: ["Focus mode activates", "Inspector can show Properties"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#focus"],
      relatedFiles: [
        "frontend/src/features/editor/EditorPane.tsx",
        "frontend/src/app/App.tsx",
      ],
      category: "navigation",
    }),
  );

  flows.push(
    baseFlow({
      seed: "files-panel-visible",
      name: "Files panel is reachable in the navigator",
      userGoal: "Confirm the Files section is visible so notes can be opened from the tree.",
      preconditions: ["App loaded"],
      startingRoute: "/humazie.html",
      testData: {},
      actions: [
        { type: "goto", description: "Open harness", url: "/humazie.html" },
        {
          type: "expect_visible",
          description: "Files panel is present",
          role: "region",
          name: "Files",
        },
        {
          type: "expect_visible",
          description: "Layers panel is present",
          role: "region",
          name: "Layers",
        },
      ],
      expectedResults: ["Navigator exposes Files and Layers"],
      riskLevel: "medium",
      safeToExecute: true,
      cleanup: [],
      relatedRoutes: ["#explore"],
      relatedFiles: [
        "frontend/src/features/explorer/FileTree.tsx",
        "frontend/src/features/layers/LayerPanel.tsx",
      ],
      category: "navigation",
    }),
  );

  if (options.mobile) {
    flows.push(
      baseFlow({
        seed: "mobile-shell-usable",
        name: "Mobile layout remains usable",
        userGoal: "On a phone viewport, open Capture and confirm the dialog is usable.",
        preconditions: ["Mobile viewport"],
        startingRoute: "/humazie.html",
        testData: {},
        actions: [
          { type: "goto", description: "Open harness", url: "/humazie.html" },
          {
            type: "click",
            description: "Open Capture",
            role: "button",
            name: "⇣ Capture",
          },
          {
            type: "expect_visible",
            description: "Dialog visible on mobile",
            role: "dialog",
            name: "Capture",
          },
          {
            type: "custom",
            description: "Assert no horizontal overflow on body",
          },
        ],
        expectedResults: ["Dialog usable", "No forced horizontal scroll"],
        riskLevel: "medium",
        safeToExecute: true,
        cleanup: [],
        relatedRoutes: ["#focus"],
        relatedFiles: ["frontend/src/app/App.tsx", "frontend/src/app/shell.css"],
        category: "responsive",
      }),
    );
  }

  // Route filter: #explore style or path fragments
  let filtered = flows;
  if (options.routeFilter) {
    const needle = options.routeFilter.toLowerCase();
    filtered = flows.filter(
      (flow) =>
        flow.startingRoute.toLowerCase().includes(needle) ||
        flow.relatedRoutes.some((r) => r.toLowerCase().includes(needle)) ||
        flow.name.toLowerCase().includes(needle.replace(/^\//, "")),
    );
    if (filtered.length === 0) filtered = flows.slice(0, 3);
  }

  return filtered.slice(0, config.maxFlows);
}
