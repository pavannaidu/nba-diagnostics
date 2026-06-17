import { FormEvent, useEffect, useMemo, useState } from "react";
import { getJson, postJson } from "./api";
import type {
  AgentModelOption,
  AgentTarget,
  EditableVisitInput,
  EvidenceSource,
  EvidenceSourceLink,
  EvidenceView,
  HealthResponse,
  PanelKey,
  RecentTestOverride,
  RecommendationRun,
  SampleCase,
} from "./types";

type CollapsedPanelRailProps = {
  kicker: string;
  title: string;
  detail: string;
  badgeLabel?: string;
  badgeClassName?: string;
  onExpand: () => void;
};

const DEFAULT_OVERRIDE_TESTS = [
  "WELLNESS_PANEL",
  "GI_PANEL",
  "URINALYSIS",
  "FRUCTOSAMINE",
];

const TEST_LABELS: Record<string, string> = {
  WELLNESS_PANEL: "Adult Wellness Panel",
  HEARTWORM_4DX: "SNAP 4Dx Plus",
  FECAL_SCREEN: "Fecal Ova and Parasite Screen",
  GI_PANEL: "Canine GI Laboratory Panel",
  FECAL_PCR: "Canine Diarrhea RealPCR Panel",
  URINALYSIS: "Complete Urinalysis",
  URINE_CULTURE: "Urine Culture and Sensitivity",
  SDMA_CHEM17: "SDMA with Chemistry 17",
  UPC_RATIO: "Urine Protein Creatinine Ratio",
  THYROID_PANEL: "Canine Thyroid Panel",
  FRUCTOSAMINE: "Fructosamine",
  CORTISOL_SCREEN: "Resting Cortisol Screen",
};

const EVIDENCE_SOURCE_LABELS: Record<string, string> = {
  patient_history: "Patient history",
  patient_intake: "Patient intake",
  duplicate_test_audit: "Duplicate test audit",
  test_metadata: "Test metadata",
  similar_case_genie: "Similar cases",
  diagnostic_guidance_ka: "Diagnostic guidance",
  query_pubmed: "PubMed literature",
};

const BOOLEAN_FIELD_LABELS: Array<[keyof EditableVisitInput, string]> = [
  ["vomiting", "vomiting"],
  ["diarrhea", "diarrhea"],
  ["urinary_accidents", "urinary accidents"],
  ["straining_to_urinate", "straining to urinate"],
  ["increased_thirst", "increased thirst"],
  ["increased_hunger", "increased hunger"],
  ["lethargy", "lethargy"],
  ["recent_diet_change", "recent diet change"],
  ["previous_same_issue", "previous same issue"],
];

const VALUE_FIELD_LABELS: Array<[keyof EditableVisitInput, string]> = [
  ["patient_id", "patient"],
  ["dog_name", "dog name"],
  ["breed", "breed"],
  ["age_years", "age"],
  ["life_stage", "life stage"],
  ["as_of_ts", "visit time"],
  ["presenting_complaint", "presenting complaint"],
  ["severity", "severity"],
  ["urgency_level", "urgency"],
  ["cohort_hint", "cohort hint"],
  ["owner_note", "owner note"],
  ["clinician_note", "clinician note"],
  ["appetite_change", "appetite change"],
  ["weight_change_pct", "weight change"],
  ["duration_days", "duration"],
];

function createDefaultOverrides(testCodes: string[] = DEFAULT_OVERRIDE_TESTS): RecentTestOverride[] {
  return testCodes.map((testCode) => ({
    test_code: testCode,
    days_since_same_test: null,
  }));
}

function createBlankIntake(): EditableVisitInput {
  return {
    patient_id: "DOG-NEW-0001",
    seed_visit_id: null,
    dog_name: "",
    breed: "",
    age_years: null,
    life_stage: null,
    as_of_ts: new Date().toISOString().slice(0, 10),
    presenting_complaint: "",
    severity: "moderate",
    urgency_level: "routine",
    owner_note: "",
    clinician_note: "",
    cohort_hint: null,
    vomiting: false,
    diarrhea: false,
    urinary_accidents: false,
    straining_to_urinate: false,
    increased_thirst: false,
    increased_hunger: false,
    lethargy: false,
    recent_diet_change: false,
    previous_same_issue: false,
    appetite_change: "stable",
    weight_change_pct: 0,
    duration_days: 0,
    recent_test_overrides: createDefaultOverrides(),
  };
}

function cloneIntake(input: EditableVisitInput): EditableVisitInput {
  return {
    ...input,
    recent_test_overrides: input.recent_test_overrides.map((override) => ({ ...override })),
  };
}

