"""Normalize supervisor output into the stable app response shape."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import EditableVisitInput
from .runtime_repository import RuntimeRepository
from .supervisor_runtime import TOOL_SPECS, TRACE_TOOL_ORDER, summarize_output_preview


EMPTY_RECENT_VISITS = json.loads("[]")
CLINICAL_SIGNAL_LABELS = [
    ("vomiting", "vomiting"),
    ("diarrhea", "diarrhea"),
    ("urinary_accidents", "urinary accidents"),
    ("straining_to_urinate", "straining to urinate"),
    ("increased_thirst", "increased thirst"),
    ("increased_hunger", "increased hunger"),
    ("lethargy", "lethargy"),
]


class RecommendationAssembler:
    def __init__(self, repository: RuntimeRepository):
        self.repository = repository

    def clean_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    def short_sentence(self, value: Any, *, max_length: int = 160) -> str | None:
        cleaned = self.clean_text(value)
        if not cleaned:
            return None

        sentence_match = re.search(r"(?<=[.!?])\s", cleaned)
        if sentence_match:
            cleaned = cleaned[: sentence_match.start() + 1]

        if len(cleaned) <= max_length:
            return cleaned

        shortened = cleaned[: max_length - 1].rsplit(" ", 1)[0]
        return f"{shortened or cleaned[: max_length - 1]}..."

    def normalize_text_list(
        self,
        value: Any,
        *,
        max_items: int,
        max_length: int,
    ) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = [value]
        else:
            raw_items = []

        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            candidate = self.short_sentence(item, max_length=max_length)
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
            if len(normalized) >= max_items:
                break
        return normalized

    def normalize_chip_list(self, value: Any, *, max_items: int = 5) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = [value]
        else:
            raw_items = []

        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            cleaned = self.clean_text(item)
            if not cleaned:
                continue
            chip = cleaned.rstrip(".,;:")
            if len(chip) > 44:
                chip = f"{chip[:43].rsplit(' ', 1)[0] or chip[:43]}..."
            key = chip.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(chip)
            if len(normalized) >= max_items:
                break
        return normalized

    def format_label(self, value: str | None) -> str:
        if not value:
            return "Unknown"
        return value.replace("_", " ").replace("/", " / ").title()

    def active_signal_labels(self, intake: EditableVisitInput) -> list[str]:
        return [
            label
            for field_name, label in CLINICAL_SIGNAL_LABELS
            if getattr(intake, field_name, False)
        ]

    def normalize_history_context(self, history_context: Any) -> dict[str, Any]:
        if not isinstance(history_context, dict):
            return {
                "history_found": False,
                "patient_id": None,
                "as_of_ts": None,
                "dog_name": None,
                "breed": None,
                "age_years": None,
                "life_stage": None,
                "prior_visit_count": 0,
                "prior_diagnostic_summary": "no prior diagnostics recorded",
                "prior_treatment_summary": "no prior treatments recorded",
                "recent_visits": EMPTY_RECENT_VISITS,
            }

        return {
            "history_found": bool(history_context.get("history_found")),
            "patient_id": history_context.get("patient_id"),
            "as_of_ts": history_context.get("as_of_ts"),
            "dog_name": history_context.get("dog_name"),
            "breed": history_context.get("breed"),
            "age_years": history_context.get("age_years"),
            "life_stage": history_context.get("life_stage"),
            "prior_visit_count": int(history_context.get("prior_visit_count") or 0),
            "prior_diagnostic_summary": history_context.get("prior_diagnostic_summary")
            or "no prior diagnostics recorded",
            "prior_treatment_summary": history_context.get("prior_treatment_summary")
            or "no prior treatments recorded",
            "recent_visits": history_context.get("recent_visits") or EMPTY_RECENT_VISITS,
        }

    def normalize_recent_test_audit(self, audit_payload: Any) -> list[dict[str, Any]]:
        if isinstance(audit_payload, list):
            return [item for item in audit_payload if isinstance(item, dict)]
        return []

    def normalize_test_metadata(self, metadata_payload: Any) -> list[dict[str, Any]]:
        if isinstance(metadata_payload, list):
            return [item for item in metadata_payload if isinstance(item, dict)]
        return []

    def metadata_by_code(self, metadata_payload: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            item.get("test_code"): item
            for item in metadata_payload
            if item.get("test_code")
        }

    def normalize_action_type(self, action_type: Any, *, test_code: Any) -> str:
        normalized = self.clean_text(action_type)
        if normalized:
            normalized = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")

        if normalized in {
            "recommend_test",
            "order_test",
            "recommend",
            "recommended_test",
            "recommend_diagnostic",
            "order_diagnostic",
            "diagnostic_recommendation",
            "next_best_test",
        }:
            return "recommend_test"

        if normalized in {
            "abstain",
            "no_additional_diagnostic",
            "needs_more_information",
            "need_more_information",
            "insufficient_evidence",
            "defer",
            "monitor",
            "watchful_waiting",
        }:
            return "abstain"

        if test_code:
            return "recommend_test"
        return "abstain"

    def default_recommendation_summary(self, *, action_type: str, test_name: str | None) -> str:
        if action_type == "abstain":
            return "No additional diagnostic is recommended until more evidence is gathered."
        return f"Proceed with {test_name or 'the recommended diagnostic'} based on the current visit evidence."

    def default_recommendation_reasons(
        self,
        intake: EditableVisitInput,
        history_context: dict[str, Any],
        derived_cohort: str | None,
    ) -> list[str]:
        reasons: list[str] = []
        signal_labels = self.active_signal_labels(intake)
        if signal_labels:
            reasons.append(f"Current intake highlights {', '.join(signal_labels[:3])}.")
        if derived_cohort and derived_cohort != "unassigned":
            reasons.append(f"Visit context aligns with the {self.format_label(derived_cohort)} pathway.")
        prior_visit_count = int(history_context.get("prior_visit_count") or 0)
        if prior_visit_count > 0:
            reasons.append(f"Historical review includes {prior_visit_count} prior visit(s) before this encounter.")
        if intake.urgency_level:
            reasons.append(f"Urgency is set to {self.format_label(intake.urgency_level).lower()}.")
        return reasons[:4]

    def default_evidence_chips(
        self,
        *,
        intake: EditableVisitInput,
        history_context: dict[str, Any],
        recent_test_audit: list[dict[str, Any]],
        derived_cohort: str | None,
    ) -> list[str]:
        chips: list[str] = []
        if derived_cohort and derived_cohort != "unassigned":
            chips.append(f"{self.format_label(derived_cohort)} cohort")
        signal_labels = self.active_signal_labels(intake)
        if signal_labels:
            chips.append(f"Signals: {', '.join(signal_labels[:2])}")
        prior_visit_count = int(history_context.get("prior_visit_count") or 0)
        chips.append(
            f"{prior_visit_count} prior visit{'s' if prior_visit_count != 1 else ''}"
            if prior_visit_count > 0
            else "No prior visit history"
        )
        if intake.severity:
            chips.append(f"{self.format_label(intake.severity)} severity")
        suppressed_count = sum(1 for item in recent_test_audit if item.get("suppressed"))
        chips.append(
            f"{suppressed_count} recent duplicate block{'s' if suppressed_count != 1 else ''}"
            if suppressed_count > 0
            else "No recent duplicate block"
        )
        return chips[:5]

    def build_trace_steps(
        self,
        parsed_response: dict[str, Any] | None,
        *,
        synthesis_status: str,
        synthesis_duration_ms: int | None,
        synthesis_preview: str | None,
        supervisor_name: str,
        agent_target_label: str = "Supervisor Agent",
    ) -> list[dict[str, Any]]:
        if parsed_response is None:
            return [
                {
                    "step_index": 1,
                    "label": f"{agent_target_label} orchestration",
                    "kind": "supervisor",
                    "tool_name": supervisor_name,
                    "status": "failed",
                    "duration_ms": synthesis_duration_ms,
                    "arguments": None,
                    "output_preview": synthesis_preview[:320] if synthesis_preview else None,
                }
            ]

        trace_steps: list[dict[str, Any]] = []
        tool_calls = parsed_response.get("tool_calls") or []
        tool_outputs = parsed_response.get("tool_outputs") or {}

        for step_index, tool_name in enumerate(TRACE_TOOL_ORDER, start=1):
            call = next((item for item in tool_calls if item.get("name") == tool_name), None)
            output = tool_outputs.get(tool_name)
            spec = TOOL_SPECS[tool_name]
            trace_steps.append(
                {
                    "step_index": step_index,
                    "label": spec["label"],
                    "kind": spec["kind"],
                    "tool_name": (
                        output.get("raw_name")
                        if output
                        else call.get("raw_name")
                        if call
                        else tool_name
                    ),
                    "status": "completed" if output or call else "missing",
                    "duration_ms": output.get("duration_ms") if output else None,
                    "arguments": call.get("arguments") if call else None,
                    "output_preview": summarize_output_preview(output.get("payload") if output else None),
                }
            )

        trace_steps.append(
            {
                "step_index": len(TRACE_TOOL_ORDER) + 1,
                "label": f"{agent_target_label} synthesis",
                "kind": "supervisor",
                "tool_name": supervisor_name,
                "status": synthesis_status,
                "duration_ms": synthesis_duration_ms,
                "arguments": None,
                "output_preview": synthesis_preview[:320] if synthesis_preview else None,
            }
        )
        return trace_steps

    def build_visit_summary(
        self,
        intake: EditableVisitInput,
        history_context: dict[str, Any],
        assistant_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        visit_summary = assistant_payload.get("visit_summary") if assistant_payload else {}
        patient = self.repository.fetch_patient_record(intake.patient_id)
        derived_cohort = "unassigned"
        if isinstance(visit_summary, dict):
            derived_cohort = visit_summary.get("derived_cohort") or derived_cohort
        if derived_cohort == "unassigned" and intake.cohort_hint:
            derived_cohort = intake.cohort_hint

        return {
            "patient_id": intake.patient_id,
            "seed_visit_id": intake.seed_visit_id,
            "dog_name": history_context.get("dog_name") or intake.dog_name or patient.get("dog_name") or intake.patient_id,
            "breed": history_context.get("breed") or intake.breed or patient.get("breed") or "Unknown",
            "age_years": history_context.get("age_years") or intake.age_years or patient.get("age_years"),
            "life_stage": history_context.get("life_stage") or intake.life_stage or patient.get("life_stage"),
            "as_of_ts": intake.as_of_ts,
            "presenting_complaint": intake.presenting_complaint,
            "severity": intake.severity,
            "urgency_level": intake.urgency_level,
            "owner_note": intake.owner_note,
            "clinician_note": intake.clinician_note,
            "derived_cohort": derived_cohort,
        }

    def normalize_recommendation(
        self,
        assistant_payload: dict[str, Any],
        metadata_by_code: dict[str, dict[str, Any]],
        *,
        intake: EditableVisitInput,
        history_context: dict[str, Any],
        recent_test_audit: list[dict[str, Any]],
        derived_cohort: str | None,
    ) -> dict[str, Any]:
        recommendation = assistant_payload.get("recommendation")
        if not isinstance(recommendation, dict):
            raise RuntimeError("Supervisor JSON did not include a recommendation object.")

        test_code = recommendation.get("test_code")
        action_type = self.normalize_action_type(recommendation.get("action_type"), test_code=test_code)
        metadata = metadata_by_code.get(test_code)
        legacy_why = self.normalize_text_list(recommendation.get("why"), max_items=4, max_length=160)
        summary = self.short_sentence(
            recommendation.get("summary") or (legacy_why[0] if legacy_why else None),
            max_length=140,
        )
        test_name = recommendation.get("test_name") or (metadata.get("test_name") if metadata else None)
        summary = summary or self.default_recommendation_summary(
            action_type=action_type,
            test_name=test_name,
        )
        reasons = self.normalize_text_list(recommendation.get("reasons"), max_items=4, max_length=160)
        if not reasons and legacy_why:
            reasons = legacy_why[1:4] if len(legacy_why) > 1 else legacy_why[:1]
        if not reasons:
            reasons = self.default_recommendation_reasons(intake, history_context, derived_cohort)
        evidence_chips = self.normalize_chip_list(recommendation.get("evidence_chips"))
        if not evidence_chips:
            evidence_chips = self.default_evidence_chips(
                intake=intake,
                history_context=history_context,
                recent_test_audit=recent_test_audit,
                derived_cohort=derived_cohort,
            )
        return {
            "action_type": action_type,
            "test_code": test_code,
            "test_name": test_name,
            "summary": summary,
            "reasons": reasons[:4],
            "evidence_chips": evidence_chips[:5],
        }

    def build_why_not_selected(
        self,
        assistant_payload: dict[str, Any],
        metadata_by_code: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        why_not_selected = assistant_payload.get("why_not_selected")
        if not isinstance(why_not_selected, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in why_not_selected:
            if not isinstance(item, dict):
                continue
            test_code = item.get("test_code")
            metadata = metadata_by_code.get(test_code)
            reason = self.short_sentence(
                item.get("why_not_selected")
                or "Not selected by the supervisor after reviewing live evidence.",
                max_length=140,
            )
            normalized.append(
                {
                    "test_code": test_code,
                    "test_name": item.get("test_name") or (metadata.get("test_name") if metadata else None),
                    "disposition": item.get("disposition")
                    if item.get("disposition") in {"not_selected", "suppressed_duplicate", "insufficient_evidence"}
                    else "not_selected",
                    "why_not_selected": reason
                    or "Not selected by the supervisor after reviewing live evidence.",
                    "days_since_same_test": item.get("days_since_same_test"),
                    "suppression_window_days": item.get("suppression_window_days"),
                }
            )
        return normalized[:3]

    def build_suppressed_duplicates(
        self,
        recent_test_audit: list[dict[str, Any]],
        metadata_by_code: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        suppressed: list[dict[str, Any]] = []
        for item in recent_test_audit:
            if not item.get("suppressed"):
                continue
            test_code = item.get("test_code")
            metadata = metadata_by_code.get(test_code)
            reason = self.short_sentence(
                item.get("suppression_reason")
                or "Not selected because the same test was completed recently.",
                max_length=140,
            )
            suppressed.append(
                {
                    "test_code": test_code,
                    "test_name": item.get("test_name") or (metadata.get("test_name") if metadata else None),
                    "disposition": "suppressed_duplicate",
                    "why_not_selected": reason
                    or "Not selected because the same test was completed recently.",
                    "days_since_same_test": item.get("days_since_same_test"),
                    "suppression_window_days": item.get("suppression_window_days"),
                }
            )
        return suppressed[:3]

    def clean_scalar_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return self.clean_text(value)
        if isinstance(value, (int, float)):
            return str(value)
        return None

    def parse_jsonish(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        current: Any = value
        for _ in range(2):
            if not isinstance(current, str):
                return current
            parsed = self.clean_text(current)
            if not parsed:
                return current
            try:
                current = json.loads(parsed)
            except json.JSONDecodeError:
                return current
        return current

    def unwrap_tool_scalar(self, value: Any) -> Any:
        current = self.parse_jsonish(value)
        for _ in range(4):
            current = self.parse_jsonish(current)
            if not isinstance(current, dict):
                return current

            rows = current.get("rows")
            if isinstance(rows, list) and rows and isinstance(rows[0], list) and rows[0]:
                current = rows[0][0]
                continue

            data_array = current.get("result", {}).get("data_array")
            if isinstance(data_array, list) and data_array and isinstance(data_array[0], list) and data_array[0]:
                current = data_array[0][0]
                continue

            if set(current.keys()) <= {"payload"} and "payload" in current:
                current = current["payload"]
                continue

            return current
        return current

    def pubmed_tool_payload(self, parsed_response: dict[str, Any]) -> dict[str, Any] | None:
        tool_output = (parsed_response.get("tool_outputs") or {}).get("query_pubmed")
        if not isinstance(tool_output, dict):
            return None
        payload = self.unwrap_tool_scalar(tool_output.get("payload"))
        return payload if isinstance(payload, dict) else None

    def build_pubmed_links(self, parsed_response: dict[str, Any]) -> list[dict[str, str]]:
        pubmed_payload = self.pubmed_tool_payload(parsed_response)
        if pubmed_payload is None:
            return []
        articles = self.unwrap_tool_scalar(pubmed_payload.get("articles"))
        if not isinstance(articles, list):
            return []

        links: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for raw_article in articles:
            article = self.unwrap_tool_scalar(raw_article)
            if not isinstance(article, dict):
                continue
            pmid = self.clean_scalar_text(article.get("pmid"))
            url = self.clean_scalar_text(article.get("pubmed_url")) or self.clean_scalar_text(article.get("url"))
            if not url and pmid:
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            if not url or url in seen_urls:
                continue
            label = (
                self.short_sentence(article.get("title"), max_length=120)
                or self.short_sentence(article.get("label"), max_length=120)
                or (f"PMID {pmid}" if pmid else "PubMed article")
            )
            link = {"label": label, "url": url}
            if pmid:
                link["pmid"] = pmid
            links.append(link)
            seen_urls.add(url)
        return links[:5]

    def build_pubmed_summary(self, parsed_response: dict[str, Any]) -> str:
        pubmed_payload = self.pubmed_tool_payload(parsed_response)
        if pubmed_payload is None:
            return "PubMed literature lookup returned article links."
        returned = pubmed_payload.get("returned")
        query = self.clean_scalar_text(pubmed_payload.get("query"))
        if isinstance(returned, str):
            try:
                returned = int(returned)
            except ValueError:
                pass
        if isinstance(returned, int) and query:
            return f"PubMed returned {returned} article(s) for {query}."
        if isinstance(returned, int):
            return f"PubMed returned {returned} article(s)."
        return "PubMed literature lookup returned article links."

    def is_pubmed_source(self, source: dict[str, Any]) -> bool:
        source_type = str(source.get("source_type") or "").lower()
        source_label = str(source.get("source_label") or "").lower()
        return source_type in {"query_pubmed", "pubmed"} or "pubmed" in source_label

    def build_evidence_sources(
        self,
        assistant_payload: dict[str, Any],
        history_context: dict[str, Any],
        parsed_response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        pubmed_links = self.build_pubmed_links(parsed_response)
        payload_sources = assistant_payload.get("evidence_sources")
        if isinstance(payload_sources, list):
            normalized = [
                {
                    "source_type": item.get("source_type") or "runtime_evidence",
                    "source_label": item.get("source_label") or "Runtime evidence",
                    "summary": item.get("summary") or "Evidence source returned without a summary.",
                    "links": [],
                }
                for item in payload_sources
                if isinstance(item, dict)
            ]
            if normalized:
                pubmed_source_found = False
                if pubmed_links:
                    for source in normalized:
                        if self.is_pubmed_source(source):
                            source["links"] = pubmed_links
                            pubmed_source_found = True
                            break
                    if not pubmed_source_found:
                        normalized.append(
                            {
                                "source_type": "query_pubmed",
                                "source_label": "PubMed literature",
                                "summary": self.build_pubmed_summary(parsed_response),
                                "links": pubmed_links,
                            }
                        )
                return normalized

        tool_outputs = parsed_response.get("tool_outputs") or {}
        genie_preview = summarize_output_preview(tool_outputs.get("similar_case_genie", {}).get("payload"))
        guidance_preview = summarize_output_preview(tool_outputs.get("diagnostic_guidance_ka", {}).get("payload"))
        sources = [
            {
                "source_type": "patient_history",
                "source_label": "Patient history",
                "summary": (
                    f"{history_context['prior_visit_count']} prior visit(s); "
                    f"{history_context['prior_diagnostic_summary']}"
                ),
                "links": [],
            },
            {
                "source_type": "similar_case_genie",
                "source_label": "Structured similar-case evidence",
                "summary": genie_preview or "Structured evidence tool did not return a readable preview.",
                "links": [],
            },
            {
                "source_type": "diagnostic_guidance_ka",
                "source_label": "Diagnostic guidance",
                "summary": guidance_preview or "Guidance tool did not return a readable preview.",
                "links": [],
            },
        ]
        if pubmed_links:
            sources.append(
                {
                    "source_type": "query_pubmed",
                    "source_label": "PubMed literature",
                    "summary": self.build_pubmed_summary(parsed_response),
                    "links": pubmed_links,
                }
            )
        return sources

    def build_completed_result(
        self,
        intake: EditableVisitInput,
        parsed_response: dict[str, Any],
        supervisor_name: str,
        supervisor_run_id: str | None,
        total_duration_ms: int,
        synthesis_duration_ms: int | None,
        agent_target_id: str = "supervisor",
        agent_target_label: str = "Supervisor Agent",
        custom_agent_model_id: str | None = None,
        custom_agent_model_label: str | None = None,
    ) -> dict[str, Any]:
        tool_outputs = parsed_response.get("tool_outputs") or {}
        assistant_payload = parsed_response.get("assistant_payload")
        if not isinstance(assistant_payload, dict):
            raise RuntimeError("Agent target did not return a valid JSON recommendation payload.")

        history_context = self.normalize_history_context(tool_outputs["get_patient_history"].get("payload"))
        recent_test_audit = self.normalize_recent_test_audit(tool_outputs["get_recent_test_audit"].get("payload"))
        test_metadata = self.normalize_test_metadata(tool_outputs.get("get_test_metadata", {}).get("payload"))
        metadata_by_code = self.metadata_by_code(test_metadata)
        visit_summary = self.build_visit_summary(intake, history_context, assistant_payload)
        recommendation = self.normalize_recommendation(
            assistant_payload,
            metadata_by_code,
            intake=intake,
            history_context=history_context,
            recent_test_audit=recent_test_audit,
            derived_cohort=visit_summary.get("derived_cohort"),
        )
        return {
            "status": "completed",
            "message": f"Live recommendation completed from {agent_target_label}.",
            "agent_target_id": agent_target_id,
            "agent_target_label": agent_target_label,
            "custom_agent_model_id": custom_agent_model_id,
            "custom_agent_model_label": custom_agent_model_label,
            "agent_run_id": supervisor_run_id,
            "supervisor_run_id": supervisor_run_id,
            "total_duration_ms": total_duration_ms,
            "visit": visit_summary,
            "recommendation": recommendation,
            "why_not_selected": self.build_why_not_selected(assistant_payload, metadata_by_code),
            "suppressed_duplicates": self.build_suppressed_duplicates(recent_test_audit, metadata_by_code),
            "evidence_sources": self.build_evidence_sources(assistant_payload, history_context, parsed_response),
            "trace_steps": self.build_trace_steps(
                parsed_response,
                synthesis_status="completed",
                synthesis_duration_ms=synthesis_duration_ms,
                synthesis_preview=parsed_response.get("assistant_text"),
                supervisor_name=supervisor_name,
                agent_target_label=agent_target_label,
            ),
        }

    def build_unavailable_result(
        self,
        intake: EditableVisitInput,
        message: str,
        total_duration_ms: int,
        synthesis_duration_ms: int | None,
        supervisor_name: str,
        supervisor_run_id: str | None = None,
        parsed_response: dict[str, Any] | None = None,
        agent_target_id: str = "supervisor",
        agent_target_label: str = "Supervisor Agent",
        custom_agent_model_id: str | None = None,
        custom_agent_model_label: str | None = None,
    ) -> dict[str, Any]:
        history_context = {}
        if parsed_response:
            history_output = (parsed_response.get("tool_outputs") or {}).get("get_patient_history")
            if history_output:
                history_context = self.normalize_history_context(history_output.get("payload"))

        return {
            "status": "unavailable",
            "message": message,
            "agent_target_id": agent_target_id,
            "agent_target_label": agent_target_label,
            "custom_agent_model_id": custom_agent_model_id,
            "custom_agent_model_label": custom_agent_model_label,
            "agent_run_id": supervisor_run_id,
            "supervisor_run_id": supervisor_run_id,
            "total_duration_ms": total_duration_ms,
            "visit": self.build_visit_summary(intake, history_context, None),
            "recommendation": None,
            "why_not_selected": [],
            "suppressed_duplicates": [],
            "evidence_sources": [],
            "trace_steps": self.build_trace_steps(
                parsed_response,
                synthesis_status="failed",
                synthesis_duration_ms=synthesis_duration_ms,
                synthesis_preview=message,
                supervisor_name=supervisor_name,
                agent_target_label=agent_target_label,
            ),
        }