function toTitleCase(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function classNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

function formatLatency(durationMs?: number | null): string {
  if (durationMs === undefined || durationMs === null) {
    return "Timing unavailable";
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`;
  }
  return `${(durationMs / 1000).toFixed(1)} s`;
}

function formatAge(age?: number | null): string {
  if (age === undefined || age === null) {
    return "Age not captured";
  }
  return `${age.toFixed(1).replace(/\.0$/, "")} years`;
}

function formatDays(days?: number | null): string {
  if (days === undefined || days === null) {
    return "Recency unavailable";
  }
  return `${days} days ago`;
}

function testLabel(testCode: string): string {
  return TEST_LABELS[testCode] ?? toTitleCase(testCode.toLowerCase());
}

function normalizeComparableValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).trim();
}

function diffIntakes(left: EditableVisitInput | null, right: EditableVisitInput): string[] {
  if (!left) {
    return [];
  }

  const changed: string[] = [];
  for (const [field, label] of VALUE_FIELD_LABELS) {
    const leftValue = normalizeComparableValue(left[field]);
    const rightValue = normalizeComparableValue(right[field]);
    if (leftValue !== rightValue) {
      changed.push(label);
    }
  }

  for (const [field, label] of BOOLEAN_FIELD_LABELS) {
    if (Boolean(left[field]) !== Boolean(right[field])) {
      changed.push(label);
    }
  }

  const leftOverrides = new Map(
    left.recent_test_overrides.map((override) => [override.test_code, override.days_since_same_test ?? null])
  );
  const rightOverrides = new Map(
    right.recent_test_overrides.map((override) => [override.test_code, override.days_since_same_test ?? null])
  );
  const overrideCodes = new Set([...leftOverrides.keys(), ...rightOverrides.keys()]);
  for (const code of overrideCodes) {
    if ((leftOverrides.get(code) ?? null) !== (rightOverrides.get(code) ?? null)) {
      changed.push(`${testLabel(code)} recency`);
    }
  }

  return changed;
}

function fieldAttrs(field: string): { id: string; name: string } {
  return {
    id: `intake-${field.replace(/_/g, "-")}`,
    name: field,
  };
}

function overrideFieldAttrs(testCode: string): { id: string; name: string } {
  const normalized = testCode.toLowerCase().replace(/_/g, "-");
  return {
    id: `override-${normalized}`,
    name: `override_${normalized}`,
  };
}

function ensureTerminalPunctuation(value: string): string {
  return /[.!?]$/.test(value) ? value : `${value}.`;
}

function cleanClinicalText(value: string | null | undefined): string {
  if (!value) {
    return "";
  }

  const withReadableCodes = value.replace(/\b[A-Z][A-Z0-9_]{3,}\b/g, (token) => {
    if (TEST_LABELS[token]) {
      return TEST_LABELS[token];
    }
    if (token.includes("_")) {
      return toTitleCase(token.toLowerCase());
    }
    return token;
  });

  return withReadableCodes
    .replace(/\bsuppressed\s*:\s*false\b/gi, "not suppressed")
    .replace(/\bsuppressed\s*:\s*true\b/gi, "suppressed")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeDisplayList(values: string[]): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();

  for (const value of values) {
    const cleaned = cleanClinicalText(value);
    if (!cleaned) {
      continue;
    }
    const key = cleaned.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    normalized.push(cleaned);
  }

  return normalized;
}

function splitNarrativePoints(value: string, maxItems = 3): string[] {
  const cleaned = cleanClinicalText(value);
  if (!cleaned) {
    return [];
  }

  const normalized = cleaned.replace(/\s*;\s*/g, ". ");
  const sentences = normalized
    .split(/(?<=[.!?])\s+/)
    .map((item) => item.trim())
    .filter(Boolean);

  if (sentences.length > 1) {
    return normalizeDisplayList(sentences.map((item) => ensureTerminalPunctuation(item))).slice(0, maxItems);
  }

  return [ensureTerminalPunctuation(cleaned)];
}

function parseDuplicateAuditPoints(summary: string): string[] {
  const cleaned = cleanClinicalText(summary);
  if (!cleaned) {
    return [];
  }

  const noSuppressionMatch = cleaned.match(/^(.*?)\s+all show not suppressed\b/i);
  if (noSuppressionMatch?.[1]) {
    return [ensureTerminalPunctuation(`No recent duplicate-testing block for ${noSuppressionMatch[1].trim()}`)];
  }

  const suppressionMatch = cleaned.match(/^(.*?)\s+all show suppressed\b/i);
  if (suppressionMatch?.[1]) {
    return [ensureTerminalPunctuation(`Recent duplicate-testing block applied to ${suppressionMatch[1].trim()}`)];
  }

  return splitNarrativePoints(cleaned, 3);
}

function formatEvidenceLinkLabel(link: EvidenceSourceLink): string {
  const label = cleanClinicalText(link.label);
  if (label) {
    return label;
  }
  if (link.pmid) {
    return `PMID ${link.pmid}`;
  }
  return "PubMed article";
}

function buildEvidenceViews(sources: EvidenceSource[]): EvidenceView[] {
  return sources
    .map((source, index) => {
      const eyebrow = EVIDENCE_SOURCE_LABELS[source.source_type] ?? toTitleCase(source.source_type);
      const title = cleanClinicalText(source.source_label) || eyebrow;
      const cleanedSummary = cleanClinicalText(source.summary);

      let points: string[] = [];
      if (source.source_type === "duplicate_test_audit") {
        points = parseDuplicateAuditPoints(cleanedSummary);
      } else if (source.source_type === "test_metadata") {
        points = splitNarrativePoints(cleanedSummary.replace(/\s+and indicated for\s+/i, ". Appropriate for "), 3);
      } else {
        points = splitNarrativePoints(cleanedSummary, 3);
      }

      return {
        key: `${source.source_type}-${index}`,
        eyebrow,
        title,
        points,
        links: source.links ?? [],
      };
    })
    .filter((source) => source.points.length > 0 || source.links.length > 0);
}

function CollapsedPanelRail({
  kicker,
  title,
  detail,
  badgeLabel,
  badgeClassName,
  onExpand,
}: CollapsedPanelRailProps) {
  return (
    <div className="panel-rail">
      <div className="panel-rail-copy">
        <span className="callout-label">{kicker}</span>
        <strong>{title}</strong>
        <span className="panel-rail-detail">{detail}</span>
      </div>
      <div className="panel-rail-actions">
        {badgeLabel ? <span className={classNames("status-chip", badgeClassName)}>{badgeLabel}</span> : null}
        <button type="button" className="panel-toggle-button" onClick={onExpand}>
          Expand
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [sampleCases, setSampleCases] = useState<SampleCase[]>([]);
  const [selectedSeed, setSelectedSeed] = useState<SampleCase | null>(null);
  const [formState, setFormState] = useState<EditableVisitInput>(createBlankIntake());
  const [submittedIntake, setSubmittedIntake] = useState<EditableVisitInput | null>(null);
  const [submittedAgentTargetId, setSubmittedAgentTargetId] = useState<string | null>(null);
  const [submittedCustomAgentModelId, setSubmittedCustomAgentModelId] = useState<string | null>(null);
  const [selectedAgentTargetId, setSelectedAgentTargetId] = useState("supervisor");
  const [selectedCustomAgentModelId, setSelectedCustomAgentModelId] = useState("databricks-claude-sonnet-4-5");
  const [runResult, setRunResult] = useState<RecommendationRun | null>(null);
  const [supportingEvidenceExpanded, setSupportingEvidenceExpanded] = useState(false);
  const [decisionReviewExpanded, setDecisionReviewExpanded] = useState(false);
  const [traceExpanded, setTraceExpanded] = useState(false);
  const [loadingBoot, setLoadingBoot] = useState(true);
  const [runningRecommendation, setRunningRecommendation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panelState, setPanelState] = useState<Record<PanelKey, boolean>>({
    intake: true,
    recommendation: true,
  });

  useEffect(() => {
    const load = async () => {
      try {
        const [healthPayload, samplePayload] = await Promise.all([
          getJson<HealthResponse>("/api/health"),
          getJson<{ items: SampleCase[] }>("/api/sample-cases"),
        ]);
        setHealth(healthPayload);
        const defaultTarget =
          healthPayload.agent_targets.find((target) => target.id === healthPayload.default_agent_target_id && target.available) ??
          healthPayload.agent_targets.find((target) => target.available) ??
          healthPayload.agent_targets.find((target) => target.id === healthPayload.default_agent_target_id);
        if (defaultTarget) {
          setSelectedAgentTargetId(defaultTarget.id);
        }
        const customTarget = healthPayload.agent_targets.find((target) => target.id === "custom_agent");
        const customModelOptions = customTarget?.model_options ?? [];
        const defaultCustomModel =
          customModelOptions.find((model) => model.id === customTarget?.default_model_id) ??
          customModelOptions[0];
        if (defaultCustomModel) {
          setSelectedCustomAgentModelId(defaultCustomModel.id);
        }
        setSampleCases(samplePayload.items);
        if (samplePayload.items.length > 0) {
          const seed = samplePayload.items[0];
          setSelectedSeed(seed);
          setFormState(cloneIntake(seed));
        }
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "Unable to load clinical diagnostic support.");
      } finally {
        setLoadingBoot(false);
      }
    };

    void load();
  }, []);

  const changedFromSeed = useMemo(() => diffIntakes(selectedSeed, formState), [selectedSeed, formState]);
  const changedSinceRun = useMemo(() => diffIntakes(submittedIntake, formState), [submittedIntake, formState]);
  const evidenceViews = useMemo(() => buildEvidenceViews(runResult?.evidence_sources ?? []), [runResult?.evidence_sources]);
  const agentTargets: AgentTarget[] =
    health?.agent_targets && health.agent_targets.length > 0
      ? health.agent_targets
      : [
          {
            id: "supervisor",
            label: "Supervisor Agent",
            transport: "model_serving",
            endpoint: health?.supervisor_endpoint,
            available: Boolean(health?.supervisor_endpoint && health.supervisor_endpoint !== "not provisioned"),
            default: true,
            detail: health?.supervisor_endpoint,
            model_options: [],
            default_model_id: null,
          },
        ];
  const selectedAgentTarget =
    agentTargets.find((target) => target.id === selectedAgentTargetId) ??
    agentTargets.find((target) => target.available) ??
    agentTargets[0];
  const selectedAgentTargetLabel = selectedAgentTarget?.label ?? "Recommendation target";
  const customAgentTarget = agentTargets.find((target) => target.id === "custom_agent");
  const customAgentModelOptions: AgentModelOption[] = customAgentTarget?.model_options ?? [];
  const selectedCustomAgentModel =
    customAgentModelOptions.find((model) => model.id === selectedCustomAgentModelId) ??
    customAgentModelOptions.find((model) => model.id === customAgentTarget?.default_model_id) ??
    customAgentModelOptions[0];
  const selectedCustomAgentModelLabel = selectedCustomAgentModel?.label ?? "Default model";
  const selectedRunTargetLabel =
    selectedAgentTargetId === "custom_agent"
      ? `${selectedAgentTargetLabel} · ${selectedCustomAgentModelLabel}`
      : selectedAgentTargetLabel;
  const targetChangedSinceRun =
    Boolean(submittedAgentTargetId) && submittedAgentTargetId !== selectedAgentTargetId;
  const customAgentModelChangedSinceRun =
    selectedAgentTargetId === "custom_agent" &&
    Boolean(submittedCustomAgentModelId) &&
    submittedCustomAgentModelId !== selectedCustomAgentModel?.id;

  const recommendationTitle =
    runResult?.recommendation?.action_type === "abstain"
      ? "No additional diagnostic recommended"
      : runResult?.recommendation?.test_name ?? "Recommendation unavailable";
  const recommendationSummary = cleanClinicalText(
    runResult?.recommendation?.summary ??
      runResult?.message ??
      "Generate a recommendation to review the next diagnostic step."
  );
  const recommendationReasons = normalizeDisplayList(runResult?.recommendation?.reasons ?? []);
  const recommendationEvidenceChips = normalizeDisplayList(runResult?.recommendation?.evidence_chips ?? []);

  const referenceName = selectedSeed ? selectedSeed.sample_label : "New intake";
  const hasReferenceChanges = Boolean(selectedSeed && changedFromSeed.length > 0);
  const referenceChangeCopy = `${changedFromSeed.length} field${
    changedFromSeed.length === 1 ? "" : "s"
  } changed from ${referenceName}.`;
  const workspaceMode =
    panelState.intake && panelState.recommendation
      ? "overview"
      : panelState.intake
      ? "intake"
      : "recommendation";
  const runtimeReady = Boolean(selectedAgentTarget?.available);
  const decisionReviewCount = (runResult?.why_not_selected.length ?? 0) + (runResult?.suppressed_duplicates.length ?? 0);
  const recommendationStatusLabel =
    runResult?.status === "completed"
      ? "Ready"
      : runResult?.status === "needs_input"
      ? "Needs input"
      : runResult?.status === "unavailable"
      ? "Unavailable"
      : "Awaiting recommendation";
  const recommendationStatusClass =
    runResult?.status === "completed"
      ? "status-chip-live"
      : runResult?.status === "needs_input" || runResult?.status === "unavailable"
      ? "status-chip-attention"
      : "status-chip-neutral";
  const workspaceGridClass = classNames(
    "workspace-grid",
    !panelState.intake && "workspace-grid-intake-collapsed",
    !panelState.recommendation && "workspace-grid-recommendation-collapsed"
  );

  function setPanelOpen(panel: PanelKey, open: boolean): void {
    setPanelState((current) => ({
      ...current,
      [panel]: open,
    }));
  }

  function togglePanel(panel: PanelKey): void {
    const otherPanel: PanelKey = panel === "intake" ? "recommendation" : "intake";
    setPanelState((current) => {
      const next = {
        ...current,
        [panel]: !current[panel],
      };
      if (!next.intake && !next.recommendation) {
        next[otherPanel] = true;
      }
      return next;
    });
  }

  function focusWorkspace(mode: "overview" | "intake" | "recommendation"): void {
    if (mode === "overview") {
      setPanelState({ intake: true, recommendation: true });
      return;
    }

    if (mode === "intake") {
      setPanelState({ intake: true, recommendation: false });
      return;
    }

    setPanelState({ intake: false, recommendation: true });
  }

  function selectAgentTarget(target: AgentTarget): void {
    setSelectedAgentTargetId(target.id);
    if (target.id !== "custom_agent") {
      return;
    }
    const modelOptions = target.model_options ?? [];
    const fallbackModel =
      modelOptions.find((model) => model.id === target.default_model_id) ??
      modelOptions[0];
    if (fallbackModel && !modelOptions.some((model) => model.id === selectedCustomAgentModelId)) {
      setSelectedCustomAgentModelId(fallbackModel.id);
    }
  }

  function selectSeedCase(sample: SampleCase): void {
    setSelectedSeed(sample);
    setFormState(cloneIntake(sample));
    setSubmittedIntake(null);
    setRunResult(null);
    setSupportingEvidenceExpanded(false);
    setDecisionReviewExpanded(false);
    setTraceExpanded(false);
    setError(null);
    setPanelOpen("intake", true);
  }

  function startBlankIntake(): void {
    setSelectedSeed(null);
    setFormState(createBlankIntake());
    setSubmittedIntake(null);
    setRunResult(null);
    setSupportingEvidenceExpanded(false);
    setDecisionReviewExpanded(false);
    setTraceExpanded(false);
    setError(null);
    setPanelOpen("intake", true);
  }

  function updateField<K extends keyof EditableVisitInput>(field: K, value: EditableVisitInput[K]): void {
    setFormState((current) => ({
      ...current,
      [field]: value,
    }));
  }

  function updateOverride(testCode: string, rawValue: string): void {
    setFormState((current) => ({
      ...current,
      recent_test_overrides: current.recent_test_overrides.map((override) =>
        override.test_code === testCode
          ? {
              ...override,
              days_since_same_test: rawValue === "" ? null : Number(rawValue),
            }
          : override
      ),
    }));
  }

  async function handleRecommend(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setRunningRecommendation(true);
    setError(null);
    setPanelOpen("recommendation", true);
    try {
      const payload = await postJson<RecommendationRun>("/api/recommendations", {
        agent_target_id: selectedAgentTargetId,
        custom_agent_model_id:
          selectedAgentTargetId === "custom_agent" ? selectedCustomAgentModel?.id ?? selectedCustomAgentModelId : null,
        intake: formState,
      });
      setSubmittedIntake(cloneIntake(formState));
      setSubmittedAgentTargetId(selectedAgentTargetId);
      setSubmittedCustomAgentModelId(
        selectedAgentTargetId === "custom_agent" ? selectedCustomAgentModel?.id ?? selectedCustomAgentModelId : null
      );
      setRunResult(payload);
      setSupportingEvidenceExpanded(false);
      setDecisionReviewExpanded(false);
      setTraceExpanded(false);
      setPanelOpen("recommendation", true);
    } catch (runError) {
      setRunResult(null);
      setError(runError instanceof Error ? runError.message : "Unable to generate a recommendation.");
    } finally {
      setRunningRecommendation(false);
    }
  }

  return (
    <div className="page-shell">
      <header className="app-bar">
        <div className="app-bar-main">
          <div className="brand-block">
            <h1>Clinical Diagnostic Support</h1>
          </div>

          <nav className="app-nav" aria-label="Workspace view">
            <button
              type="button"
              className={classNames("app-nav-item", workspaceMode === "overview" && "app-nav-item-active")}
              onClick={() => focusWorkspace("overview")}
            >
              Overview
            </button>
            <button
              type="button"
              className={classNames("app-nav-item", workspaceMode === "intake" && "app-nav-item-active")}
              onClick={() => focusWorkspace("intake")}
            >
              Intake
            </button>
            <button
              type="button"
              className={classNames("app-nav-item", workspaceMode === "recommendation" && "app-nav-item-active")}
              onClick={() => focusWorkspace("recommendation")}
            >
              Recommendation
            </button>
          </nav>
        </div>

        <div className="runtime-strip">
          {!runtimeReady ? (
            <span className="runtime-chip runtime-chip-limited" title={`${selectedRunTargetLabel} is not available.`}>
              Unavailable
            </span>
          ) : null}
          <div className="agent-target-control" role="radiogroup" aria-label="Recommendation target">
            {agentTargets.map((target) => (
              <button
                key={target.id}
                type="button"
                className={classNames(
                  "agent-target-button",
                  selectedAgentTargetId === target.id && "agent-target-button-active",
                  !target.available && "agent-target-button-disabled"
                )}
                aria-checked={selectedAgentTargetId === target.id}
                disabled={!target.available || runningRecommendation}
                role="radio"
                title={target.detail ?? target.label}
                onClick={() => selectAgentTarget(target)}
              >
                {target.label}
              </button>
            ))}
          </div>
          {selectedAgentTargetId === "custom_agent" && customAgentModelOptions.length > 1 ? (
            <label className="model-select-label">
              <span>Model</span>
              <select
                className="model-select"
                value={selectedCustomAgentModel?.id ?? selectedCustomAgentModelId}
                disabled={runningRecommendation}
                onChange={(event) => setSelectedCustomAgentModelId(event.target.value)}
              >
                {customAgentModelOptions.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
      </header>

      <section className="setup-strip">
        <div className="setup-strip-header">
          <div className="eyebrow">Reference cases</div>
        </div>

        <div className="reference-row">
          <button
            type="button"
            className={classNames(
              "reference-card",
              "reference-card-neutral",
              !selectedSeed && "reference-card-active"
            )}
            onClick={startBlankIntake}
          >
            <div className="reference-card-top">
              <span>New intake</span>
            </div>
            <div className="reference-card-title">Fresh visit</div>
          </button>

          {loadingBoot
            ? Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="reference-card reference-card-skeleton" />
              ))
            : sampleCases.map((sample) => (
                <button
                  key={sample.seed_visit_id ?? `${sample.patient_id}-${sample.as_of_ts}`}
                  type="button"
                  className={classNames(
                    "reference-card",
                    selectedSeed?.seed_visit_id === sample.seed_visit_id && "reference-card-active"
                  )}
                  onClick={() => selectSeedCase(sample)}
                >
                  <div className="reference-card-top">
                    <span>{sample.dog_name ?? sample.patient_id}</span>
                    <span>{sample.as_of_ts}</span>
                  </div>
                  <div className="reference-card-title">{sample.sample_label}</div>
                </button>
              ))}
        </div>
      </section>

      {error ? <div className="error-banner card">{error}</div> : null}

      <main className={workspaceGridClass}>
        <section
          className={classNames(
            "workspace-panel",
            "intake-panel",
            "card",
            !panelState.intake && "workspace-panel-collapsed"
          )}
        >
          {panelState.intake ? (
            <>
              <div className="panel-header">
                <div className="section-header">
                  <div className="callout-label">Intake</div>
                  <h2>Editable intake</h2>
                  <p>Capture the live visit details before generating a recommendation.</p>
                </div>
                <div className="panel-header-actions">
                  <button
                    type="button"
                    className="panel-toggle-button"
                    onClick={() => togglePanel("intake")}
                    aria-expanded={panelState.intake}
                  >
                    Collapse
                  </button>
                </div>
              </div>

              {hasReferenceChanges ? (
                <div className="status-notice status-notice-attention">
                  <div className="status-notice-head">
                    <div>
                      <div className="callout-label">Reference changes</div>
                      <strong>Modified from reference</strong>
                    </div>
                    <span className="status-chip status-chip-attention">Edited</span>
                  </div>
                  <p>{referenceChangeCopy}</p>
                  <div className="diff-chip-row">
                    {changedFromSeed.slice(0, 8).map((field) => (
                      <span key={field} className="diff-chip">
                        {field}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              <form id="intake-form" className="intake-form" onSubmit={handleRecommend}>
                <div className="form-grid">
                  <label className="field">
                    <span>Patient ID</span>
                    <input
                      {...fieldAttrs("patient_id")}
                      value={formState.patient_id}
                      onChange={(event) => updateField("patient_id", event.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span>Dog name</span>
                    <input
                      {...fieldAttrs("dog_name")}
                      value={formState.dog_name ?? ""}
                      onChange={(event) => updateField("dog_name", event.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span>Breed</span>
                    <input
                      {...fieldAttrs("breed")}
                      value={formState.breed ?? ""}
                      onChange={(event) => updateField("breed", event.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span>Life stage</span>
                    <select
                      {...fieldAttrs("life_stage")}
                      value={formState.life_stage ?? ""}
                      onChange={(event) => updateField("life_stage", event.target.value || null)}
                    >
                      <option value="">Unknown</option>
                      <option value="juvenile">Juvenile</option>
                      <option value="adult">Adult</option>
                      <option value="mature">Mature</option>
                      <option value="senior">Senior</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Age (years)</span>
                    <input
                      {...fieldAttrs("age_years")}
                      type="number"
                      step="0.1"
                      value={formState.age_years ?? ""}
                      onChange={(event) =>
                        updateField("age_years", event.target.value === "" ? null : Number(event.target.value))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>As of timestamp</span>
                    <input
                      {...fieldAttrs("as_of_ts")}
                      value={formState.as_of_ts}
                      onChange={(event) => updateField("as_of_ts", event.target.value)}
                    />
                  </label>
                  <label className="field field-wide">
                    <span>Presenting complaint</span>
                    <input
                      {...fieldAttrs("presenting_complaint")}
                      value={formState.presenting_complaint}
                      onChange={(event) => updateField("presenting_complaint", event.target.value)}
                      placeholder="e.g. increased thirst with urinary accidents"
                    />
                  </label>
                  <label className="field">
                    <span>Severity</span>
                    <select
                      {...fieldAttrs("severity")}
                      value={formState.severity}
                      onChange={(event) => updateField("severity", event.target.value)}
                    >
                      <option value="low">Low</option>
                      <option value="moderate">Moderate</option>
                      <option value="high">High</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Urgency</span>
                    <select
                      {...fieldAttrs("urgency_level")}
                      value={formState.urgency_level}
                      onChange={(event) => updateField("urgency_level", event.target.value)}
                    >
                      <option value="routine">Routine</option>
                      <option value="soon">Soon</option>
                      <option value="urgent">Urgent</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Cohort hint</span>
                    <select
                      {...fieldAttrs("cohort_hint")}
                      value={formState.cohort_hint ?? ""}
                      onChange={(event) => updateField("cohort_hint", event.target.value || null)}
                    >
                      <option value="">No hint</option>
                      <option value="wellness">Wellness</option>
                      <option value="gi">GI</option>
                      <option value="renal_urinary">Renal / urinary</option>
                      <option value="endocrine_metabolic">Endocrine / metabolic</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Appetite change</span>
                    <select
                      {...fieldAttrs("appetite_change")}
                      value={formState.appetite_change}
                      onChange={(event) => updateField("appetite_change", event.target.value)}
                    >
                      <option value="stable">Stable</option>
                      <option value="decreased">Decreased</option>
                      <option value="increased">Increased</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Weight change (%)</span>
                    <input
                      {...fieldAttrs("weight_change_pct")}
                      type="number"
                      step="0.1"
                      value={formState.weight_change_pct}
                      onChange={(event) => updateField("weight_change_pct", Number(event.target.value))}
                    />
                  </label>
                  <label className="field">
                    <span>Duration (days)</span>
                    <input
                      {...fieldAttrs("duration_days")}
                      type="number"
                      min="0"
                      value={formState.duration_days}
                      onChange={(event) => updateField("duration_days", Number(event.target.value))}
                    />
                  </label>
                </div>

                <div className="subsection">
                  <div className="subsection-header">
                    <h3>Structured symptoms and signals</h3>
                    <p>Select the findings that should shape the recommendation.</p>
                  </div>

                  <div className="signal-grid">
                    {BOOLEAN_FIELD_LABELS.map(([field, label]) => (
                      <label
                        key={field}
                        className={classNames("signal-tile", Boolean(formState[field]) && "signal-tile-active")}
                      >
                        <input
                          {...fieldAttrs(String(field))}
                          type="checkbox"
                          checked={Boolean(formState[field])}
                          onChange={(event) =>
                            updateField(field, event.target.checked as EditableVisitInput[typeof field])
                          }
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="subsection notes-grid">
                  <label className="field field-tall">
                    <span>Owner note</span>
                    <textarea
                      {...fieldAttrs("owner_note")}
                      value={formState.owner_note}
                      onChange={(event) => updateField("owner_note", event.target.value)}
                    />
                  </label>
                  <label className="field field-tall">
                    <span>Clinician note</span>
                    <textarea
                      {...fieldAttrs("clinician_note")}
                      value={formState.clinician_note}
                      onChange={(event) => updateField("clinician_note", event.target.value)}
                    />
                  </label>
                </div>

                <div className="subsection">
                  <div className="subsection-header">
                    <h3>Recent diagnostic recency</h3>
                    <p>Reflect recently completed diagnostics to avoid redundant recommendations.</p>
                  </div>

                  <div className="override-grid">
                    {formState.recent_test_overrides.map((override) => (
                      <label key={override.test_code} className="field override-field">
                        <span>{testLabel(override.test_code)}</span>
                        <input
                          {...overrideFieldAttrs(override.test_code)}
                          type="number"
                          min="0"
                          placeholder="blank = use history"
                          value={override.days_since_same_test ?? ""}
                          onChange={(event) => updateOverride(override.test_code, event.target.value)}
                        />
                        <small>{override.test_code}</small>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="form-actions">
                  <div className="button-row">
                    <button
                      type="submit"
                      className="primary-button"
                      disabled={runningRecommendation || !selectedAgentTarget?.available}
                    >
                      {runningRecommendation ? "Generating recommendation..." : "Generate Recommendation"}
                    </button>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => (selectedSeed ? selectSeedCase(selectedSeed) : startBlankIntake())}
                    >
                      Reset intake
                    </button>
                  </div>
                </div>
              </form>
            </>
          ) : (
            <CollapsedPanelRail
              kicker="Intake"
              title="Editable intake"
              detail={referenceName}
              onExpand={() => setPanelOpen("intake", true)}
            />
          )}
        </section>

        <section
          className={classNames(
            "workspace-panel",
            "output-panel",
            "card",
            !panelState.recommendation && "workspace-panel-collapsed"
          )}
        >
          {panelState.recommendation ? (
            <>
              <div className="panel-header recommendation-panel-header">
                <div className="panel-header-actions">
                  <span className="status-chip status-chip-neutral">
                    {runResult?.custom_agent_model_label
                      ? `${runResult.agent_target_label} · ${runResult.custom_agent_model_label}`
                      : runResult?.agent_target_label ?? selectedRunTargetLabel}
                  </span>
                  <span className={classNames("status-chip", recommendationStatusClass)}>
                    {recommendationStatusLabel}
                  </span>
                  <button
                    type="button"
                    className="panel-toggle-button"
                    onClick={() => togglePanel("recommendation")}
                    aria-expanded={panelState.recommendation}
                  >
                    Collapse
                  </button>
                </div>
              </div>

              {submittedIntake && (changedSinceRun.length > 0 || targetChangedSinceRun || customAgentModelChangedSinceRun) ? (
                <div className="status-notice status-notice-attention">
                  <div className="status-notice-head">
                    <div>
                      <div className="callout-label">Recommendation out of date</div>
                      <strong>
                        {targetChangedSinceRun
                          ? "Recommendation target changed since the last run"
                          : customAgentModelChangedSinceRun
                          ? "Custom agent model changed since the last run"
                          : "Intake updated since the last run"}
                      </strong>
                    </div>
                    <span className="status-chip status-chip-attention">Refresh needed</span>
                  </div>
                  <p>Generate a new recommendation to reflect the current intake and selected target.</p>
                </div>
              ) : null}

              {runningRecommendation ? (
                <div className="loading-block">
                  <div className="loading-bar loading-bar-wide" />
                  <div className="loading-bar" />
                  <div className="loading-bar loading-bar-short" />
                  <p className="loading-copy">
                    {selectedAgentTargetLabel} is retrieving patient history, similar-case evidence, and diagnostic guidance.
                  </p>
                </div>
              ) : runResult === null ? (
                <div className="empty-state empty-state-compact">
                  <div className="callout-label">Awaiting recommendation</div>
                  <p>
                    Generate a recommendation to review the next action, supporting evidence, and execution trace.
                  </p>
                </div>
              ) : runResult.status === "needs_input" ? (
                <div className="needs-input-card">
                  <div className="callout-label">Needs input</div>
                  <h3>{runResult.message ?? "Add more intake detail before generating a recommendation."}</h3>
                  <div className="diff-chip-row">
                    {runResult.missing_fields.map((field) => (
                      <span key={field} className="diff-chip">
                        {field}
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <>
                  <div className="recommendation-card">
                    <div className="recommendation-header-row">
                      <div className="recommendation-card-main">
                        <div className="callout-label">
                          {runResult.recommendation?.action_type === "abstain"
                            ? "Recommended action"
                            : "Recommended next step"}
                        </div>
                        <div className="recommendation-title">{recommendationTitle}</div>
                        <p className="recommendation-summary">{recommendationSummary}</p>
                        {recommendationReasons.length > 0 ? (
                          <ul className="reason-list">
                            {recommendationReasons.map((reason) => (
                              <li key={reason}>{reason}</li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    </div>

                    {recommendationEvidenceChips.length > 0 ? (
                      <div className="recommendation-chip-block">
                        <div className="callout-label">Key drivers</div>
                        <div className="evidence-chip-row">
                          {recommendationEvidenceChips.map((chip) => (
                            <span key={chip} className="evidence-chip">
                              {chip}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>

                  {runResult.status === "unavailable" ? (
                    <div className="needs-input-card unavailable-card">
                      <div className="callout-label">Unavailable</div>
                      <h3>{runResult.message ?? "The live recommendation could not be completed."}</h3>
                    </div>
                  ) : (
                    <>
                      <section
                        className={classNames(
                          "detail-section",
                          supportingEvidenceExpanded ? "detail-section-open" : "detail-section-collapsed"
                        )}
                      >
                        <button
                          type="button"
                          className="detail-toggle"
                          onClick={() => setSupportingEvidenceExpanded((current) => !current)}
                          aria-expanded={supportingEvidenceExpanded}
                        >
                          <div className="detail-toggle-copy">
                            <div className="callout-label">Supporting evidence</div>
                            <h3>{supportingEvidenceExpanded ? "Evidence used" : "Show supporting evidence"}</h3>
                            {supportingEvidenceExpanded ? (
                              <p>Patient facts, recent-test checks, and grounded guidance used for this recommendation.</p>
                            ) : null}
                          </div>
                          <div className="detail-toggle-meta">
                            <span className="status-chip">{evidenceViews.length} sources</span>
                            <span className="detail-toggle-indicator">
                              {supportingEvidenceExpanded ? "Hide" : "Show"}
                            </span>
                          </div>
                        </button>

                        {supportingEvidenceExpanded ? (
                          evidenceViews.length === 0 ? (
                            <p className="muted-copy">No supporting evidence summaries were returned for this run.</p>
                          ) : (
                            <div className="evidence-grid">
                              {evidenceViews.map((source) => (
                                <article key={source.key} className="evidence-summary-card">
                                  <div className="evidence-card-top">
                                    <div className="callout-label">{source.eyebrow}</div>
                                    <h4>{source.title}</h4>
                                  </div>
                                  {source.points.length > 1 ? (
                                    <ul className="evidence-point-list">
                                      {source.points.map((point) => (
                                        <li key={point}>{point}</li>
                                      ))}
                                    </ul>
                                  ) : source.points.length === 1 ? (
                                    <p className="evidence-note">{source.points[0]}</p>
                                  ) : null}
                                  {source.links.length > 0 ? (
                                    <div className="evidence-link-list">
                                      {source.links.map((link) => (
                                        <a
                                          key={`${link.url}-${link.pmid ?? link.label}`}
                                          className="evidence-link"
                                          href={link.url}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          {formatEvidenceLinkLabel(link)}
                                        </a>
                                      ))}
                                    </div>
                                  ) : null}
                                </article>
                              ))}
                            </div>
                          )
                        ) : null}
                      </section>

                      <section
                        className={classNames(
                          "detail-section",
                          decisionReviewExpanded ? "detail-section-open" : "detail-section-collapsed"
                        )}
                      >
                        <button
                          type="button"
                          className="detail-toggle"
                          onClick={() => setDecisionReviewExpanded((current) => !current)}
                          aria-expanded={decisionReviewExpanded}
                        >
                          <div className="detail-toggle-copy">
                            <div className="callout-label">Decision review</div>
                            <h3>{decisionReviewExpanded ? "Alternatives and constraints" : "Show decision review"}</h3>
                            {decisionReviewExpanded ? (
                              <p>Other diagnostics considered and duplicate-test constraints applied during the run.</p>
                            ) : null}
                          </div>
                          <div className="detail-toggle-meta">
                            <span className="status-chip">{decisionReviewCount} items</span>
                            <span className="detail-toggle-indicator">
                              {decisionReviewExpanded ? "Hide" : "Show"}
                            </span>
                          </div>
                        </button>

                        {decisionReviewExpanded ? (
                          <div className="decision-stack">
                            <div className="decision-section">
                              <div className="section-header">
                                <h4>Alternatives reviewed</h4>
                                <p>Diagnostics considered but not selected for this visit.</p>
                              </div>
                              {runResult.why_not_selected.length === 0 ? (
                                <p className="muted-copy">No alternative diagnostics were returned for this run.</p>
                              ) : (
                                <div className="detail-list">
                                  {runResult.why_not_selected.map((item, index) => (
                                    <div key={`${item.test_code ?? item.test_name}-${index}`} className="detail-list-item">
                                      <div className="detail-list-top">
                                        <strong>{item.test_name ?? item.test_code ?? "Alternative"}</strong>
                                        <span className="detail-list-status">{toTitleCase(item.disposition)}</span>
                                      </div>
                                      <p>{cleanClinicalText(item.why_not_selected)}</p>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>

                            <div className="decision-section">
                              <div className="section-header">
                                <h4>Duplicate suppression</h4>
                                <p>Recent same-test history that blocked or allowed repeat testing.</p>
                              </div>
                              {runResult.suppressed_duplicates.length === 0 ? (
                                <p className="muted-copy">No recent duplicate suppressions were returned for this run.</p>
                              ) : (
                                <div className="detail-list">
                                  {runResult.suppressed_duplicates.map((item, index) => (
                                    <div key={`${item.test_code ?? item.test_name}-${index}`} className="detail-list-item">
                                      <div className="detail-list-top">
                                        <strong>{item.test_name ?? item.test_code ?? "Suppressed diagnostic"}</strong>
                                        <span className="detail-list-status">
                                          {item.days_since_same_test !== undefined && item.days_since_same_test !== null
                                            ? `${formatDays(item.days_since_same_test)}`
                                            : "Recency unavailable"}
                                        </span>
                                      </div>
                                      <p>{cleanClinicalText(item.why_not_selected)}</p>
                                      {item.suppression_window_days ? (
                                        <span className="detail-list-note">
                                          {item.suppression_window_days}-day suppression window
                                        </span>
                                      ) : null}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        ) : null}
                      </section>
                    </>
                  )}

                  <section
                    className={classNames(
                      "detail-section",
                      traceExpanded ? "detail-section-open" : "detail-section-collapsed"
                    )}
                  >
                    <button
                      type="button"
                      className="detail-toggle"
                      onClick={() => setTraceExpanded((current) => !current)}
                      aria-expanded={traceExpanded}
                    >
                      <div className="detail-toggle-copy">
                        <div className="callout-label">Execution trace</div>
                        <h3>{traceExpanded ? "Recommendation trace" : "Show execution details"}</h3>
                        {traceExpanded ? <p>Review the live steps captured for this recommendation.</p> : null}
                      </div>
                      <div className="detail-toggle-meta">
                        <span className="status-chip">{runResult.trace_steps.length} steps</span>
                        <span className="status-chip">{formatLatency(runResult.total_duration_ms)}</span>
                        <span className="detail-toggle-indicator">{traceExpanded ? "Hide" : "Show"}</span>
                      </div>
                    </button>

                    {traceExpanded ? (
                      <div className="trace-stack">
                        {runResult.trace_steps.map((step) => (
                          <article key={`${step.step_index}-${step.label}`} className="trace-step">
                            <div className="trace-step-top">
                              <div>
                                <div className="callout-label">Step {step.step_index}</div>
                                <h4>{step.label}</h4>
                              </div>
                              <div className="trace-step-meta">
                                <span className={classNames("trace-status", `trace-status-${step.status}`)}>
                                  {step.status}
                                </span>
                                <span className="status-chip">{toTitleCase(step.kind)}</span>
                                <span className="trace-duration">{formatLatency(step.duration_ms)}</span>
                              </div>
                            </div>
                            <div className="trace-tool">{step.tool_name ?? "Tool unavailable"}</div>
                            {step.arguments ? (
                              <pre className="trace-preview">{JSON.stringify(step.arguments, null, 2)}</pre>
                            ) : null}
                            {step.output_preview ? <pre className="trace-preview">{step.output_preview}</pre> : null}
                          </article>
                        ))}
                      </div>
                    ) : null}
                  </section>
                </>
              )}
            </>
          ) : (
            <CollapsedPanelRail
              kicker="Recommendation"
              title="Recommendation"
              detail={runResult?.status === "completed" ? recommendationTitle : recommendationStatusLabel}
              badgeLabel={recommendationStatusLabel}
              badgeClassName={recommendationStatusClass}
              onExpand={() => setPanelOpen("recommendation", true)}
            />
          )}
        </section>
      </main>
    </div>
  );
}
